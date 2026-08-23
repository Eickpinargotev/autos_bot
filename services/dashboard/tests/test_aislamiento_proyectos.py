"""Regresiones de aislamiento con el mismo cliente final en dos proyectos."""

from src.db import pool
from src.services import (
    bloqueos_permanentes,
    envios,
    facturacion,
    instrucciones,
    mensajeria,
    trazabilidad,
)


NUMERO = "50688887777"


def _segundo_proyecto() -> int:
    return pool.consultar_uno(
        """INSERT INTO clientes_whatsapp (nombre, slug, webhook_token)
           VALUES ('Proyecto dos', 'proyecto-dos', 'token-proyecto-dos')
           RETURNING id"""
    )["id"]


def _mensaje(proyecto_id: int, texto: str) -> int:
    return pool.consultar_uno(
        """INSERT INTO conversation_messages
           (proyecto_id, client_id, canal, direction, author, text)
           VALUES (%s, %s, 'whatsapp', 'inbound', 'cliente', %s)
           RETURNING id""",
        (proyecto_id, NUMERO, texto),
    )["id"]


def test_mismo_numero_mantiene_historial_bloqueo_reporte_y_consumo_separados():
    proyecto_dos = _segundo_proyecto()
    _mensaje(1, "historial de uno")
    _mensaje(proyecto_dos, "historial de dos")
    bloqueos_permanentes.agregar(1, NUMERO, "dueno-uno")
    pool.ejecutar(
        """INSERT INTO reportes (proyecto_id, nombre, numero, problema)
           VALUES (1, 'Ana', %s, 'reporte de uno'),
                  (%s, 'Ana', %s, 'reporte de dos')""",
        (NUMERO, proyecto_dos, NUMERO),
    )
    periodo = facturacion.periodo_abierto()
    pool.ejecutar(
        """INSERT INTO uso_eventos
           (proyecto_id, periodo_id, client_id, canal, categoria, origen,
            mensajes, costo_real_microusd, costo_cliente_microusd)
           VALUES (1, %s, %s, 'whatsapp', 'llm', 'agente', 1, 10, 100),
                  (%s, %s, %s, 'whatsapp', 'llm', 'agente', 2, 20, 200)""",
        (periodo["id"], NUMERO, proyecto_dos, periodo["id"], NUMERO),
    )

    assert [m["text"] for m in trazabilidad.mensajes_de(1, NUMERO, "whatsapp")["mensajes"]] == ["historial de uno"]
    assert [m["text"] for m in trazabilidad.mensajes_de(proyecto_dos, NUMERO, "whatsapp")["mensajes"]] == ["historial de dos"]
    assert bloqueos_permanentes.estado_de(1, "whatsapp", NUMERO)
    assert bloqueos_permanentes.estado_de(proyecto_dos, "whatsapp", NUMERO) is None
    assert [r["problema"] for r in trazabilidad.listar_reportes(1)] == ["reporte de uno"]
    assert [r["problema"] for r in trazabilidad.listar_reportes(proyecto_dos)] == ["reporte de dos"]
    assert facturacion.totales_de_periodo(periodo["id"], 1)["cliente_microusd"] == 100
    assert facturacion.totales_de_periodo(periodo["id"], proyecto_dos)["cliente_microusd"] == 200


def test_conocimiento_instrucciones_plantillas_y_envios_no_cruzan_proyectos():
    proyecto_dos = _segundo_proyecto()
    trazabilidad.crear_chunk(1, "conocimiento de uno")
    trazabilidad.crear_chunk(proyecto_dos, "conocimiento de dos")
    instrucciones.guardar(1, "vende el servicio uno", "dueno-uno")
    instrucciones.guardar(proyecto_dos, "vende el servicio dos", "dueno-dos")

    plantilla_uno = mensajeria.crear_plantilla(1, "SALUDO", "dueno-uno")
    plantilla_dos = mensajeria.crear_plantilla(proyecto_dos, "SALUDO", "dueno-dos")
    mensajeria.guardar_parte(1, plantilla_uno["id"], 1, "mensaje de uno", "", "")
    mensajeria.guardar_parte(
        proyecto_dos, plantilla_dos["id"], 1, "mensaje de dos", "", ""
    )
    lote_uno = envios.crear_lote(
        proyecto_id=1, categoria="mensaje", referencia_id=plantilla_uno["id"],
        canal="whatsapp", destinos=[NUMERO], usuario="dueno-uno",
    )
    lote_dos = envios.crear_lote(
        proyecto_id=proyecto_dos, categoria="mensaje", referencia_id=plantilla_dos["id"],
        canal="whatsapp", destinos=[NUMERO], usuario="dueno-dos",
    )

    assert [c["contenido"] for c in trazabilidad.listar_chunks(1)] == ["conocimiento de uno"]
    assert [c["contenido"] for c in trazabilidad.listar_chunks(proyecto_dos)] == ["conocimiento de dos"]
    assert instrucciones.activa(1)["contenido"] == "vende el servicio uno"
    assert instrucciones.activa(proyecto_dos)["contenido"] == "vende el servicio dos"
    assert mensajeria.obtener_plantilla(1, plantilla_dos["id"]) is None
    assert envios.obtener_lote(1, lote_dos["id"]) is None
    envio_uno = envios.destinos_de(1, lote_uno["id"])[0]
    envio_dos = envios.destinos_de(proyecto_dos, lote_dos["id"])[0]
    assert mensajeria.obtener_envio(1, envio_uno["id"])["partes"][0]["texto"] == "mensaje de uno"
    assert mensajeria.obtener_envio(proyecto_dos, envio_dos["id"])["partes"][0]["texto"] == "mensaje de dos"


def test_un_dueno_recibe_404_al_manipular_una_conversacion_ajena(sesion_cliente):
    proyecto_dos = _segundo_proyecto()
    _mensaje(proyecto_dos, "solo del segundo")

    respuesta = sesion_cliente.get(f"/conversaciones/whatsapp/{NUMERO}?fragmento=1")

    assert respuesta.status_code == 404
    assert "solo del segundo" not in respuesta.text
