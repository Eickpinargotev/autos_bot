"""Contraseñas, sesiones, CSRF y control de acceso por rol."""

import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from fastapi import HTTPException, Request

from src.core.config import settings
from src.db import pool

ROL_ADMIN = "admin"
# El valor guardado sigue siendo "cliente" (es lo que hay en la base y en las
# sesiones abiertas), pero lo que designa es un NEGOCIO: uno de nuestros
# clientes, con su propio panel. `ROL_CLIENTE` se conserva como alias para no
# romper el código que ya lo usaba.
ROL_NEGOCIO = "cliente"
ROL_CLIENTE = ROL_NEGOCIO


# --- Contraseñas -------------------------------------------------------------

def hashear_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verificar_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Hash corrupto o vacío: se trata como credencial inválida, no como error.
        return False


# --- Sesiones ----------------------------------------------------------------

def crear_sesion(usuario_id: int, ip: str = "", user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)[:64]
    expira = datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_TTL_HOURS)
    pool.ejecutar(
        """
        INSERT INTO dashboard_sesiones (token, usuario_id, expira_en, ip, user_agent)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (token, usuario_id, expira, ip[:64], (user_agent or "")[:500]),
    )
    return token


def cerrar_sesion(token: str) -> None:
    pool.ejecutar("DELETE FROM dashboard_sesiones WHERE token = %s", (token,))


def cerrar_sesiones_de(usuario_id: int) -> None:
    """Invalida todas las sesiones de un usuario (cambio de clave, baja, etc.)."""
    pool.ejecutar("DELETE FROM dashboard_sesiones WHERE usuario_id = %s", (usuario_id,))


def purgar_sesiones_vencidas() -> int:
    return pool.ejecutar("DELETE FROM dashboard_sesiones WHERE expira_en < NOW()")


def usuario_de_sesion(token: str) -> dict[str, Any] | None:
    """Usuario EFECTIVO de la sesión.

    Si un administrador está suplantando a un cliente, devuelve al cliente: así
    ve exactamente el mismo panel que él, con las mismas restricciones (incluido
    el 403 en las rutas de administrador). Se añaden dos campos para que la
    interfaz avise de la suplantación y permita salir de ella:
    `suplantado_por` y `admin_real_id`.
    """
    if not token:
        return None

    sesion = pool.consultar_uno(
        """
        SELECT u.id, u.usuario, u.rol, u.activo, u.debe_cambiar_password, s.token,
               s.suplantando_a, s.usuario_id AS titular_id
        FROM dashboard_sesiones s
        JOIN dashboard_usuarios u ON u.id = s.usuario_id
        WHERE s.token = %s AND s.expira_en > NOW() AND u.activo
        """,
        (token,),
    )
    if not sesion:
        return None

    if not sesion.get("suplantando_a"):
        return {**sesion, "suplantado_por": None, "admin_real_id": sesion["id"]}

    # Solo un administrador puede estar suplantando; si el titular dejó de serlo
    # (o se desactivó), la suplantación se ignora y vuelve a ser él mismo.
    if sesion["rol"] != ROL_ADMIN:
        return {**sesion, "suplantado_por": None, "admin_real_id": sesion["id"]}

    objetivo = pool.consultar_uno(
        "SELECT id, usuario, rol, activo, debe_cambiar_password FROM dashboard_usuarios "
        "WHERE id = %s AND activo",
        (sesion["suplantando_a"],),
    )
    if not objetivo:
        return {**sesion, "suplantado_por": None, "admin_real_id": sesion["id"]}

    return {
        **objetivo,
        "token": sesion["token"],
        # Al suplantar nunca se arrastra el "debe cambiar contraseña" del
        # cliente: el admin no debe poder cambiársela por este camino.
        "debe_cambiar_password": False,
        "suplantado_por": sesion["usuario"],
        "admin_real_id": sesion["titular_id"],
    }


# --- CSRF --------------------------------------------------------------------
#
# El token se deriva del token de sesión con HMAC: no hace falta guardarlo ni
# sincronizar estado entre procesos, y solo es válido para esa sesión.

def token_csrf(token_sesion: str) -> str:
    return hmac.new(
        settings.SESSION_SECRET.encode("utf-8"),
        f"csrf:{token_sesion}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verificar_csrf(request: Request, enviado: str) -> None:
    token_sesion = request.cookies.get(settings.SESSION_COOKIE_NAME, "")
    if not token_sesion or not hmac.compare_digest(token_csrf(token_sesion), enviado or ""):
        raise HTTPException(status_code=403, detail="Token CSRF inválido")


# --- Freno a la fuerza bruta en el login -------------------------------------
#
# En memoria del proceso: suficiente para una instalación de un solo contenedor
# y sin dependencias extra. Si algún día hay varias réplicas, esto se mueve a
# Redis (que ya está en el stack).

_intentos: dict[str, list[float]] = {}
_intentos_lock = threading.Lock()


def registrar_intento_fallido(clave: str) -> None:
    ahora = time.time()
    with _intentos_lock:
        marcas = [t for t in _intentos.get(clave, []) if ahora - t < settings.LOGIN_VENTANA_SEGUNDOS]
        marcas.append(ahora)
        _intentos[clave] = marcas


def limpiar_intentos(clave: str) -> None:
    with _intentos_lock:
        _intentos.pop(clave, None)


def demasiados_intentos(clave: str) -> bool:
    ahora = time.time()
    with _intentos_lock:
        marcas = [t for t in _intentos.get(clave, []) if ahora - t < settings.LOGIN_VENTANA_SEGUNDOS]
        _intentos[clave] = marcas
        return len(marcas) >= settings.LOGIN_MAX_INTENTOS


# --- Dependencias de acceso --------------------------------------------------

def usuario_actual(request: Request) -> dict[str, Any] | None:
    """Usuario de la petición, o None si no hay sesión válida."""
    return usuario_de_sesion(request.cookies.get(settings.SESSION_COOKIE_NAME, ""))


def requiere_sesion(request: Request) -> dict[str, Any]:
    usuario = usuario_actual(request)
    if not usuario:
        raise HTTPException(status_code=401, detail="Sesión requerida")
    return usuario


def requiere_admin(request: Request) -> dict[str, Any]:
    """Puerta única del rol administrador.

    Todo lo que solo debe ver el dueño (costo real, logs, tarifas, cierre de
    periodo, incidencias, usuarios) depende de esta función. Un `cliente` recibe
    403 aunque escriba la URL a mano.
    """
    usuario = requiere_sesion(request)
    if usuario["rol"] != ROL_ADMIN:
        raise HTTPException(status_code=403, detail="Solo el administrador puede ver esto")
    return usuario


def requiere_negocio(request: Request) -> dict[str, Any]:
    """Puerta de las páginas que son el trabajo del NEGOCIO, no el nuestro.

    El conocimiento, las preguntas sin responder, los reportes, los mensajes y
    su catálogo los administra el negocio. El administrador no las ve en su
    menú: no son su trabajo, y verlas todas mezcladas cuando haya varios
    negocios no significaría nada.

    Para entrar a ellas, el administrador usa la suplantación desde el perfil
    del negocio (`/admin/negocios/{id}`), que además deja registro de quién
    entró y cuándo — un acceso directo sin rastro sería peor.
    """
    usuario = requiere_sesion(request)
    if usuario["rol"] != ROL_NEGOCIO:
        raise HTTPException(
            status_code=403,
            detail="Esta página es del panel del negocio. Entra a su perfil desde Clientes.",
        )
    return usuario


def requiere_admin_titular(request: Request) -> dict[str, Any]:
    """El administrador dueño de la sesión, esté o no suplantando a alguien.

    La usa solo la salida de la suplantación: mientras suplanta, el usuario
    efectivo es un cliente y `requiere_admin` daría 403, dejándolo atrapado en
    el panel del cliente sin poder volver a su cuenta.
    """
    usuario = requiere_sesion(request)
    if usuario.get("suplantado_por"):
        return usuario
    if usuario["rol"] != ROL_ADMIN:
        raise HTTPException(status_code=403, detail="Solo el administrador puede ver esto")
    return usuario
