"""Cuentas del panel: alta, baja, autenticación e impersonación."""

import hashlib
import secrets
from typing import Any

from src.core import security
from src.core.config import settings
from src.db import pool

# Marca en `sistema_config` con la huella de la contraseña que venía del `.env`.
# Sirve para distinguir "el dueño editó el .env" de "la contraseña cambió desde
# el panel", que es lo que decide si el arranque debe re-aplicarla.
_CLAVE_HUELLA = "admin_password_huella_env"
_CLAVE_DESINCRONIZADO = "admin_password_fuera_del_env"


# --- Consultas ---------------------------------------------------------------

def listar() -> list[dict[str, Any]]:
    """Las cuentas, con el negocio de cada una.

    El nombre del negocio va en la misma consulta porque «rol: cliente» a secas
    no dice nada: en el panel del administrador, «cliente» es un NEGOCIO, y lo
    que hace falta saber de una cuenta es a cuál pertenece.
    """
    return pool.consultar(
        """
        SELECT u.id, u.usuario, u.rol, u.activo, u.debe_cambiar_password,
               u.creado_en, u.ultimo_acceso,
               c.id AS negocio_id, c.nombre AS negocio
        FROM dashboard_usuarios u
        LEFT JOIN clientes_whatsapp c ON c.usuario_id = u.id
        ORDER BY u.rol, u.usuario
        """
    )


def listar_negocios_sin_vincular(incluir_id: int | None = None) -> list[dict[str, Any]]:
    """Cuentas de negocio que todavía no son de ningún cliente.

    `incluir_id` deja además la cuenta ya vinculada a ESTE cliente, para que el
    desplegable de su perfil muestre lo que tiene puesto y no aparezca vacío.
    """
    return pool.consultar(
        """
        SELECT u.id, u.usuario, u.activo
        FROM dashboard_usuarios u
        LEFT JOIN clientes_whatsapp c ON c.usuario_id = u.id
        WHERE u.rol = 'cliente' AND (c.id IS NULL OR u.id = %s)
        ORDER BY u.usuario
        """,
        (incluir_id,),
    )


def obtener(usuario_id: int) -> dict[str, Any] | None:
    return pool.consultar_uno(
        "SELECT id, usuario, rol, activo, debe_cambiar_password FROM dashboard_usuarios WHERE id = %s",
        (usuario_id,),
    )


def buscar_por_usuario(usuario: str) -> dict[str, Any] | None:
    return pool.consultar_uno("SELECT * FROM dashboard_usuarios WHERE usuario = %s", (usuario,))


# --- Altas y cambios ---------------------------------------------------------

def generar_password() -> str:
    """Contraseña provisional legible pero no adivinable."""
    return secrets.token_urlsafe(12)


def crear(usuario: str, password: str, rol: str, debe_cambiar: bool = True) -> dict[str, Any]:
    if rol not in (security.ROL_ADMIN, security.ROL_CLIENTE):
        raise ValueError(f"Rol desconocido: {rol}")
    return pool.consultar_uno(
        """
        INSERT INTO dashboard_usuarios (usuario, password_hash, rol, debe_cambiar_password)
        VALUES (%s, %s, %s, %s)
        RETURNING id, usuario, rol, activo
        """,
        (usuario.strip(), security.hashear_password(password), rol, debe_cambiar),
    )


def renombrar(usuario_id: int, nuevo: str) -> dict[str, Any]:
    """Cambia el nombre con el que una cuenta ingresa.

    Existe porque el nombre de la cuenta se elige al crearla y hasta ahora era
    definitivo: un proyecto se quedaba con «Cliente Germán» de por vida, que ni
    es una persona ni dice quién entra. El nombre es la credencial con la que se
    ingresa, así que se valida contra el resto: dos cuentas con el mismo nombre
    harían que el login no supiera cuál es cuál.

    NO cierra las sesiones abiertas: la sesión va por token, no por nombre, y
    echar a alguien del panel por corregirle una letra sería un castigo raro.
    """
    nuevo = str(nuevo or "").strip()
    if not nuevo:
        raise ValueError("El nombre de usuario no puede estar vacío.")

    ocupado = buscar_por_usuario(nuevo)
    if ocupado and ocupado["id"] != int(usuario_id):
        raise ValueError(f"Ya existe una cuenta con el usuario «{nuevo}».")

    fila = pool.consultar_uno(
        "UPDATE dashboard_usuarios SET usuario = %s WHERE id = %s RETURNING id, usuario, rol, activo",
        (nuevo, int(usuario_id)),
    )
    if not fila:
        raise ValueError("Esa cuenta ya no existe.")
    return fila


def cambiar_password(usuario_id: int, password: str) -> None:
    """Cambia la clave y cierra TODAS sus sesiones.

    Si la clave se cambió porque se sospecha que estaba comprometida, dejar las
    sesiones abiertas haría inútil el cambio.
    """
    pool.ejecutar(
        "UPDATE dashboard_usuarios SET password_hash = %s, debe_cambiar_password = FALSE WHERE id = %s",
        (security.hashear_password(password), usuario_id),
    )
    security.cerrar_sesiones_de(usuario_id)


def alternar_activo(usuario_id: int) -> dict[str, Any] | None:
    fila = pool.consultar_uno(
        "UPDATE dashboard_usuarios SET activo = NOT activo WHERE id = %s RETURNING id, usuario, rol, activo",
        (usuario_id,),
    )
    if fila and not fila["activo"]:
        security.cerrar_sesiones_de(usuario_id)
    return fila


def autenticar(usuario: str, password: str) -> dict[str, Any] | None:
    fila = buscar_por_usuario(usuario.strip())
    if not fila or not fila["activo"]:
        # Se verifica igual un hash falso para que la respuesta tarde lo mismo
        # exista o no el usuario: si no, el tiempo delata qué usuarios existen.
        security.verificar_password(password, "$2b$12$" + "x" * 53)
        return None
    if not security.verificar_password(password, fila["password_hash"]):
        return None
    pool.ejecutar("UPDATE dashboard_usuarios SET ultimo_acceso = NOW() WHERE id = %s", (fila["id"],))
    return fila


# --- Cuenta de administrador definida en el entorno --------------------------

def _config_leer(clave: str) -> str:
    fila = pool.consultar_uno("SELECT valor FROM sistema_config WHERE clave = %s", (clave,))
    return (fila or {}).get("valor") or ""


def _config_escribir(clave: str, valor: str) -> None:
    pool.ejecutar(
        """
        INSERT INTO sistema_config (clave, valor, actualizado_en) VALUES (%s, %s, NOW())
        ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor, actualizado_en = NOW()
        """,
        (clave, valor),
    )


def _huella(texto: str) -> str:
    """Huella de la contraseña del entorno; nunca se guarda el valor en claro."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def password_fuera_del_entorno() -> bool:
    """¿La contraseña real del admin ya no es la del `.env`?

    Pasa cuando se cambió desde el panel o recuperándola por Telegram. El panel
    lo avisa para que el dueño sepa que su `.env` está desactualizado.
    """
    return _config_leer(_CLAVE_DESINCRONIZADO) == "1"


def marcar_password_fuera_del_entorno() -> None:
    _config_escribir(_CLAVE_DESINCRONIZADO, "1")


def sincronizar_admin() -> str:
    """Crea o actualiza la cuenta de administrador según el `.env`.

    Reglas, pensadas para que el dueño nunca se quede fuera y a la vez no se le
    revierta un cambio hecho a propósito:

    - Si la cuenta no existe, se crea con `ADMIN_PASSWORD`.
    - Si existe y `ADMIN_PASSWORD` **cambió respecto a la última vez**, se aplica
      la nueva. Editar el `.env` es, por tanto, una forma válida de recuperar el
      acceso teniendo el servidor a mano.
    - Si el `.env` no cambió, no se toca nada: así una contraseña puesta desde el
      panel (o recuperada por Telegram) sobrevive a los reinicios.

    Devuelve un texto describiendo qué se hizo, para el log de arranque.
    """
    nombre = (settings.ADMIN_USER or "admin").strip()
    password = settings.ADMIN_PASSWORD
    cuenta = buscar_por_usuario(nombre)

    if not cuenta:
        if not password:
            # Sin contraseña definida se genera una y se avisa: es preferible a
            # arrancar sin ninguna cuenta de administrador.
            password = generar_password()
            crear(nombre, password, security.ROL_ADMIN, debe_cambiar=False)
            _config_escribir(_CLAVE_HUELLA, "")
            marcar_password_fuera_del_entorno()
            return (
                f"Administrador '{nombre}' creado con una contraseña temporal: {password}\n"
                "Define ADMIN_PASSWORD en el .env para fijar la tuya."
            )
        crear(nombre, password, security.ROL_ADMIN, debe_cambiar=False)
        _config_escribir(_CLAVE_HUELLA, _huella(password))
        _config_escribir(_CLAVE_DESINCRONIZADO, "0")
        return f"Administrador '{nombre}' creado con la contraseña de ADMIN_PASSWORD."

    if password and _huella(password) != _config_leer(_CLAVE_HUELLA):
        cambiar_password(cuenta["id"], password)
        _config_escribir(_CLAVE_HUELLA, _huella(password))
        _config_escribir(_CLAVE_DESINCRONIZADO, "0")
        return (
            f"ADMIN_PASSWORD cambió en el entorno: la contraseña de '{nombre}' se actualizó "
            "y se cerraron sus sesiones."
        )

    return ""


# --- Impersonación -----------------------------------------------------------

def puede_suplantar(objetivo: dict[str, Any] | None) -> bool:
    """Solo se entra a cuentas de cliente activas.

    Suplantar a otro administrador no aportaría nada (ve lo mismo) y sí abriría
    un camino para saltarse un cambio de contraseña ajeno.
    """
    return bool(objetivo and objetivo["activo"] and objetivo["rol"] == security.ROL_CLIENTE)


def iniciar_suplantacion(token_sesion: str, admin_id: int, objetivo_id: int, ip: str = "") -> None:
    pool.ejecutar(
        "UPDATE dashboard_sesiones SET suplantando_a = %s WHERE token = %s",
        (objetivo_id, token_sesion),
    )
    pool.ejecutar(
        "INSERT INTO accesos_suplantacion (admin_id, objetivo_id, ip) VALUES (%s, %s, %s)",
        (admin_id, objetivo_id, ip[:64]),
    )


def terminar_suplantacion(token_sesion: str, admin_id: int) -> None:
    pool.ejecutar(
        "UPDATE dashboard_sesiones SET suplantando_a = NULL WHERE token = %s", (token_sesion,)
    )
    pool.ejecutar(
        """
        UPDATE accesos_suplantacion SET fin_en = NOW()
        WHERE admin_id = %s AND fin_en IS NULL
        """,
        (admin_id,),
    )


def historial_suplantacion(limite: int = 100) -> list[dict[str, Any]]:
    return pool.consultar(
        """
        SELECT a.*, ad.usuario AS admin, ob.usuario AS objetivo
        FROM accesos_suplantacion a
        LEFT JOIN dashboard_usuarios ad ON ad.id = a.admin_id
        LEFT JOIN dashboard_usuarios ob ON ob.id = a.objetivo_id
        ORDER BY a.inicio_en DESC
        LIMIT %s
        """,
        (int(limite),),
    )
