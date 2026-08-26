"""Ingreso, salida y cambio de contraseña."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.core import security
from src.core.config import settings
from src.core.plantillas import render
from src.services import recuperacion, usuarios

router = APIRouter()


def _inicio_del_rol(rol: str) -> str:
    return "/admin/negocios" if rol == security.ROL_ADMIN else "/conversaciones"


def _destino_tras_login(rol: str, siguiente: str) -> str:
    """Respeta la URL pendiente solo cuando pertenece al rol autenticado.

    El navegador suele volver a la última página abierta. Si esa página era
    `/conversaciones` y después entra el administrador, enviarlo allí produce
    un 403 nada más iniciar sesión. Lo mismo ocurriría al revés con `/admin`.
    """
    inicio = _inicio_del_rol(rol)
    if not siguiente.startswith("/") or siguiente.startswith("//"):
        return inicio
    if siguiente == "/":
        return inicio
    if rol == security.ROL_ADMIN:
        return siguiente if siguiente.startswith("/admin/") else inicio
    return inicio if siguiente.startswith("/admin/") else siguiente


def _redirigir(destino: str, aviso: str = "", error: str = "") -> RedirectResponse:
    separador = "&" if "?" in destino else "?"
    if aviso:
        destino = f"{destino}{separador}aviso={aviso}"
    elif error:
        destino = f"{destino}{separador}error={error}"
    return RedirectResponse(url=destino, status_code=303)


@router.get("/")
def inicio(request: Request):
    """Cada rol aterriza en su espacio de trabajo."""
    usuario = security.usuario_actual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
    if usuario["debe_cambiar_password"]:
        return RedirectResponse(url="/password", status_code=303)
    return RedirectResponse(url=_inicio_del_rol(usuario["rol"]), status_code=303)


@router.get("/login")
def login_form(request: Request):
    if security.usuario_actual(request):
        return RedirectResponse(url="/", status_code=303)
    return render(request, "login.html", None, siguiente=request.query_params.get("siguiente", "/"))


@router.post("/login")
def login(
    request: Request,
    usuario: str = Form(...),
    password: str = Form(...),
    siguiente: str = Form("/"),
):
    # La clave del freno es la IP: si fuera el nombre de usuario, cualquiera
    # podría dejar fuera al administrador fallando adrede ocho veces.
    clave = request.client.host if request.client else "desconocido"
    if security.demasiados_intentos(clave):
        return render(
            request,
            "login.html",
            None,
            siguiente=siguiente,
            error="Demasiados intentos fallidos. Espera unos minutos.",
        )

    cuenta = usuarios.autenticar(usuario, password)
    if not cuenta:
        security.registrar_intento_fallido(clave)
        # Mensaje único: decir "ese usuario no existe" revelaría qué cuentas hay.
        return render(
            request, "login.html", None, siguiente=siguiente, error="Usuario o contraseña incorrectos."
        )

    security.limpiar_intentos(clave)
    token = security.crear_sesion(
        cuenta["id"],
        ip=clave,
        user_agent=request.headers.get("user-agent", ""),
    )
    # Además de impedir redirecciones externas, no se conserva una página del
    # otro rol: el administrador nunca debe aterrizar en el panel del negocio.
    destino = _destino_tras_login(cuenta["rol"], siguiente)
    respuesta = RedirectResponse(url=destino, status_code=303)
    respuesta.set_cookie(
        settings.SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=settings.SESSION_TTL_HOURS * 3600,
    )
    return respuesta


@router.post("/logout")
def logout(request: Request):
    token = request.cookies.get(settings.SESSION_COOKIE_NAME, "")
    if token:
        security.cerrar_sesion(token)
    respuesta = RedirectResponse(url="/login", status_code=303)
    respuesta.delete_cookie(settings.SESSION_COOKIE_NAME)
    return respuesta


# --- Recuperación de la cuenta de administrador ------------------------------

@router.get("/recuperar")
def recuperar_form(request: Request):
    """Pide el código. Solo llega al Telegram autorizado del dueño."""
    return render(
        request,
        "recuperar.html",
        None,
        paso=request.query_params.get("paso", "solicitar"),
        usuario_pedido=request.query_params.get("usuario", ""),
        configurado=recuperacion.canal_configurado(),
    )


@router.post("/recuperar")
def recuperar_solicitar(request: Request, usuario: str = Form(...)):
    clave = request.client.host if request.client else "desconocido"
    # Mismo freno que el login: pedir códigos en masa no debe ser gratis.
    if security.demasiados_intentos(f"recuperar:{clave}"):
        return _redirigir("/recuperar", error="Demasiadas solicitudes. Espera unos minutos.")
    security.registrar_intento_fallido(f"recuperar:{clave}")

    _, mensaje = recuperacion.solicitar(usuario, ip=clave)
    return RedirectResponse(
        url=f"/recuperar?paso=confirmar&usuario={usuario}&aviso={mensaje}", status_code=303
    )


@router.post("/recuperar/confirmar")
def recuperar_confirmar(
    request: Request,
    usuario: str = Form(...),
    codigo: str = Form(...),
    nueva: str = Form(...),
    repetir: str = Form(...),
):
    destino = f"/recuperar?paso=confirmar&usuario={usuario}"
    if nueva != repetir:
        return _redirigir(destino, error="Las contraseñas no coinciden.")

    ok, mensaje = recuperacion.confirmar(usuario, codigo, nueva)
    if not ok:
        return _redirigir(destino, error=mensaje)
    return RedirectResponse(url=f"/login?aviso={mensaje}", status_code=303)


@router.post("/salir-de-cuenta")
def salir_de_cuenta(request: Request, usuario=Depends(security.requiere_admin_titular)):
    """Vuelve a tu propia cuenta tras entrar a la de un cliente.

    Depende de `requiere_admin_titular` y no de `requiere_admin`: mientras
    suplantas, el usuario efectivo es el cliente y `requiere_admin` daría 403,
    dejándote atrapado en su panel.
    """
    usuarios.terminar_suplantacion(usuario["token"], usuario["admin_real_id"])
    return RedirectResponse(url="/admin/usuarios?aviso=Volviste a tu cuenta.", status_code=303)


# --- Cambio de contraseña ----------------------------------------------------
#
# «Mi cuenta» ya NO es una página: con qué cuenta estás dentro, de qué proyecto
# es y el cambio de contraseña viven en una ventana flotante del armazón
# (`templates/base.html`), disponible desde el menú de la cuenta en cualquier
# pantalla. Como página era un panel de «sesiones abiertas» y un rol que decía
# «cliente»: nada que le sirviera a quien la abría.
#
# Esta ruta se queda porque el cambio OBLIGATORIO del primer ingreso sí necesita
# una pantalla propia: ahí no hay panel al que volver hasta que la contraseña
# provisional se sustituya.

@router.get("/password")
def password_form(request: Request, usuario=Depends(security.requiere_sesion)):
    return render(request, "password.html", usuario)


@router.post("/password")
def cambiar_password(
    request: Request,
    actual: str = Form(...),
    nueva: str = Form(...),
    repetir: str = Form(...),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_sesion),
):
    security.verificar_csrf(request, csrf)

    if usuario.get("suplantado_por"):
        # Estás viendo el panel de un cliente: cambiarle la contraseña desde
        # aquí sería hacerlo en su nombre y sin su conocimiento.
        return _redirigir(
            "/password",
            error="Estás dentro de la cuenta de un cliente. Sal de ella para cambiar tu contraseña.",
        )

    if nueva != repetir:
        return _redirigir("/password", error="Las contraseñas no coinciden.")
    if len(nueva) < 10:
        return _redirigir("/password", error="La contraseña debe tener al menos 10 caracteres.")
    if not usuarios.autenticar(usuario["usuario"], actual):
        return _redirigir("/password", error="La contraseña actual no es correcta.")

    # Cambiar la clave cierra todas las sesiones, incluida esta: hay que reingresar.
    usuarios.cambiar_password(usuario["id"], nueva)
    if usuario["rol"] == security.ROL_ADMIN:
        # A partir de aquí manda la base: el arranque ya no re-aplica el .env
        # (hasta que cambies ADMIN_PASSWORD allí).
        usuarios.marcar_password_fuera_del_entorno()
    respuesta = RedirectResponse(url="/login?aviso=Contraseña actualizada, ingresa de nuevo.", status_code=303)
    respuesta.delete_cookie(settings.SESSION_COOKIE_NAME)
    return respuesta
