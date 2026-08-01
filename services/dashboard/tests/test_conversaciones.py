"""Visor de conversaciones y webhooks por cliente.

Lo que se prueba aquí es lo que hace usable el historial cuando ya hay meses
de mensajes: que la página no cargue la conversación entera, que la búsqueda
sea por número, que las horas salgan en la zona del negocio y que las tres
voces del chat (cliente, bot, dueño) se distingan.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.db import pool
from src.services import clientes_whatsapp, trazabilidad


def _mensaje(client_id: str, texto: str, *, direction="inbound", author="cliente",
             creado: datetime | None = None, event_type="message", tool=""):
    pool.ejecutar(
        """
        INSERT INTO conversation_messages
            (client_id, canal, direction, author, sender_name, text, event_type,
             tool_name, created_at)
        VALUES (%s, 'whatsapp', %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()))
        """,
        (client_id, direction, author, "Ana", texto, event_type, tool, creado),
    )


# --- Paginación --------------------------------------------------------------

def test_la_conversacion_llega_por_tandas_no_entera():
    """Cargar miles de mensajes de golpe es lo que hacía la página inservible."""
    for i in range(10):
        _mensaje("50688888888", f"mensaje {i}")

    pagina = trazabilidad.mensajes_de("50688888888", "whatsapp", limite=4)

    assert len(pagina["mensajes"]) == 4
    assert pagina["hay_mas"] is True
    # La primera tanda es la MÁS RECIENTE, ordenada de más antigua a más nueva.
    assert [m["text"] for m in pagina["mensajes"]] == [
        "mensaje 6", "mensaje 7", "mensaje 8", "mensaje 9"
    ]


def test_el_cursor_sigue_hacia_atras_sin_repetir_ni_saltarse_nada():
    for i in range(10):
        _mensaje("50688888888", f"mensaje {i}")

    primera = trazabilidad.mensajes_de("50688888888", "whatsapp", limite=4)
    segunda = trazabilidad.mensajes_de(
        "50688888888", "whatsapp", limite=4, antes_de=primera["cursor"]
    )

    assert [m["text"] for m in segunda["mensajes"]] == [
        "mensaje 2", "mensaje 3", "mensaje 4", "mensaje 5"
    ]
    tercera = trazabilidad.mensajes_de(
        "50688888888", "whatsapp", limite=4, antes_de=segunda["cursor"]
    )
    assert [m["text"] for m in tercera["mensajes"]] == ["mensaje 0", "mensaje 1"]
    assert tercera["hay_mas"] is False


def test_un_mensaje_nuevo_no_descoloca_la_tanda_siguiente():
    """Con OFFSET, lo que llega mientras se lee haría repetir mensajes."""
    for i in range(6):
        _mensaje("50688888888", f"mensaje {i}")

    primera = trazabilidad.mensajes_de("50688888888", "whatsapp", limite=3)
    _mensaje("50688888888", "llega mientras leo")
    segunda = trazabilidad.mensajes_de(
        "50688888888", "whatsapp", limite=3, antes_de=primera["cursor"]
    )

    textos = [m["text"] for m in segunda["mensajes"]]
    assert textos == ["mensaje 0", "mensaje 1", "mensaje 2"]
    assert "llega mientras leo" not in textos


def test_los_eventos_tecnicos_no_estorban_salvo_que_se_pidan():
    _mensaje("50688888888", "hola")
    _mensaje("50688888888", "rag: ok", direction="internal", author="tool", tool="rag.answer")

    sin_internos = trazabilidad.mensajes_de("50688888888", "whatsapp")
    con_internos = trazabilidad.mensajes_de("50688888888", "whatsapp", incluir_internos=True)

    assert len(sin_internos["mensajes"]) == 1
    assert len(con_internos["mensajes"]) == 2


def test_el_resumen_cuenta_mensajes_y_eventos_por_separado():
    _mensaje("50688888888", "hola")
    _mensaje("50688888888", "gracias", direction="outbound", author="ia")
    _mensaje("50688888888", "rag: ok", direction="internal", author="tool", tool="rag.answer")

    resumen = trazabilidad.resumen_conversacion("50688888888", "whatsapp")
    assert resumen["mensajes"] == 2
    assert resumen["eventos"] == 1


# --- Búsqueda por número -----------------------------------------------------

@pytest.mark.parametrize("escrito", ["50688888888", "+506 8888-8888", "(506) 88888888"])
def test_la_busqueda_ignora_el_formato_del_numero(escrito):
    _mensaje("50688888888", "hola")
    encontradas = trazabilidad.listar_conversaciones(escrito)
    assert [c["client_id"] for c in encontradas] == ["50688888888"]


def test_no_se_busca_por_el_contenido_del_mensaje():
    """A propósito: buscar texto obliga a recorrer todo el historial."""
    _mensaje("50688888888", "pregunta por el curso teórico")
    assert trazabilidad.listar_conversaciones("teórico") == []


# --- Presentación ------------------------------------------------------------

def test_la_pagina_distingue_al_bot_del_dueno_del_negocio(sesion_admin):
    """Los dos son mensajes salientes: sin distinguirlos el chat no dice quién atendió."""
    _mensaje("50688888888", "hola")
    _mensaje("50688888888", "soy el bot", direction="outbound", author="ia")
    _mensaje("50688888888", "le atiendo yo", direction="outbound", author="dueño")

    cuerpo = sesion_admin.get("/admin/logs/whatsapp/50688888888").text

    assert "Bot" in cuerpo
    assert "Dueño del negocio" in cuerpo
    assert "de-dueño" in cuerpo


def test_las_horas_se_muestran_en_la_zona_del_negocio(sesion_admin):
    """Costa Rica es UTC-6: un mensaje de las 15:00 UTC se lee a las 09:00."""
    _mensaje(
        "50688888888",
        "a media mañana",
        creado=datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc),
    )

    cuerpo = sesion_admin.get("/admin/logs/whatsapp/50688888888").text
    assert "09:00" in cuerpo
    assert "miércoles 8 de julio de 2026" in cuerpo


def test_el_enlace_de_mas_antiguos_solo_aparece_si_queda_historial(sesion_admin):
    _mensaje("50688888888", "único mensaje")
    cuerpo = sesion_admin.get("/admin/logs/whatsapp/50688888888").text
    assert "Cargar mensajes anteriores" not in cuerpo

    for i in range(trazabilidad.MENSAJES_POR_PAGINA + 1):
        _mensaje("50688888888", f"m{i}")
    cuerpo = sesion_admin.get("/admin/logs/whatsapp/50688888888").text
    assert "Cargar mensajes anteriores" in cuerpo


# --- Webhooks por cliente ----------------------------------------------------

def test_cada_cliente_nace_con_su_propio_token():
    uno = clientes_whatsapp.crear("Escuela de manejo")
    otro = clientes_whatsapp.crear("Taller mecánico")

    assert uno["webhook_token"] != otro["webhook_token"]
    assert len(uno["webhook_token"]) == 64
    assert uno["slug"] == "escuela-de-manejo"


def test_no_se_repiten_dos_clientes_con_el_mismo_nombre():
    clientes_whatsapp.crear("Escuela de manejo")
    with pytest.raises(ValueError):
        clientes_whatsapp.crear("Escuela de Manejo")


def test_rotar_el_token_invalida_la_url_anterior():
    cliente = clientes_whatsapp.crear("Escuela de manejo")
    antiguo = cliente["webhook_token"]

    rotado = clientes_whatsapp.rotar_token(cliente["id"])

    assert rotado["webhook_token"] != antiguo
    assert clientes_whatsapp.url_webhook(antiguo) not in [
        c["url_webhook"] for c in clientes_whatsapp.listar()
    ]


def test_el_perfil_del_cliente_muestra_su_url_y_los_eventos(sesion_admin, monkeypatch):
    monkeypatch.setattr(
        clientes_whatsapp.settings, "PUBLIC_WEBHOOK_BASE_URL", "https://webhook.ejemplo.com"
    )
    cliente = clientes_whatsapp.crear("Escuela de manejo")

    cuerpo = sesion_admin.get(f"/admin/negocios/{cliente['id']}").text

    assert f"https://webhook.ejemplo.com/webhooks/wasender/{cliente['webhook_token']}" in cuerpo
    assert "messages.received" in cuerpo
    assert "group-participants.update" in cuerpo


def test_el_webhook_no_cambia_al_editar_la_configuracion():
    """El token se asigna al crear el cliente y vive lo que viva el cliente:
    cambiarlo sin querer dejaria al negocio sin recibir mensajes."""
    cliente = clientes_whatsapp.crear("Escuela de manejo")
    token = cliente["webhook_token"]

    clientes_whatsapp.actualizar_config(
        cliente["id"], nombre="Escuela de manejo CR", numero="50611112222",
        zona_horaria="America/Costa_Rica",
    )
    clientes_whatsapp.actualizar_credenciales(cliente["id"], api_key="clave")

    assert clientes_whatsapp.obtener(cliente["id"])["webhook_token"] == token


def test_lo_guardado_no_se_borra_al_dejar_el_campo_vacio():
    """El formulario nunca muestra lo guardado, así que enviarlo en blanco es lo
    normal cuando solo se estaba cambiando el otro campo."""
    cliente = clientes_whatsapp.crear("Escuela de manejo")
    clientes_whatsapp.actualizar_credenciales(cliente["id"], api_key="clave-secreta")
    clientes_whatsapp.actualizar_credenciales(cliente["id"], webhook_secret="firma")

    guardado = clientes_whatsapp.obtener(cliente["id"])
    assert guardado["wasender_api_key"] == "clave-secreta"
    assert guardado["wasender_webhook_secret"] == "firma"


def test_no_se_pide_una_url_de_api_por_cliente(sesion_admin):
    """El dominio de WasenderAPI es del proveedor y el mismo para todos: vive en
    el entorno, no en la ficha de cada cliente."""
    cliente = clientes_whatsapp.crear("Escuela de manejo")
    cuerpo = sesion_admin.get(f"/admin/negocios/{cliente['id']}").text

    assert "wasender_api_url" not in cuerpo
    assert "API Access Token" in cuerpo
    assert "Webhook Secret" in cuerpo


def test_eliminar_el_cliente_se_lleva_su_webhook():
    cliente = clientes_whatsapp.crear("Escuela de manejo")
    clientes_whatsapp.eliminar(cliente["id"])
    assert clientes_whatsapp.obtener(cliente["id"]) is None
