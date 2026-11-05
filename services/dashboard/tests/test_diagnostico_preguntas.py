"""La auditoría exporta una sola interacción y solo la descarga el admin."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.db import pool
from src.services import clientes_whatsapp


def _pregunta(proyecto_id=1, *, snapshot=None, client_id="50612345678", correlation="consulta-1"):
    return pool.consultar_uno(
        "INSERT INTO preguntas_sin_respuesta "
        "(proyecto_id, pregunta, client_id, canal, correlation_id, diagnostico) "
        "VALUES (%s, 'Costo del dictamen', %s, 'whatsapp', %s, %s::jsonb) RETURNING *",
        (proyecto_id, client_id, correlation, json.dumps(snapshot) if snapshot else None),
    )


def _evento(correlation, texto, *, client_id="50612345678", proyecto_id=1):
    return pool.consultar_uno(
        "INSERT INTO conversation_messages "
        "(proyecto_id, client_id, canal, direction, author, message_type, text, correlation_id) "
        "VALUES (%s, %s, 'whatsapp', 'inbound', 'cliente', 'text', %s, %s) RETURNING *",
        (proyecto_id, client_id, texto, correlation),
    )


def test_el_negocio_ve_el_id_pero_no_la_descarga(sesion_cliente):
    p = _pregunta()
    for ruta in ("/preguntas", "/preguntas/lista"):
        html = sesion_cliente.get(ruta).text
        assert "ID de conversación" in html and p["client_id"] in html
        assert "Descargar reporte" not in html
        assert "diagnostico.json" not in html
    ruta = f"/admin/negocios/1/preguntas/{p['id']}/diagnostico.json"
    assert sesion_cliente.get(ruta).status_code == 403


def test_el_admin_descarga_solo_la_interaccion_correspondiente(sesion_admin):
    _evento("consulta-1", "Pregunta actual")
    _evento("otra-consulta", "ESTO NO DEBE EXPORTARSE")
    _evento("consulta-1", "OTRO CLIENTE", client_id="50699999999")
    p = _pregunta(snapshot={"consulta_cliente": "¿Cuánto cuesta el dictamen?", "eventos": []})
    html = sesion_admin.get("/admin/negocios/1/preguntas").text
    assert "Descargar reporte" in html and p["client_id"] in html
    respuesta = sesion_admin.get(f"/admin/negocios/1/preguntas/{p['id']}/diagnostico.json")
    assert respuesta.status_code == 200
    assert "attachment" in respuesta.headers["content-disposition"]
    assert respuesta.headers["cache-control"] == "no-store"
    datos = respuesta.json()
    assert datos["question"]["client_id"] == p["client_id"]
    assert datos["client_query"] == "¿Cuánto cuesta el dictamen?"
    assert [e["text"] for e in datos["events"]] == ["Pregunta actual"]
    assert "ESTO NO DEBE EXPORTARSE" not in respuesta.text
    assert "OTRO CLIENTE" not in respuesta.text


def test_snapshot_sobrevive_al_borrado_y_redacta_secretos(sesion_admin):
    p = _pregunta(snapshot={"eventos": [{"id": 77, "tool_name": "rag.search", "salida": {
        "sources": [{"source_id": "pg:rag_chunks:5", "content": "Contenido original", "score": 0.8}],
        "api_key": "NO EXPORTAR",
    }}]})
    datos = sesion_admin.get(f"/admin/negocios/1/preguntas/{p['id']}/diagnostico.json").json()
    assert datos["events"][0]["salida"]["sources"][0]["content"] == "Contenido original"
    assert datos["events"][0]["salida"]["api_key"] == "[REDACTADO]"


def test_no_se_descarga_una_pregunta_de_otro_proyecto(sesion_admin):
    otro = clientes_whatsapp.crear("Ajeno")
    p = _pregunta(otro["id"])
    assert sesion_admin.get(f"/admin/negocios/1/preguntas/{p['id']}/diagnostico.json").status_code == 404


def test_pregunta_historica_sin_origen_no_inventa_una_conversacion(sesion_admin):
    p = _pregunta(client_id="", correlation="")
    _evento("otra", "NO RELACIONAR POR TEXTO O FECHA")
    datos = sesion_admin.get(f"/admin/negocios/1/preguntas/{p['id']}/diagnostico.json").json()
    assert datos["events"] == []
    assert datos["question"]["client_id"] == ""
    assert any("inequívocamente" in aviso for aviso in datos["limitations"])


def test_descarga_sin_sesion_exige_login(cliente_http):
    r = cliente_http.get("/admin/negocios/1/preguntas/1/diagnostico.json", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_migracion_solo_asocia_preguntas_historicas_inequivocas():
    unica = _pregunta(client_id="", correlation="")
    pool.ejecutar("UPDATE preguntas_sin_respuesta SET pregunta = 'Consulta única' WHERE id = %s", (unica["id"],))
    ambiguas = [_pregunta(client_id="", correlation="") for _ in range(2)]
    for p, texto, correlacion in ((unica, "Consulta única", "origen-unico"),
                                  (ambiguas[0], "Costo del dictamen", "origen-ambiguo")):
        pool.ejecutar(
            "INSERT INTO conversation_messages (proyecto_id, client_id, canal, direction, author, "
            "message_type, tool_name, entrada, salida, created_at, correlation_id) "
            "VALUES (1, '50612345678', 'whatsapp', 'internal', 'tool', 'tool_event', "
            "'unanswered_question.create', %s::jsonb, '{\"created\":true}'::jsonb, %s, %s)",
            (json.dumps({"question": texto}), p["creado_en"] + timedelta(seconds=1), correlacion),
        )
    sql = Path("src/db/migrations/037_trazabilidad_preguntas.sql").read_text()
    pool.ejecutar("WITH candidatos AS" + sql.split("WITH candidatos AS", 1)[1])
    vinculada = pool.consultar_uno("SELECT * FROM preguntas_sin_respuesta WHERE id = %s", (unica["id"],))
    assert vinculada["client_id"] == "50612345678" and vinculada["correlation_id"] == "origen-unico"
    assert all(pool.consultar_uno("SELECT client_id FROM preguntas_sin_respuesta WHERE id = %s", (p["id"],))["client_id"] == "" for p in ambiguas)


def test_el_admin_suplantando_no_descarga_evidencias(sesion_admin):
    from src.services import usuarios
    from tests.conftest import token_csrf
    negocio = clientes_whatsapp.obtener(1)
    usuario = usuarios.crear("dueno-auditado", "clave-larga-de-prueba", "cliente", debe_cambiar=False)
    clientes_whatsapp.vincular_cuenta(negocio["id"], usuario["id"])
    p = _pregunta()
    respuesta = sesion_admin.post(
        f"/admin/usuarios/{usuario['id']}/entrar", data={"csrf": token_csrf(sesion_admin)},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert sesion_admin.get(f"/admin/negocios/1/preguntas/{p['id']}/diagnostico.json").status_code == 403


def test_migracion_recupera_audios_bloqueados_sin_duplicarlos():
    fecha = datetime(2026, 11, 4, 16, 48, tzinfo=timezone.utc)
    pool.ejecutar(
        "INSERT INTO conversation_messages (proyecto_id, client_id, canal, direction, author, "
        "message_type, text, created_at) VALUES (1, '50612345678', 'whatsapp', 'inbound', 'cliente', 'text', 'Hola', %s)",
        (fecha,),
    )
    for indice in range(2):
        recibida = fecha + timedelta(hours=4, seconds=indice * 6)
        for tipo, meta in (("message_received", {"message_type": "audio"}),
                           ("decision_blocked", {"branch": "temporary_block"})):
            pool.ejecutar(
                "INSERT INTO conversation_audit (proyecto_id, client_id, canal, event_type, "
                "source, correlation_id, occurred_at, metadata) "
                "VALUES (1, '50612345678', 'whatsapp', %s, 'whatsapp', %s, %s, %s::jsonb)",
                (tipo, f"audio-{indice}", recibida, json.dumps(meta)),
            )
    sql = Path("src/db/migrations/039_recuperar_audios_bloqueados.sql").read_text()
    pool.ejecutar(sql)
    pool.ejecutar(sql)
    audios = pool.consultar("SELECT * FROM conversation_messages WHERE event_type = 'audio_bloqueado' ORDER BY id")
    assert [a["correlation_id"] for a in audios] == ["audio-0", "audio-1"]
    assert all(a["direction"] == "inbound" and a["message_type"] == "audio" for a in audios)
