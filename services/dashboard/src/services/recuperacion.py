"""Recuperación de la contraseña del administrador con un código por Telegram.

Cómo está pensado para que no se pueda abusar:

- El código **solo se envía a un chat de Telegram fijo** (`ADMIN_TELEGRAM_CHAT_ID`).
  Cualquiera puede pedirlo desde la pantalla de login; solo quien tenga ese
  Telegram puede leerlo. Ese es el segundo factor real.
- Se guarda **hasheado**, como una contraseña: quien lea la base no puede usarlo.
- Caduca a los `RECUPERACION_CODIGO_MINUTOS` (10 por defecto), es de un solo uso,
  y tras `RECUPERACION_MAX_INTENTOS` fallidos queda inutilizado.
- Pedir un código nuevo **invalida los anteriores**, para que no queden varios
  válidos a la vez.
- La pantalla responde lo mismo exista o no la cuenta: si dijera "ese usuario no
  existe" serviría para averiguar qué cuentas hay.

Solo aplica a cuentas de administrador: un cliente que pierde su contraseña se la
restablece su administrador desde el panel.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.core import security
from src.core.config import settings
from src.db import pool

# Seis dígitos: cómodo de teclear desde el móvil y, con caducidad de 10 minutos
# y tope de 5 intentos, muy lejos de poder adivinarse (5 de un millón).
_LONGITUD = 6


def canal_configurado() -> bool:
    return bool(settings.ADMIN_TELEGRAM_CHAT_ID and settings.TELEGRAM_BOT_TOKEN)


def _generar_codigo() -> str:
    return f"{secrets.randbelow(10 ** _LONGITUD):0{_LONGITUD}d}"


def solicitar(usuario: str, ip: str = "") -> tuple[bool, str]:
    """Crea un código y lo manda al Telegram autorizado.

    Devuelve `(enviado, mensaje_para_la_pantalla)`. El mensaje es siempre el
    mismo hacia el usuario final; el booleano solo sirve para registrar.
    """
    generico = (
        "Si esa cuenta puede recuperarse, el código ya salió por Telegram. "
        "Revisa tu chat con el bot."
    )

    if not canal_configurado():
        return False, (
            "La recuperación por Telegram no está configurada en el servidor "
            "(faltan ADMIN_TELEGRAM_CHAT_ID o TELEGRAM_BOT_TOKEN)."
        )

    cuenta = pool.consultar_uno(
        "SELECT id, usuario, rol, activo FROM dashboard_usuarios WHERE usuario = %s",
        ((usuario or "").strip(),),
    )
    if not cuenta or not cuenta["activo"] or cuenta["rol"] != security.ROL_ADMIN:
        return False, generico

    codigo = _generar_codigo()
    # Un código nuevo deja sin efecto los anteriores: si no, varios códigos
    # válidos a la vez amplían la ventana de un atacante sin ningún beneficio.
    pool.ejecutar(
        "UPDATE codigos_recuperacion SET usado_en = NOW() WHERE usuario_id = %s AND usado_en IS NULL",
        (cuenta["id"],),
    )
    pool.ejecutar(
        """
        INSERT INTO codigos_recuperacion (usuario_id, codigo_hash, expira_en, ip)
        VALUES (%s, %s, %s, %s)
        """,
        (
            cuenta["id"],
            security.hashear_password(codigo),
            datetime.now(timezone.utc) + timedelta(minutes=settings.RECUPERACION_CODIGO_MINUTOS),
            ip[:64],
        ),
    )

    if not _enviar_por_telegram(cuenta["usuario"], codigo):
        return False, (
            "No se pudo enviar el código por Telegram. Revisa el token del bot y "
            "que hayas iniciado una conversación con él."
        )
    return True, generico


def _enviar_por_telegram(usuario: str, codigo: str) -> bool:
    texto = (
        "🔐 Recuperación de acceso al panel\n\n"
        f"Usuario: {usuario}\n"
        f"Código: {codigo}\n\n"
        f"Vence en {settings.RECUPERACION_CODIGO_MINUTOS} minutos y solo sirve una vez.\n"
        "Si no lo pediste tú, ignóralo: sin este código nadie puede entrar."
    )
    try:
        respuesta = httpx.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": settings.ADMIN_TELEGRAM_CHAT_ID, "text": texto},
            timeout=10.0,
        )
        respuesta.raise_for_status()
        return True
    except Exception as e:
        print(f"No se pudo enviar el código de recuperación por Telegram: {e}")
        return False


def _codigo_vigente(usuario_id: int) -> dict[str, Any] | None:
    return pool.consultar_uno(
        """
        SELECT * FROM codigos_recuperacion
        WHERE usuario_id = %s AND usado_en IS NULL AND expira_en > NOW()
              AND intentos < %s
        ORDER BY creado_en DESC LIMIT 1
        """,
        (usuario_id, settings.RECUPERACION_MAX_INTENTOS),
    )


def confirmar(usuario: str, codigo: str, password_nueva: str) -> tuple[bool, str]:
    """Valida el código y, si es correcto, cambia la contraseña."""
    if len(password_nueva) < 10:
        return False, "La contraseña debe tener al menos 10 caracteres."

    cuenta = pool.consultar_uno(
        "SELECT id, rol, activo FROM dashboard_usuarios WHERE usuario = %s",
        ((usuario or "").strip(),),
    )
    invalido = "El código no es válido o ya venció. Pide uno nuevo."
    if not cuenta or not cuenta["activo"] or cuenta["rol"] != security.ROL_ADMIN:
        return False, invalido

    vigente = _codigo_vigente(cuenta["id"])
    if not vigente:
        return False, invalido

    if not security.verificar_password((codigo or "").strip(), vigente["codigo_hash"]):
        # El intento se cuenta ANTES de responder: si no, se podrían probar
        # códigos indefinidamente.
        pool.ejecutar(
            "UPDATE codigos_recuperacion SET intentos = intentos + 1 WHERE id = %s",
            (vigente["id"],),
        )
        restantes = settings.RECUPERACION_MAX_INTENTOS - (vigente["intentos"] + 1)
        if restantes <= 0:
            return False, "Código incorrecto. Se agotaron los intentos; pide uno nuevo."
        return False, f"Código incorrecto. Te quedan {restantes} intento(s)."

    pool.ejecutar("UPDATE codigos_recuperacion SET usado_en = NOW() WHERE id = %s", (vigente["id"],))

    from src.services import usuarios

    # `cambiar_password` cierra todas las sesiones abiertas: si la cuenta se está
    # recuperando, cualquier sesión existente es sospechosa.
    usuarios.cambiar_password(cuenta["id"], password_nueva)
    # A partir de aquí manda la base, no el `.env`: ver `usuarios.sincronizar_admin`.
    usuarios.marcar_password_fuera_del_entorno()
    return True, "Contraseña actualizada. Ya puedes ingresar."
