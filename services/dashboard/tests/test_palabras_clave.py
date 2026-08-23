"""Palabras clave: lo que el cliente escribe para disparar un flujo entero.

Estaban puestas a mano en el bot (`if keyword in {"tareas", "transporte"}`) y sus
recordatorios se agendaban con los segundos de `mensajes.json`. Añadir «examen»
era un cambio de código y un redespliegue.

Lo delicado aquí son los MINUTOS: se cuentan desde que se disparó la palabra (no
en cascada desde el anterior), cada recordatorio tiene que salir después del que
va antes, y hay un tope que no es decorativo — más allá, Celery re-entrega la
tarea y el cliente recibe el mismo recordatorio una y otra vez.
"""

import pytest

from src.services import media, palabras_clave
from tests.conftest import ServicioDeProyecto, token_csrf

palabras_clave = ServicioDeProyecto(palabras_clave, {
    "listar", "obtener", "crear", "renombrar", "alternar_activa", "eliminar",
    "piezas_de", "agregar_pieza", "guardar_pieza", "eliminar_pieza", "revisar_media_de",
})


# --- La palabra ---------------------------------------------------------------

def test_las_dos_palabras_de_siempre_estan_migradas():
    """«tareas» y «transporte» tienen que seguir funcionando sin tocar nada."""
    palabras = {p["palabra"]: p for p in palabras_clave.listar()}

    assert {"tareas", "transporte"} <= set(palabras)
    assert "curso teórico" in palabras["tareas"]["mensajes"][0]["texto"]
    # Los dos se llevan los mismos recordatorios: solo cambia el primer mensaje.
    assert len(palabras["tareas"]["recordatorios"]) == 3
    assert len(palabras["transporte"]["recordatorios"]) == 3


def test_la_palabra_se_guarda_en_minusculas():
    """El cliente escribe «Examen» o «EXAMEN» y tiene que ser la misma."""
    creada = palabras_clave.crear("  Examen  ", "german")

    assert creada["palabra"] == "examen"


def test_no_se_repiten_las_palabras():
    palabras_clave.crear("examen", "german")

    with pytest.raises(ValueError, match="Ya existe"):
        palabras_clave.crear("EXAMEN", "german")


def test_tampoco_al_renombrar():
    palabras_clave.crear("examen", "german")
    otra = palabras_clave.crear("practica", "german")

    with pytest.raises(ValueError, match="Ya existe"):
        palabras_clave.renombrar(otra["id"], "Examen")


def test_una_palabra_nueva_nace_con_un_mensaje_que_rellenar():
    """Sin nada que enviar no hace nada; así se ve qué es lo siguiente."""
    creada = palabras_clave.crear("examen", "german")

    assert len(creada["mensajes"]) == 1
    assert creada["problemas"], "un mensaje vacío tiene que salir marcado"


# --- Los minutos --------------------------------------------------------------

def _con_recordatorios(cuantos: int) -> dict:
    """Una palabra lista (con su mensaje relleno) y N recordatorios en blanco."""
    palabra = palabras_clave.crear("examen", "german")
    palabras_clave.guardar_pieza(
        palabra["mensajes"][0]["id"], texto="Aquí tiene el temario.", media_tipo="", media_ref=""
    )
    for _ in range(cuantos):
        palabras_clave.agregar_pieza(palabra["id"], "recordatorio")
    return palabras_clave.obtener(palabra["id"])


def test_un_recordatorio_no_puede_salir_antes_que_el_anterior():
    """Dos con el mismo minuto le llegan al cliente pegados y sin motivo."""
    palabra = _con_recordatorios(2)
    primero, segundo = palabra["recordatorios"]
    palabras_clave.guardar_pieza(primero["id"], texto="uno", media_tipo="", media_ref="", minutos=60)

    with pytest.raises(ValueError, match="después del anterior"):
        palabras_clave.guardar_pieza(
            segundo["id"], texto="dos", media_tipo="", media_ref="", minutos=60
        )


def test_tampoco_puede_adelantar_al_siguiente():
    palabra = _con_recordatorios(2)
    primero, segundo = palabra["recordatorios"]
    palabras_clave.guardar_pieza(segundo["id"], texto="dos", media_tipo="", media_ref="", minutos=120)

    with pytest.raises(ValueError, match="antes del siguiente"):
        palabras_clave.guardar_pieza(
            primero["id"], texto="uno", media_tipo="", media_ref="", minutos=180
        )


def test_creciendo_si_se_guarda():
    palabra = _con_recordatorios(3)
    for pieza, minutos in zip(palabra["recordatorios"], (60, 120, 180)):
        palabras_clave.guardar_pieza(
            pieza["id"], texto="hola", media_tipo="", media_ref="", minutos=minutos
        )

    guardados = palabras_clave.obtener(palabra["id"])["recordatorios"]
    assert [p["minutos"] for p in guardados] == [60, 120, 180]


def test_no_se_pasa_del_tope():
    """El tope no es decorativo: más allá, Celery re-entrega la tarea y el
    cliente recibe el mismo recordatorio una y otra vez."""
    palabra = _con_recordatorios(1)

    with pytest.raises(ValueError, match="máximo"):
        palabras_clave.guardar_pieza(
            palabra["recordatorios"][0]["id"],
            texto="hola",
            media_tipo="",
            media_ref="",
            minutos=palabras_clave.MAX_MINUTOS + 1,
        )


def test_el_tope_del_panel_es_el_que_reserva_el_bot():
    """Si aquí sube y en el bot no, los recordatorios largos se duplican."""
    import sys

    sys.path.insert(0, "/app")  # el bot no está en el path del dashboard
    esperado = 20160
    assert palabras_clave.MAX_MINUTOS == esperado, (
        "Si cambias este tope, cambia también MAX_RECORDATORIO_MINUTOS en "
        "services/bot_agent/src/infrastructure/tasks/celery_app.py"
    )


def test_un_recordatorio_no_sale_antes_de_un_minuto():
    palabra = _con_recordatorios(1)

    with pytest.raises(ValueError, match="antes de un minuto"):
        palabras_clave.guardar_pieza(
            palabra["recordatorios"][0]["id"], texto="hola", media_tipo="", media_ref="", minutos=0
        )


def test_un_recordatorio_nuevo_se_coloca_despues_del_ultimo():
    """Empezar por debajo del anterior sería crear algo inválido de entrada."""
    palabra = _con_recordatorios(1)
    palabras_clave.guardar_pieza(
        palabra["recordatorios"][0]["id"], texto="uno", media_tipo="", media_ref="", minutos=600
    )

    nuevo = palabras_clave.agregar_pieza(palabra["id"], "recordatorio")

    assert nuevo["minutos"] > 600


# --- Los adjuntos, igual que en los mensajes ----------------------------------

def test_el_adjunto_se_comprueba_al_guardar(monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        media, "verificar", lambda ref, tipo: (llamadas.append((ref, tipo)), (True, ""))[1]
    )
    palabra = palabras_clave.crear("examen", "german")
    pieza = palabra["mensajes"][0]

    palabras_clave.guardar_pieza(
        pieza["id"], texto="Mira", media_tipo="imagen", media_ref="1AbC_defGHIjklMNO"
    )

    assert llamadas == [("1AbC_defGHIjklMNO", "imagen")]


def test_un_recordatorio_apagado_no_cuenta_como_problema(monkeypatch):
    """Apagado está escrito pero fuera del envío: que esté vacío no importa."""
    palabra = _con_recordatorios(1)
    pieza = palabra["recordatorios"][0]
    palabras_clave.guardar_pieza(
        pieza["id"], texto="", media_tipo="", media_ref="", minutos=60, activo=False
    )

    assert palabras_clave.obtener(palabra["id"])["problemas"] == []


def test_un_recordatorio_encendido_y_vacio_si_es_un_problema():
    palabra = _con_recordatorios(1)
    pieza = palabra["recordatorios"][0]
    palabras_clave.guardar_pieza(
        pieza["id"], texto="", media_tipo="", media_ref="", minutos=60, activo=True
    )

    assert palabras_clave.obtener(palabra["id"])["problemas"]


# --- La ruta ------------------------------------------------------------------

def test_la_pagina_lista_las_palabras_y_abre_sus_ventanas(sesion_cliente):
    cuerpo = sesion_cliente.get("/palabras-clave").text

    assert "tareas" in cuerpo and "transporte" in cuerpo
    assert "data-abre=\"palabra-" in cuerpo
    assert "Recordatorio 1" in cuerpo


def test_un_minuto_invalido_avisa_y_no_guarda(sesion_cliente):
    palabra = _con_recordatorios(2)
    primero, segundo = palabra["recordatorios"]
    palabras_clave.guardar_pieza(primero["id"], texto="uno", media_tipo="", media_ref="", minutos=90)

    respuesta = sesion_cliente.post(
        f"/palabras-clave/{palabra['id']}/pieza/{segundo['id']}",
        data={"texto": "dos", "minutos": "10", "activo": "1", "csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )

    assert "error=" in respuesta.headers["location"]
    actual = palabras_clave.obtener(palabra["id"])["recordatorios"][1]
    assert actual["texto"] != "dos"


def test_desactivar_una_palabra_no_la_borra(sesion_cliente):
    palabra = palabras_clave.crear("examen", "german")

    sesion_cliente.post(
        f"/palabras-clave/{palabra['id']}/activa",
        data={"csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )

    assert palabras_clave.obtener(palabra["id"])["activa"] is False


def test_eliminar_la_palabra_se_lleva_sus_piezas():
    palabra = _con_recordatorios(2)

    palabras_clave.eliminar(palabra["id"])

    assert palabras_clave.obtener(palabra["id"]) is None
    assert palabras_clave.piezas_de(palabra["id"], "recordatorio") == []
