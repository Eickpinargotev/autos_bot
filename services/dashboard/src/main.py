"""Dashboard de operación: facturación, catálogos, envíos y trazabilidad.

Sirve páginas renderadas en el servidor con Jinja. No hay build de JavaScript ni
framework de frontend: el único script propio son ~130 líneas para refrescar el
panel de consumo, editar celdas en línea y abrir/cerrar los dos menús. Todo se
descarga desde este mismo servicio, sin CDNs.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.core import security
from src.core.config import settings
from src.core.plantillas import plantillas
from src.db.migrate import aplicar_migraciones
from src.routes import admin, auth, catalogo, envios, negocio, panel
from src.services import usuarios

@asynccontextmanager
async def arranque(app: FastAPI):
    """Comprobaciones y preparación previas a atender la primera petición."""
    # Sin secreto de sesión las cookies serían falsificables. Se falla al
    # arrancar, en vez de servir un login inseguro sin que nadie lo note.
    if not settings.SESSION_SECRET:
        raise RuntimeError(
            "Falta SESSION_SECRET en el entorno: sin él las sesiones no son seguras."
        )

    aplicar_migraciones()

    novedad = usuarios.sincronizar_admin()
    if novedad:
        print("\n" + "=" * 70, novedad, "=" * 70 + "\n", sep="\n")
    yield


app = FastAPI(title="Dashboard Autos", docs_url=None, redoc_url=None, lifespan=arranque)

app.mount("/static", StaticFiles(directory="src/static"), name="static")

app.include_router(auth.router)
app.include_router(panel.router)
app.include_router(catalogo.router)
app.include_router(envios.router)
app.include_router(negocio.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# Rutas que deben seguir siendo accesibles con la contraseña sin cambiar; si no,
# el usuario quedaría atrapado sin poder cambiarla ni salir.
_SIN_BLOQUEO = ("/password", "/logout", "/login", "/health", "/static")


@app.middleware("http")
async def forzar_cambio_de_password(request: Request, call_next):
    """Con una contraseña provisional no se puede usar el resto del panel.

    Se controla aquí y no en cada ruta: una ruta nueva que se olvide de
    comprobarlo quedaría abierta sin que nadie lo note.
    """
    if not request.url.path.startswith(_SIN_BLOQUEO):
        usuario = security.usuario_actual(request)
        if usuario and usuario["debe_cambiar_password"]:
            return RedirectResponse(url="/password", status_code=303)
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def errores(request: Request, exc: StarletteHTTPException):
    """Sin sesión se manda al login; sin permisos, a una página que lo explica.

    Devolver el JSON crudo de FastAPI dejaría al usuario mirando `{"detail":...}`
    sin saber qué hacer.
    """
    if exc.status_code == 401:
        return RedirectResponse(url=f"/login?siguiente={request.url.path}", status_code=303)

    return plantillas.TemplateResponse(
        request,
        "error.html",
        {"codigo": exc.status_code, "detalle": exc.detail, "usuario": None},
        status_code=exc.status_code,
    )
