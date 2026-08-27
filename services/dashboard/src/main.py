"""Dashboard de operación: facturación, catálogos, envíos y trazabilidad.

Sirve páginas renderadas en el servidor con Jinja. No hay build de JavaScript ni
framework de frontend: el único script propio es `static/app.js`, que escucha
las novedades por SSE, repinta fragmentos, edita celdas en línea y abre los dos
menús. Todo se descarga desde este mismo servicio, sin CDNs.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from src.core import eventos, security
from src.core.config import settings
from src.core.plantillas import plantillas
from src.db.migrate import aplicar_migraciones
from src.routes import admin, auth, catalogo, envios, negocio, panel, registros, tiempo_real
from src.services import fragmentos, usuarios

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
    fragmentos.sembrar_catalogos_faltantes()

    novedad = usuarios.sincronizar_admin()
    if novedad:
        print("\n" + "=" * 70, novedad, "=" * 70 + "\n", sep="\n")

    # El hub de novedades. Queda esperando y no consulta nada hasta que alguien
    # abre el panel.
    eventos.arrancar()
    try:
        yield
    finally:
        await eventos.detener()


app = FastAPI(title="Dashboard Autos", docs_url=None, redoc_url=None, lifespan=arranque)

# El HTML comprime muchísimo (una tabla de reportes baja a la cuarta parte), y
# ahora que los fragmentos viajan solos cada vez que cambia algo, eso se nota.
#
# `text/event-stream` queda FUERA a propósito: comprimir un flujo obliga a
# acumular bytes antes de mandarlos, y el aviso de que llegó un reporte se
# quedaría esperando a llenar el buffer. Starlette no distingue por tipo, así
# que se filtra aquí.
class GZipSalvoFlujos(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/eventos":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(GZipSalvoFlujos, minimum_size=500)


class EstaticosCacheados(StaticFiles):
    """Los estáticos se cachean para siempre; el `?v=` es quien los renueva.

    Sin cabecera de caché el navegador revalidaba `app.css` y `app.js` en CADA
    carga de página: dos peticiones de ida y vuelta para recibir un 304. Como la
    URL ya lleva versión (`?v=17` en base.html), la respuesta puede declararse
    inmutable sin riesgo de servir algo viejo.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:
        response_headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return super().is_not_modified(response_headers, request_headers)

    async def get_response(self, path, scope):
        respuesta = await super().get_response(path, scope)
        respuesta.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return respuesta


app.mount("/static", EstaticosCacheados(directory="src/static"), name="static")

app.include_router(auth.router)
app.include_router(panel.router)
app.include_router(catalogo.router)
app.include_router(envios.router)
app.include_router(registros.router)
app.include_router(negocio.router)
app.include_router(admin.router)
app.include_router(tiempo_real.router)


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
