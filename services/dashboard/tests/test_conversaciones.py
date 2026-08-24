"""Visor de conversaciones y webhooks por cliente.

Lo que se prueba aquí es lo que hace usable el historial cuando ya hay meses
de mensajes: que la página no cargue la conversación entera, que la búsqueda
sea por número, que las horas salgan en la zona del negocio y que las tres
voces del chat (cliente, bot, dueño) se distingan.
"""

from datetime import datetime, timedelta, timezone
import io
import json
import zipfile

import pytest

from src.db import pool
from src.services import bot_interno, clientes_whatsapp, trazabilidad
from tests.conftest import token_csrf


def _mensaje(client_id: str, texto: str, *, direction="inbound", author="cliente",
             creado: datetime | None = None, event_type="message", tool="", proyecto_id=1,
             message_type="text", quoted_text=""):
    pool.ejecutar(
        """
        INSERT INTO conversation_messages
            (proyecto_id, client_id, canal, direction, author, sender_name, message_type,
             text, quoted_text, event_type, tool_name, created_at)
        VALUES (%s, %s, 'whatsapp', %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()))
        """,
        (
            proyecto_id, client_id, direction, author, "Ana", message_type,
            texto, quoted_text, event_type, tool, creado,
        ),
    )


# --- Paginación --------------------------------------------------------------

def test_la_conversacion_llega_por_tandas_no_entera():
    """Cargar miles de mensajes de golpe es lo que hacía la página inservible."""
    for i in range(10):
        _mensaje("50688888888", f"mensaje {i}")

    pagina = trazabilidad.mensajes_de(1, "50688888888", "whatsapp", limite=4)

    assert len(pagina["mensajes"]) == 4
    assert pagina["hay_mas"] is True
    # La primera tanda es la MÁS RECIENTE, ordenada de más antigua a más nueva.
    assert [m["text"] for m in pagina["mensajes"]] == [
        "mensaje 6", "mensaje 7", "mensaje 8", "mensaje 9"
    ]


def test_el_cursor_sigue_hacia_atras_sin_repetir_ni_saltarse_nada():
    for i in range(10):
        _mensaje("50688888888", f"mensaje {i}")

    primera = trazabilidad.mensajes_de(1, "50688888888", "whatsapp", limite=4)
    segunda = trazabilidad.mensajes_de(
        1, "50688888888", "whatsapp", limite=4, antes_de=primera["cursor"]
    )

    assert [m["text"] for m in segunda["mensajes"]] == [
        "mensaje 2", "mensaje 3", "mensaje 4", "mensaje 5"
    ]
    tercera = trazabilidad.mensajes_de(
        1, "50688888888", "whatsapp", limite=4, antes_de=segunda["cursor"]
    )
    assert [m["text"] for m in tercera["mensajes"]] == ["mensaje 0", "mensaje 1"]
    assert tercera["hay_mas"] is False


def test_un_mensaje_nuevo_no_descoloca_la_tanda_siguiente():
    """Con OFFSET, lo que llega mientras se lee haría repetir mensajes."""
    for i in range(6):
        _mensaje("50688888888", f"mensaje {i}")

    primera = trazabilidad.mensajes_de(1, "50688888888", "whatsapp", limite=3)
    _mensaje("50688888888", "llega mientras leo")
    segunda = trazabilidad.mensajes_de(
        1, "50688888888", "whatsapp", limite=3, antes_de=primera["cursor"]
    )

    textos = [m["text"] for m in segunda["mensajes"]]
    assert textos == ["mensaje 0", "mensaje 1", "mensaje 2"]
    assert "llega mientras leo" not in textos


def test_los_eventos_tecnicos_no_estorban_salvo_que_se_pidan():
    _mensaje("50688888888", "hola")
    _mensaje("50688888888", "rag: ok", direction="internal", author="tool", tool="rag.answer")

    sin_internos = trazabilidad.mensajes_de(1, "50688888888", "whatsapp")
    con_internos = trazabilidad.mensajes_de(1, "50688888888", "whatsapp", incluir_internos=True)

    assert len(sin_internos["mensajes"]) == 1
    assert len(con_internos["mensajes"]) == 2


def test_un_reporte_si_aparece_como_nota_sin_contar_como_mensaje(sesion_cliente):
    _mensaje("50688888888", "hola")
    _mensaje(
        "50688888888", "Necesita ayuda humana", direction="internal",
        author="system", event_type="report_created",
    )

    pagina = trazabilidad.mensajes_de(1, "50688888888", "whatsapp")
    listado = trazabilidad.listar_conversaciones(1)[0]
    html = sesion_cliente.get("/conversaciones/whatsapp/50688888888").text

    assert [m["event_type"] for m in pagina["mensajes"]] == ["message", "report_created"]
    assert listado["mensajes"] == 1
    assert "Se generó un reporte" in html
    assert "Necesita ayuda humana" in html
    assert 'href="/reportes"' in html


def test_el_resumen_cuenta_mensajes_y_eventos_por_separado():
    _mensaje("50688888888", "hola")
    _mensaje("50688888888", "gracias", direction="outbound", author="ia")
    _mensaje("50688888888", "rag: ok", direction="internal", author="tool", tool="rag.answer")

    resumen = trazabilidad.resumen_conversacion(1, "50688888888", "whatsapp")
    assert resumen["mensajes"] == 2
    assert resumen["eventos"] == 1


def test_el_listado_cuenta_solo_lo_que_el_hilo_muestra():
    """Las llamadas internas existen para diagnóstico, pero el dueño no las ve.

    Contarlas como «mensajes» hacía que la tarjeta dijera 6 y el hilo mostrara 5.
    """
    _mensaje("50688888888", "hola")
    _mensaje("50688888888", "respuesta", direction="outbound", author="ia")
    _mensaje(
        "50688888888", "agent: ok", direction="internal", author="tool", tool="agent.decide"
    )

    conversacion = trazabilidad.listar_conversaciones(1)[0]

    assert conversacion["mensajes"] == 2
    assert conversacion["respuestas_bot"] == 1


def test_el_listado_omite_metricas_y_facturacion(sesion_cliente):
    _mensaje("50677770007", "hola")
    pool.ejecutar(
        """
        INSERT INTO uso_eventos
            (proyecto_id, periodo_id, client_id, canal, categoria, origen,
             mensajes, costo_real_microusd, costo_cliente_microusd)
        SELECT 1, id, '50677770007', 'whatsapp', 'llm', 'agente', 4, 100, 300
        FROM periodos_facturacion WHERE cerrado_en IS NULL
        """
    )

    cuerpo = sesion_cliente.get("/conversaciones/lista").text

    assert "50677770007" in cuerpo
    assert "elementos visibles" not in cuerpo
    assert "Período de factura" not in cuerpo
    assert "Facturado" not in cuerpo


# --- Búsqueda por número -----------------------------------------------------

@pytest.mark.parametrize("escrito", ["50688888888", "+506 8888-8888", "(506) 88888888"])
def test_la_busqueda_ignora_el_formato_del_numero(escrito):
    _mensaje("50688888888", "hola")
    encontradas = trazabilidad.listar_conversaciones(1, escrito)
    assert [c["client_id"] for c in encontradas] == ["50688888888"]


def test_las_conversaciones_se_filtran_por_negocio():
    """Cada negocio ve SOLO a sus clientes.

    Es lo que hace usable el visor: una lista con los clientes de todos los
    negocios mezclados no se puede leer, y peor aún, le mostraría a un negocio
    los chats de otro.
    """
    uno = clientes_whatsapp.crear("Escuela A")
    otro = clientes_whatsapp.crear("Escuela B")
    _mensaje("50611110001", "hola desde A", proyecto_id=uno["id"])
    _mensaje("50622220002", "hola desde B", proyecto_id=otro["id"])
    _mensaje("50633330003", "sin negocio anotado")
    de_a = {c["client_id"] for c in trazabilidad.listar_conversaciones(uno["id"])}
    de_b = {c["client_id"] for c in trazabilidad.listar_conversaciones(otro["id"])}

    assert de_a == {"50611110001"}
    assert de_b == {"50622220002"}
    # La conversación sin pertenencia no se le atribuye a nadie: afirmarlo sin
    # saberlo sería peor que no mostrarla.
    assert "50633330003" not in de_a | de_b
    assert "50633330003" not in de_a | de_b


def test_no_se_busca_por_el_contenido_del_mensaje():
    """A propósito: buscar texto obliga a recorrer todo el historial."""
    _mensaje("50688888888", "pregunta por el curso teórico")
    assert trazabilidad.listar_conversaciones(1, "teórico") == []


# --- Presentación ------------------------------------------------------------

def test_el_panel_marca_de_donde_salio_cada_mensaje(sesion_cliente):
    """Audio, sticker y adjunto se distinguen a simple vista de un texto escrito.

    Quien lee el chat necesita saber que un párrafo salió de una nota de voz y
    no de los dedos del cliente, y que el sticker no se quedó sin respuesta por
    un fallo sino a propósito.
    """
    _mensaje("50644440004", "quiero informacion del curso", event_type="audio_transcrito")
    _mensaje("50644440004", "", event_type="sticker_ignorado")
    _mensaje("50644440004", "", event_type="media_avisada")

    html = sesion_cliente.get("/conversaciones/whatsapp/50644440004").text

    assert "Audio transcrito" in html
    assert "quiero informacion del curso" in html
    assert "no responde a stickers" in html
    assert "se envió el aviso" in html


def test_el_visor_del_negocio_devuelve_solo_el_fragmento(sesion_admin):
    """El administrador ya no tiene un visor directo por proyecto."""
    negocio = clientes_whatsapp.crear("Escuela con chats")
    assert sesion_admin.get(f"/admin/negocios/{negocio['id']}/conversaciones").status_code == 404


def test_el_hilo_se_sirve_como_fragmento_al_dueno(sesion_cliente):
    _mensaje("50666660006", "buenas tardes")

    fragmento = sesion_cliente.get("/conversaciones/whatsapp/50666660006").text

    assert "buenas tardes" in fragmento
    assert "<html" not in fragmento.lower()


def test_la_pagina_distingue_al_bot_del_dueno_del_negocio(sesion_cliente):
    """Los dos son mensajes salientes: sin distinguirlos el chat no dice quién atendió."""
    _mensaje("50688888888", "hola")
    _mensaje("50688888888", "soy el bot", direction="outbound", author="ia")
    _mensaje("50688888888", "le atiendo yo", direction="outbound", author="dueño")

    cuerpo = sesion_cliente.get("/conversaciones/whatsapp/50688888888").text

    assert "Bot" in cuerpo
    assert "Dueño del negocio" in cuerpo
    assert "de-dueño" in cuerpo


def test_el_dashboard_muestra_media_del_dueno_y_el_mensaje_citado(sesion_cliente):
    _mensaje("50688888888", "Información del formulario")
    _mensaje(
        "50688888888", "[Image]", direction="outbound", author="dueño",
        message_type="image", quoted_text="Información del formulario",
    )

    cuerpo = sesion_cliente.get("/conversaciones/whatsapp/50688888888").text

    assert "Dueño del negocio" in cuerpo
    assert "· image" in cuerpo
    assert "Mensaje citado" in cuerpo
    assert cuerpo.count("Información del formulario") == 2


def test_una_pausa_sin_fecha_muestra_motivo_y_que_no_expira(sesion_cliente):
    _mensaje("50688888888", "hola")
    pool.ejecutar(
        """INSERT INTO users_blocked (proyecto_id, user_id, reason, expires_at)
           VALUES (1, 'whatsapp:50688888888', 'Flujo de publicidad', NULL)"""
    )

    cuerpo = sesion_cliente.get("/conversaciones/whatsapp/50688888888").text

    assert "IA pausada sin fecha" in cuerpo
    assert "Flujo de publicidad" in cuerpo


def test_el_dueno_puede_responder_whatsapp_desde_el_hilo(sesion_cliente, monkeypatch):
    _mensaje("50688888888", "hola")
    llamadas = []
    monkeypatch.setattr(
        bot_interno,
        "responder_como_dueno",
        lambda proyecto, canal, numero, texto: llamadas.append((proyecto, canal, numero, texto)) or "",
    )

    respuesta = sesion_cliente.post(
        "/conversaciones/whatsapp/50688888888/responder",
        data={"texto": "Le atiendo personalmente.", "csrf": token_csrf(sesion_cliente)},
        headers={"X-Fragmento": "1"},
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["ok"] is True
    assert llamadas == [(1, "whatsapp", "50688888888", "Le atiendo personalmente.")]


def test_un_fallo_de_envio_devuelve_error_y_el_hilo_tiene_compositor(sesion_cliente, monkeypatch):
    _mensaje("50688888888", "hola")
    monkeypatch.setattr(bot_interno, "responder_como_dueno", lambda *_: "Wasender no respondió")
    hilo = sesion_cliente.get("/conversaciones/whatsapp/50688888888").text
    assert ">Responder<" in hilo
    assert ">Enviar<" in hilo

    respuesta = sesion_cliente.post(
        "/conversaciones/whatsapp/50688888888/responder",
        data={"texto": "No pierda este borrador", "csrf": token_csrf(sesion_cliente)},
        headers={"X-Fragmento": "1"},
    )
    assert respuesta.status_code == 400
    assert "Wasender" in respuesta.json()["error"]


def test_no_se_acepta_responder_un_canal_distinto_de_whatsapp(sesion_cliente):
    respuesta = sesion_cliente.post(
        "/conversaciones/telegram/123/responder",
        data={"texto": "hola", "csrf": token_csrf(sesion_cliente)},
        headers={"X-Fragmento": "1"},
    )
    assert respuesta.status_code == 400


def test_el_dueno_normal_no_puede_descargar_diagnostico(sesion_cliente):
    _mensaje("50688888888", "hola")
    hilo = sesion_cliente.get("/conversaciones/whatsapp/50688888888").text
    assert "Descargar diagnóstico" not in hilo
    respuesta = sesion_cliente.post(
        "/conversaciones/whatsapp/50688888888/diagnostico",
        data={"csrf": token_csrf(sesion_cliente)},
    )
    assert respuesta.status_code == 403


def test_el_admin_suplantando_descarga_zip_sanitizado_y_deja_auditoria(sesion_admin):
    from src.services import usuarios

    cuenta = usuarios.crear("negocio_diagnostico", "clave-segura-123", "cliente", debe_cambiar=False)
    clientes_whatsapp.vincular_cuenta(1, cuenta["id"])
    _mensaje("50688888888", "hola")
    pool.ejecutar(
        """INSERT INTO conversation_messages
           (proyecto_id, client_id, canal, direction, author, message_type, event_type,
            tool_name, status, entrada, salida)
           VALUES (1, '50688888888', 'whatsapp', 'internal', 'tool', 'tool_event',
                   'provider_webhook', 'wasender.webhook', 'success', %s::jsonb, %s::jsonb)""",
        (
            json.dumps({"authorization": "Bearer secreto", "url": "https://x.test/media?a=1&signature=privada"}),
            json.dumps({"ok": True, "blob": "A" * 800}),
        ),
    )
    pool.ejecutar(
        """INSERT INTO conversation_shots (proyecto_id, id_user, canal, shot)
           VALUES (1, '50688888888', 'whatsapp', %s::jsonb)""",
        (json.dumps({"turn": {"events": [{"type": "model_call", "request": {"messages": ["prompt efectivo"]}}]}}),),
    )
    entrar = sesion_admin.post(
        f"/admin/usuarios/{cuenta['id']}/entrar",
        data={"csrf": token_csrf(sesion_admin)},
        follow_redirects=False,
    )
    assert entrar.status_code == 303
    hilo = sesion_admin.get("/conversaciones/whatsapp/50688888888").text
    assert "Descargar diagnóstico" in hilo

    respuesta = sesion_admin.post(
        "/conversaciones/whatsapp/50688888888/diagnostico",
        data={"csrf": token_csrf(sesion_admin)},
    )
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(respuesta.content)) as archivo:
        assert set(archivo.namelist()) == {"reporte.html", "diagnostico.json", "README.txt"}
        diagnostico = archivo.read("diagnostico.json").decode()
        datos = json.loads(diagnostico)
        assert datos["schema_version"] == "1.0"
        assert datos["project"]["id"] == 1
        assert datos["conversation"]["client_id"] == "50688888888"
        assert "prompt efectivo" in diagnostico
        assert "Bearer secreto" not in diagnostico
        assert "signature=privada" not in diagnostico
        assert "[REDACTADO]" in diagnostico

    auditoria = pool.consultar_uno("SELECT * FROM diagnostico_descargas")
    administrador = pool.consultar_uno("SELECT id FROM dashboard_usuarios WHERE usuario = 'admin_test'")
    assert auditoria["proyecto_id"] == 1
    assert auditoria["administrador_id"] == administrador["id"]
    assert auditoria["client_id"] == "50688888888"


def test_las_horas_se_muestran_en_la_zona_del_negocio(sesion_cliente):
    """Costa Rica es UTC-6: un mensaje de las 15:00 UTC se lee a las 09:00."""
    _mensaje(
        "50688888888",
        "a media mañana",
        creado=datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc),
    )

    cuerpo = sesion_cliente.get("/conversaciones/whatsapp/50688888888").text
    assert "09:00" in cuerpo
    assert "miércoles 8 de julio de 2026" in cuerpo


def test_el_enlace_de_mas_antiguos_solo_aparece_si_queda_historial(sesion_cliente):
    _mensaje("50688888888", "único mensaje")
    cuerpo = sesion_cliente.get("/conversaciones/whatsapp/50688888888").text
    assert "Cargar mensajes anteriores" not in cuerpo

    for i in range(trazabilidad.MENSAJES_POR_PAGINA + 1):
        _mensaje("50688888888", f"m{i}")
    cuerpo = sesion_cliente.get("/conversaciones/whatsapp/50688888888").text
    assert "Cargar mensajes anteriores" in cuerpo


# --- Borrado -----------------------------------------------------------------

def test_borrar_una_conversacion_no_toca_las_demas():
    _mensaje("50677770007", "esta se borra")
    _mensaje("50688880008", "esta se queda")
    pool.ejecutar(
        "INSERT INTO conversation_shots (id_user, canal, shot) VALUES (%s, 'whatsapp', '{}'::jsonb)",
        ("50677770007",),
    )

    borrado = trazabilidad.eliminar_conversacion(1, "50677770007", "whatsapp")

    assert borrado["mensajes"] == 1
    assert borrado["shots"] == 1
    assert trazabilidad.mensajes_de(1, "50677770007", "whatsapp")["mensajes"] == []
    assert len(trazabilidad.mensajes_de(1, "50688880008", "whatsapp")["mensajes"]) == 1


def test_borrar_la_conversacion_no_borra_lo_facturado():
    """`uso_eventos` es el libro mayor: el pasado no se recalcula nunca."""
    _mensaje("50699990009", "hola")
    pool.ejecutar(
        """
        INSERT INTO uso_eventos (periodo_id, client_id, canal, categoria, origen,
                                 mensajes, costo_real_microusd, costo_cliente_microusd)
        SELECT id, '50699990009', 'whatsapp', 'llm', 'agente', 1, 100, 300
        FROM periodos_facturacion WHERE cerrado_en IS NULL
        """
    )

    trazabilidad.eliminar_conversacion(1, "50699990009", "whatsapp")

    fila = pool.consultar_uno(
        "SELECT COUNT(*) AS total FROM uso_eventos WHERE client_id = '50699990009'"
    )
    assert fila["total"] == 1


def test_la_ruta_de_borrado_avisa_al_bot_y_vacia_el_hilo(sesion_cliente, monkeypatch):
    from src.routes import negocio as rutas_negocio
    from tests.conftest import token_csrf

    _mensaje("50612340001", "hola")
    llamadas = []
    monkeypatch.setattr(
        rutas_negocio.bot_interno,
        "olvidar_conversacion",
        lambda proyecto_id, canal, client_id: llamadas.append((proyecto_id, canal, client_id)) or "",
    )

    respuesta = sesion_cliente.post(
        "/conversaciones/whatsapp/50612340001/eliminar",
        data={"csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/conversaciones?aviso=")
    # Sin este aviso el bot seguiría con el hilo en memoria y contestaría el
    # siguiente mensaje como si nada se hubiera borrado.
    assert llamadas == [(1, "whatsapp", "50612340001")]


def test_si_el_bot_no_puede_olvidar_se_dice_en_vez_de_callarlo(sesion_cliente, monkeypatch):
    """Media verdad es peor que un error: en la base ya no hay historial, pero el
    bot sigue recordando el hilo."""
    from src.routes import negocio as rutas_negocio
    from tests.conftest import token_csrf

    _mensaje("50612340002", "hola")
    monkeypatch.setattr(
        rutas_negocio.bot_interno, "olvidar_conversacion", lambda proyecto_id, canal, client_id: "no responde"
    )

    respuesta = sesion_cliente.post(
        "/conversaciones/whatsapp/50612340002/eliminar",
        data={"csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )

    assert "error=" in respuesta.headers["location"]
    assert trazabilidad.mensajes_de(1, "50612340002", "whatsapp")["mensajes"] == []


def test_el_admin_no_puede_borrar_conversaciones_directamente(sesion_admin):
    from tests.conftest import token_csrf

    _mensaje("50612340003", "hola")
    respuesta = sesion_admin.post(
        "/admin/logs/whatsapp/50612340003/eliminar", data={"csrf": token_csrf(sesion_admin)}
    )

    assert respuesta.status_code == 404
    assert len(trazabilidad.mensajes_de(1, "50612340003", "whatsapp")["mensajes"]) == 1


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


def test_el_perfil_confirma_credenciales_sin_revelarlas(sesion_admin, monkeypatch):
    cliente = clientes_whatsapp.crear("Escuela conectada")
    clientes_whatsapp.actualizar_credenciales(
        cliente["id"], api_key="token-que-no-debe-salir", webhook_secret="firma-privada"
    )
    monkeypatch.setattr(
        clientes_whatsapp,
        "estado_wasender",
        lambda _api_key: {"codigo": "conectado", "texto": "Wasender conectado", "clase": "ok"},
    )

    cuerpo = sesion_admin.get(f"/admin/negocios/{cliente['id']}").text

    assert "Wasender conectado" in cuerpo
    assert "API Token guardado" in cuerpo
    assert "Webhook Secret guardado" in cuerpo
    assert "token-que-no-debe-salir" not in cuerpo
    assert "firma-privada" not in cuerpo


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
