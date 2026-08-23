"""Sesiones de envío: la tanda como unidad, el ritmo y los números que fallaron.

Mandar a cien números creaba cien filas sueltas y la pantalla las listaba una
debajo de otra: no había forma de saber «¿cómo va lo que mandé hace un rato?»
sin contarlas a ojo. Y salían todas de golpe, veinte por pasada del worker, que
es exactamente la firma de un bot.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.db import pool
from src.services import envios, media, mensajeria, palabras_clave
from tests.conftest import ServicioDeProyecto, token_csrf

mensajeria = ServicioDeProyecto(mensajeria, {
    "listar_plantillas", "obtener_plantilla", "buscar_por_clave", "partes_de",
    "crear_plantilla", "renombrar_plantilla", "eliminar_plantilla", "guardar_parte",
    "agregar_parte", "eliminar_parte", "obtener_envio", "reintentar", "reportar",
})
palabras_clave = ServicioDeProyecto(palabras_clave, {
    "listar", "obtener", "crear", "renombrar", "alternar_activa", "eliminar",
    "piezas_de", "agregar_pieza", "guardar_pieza", "eliminar_pieza", "revisar_media_de",
})
envios = ServicioDeProyecto(envios, {
    "opciones", "crear_lote", "listar_lotes", "obtener_lote", "destinos_de",
    "cancelar", "eliminar_lote",
})


@pytest.fixture(autouse=True)
def _sin_comprobar_adjuntos(monkeypatch):
    monkeypatch.setattr(media, "verificar", lambda ref, tipo: (True, ""))


def _mensaje(clave: str = "ALAJUELA") -> dict:
    plantilla = mensajeria.crear_plantilla(clave, "german")
    mensajeria.guardar_parte(plantilla["id"], 1, "Nos vemos el lunes.", "", "")
    return plantilla


# --- Los números --------------------------------------------------------------

def test_los_numeros_se_separan_por_lineas():
    validos, rechazados = envios.numeros("50688888888\n593987654321\n")

    assert validos == ["50688888888", "593987654321"]
    assert rechazados == []


def test_se_limpian_espacios_guiones_y_parentesis():
    validos, _ = envios.numeros("+506 8888-8888\n(593) 98 765 4321")

    assert validos == ["50688888888", "593987654321"]


def test_un_numero_sin_codigo_de_pais_se_rechaza():
    """Sin código, el mensaje o no sale o le llega a otra persona en otro país."""
    validos, rechazados = envios.numeros("88888888\n50688888888")

    assert validos == ["50688888888"]
    assert rechazados == ["88888888"]


def test_un_numero_repetido_se_envia_una_sola_vez():
    validos, _ = envios.numeros("50688888888\n+506 8888 8888")

    assert validos == ["50688888888"]


# --- Las tres categorías ------------------------------------------------------

def test_se_puede_enviar_un_mensaje():
    plantilla = _mensaje()

    lote = envios.crear_lote(
        categoria="mensaje", referencia_id=plantilla["id"], canal="whatsapp",
        destinos=["50611110000"], usuario="german",
    )

    assert lote["etiqueta"] == "ALAJUELA"
    assert envios.destinos_de(lote["id"])[0]["destino_id"] == "50611110000"


def test_se_puede_enviar_una_palabra_clave():
    """De una palabra clave se manda lo que sale al instante, no sus recordatorios:
    mandar un recordatorio a mano no tendría a qué recordar."""
    palabra = palabras_clave.crear("examen", "german")
    palabras_clave.guardar_pieza(
        palabra["mensajes"][0]["id"], texto="Aquí tiene el temario.", media_tipo="", media_ref=""
    )
    palabras_clave.agregar_pieza(palabra["id"], "recordatorio")

    lote = envios.crear_lote(
        categoria="palabra_clave", referencia_id=palabra["id"], canal="whatsapp",
        destinos=["50611110000"], usuario="german",
    )

    envio = mensajeria.obtener_envio(envios.destinos_de(lote["id"])[0]["id"])
    assert lote["etiqueta"] == "examen"
    assert [p["texto"] for p in envio["partes"]] == ["Aquí tiene el temario."]


def test_las_opciones_traen_los_problemas_de_cada_una():
    """Lo que está roto sale deshabilitado en el desplegable, no se encola."""
    mensajeria.crear_plantilla("VACIA", "german")

    rota = next(o for o in envios.opciones("mensaje") if o["etiqueta"] == "VACIA")

    assert rota["problemas"]


def test_no_se_encola_algo_roto():
    vacia = mensajeria.crear_plantilla("VACIA", "german")

    with pytest.raises(ValueError, match="revisar"):
        envios.crear_lote(
            categoria="mensaje", referencia_id=vacia["id"], canal="whatsapp",
            destinos=["50611110000"], usuario="german",
        )


def test_la_etiqueta_queda_congelada():
    """Si luego se renombra el origen, el histórico tiene que seguir diciendo
    qué se mandó."""
    plantilla = _mensaje()
    lote = envios.crear_lote(
        categoria="mensaje", referencia_id=plantilla["id"], canal="whatsapp",
        destinos=["50611110000"], usuario="german",
    )

    mensajeria.renombrar_plantilla(plantilla["id"], "OTRA_COSA")

    assert envios.obtener_lote(lote["id"])["etiqueta"] == "ALAJUELA"


# --- El progreso --------------------------------------------------------------

_siguiente = iter(range(1, 100))


def _lote_de(cuantos: int) -> dict:
    """Una sesión con N destinatarios. Cada una estrena su propia clave: dos
    mensajes no pueden llamarse igual."""
    plantilla = _mensaje(f"TANDA_{next(_siguiente)}")
    return envios.crear_lote(
        categoria="mensaje", referencia_id=plantilla["id"], canal="whatsapp",
        destinos=[f"5061111{i:04d}" for i in range(cuantos)], usuario="german",
    )


def test_el_progreso_cuenta_lo_hecho_no_solo_lo_que_salio_bien():
    """Lo que se está esperando es que la tanda TERMINE."""
    lote = _lote_de(4)
    ids = [d["id"] for d in envios.destinos_de(lote["id"])]
    pool.ejecutar("UPDATE envios SET estado='enviado' WHERE id = ANY(%s)", (ids[:2],))
    pool.ejecutar("UPDATE envios SET estado='error' WHERE id = %s", (ids[2],))

    actual = envios.obtener_lote(lote["id"])

    assert actual["enviados"] == 2
    assert actual["errores"] == 1
    assert actual["quedan"] == 1
    assert actual["porcentaje"] == 75
    assert actual["estado"] == "en curso"


def test_una_sesion_terminada_se_marca_como_tal():
    lote = _lote_de(2)
    pool.ejecutar("UPDATE envios SET estado='enviado' WHERE lote_id = %s", (lote["id"],))

    actual = envios.obtener_lote(lote["id"])

    assert actual["terminado"] is True
    assert actual["porcentaje"] == 100
    assert actual["estado"] == "terminada"


def test_las_sesiones_en_marcha_van_primero():
    """Es lo que se viene a mirar; el histórico se cae al fondo solo."""
    vieja = _lote_de(1)
    pool.ejecutar("UPDATE envios SET estado='enviado' WHERE lote_id = %s", (vieja["id"],))
    en_curso = _lote_de(1)
    pool.ejecutar(
        "UPDATE envios_lote SET creado_en = NOW() - INTERVAL '1 hour' WHERE id = %s",
        (en_curso["id"],),
    )

    orden = [l["id"] for l in envios.listar_lotes()]

    assert orden[0] == en_curso["id"]


# --- Qué falló ----------------------------------------------------------------

def test_se_puede_ver_solo_lo_que_fallo_en_cualquier_momento():
    """Si de los primeros veinte fallan quince, más vale enterarse antes de que
    salgan los ochenta restantes."""
    lote = _lote_de(3)
    ids = [d["id"] for d in envios.destinos_de(lote["id"])]
    pool.ejecutar(
        "UPDATE envios SET estado='error', error_cliente='El número no existe' WHERE id = %s",
        (ids[0],),
    )

    fallidos = envios.destinos_de(lote["id"], solo_fallidos=True)

    assert len(fallidos) == 1
    assert fallidos[0]["error_cliente"] == "El número no existe"


def test_los_que_fallaron_salen_primero():
    lote = _lote_de(3)
    ids = [d["id"] for d in envios.destinos_de(lote["id"])]
    pool.ejecutar("UPDATE envios SET estado='error' WHERE id = %s", (ids[2],))

    assert envios.destinos_de(lote["id"])[0]["id"] == ids[2]


# --- Cancelar y caducar -------------------------------------------------------

def test_cancelar_quita_lo_pendiente_y_deja_lo_enviado():
    lote = _lote_de(3)
    ids = [d["id"] for d in envios.destinos_de(lote["id"])]
    pool.ejecutar("UPDATE envios SET estado='enviado' WHERE id = %s", (ids[0],))

    quitados = envios.cancelar(lote["id"])

    assert quitados == 2
    actual = envios.obtener_lote(lote["id"])
    assert actual["estado"] == "cancelada"
    assert actual["enviados"] == 1


def test_una_sesion_caduca_a_los_doce_dias():
    vieja = _lote_de(1)
    reciente = _lote_de(1)
    pool.ejecutar(
        "UPDATE envios_lote SET creado_en = NOW() - INTERVAL '13 days' WHERE id = %s",
        (vieja["id"],),
    )

    assert envios.purgar_lotes_vencidos() == 1

    ids = {l["id"] for l in envios.listar_lotes()}
    assert vieja["id"] not in ids
    assert reciente["id"] in ids


def test_borrar_la_sesion_se_lleva_sus_envios():
    lote = _lote_de(3)

    envios.eliminar_lote(lote["id"])

    assert envios.destinos_de(lote["id"]) == []


# --- La ruta ------------------------------------------------------------------

def test_enviar_desde_el_panel_crea_la_sesion(sesion_cliente):
    plantilla = _mensaje()

    respuesta = sesion_cliente.post(
        "/enviar",
        data={
            "categoria": "mensaje",
            "referencia_id": plantilla["id"],
            "canal": "whatsapp",
            "destinos": "50611110000\n50611110001",
            "csrf": token_csrf(sesion_cliente),
        },
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    assert "/envios" in respuesta.headers["location"]
    assert envios.listar_lotes()[0]["total"] == 2


def test_los_numeros_sin_codigo_se_avisan_uno_por_uno(sesion_cliente):
    """«faltan tres» no sirve: hay que saber CUÁLES quedaron fuera."""
    plantilla = _mensaje()

    respuesta = sesion_cliente.post(
        "/enviar",
        data={
            "categoria": "mensaje",
            "referencia_id": plantilla["id"],
            "canal": "whatsapp",
            "destinos": "50611110000\n88888888",
            "csrf": token_csrf(sesion_cliente),
        },
        follow_redirects=False,
    )

    assert "88888888" in respuesta.headers["location"]
    assert envios.listar_lotes()[0]["total"] == 1


def test_sin_ningun_numero_valido_no_se_crea_nada(sesion_cliente):
    plantilla = _mensaje()

    respuesta = sesion_cliente.post(
        "/enviar",
        data={
            "categoria": "mensaje",
            "referencia_id": plantilla["id"],
            "canal": "whatsapp",
            "destinos": "88888888",
            "csrf": token_csrf(sesion_cliente),
        },
        follow_redirects=False,
    )

    assert "error=" in respuesta.headers["location"]
    assert envios.listar_lotes() == []


def test_se_puede_programar_para_mas_tarde(sesion_cliente):
    plantilla = _mensaje()
    dentro_de_un_rato = (datetime.now() + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")

    sesion_cliente.post(
        "/enviar",
        data={
            "categoria": "mensaje",
            "referencia_id": plantilla["id"],
            "canal": "whatsapp",
            "destinos": "50611110000",
            "empieza_en": dentro_de_un_rato,
            "csrf": token_csrf(sesion_cliente),
        },
        follow_redirects=False,
    )

    lote = envios.listar_lotes()[0]
    assert lote["empieza_en"] > datetime.now(timezone.utc) + timedelta(hours=2)


def test_la_pagina_de_envios_pinta_la_barra(sesion_cliente):
    _lote_de(2)

    cuerpo = sesion_cliente.get("/envios").text

    assert 'class="barra"' in cuerpo
    assert 'data-refrescar="/envios/sesiones"' in cuerpo


# --- El ritmo, contra Postgres de verdad --------------------------------------
#
# La consulta que reparte el ritmo vive en el bot, pero es SQL: `DISTINCT ON`,
# `FOR UPDATE SKIP LOCKED` y una condición sobre el lote. Con la base simulada no
# se probaría nada de eso, así que se ejerce aquí, que es donde hay Postgres.

_TOMAR = """
WITH elegibles AS (
    SELECT DISTINCT ON (COALESCE(e.lote_id, -e.id)) e.id
    FROM envios e
    LEFT JOIN envios_lote l ON l.id = e.lote_id
    WHERE e.estado = 'pendiente'
      AND (
            e.lote_id IS NULL
            OR (NOT l.cancelado AND l.empieza_en <= NOW() AND l.proximo_en <= NOW())
          )
    ORDER BY COALESCE(e.lote_id, -e.id), e.creado_en, e.id
    LIMIT 20
),
tomados AS (
    SELECT e.id FROM envios e
    WHERE e.id IN (SELECT id FROM elegibles) AND e.estado = 'pendiente'
    FOR UPDATE SKIP LOCKED
)
UPDATE envios e
SET estado = 'enviando', intentos = e.intentos + 1, actualizado_en = NOW()
FROM tomados
WHERE e.id = tomados.id
RETURNING e.id, e.lote_id
"""


def test_solo_sale_uno_por_sesion_en_cada_pasada():
    """Si se tomaran veinte de la misma tanda, saldrían las veinte seguidas y el
    ritmo no existiría."""
    lote = _lote_de(5)

    tomados = pool.consultar(_TOMAR)

    assert len(tomados) == 1
    assert tomados[0]["lote_id"] == lote["id"]


def test_dos_sesiones_avanzan_a_la_vez():
    """El ritmo es por tanda: dos tandas distintas no se hacen cola entre sí."""
    uno = _lote_de(3)
    otro = _lote_de(3)

    tomados = pool.consultar(_TOMAR)

    assert {t["lote_id"] for t in tomados} == {uno["id"], otro["id"]}


def test_no_sale_nada_hasta_que_toca():
    lote = _lote_de(2)
    pool.ejecutar(
        "UPDATE envios_lote SET proximo_en = NOW() + INTERVAL '20 seconds' WHERE id = %s",
        (lote["id"],),
    )

    assert pool.consultar(_TOMAR) == []


def test_una_sesion_programada_espera_a_su_hora():
    lote = _lote_de(2)
    pool.ejecutar(
        "UPDATE envios_lote SET empieza_en = NOW() + INTERVAL '3 hours' WHERE id = %s",
        (lote["id"],),
    )

    assert pool.consultar(_TOMAR) == []


def test_una_sesion_cancelada_no_manda_nada_mas():
    lote = _lote_de(3)
    pool.ejecutar("UPDATE envios_lote SET cancelado = TRUE WHERE id = %s", (lote["id"],))

    assert pool.consultar(_TOMAR) == []


def test_los_envios_sin_sesion_siguen_saliendo_todos():
    """Son de antes de que existieran los lotes: no tienen dónde apuntar un
    ritmo, y agruparlos los dejaría saliendo de uno en uno para siempre."""
    lote = _lote_de(3)
    pool.ejecutar("UPDATE envios SET lote_id = NULL WHERE lote_id = %s", (lote["id"],))

    tomados = pool.consultar(_TOMAR)

    assert len(tomados) == 3
