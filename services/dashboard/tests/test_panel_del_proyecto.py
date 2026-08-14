"""Lo que el proyecto ve y lo que NO, y en qué orden y con qué hora lo ve.

Tres cosas que estaban mal en su panel:

* El **costo real** —lo que nos cuesta el proveedor a nosotros— salía en su tabla
  de clientes, al lado de lo facturado. Con los dos números, el margen se calcula
  solo.
* La tabla y los reportes se ordenaban por gasto y por fecha a secas, cuando lo
  que se busca al abrirlos es lo más reciente y lo que falta por atender.
* Las horas salían en la zona del despliegue, no en la del proyecto, que es la
  única que significa algo para quien atiende ese número.
"""

from datetime import datetime, timedelta, timezone

from src.core import plantillas
from src.db import pool
from src.services import clientes_whatsapp, facturacion, trazabilidad, usuarios
from tests.conftest import token_csrf


# --- El costo real es nuestro, no suyo ---------------------------------------

def _sembrar_consumo(client_id: str, canal: str = "whatsapp") -> None:
    periodo = facturacion.periodo_abierto()
    pool.ejecutar(
        """
        INSERT INTO uso_eventos (periodo_id, client_id, canal, categoria, origen,
                                 mensajes, costo_real_microusd, costo_cliente_microusd)
        VALUES (%s, %s, %s, 'llm', 'agente', 1, 90000, 500000)
        """,
        (periodo["id"], client_id, canal),
    )
    pool.ejecutar(
        """
        INSERT INTO seguimiento_clientes (client_id, canal, nombre, ultima_interaccion)
        VALUES (%s, %s, 'Quien sea', NOW())
        ON CONFLICT (client_id, canal) DO UPDATE SET ultima_interaccion = NOW()
        """,
        (client_id, canal),
    )


def test_el_costo_real_no_llega_ni_a_la_consulta_del_proyecto():
    """Ocultarlo en la plantilla dejaría el dato viajando en el HTML."""
    _sembrar_consumo("50611110000")
    periodo = facturacion.periodo_abierto()

    filas = facturacion.actividad_por_cliente(periodo["id"], incluir_costo_real=False)

    assert filas, "hacía falta al menos una fila para que la prueba diga algo"
    assert all(f["real_microusd"] is None for f in filas)
    assert all(f["real_usd"] is None for f in filas)
    # …y lo facturado, que sí es suyo, sigue estando.
    assert filas[0]["cliente_microusd"] == 500000


def test_el_administrador_si_ve_el_costo_real():
    _sembrar_consumo("50611110001")
    periodo = facturacion.periodo_abierto()

    filas = facturacion.actividad_por_cliente(periodo["id"], incluir_costo_real=True)

    assert filas[0]["real_microusd"] == 90000


def test_la_pagina_de_clientes_del_proyecto_no_menciona_el_costo_real(sesion_cliente):
    _sembrar_consumo("50611110002")

    cuerpo = sesion_cliente.get("/clientes").text

    assert "Costo real" not in cuerpo
    assert "Facturado" in cuerpo


# --- El orden: lo reciente arriba --------------------------------------------

def test_los_clientes_se_ordenan_por_ultima_actividad_no_por_gasto():
    """Antes mandaba el dinero y lo de hoy aparecía en mitad de la lista."""
    periodo = facturacion.periodo_abierto()
    ahora = datetime.now(timezone.utc)

    for client_id, gasto, hace_horas in (("gasta-mucho", 900000, 48), ("escribio-hoy", 1000, 1)):
        pool.ejecutar(
            """
            INSERT INTO uso_eventos (periodo_id, client_id, canal, categoria, origen,
                                     mensajes, costo_real_microusd, costo_cliente_microusd)
            VALUES (%s, %s, 'whatsapp', 'llm', 'agente', 1, 1, %s)
            """,
            (periodo["id"], client_id, gasto),
        )
        pool.ejecutar(
            """
            INSERT INTO seguimiento_clientes (client_id, canal, nombre, ultima_interaccion)
            VALUES (%s, 'whatsapp', %s, %s)
            """,
            (client_id, client_id, ahora - timedelta(hours=hace_horas)),
        )

    filas = facturacion.actividad_por_cliente(periodo["id"])

    assert filas[0]["client_id"] == "escribio-hoy"


# --- Reportes: pendientes arriba, revisados con fecha de caducidad ------------

def _crear_reporte(problema: str, revisado: bool = False) -> int:
    fila = pool.consultar_uno(
        "INSERT INTO reportes (nombre, numero, problema) VALUES ('X', '506', %s) RETURNING id",
        (problema,),
    )
    if revisado:
        trazabilidad.marcar_reporte_revisado(fila["id"])
    return fila["id"]


def test_lo_pendiente_va_arriba_aunque_sea_mas_viejo():
    viejo_pendiente = _crear_reporte("sin atender")
    _crear_reporte("ya resuelto", revisado=True)

    reportes = trazabilidad.listar_reportes()

    assert reportes[0]["id"] == viejo_pendiente
    assert reportes[-1]["problema"] == "ya resuelto"


def test_marcar_revisado_arranca_el_plazo_y_no_se_reinicia():
    """Volver a pulsar el botón no puede regalarle otros 7 días."""
    reporte_id = _crear_reporte("algo")
    trazabilidad.marcar_reporte_revisado(reporte_id)
    primera = pool.consultar_uno("SELECT revisado_en FROM reportes WHERE id = %s", (reporte_id,))

    trazabilidad.marcar_reporte_revisado(reporte_id)
    segunda = pool.consultar_uno("SELECT revisado_en FROM reportes WHERE id = %s", (reporte_id,))

    assert primera["revisado_en"] is not None
    assert primera["revisado_en"] == segunda["revisado_en"]


def test_un_reporte_revisado_caduca_a_los_siete_dias():
    caduco = _crear_reporte("resuelto hace tiempo", revisado=True)
    reciente = _crear_reporte("resuelto hoy", revisado=True)
    pool.ejecutar(
        "UPDATE reportes SET revisado_en = NOW() - INTERVAL '8 days' WHERE id = %s", (caduco,)
    )

    borrados = trazabilidad.purgar_reportes_revisados()

    assert borrados == 1
    ids = {r["id"] for r in trazabilidad.listar_reportes()}
    assert caduco not in ids
    assert reciente in ids


def test_lo_pendiente_no_caduca_nunca():
    """Que nadie lo haya mirado en un mes no lo hace menos urgente."""
    antiguo = _crear_reporte("nadie lo miró")
    pool.ejecutar(
        "UPDATE reportes SET creado_en = NOW() - INTERVAL '90 days' WHERE id = %s", (antiguo,)
    )

    assert trazabilidad.purgar_reportes_revisados() == 0
    assert antiguo in {r["id"] for r in trazabilidad.listar_reportes()}


# --- La hora es la del proyecto ----------------------------------------------

def test_la_fecha_se_muestra_en_la_zona_del_proyecto():
    """Por algo cada proyecto tiene su zona: la del servidor no significa nada."""
    # Las 03:00 UTC son las 21:00 del día anterior en Costa Rica y las 22:00 en
    # Guayaquil: una hora que distingue las dos zonas Y el día.
    momento = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)

    costa_rica = plantillas._fecha({"proyecto": {"zona_horaria": "America/Costa_Rica"}}, momento)
    guayaquil = plantillas._fecha({"proyecto": {"zona_horaria": "America/Guayaquil"}}, momento)

    assert costa_rica == "01/08/2026 21:00"
    assert guayaquil == "01/08/2026 22:00"


def test_sin_proyecto_se_usa_la_zona_del_despliegue():
    momento = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)

    assert plantillas._fecha({}, momento) == plantillas._fecha({"proyecto": None}, momento)


def test_una_zona_invalida_no_deja_la_pagina_en_blanco():
    momento = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)

    assert plantillas._fecha({"proyecto": {"zona_horaria": "Marte/Olympus"}}, momento)


def test_el_panel_del_proyecto_usa_su_zona(sesion_cliente):
    """Comprobado de punta a punta: la zona sale de su ficha, no del entorno."""
    cuenta = usuarios.buscar_por_usuario("cliente_test")
    negocio = clientes_whatsapp.crear("Escuela de Manejo")
    clientes_whatsapp.vincular_cuenta(negocio["id"], cuenta["id"])
    clientes_whatsapp.actualizar_config(
        negocio["id"], nombre="Escuela de Manejo", zona_horaria="America/Guayaquil"
    )
    reporte_id = _crear_reporte("a las tres UTC")
    pool.ejecutar(
        "UPDATE reportes SET creado_en = %s WHERE id = %s",
        (datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc), reporte_id),
    )

    cuerpo = sesion_cliente.get("/reportes").text

    assert "01/08/2026 22:00" in cuerpo


# --- La ruta, entera ----------------------------------------------------------

def test_el_proyecto_marca_un_reporte_revisado_desde_su_panel(sesion_cliente):
    reporte_id = _crear_reporte("por revisar")

    respuesta = sesion_cliente.post(
        f"/reportes/{reporte_id}/revisado",
        data={"csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    fila = pool.consultar_uno("SELECT revisado, revisado_en FROM reportes WHERE id = %s", (reporte_id,))
    assert fila["revisado"] is True
    assert fila["revisado_en"] is not None


# --- Preguntas sin respuesta: una bandeja, no un archivo ----------------------

def _crear_pregunta(texto: str) -> int:
    return pool.consultar_uno(
        "INSERT INTO preguntas_sin_respuesta (pregunta) VALUES (%s) RETURNING id", (texto,)
    )["id"]


def test_entendido_pone_la_pregunta_en_verde_y_arranca_el_plazo(sesion_cliente):
    pregunta_id = _crear_pregunta("¿Aceptan tarjeta?")

    respuesta = sesion_cliente.post(
        f"/preguntas/{pregunta_id}/atendida",
        data={"csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    fila = pool.consultar_uno(
        "SELECT atendida, atendida_en FROM preguntas_sin_respuesta WHERE id = %s", (pregunta_id,)
    )
    assert fila["atendida"] is True
    assert fila["atendida_en"] is not None


def test_volver_a_pulsar_entendido_no_regala_otro_dia():
    pregunta_id = _crear_pregunta("¿Aceptan tarjeta?")
    trazabilidad.marcar_pregunta_atendida(pregunta_id)
    primera = pool.consultar_uno(
        "SELECT atendida_en FROM preguntas_sin_respuesta WHERE id = %s", (pregunta_id,)
    )

    trazabilidad.marcar_pregunta_atendida(pregunta_id)
    segunda = pool.consultar_uno(
        "SELECT atendida_en FROM preguntas_sin_respuesta WHERE id = %s", (pregunta_id,)
    )

    assert primera["atendida_en"] == segunda["atendida_en"]


def test_una_pregunta_entendida_caduca_a_las_24_horas():
    vieja = _crear_pregunta("ya resuelta hace tiempo")
    reciente = _crear_pregunta("resuelta hace un rato")
    trazabilidad.marcar_pregunta_atendida(vieja)
    trazabilidad.marcar_pregunta_atendida(reciente)
    pool.ejecutar(
        "UPDATE preguntas_sin_respuesta SET atendida_en = NOW() - INTERVAL '25 hours' WHERE id = %s",
        (vieja,),
    )

    assert trazabilidad.purgar_preguntas_atendidas() == 1

    ids = {p["id"] for p in trazabilidad.listar_preguntas_sin_respuesta()}
    assert vieja not in ids
    assert reciente in ids


def test_una_pregunta_pendiente_no_caduca_nunca():
    """Sigue siendo un agujero en la base de conocimiento, tenga la edad que tenga."""
    antigua = _crear_pregunta("nadie la miró")
    pool.ejecutar(
        "UPDATE preguntas_sin_respuesta SET creado_en = NOW() - INTERVAL '90 days' WHERE id = %s",
        (antigua,),
    )

    assert trazabilidad.purgar_preguntas_atendidas() == 0
    assert antigua in {p["id"] for p in trazabilidad.listar_preguntas_sin_respuesta()}


def test_la_pagina_de_preguntas_explica_para_que_sirve(sesion_cliente):
    _crear_pregunta("¿Aceptan tarjeta?")

    cuerpo = sesion_cliente.get("/preguntas").text

    assert "base de conocimiento" in cuerpo
    assert "Entendido" in cuerpo
    # La única acción es entender: aquí no se edita nada.
    assert "Marcar atendida" not in cuerpo
