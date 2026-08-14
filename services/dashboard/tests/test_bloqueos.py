"""Ver quién está bloqueado y levantarle el bloqueo.

Los bloqueos se ponen SOLOS (el dueño contesta desde su teléfono, alguien entra
al grupo, un `/block`) y hasta ahora la única forma de deshacer uno era entrar a
la base a mano. Lo que se prueba aquí es que la pantalla no mienta —un bloqueo
vencido ya no calla a nadie— y que desbloquear borre de verdad las dos formas de
la clave, porque dejar una huérfana mantendría al usuario mudo sin explicación.
"""

from datetime import datetime, timedelta

from src.db import pool
from src.services import bloqueos
from tests.conftest import token_csrf


def _bloquear(user_id: str, *, motivo: str = "prueba", vence_en_horas: float | None = 24):
    expira = (
        datetime.utcnow() + timedelta(hours=vence_en_horas) if vence_en_horas is not None else None
    )
    pool.ejecutar(
        "INSERT INTO users_blocked (user_id, reason, expires_at) VALUES (%s, %s, %s)",
        (user_id, motivo, expira),
    )


def test_la_clave_se_parte_en_canal_y_numero():
    _bloquear("whatsapp:50688888888")

    fila = bloqueos.listar()[0]

    assert fila["canal"] == "whatsapp"
    assert fila["client_id"] == "50688888888"
    assert fila["en_vigor"] is True


def test_una_fila_antigua_sin_canal_se_entiende_como_telegram():
    """Quedan bloqueos guardados solo con el id, de antes de que hubiera canal."""
    _bloquear("123456789")

    fila = bloqueos.listar()[0]

    assert fila["canal"] == "telegram"
    assert fila["client_id"] == "123456789"


def test_un_bloqueo_vencido_no_se_presenta_como_vigente():
    """Sigue en la tabla hasta que el bot la pisa, pero ya no calla a nadie."""
    _bloquear("whatsapp:50611110001", vence_en_horas=-1)

    fila = bloqueos.listar()[0]

    assert fila["vencido"] is True
    assert fila["en_vigor"] is False
    assert bloqueos.estado_de("whatsapp", "50611110001") is None


def test_un_bloqueo_sin_vencimiento_es_permanente():
    _bloquear("whatsapp:50622220002", vence_en_horas=None)

    fila = bloqueos.listar()[0]

    assert fila["permanente"] is True
    assert bloqueos.estado_de("whatsapp", "50622220002")["permanente"] is True


def test_la_busqueda_ignora_el_formato_del_numero():
    _bloquear("whatsapp:50688888888")
    _bloquear("whatsapp:50699999999")

    encontrados = bloqueos.listar("+506 8888-8888")

    assert [b["client_id"] for b in encontrados] == ["50688888888"]


def test_desbloquear_borra_las_dos_formas_de_la_clave():
    """Con canal y sin él: una fila huérfana dejaría al usuario mudo sin motivo."""
    _bloquear("telegram:123456789")
    _bloquear("123456789")

    bloqueos.desbloquear("telegram", "123456789")

    assert bloqueos.listar() == []


def test_un_numero_de_whatsapp_no_hereda_el_bloqueo_de_un_id_de_telegram():
    """La clave sin canal solo vale para Telegram, igual que en el bot."""
    _bloquear("50688888888")

    assert bloqueos.estado_de("whatsapp", "50688888888") is None
    assert bloqueos.estado_de("telegram", "50688888888") is not None


def test_el_admin_desbloquea_desde_el_panel(sesion_admin):
    _bloquear("whatsapp:50633330003")

    respuesta = sesion_admin.post(
        "/admin/bloqueos/whatsapp/50633330003/desbloquear",
        data={"csrf": token_csrf(sesion_admin)},
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    assert bloqueos.estado_de("whatsapp", "50633330003") is None


def test_el_negocio_no_puede_desbloquear(sesion_cliente):
    _bloquear("whatsapp:50644440004")

    respuesta = sesion_cliente.post(
        "/admin/bloqueos/whatsapp/50644440004/desbloquear",
        data={"csrf": token_csrf(sesion_cliente)},
    )

    assert respuesta.status_code == 403
    assert bloqueos.estado_de("whatsapp", "50644440004") is not None


def test_el_chat_avisa_del_bloqueo_y_ofrece_levantarlo(sesion_admin):
    """Es donde uno nota que el bot dejó de contestar; buscarlo en otra pantalla
    sería el paso que nadie da."""
    pool.ejecutar(
        "INSERT INTO conversation_messages (client_id, canal, direction, author, text) "
        "VALUES ('50655550005', 'whatsapp', 'inbound', 'cliente', 'hola')"
    )
    _bloquear("whatsapp:50655550005")

    cuerpo = sesion_admin.get("/admin/logs/whatsapp/50655550005").text

    assert "bloqueado" in cuerpo
    assert "/admin/bloqueos/whatsapp/50655550005/desbloquear" in cuerpo
