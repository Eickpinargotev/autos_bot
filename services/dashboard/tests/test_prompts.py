"""Versionado de playbooks por agente y configuración del recordatorio."""

import pytest

from src.services import instrucciones
from tests.conftest import token_csrf


def test_un_proyecto_nace_con_todos_los_playbooks_y_una_hora():
    activos = {
        tipo: instrucciones.activa(1, tipo)
        for tipo in instrucciones.TIPOS_EDITABLES
    }
    recordatorio = instrucciones.activa(1, "recordatorio")
    config = instrucciones.configuracion_recordatorios(1)

    assert set(activos) == set(instrucciones.TIPOS_EDITABLES)
    assert all(fila["contenido"].strip() for fila in activos.values())
    assert "COORDINADOR / RECEPCIÓN" in activos["supervisor"]["contenido"]
    assert "ALQUILER DE VEHÍCULO" in activos["alquiler"]["contenido"]
    assert "CUÁNDO NO ENVIAR" in recordatorio["contenido"]
    assert config["habilitado"] is True
    assert config["intervalo_minutos"] == 60


def test_los_historiales_son_independientes_y_rollback_crea_otra_version():
    original = instrucciones.activa(1, "supervisor")
    versiones_recordatorio = len(instrucciones.historial(1, "recordatorio"))
    instrucciones.guardar(1, "Atienda con mucha brevedad.", "dueño", "supervisor")
    instrucciones.guardar(1, "Recuerde sin insistir.", "dueño", "recordatorio")

    restaurada = instrucciones.activar(1, original["version"], "dueño", "supervisor")

    assert restaurada["version"] == 3
    assert "COORDINADOR / RECEPCIÓN" in restaurada["contenido"]
    assert instrucciones.activa(1, "recordatorio")["contenido"] == "Recuerde sin insistir."
    assert len(instrucciones.historial(1, "supervisor")) == 3
    assert len(instrucciones.historial(1, "recordatorio")) == versiones_recordatorio + 1


def test_rollback_de_recordatorio_antiguo_no_reactiva_el_contrato_tecnico():
    antigua = next(
        fila for fila in instrucciones.historial(1, "recordatorio")
        if "Los datos llegan como JSON" in fila["contenido"]
    )
    restaurada = instrucciones.activar(1, antigua["version"], "dueño", "recordatorio")

    assert "CUÁNDO NO ENVIAR" in restaurada["contenido"]
    assert "Los datos llegan como JSON" not in restaurada["contenido"]


def test_guardar_el_mismo_texto_no_duplica_y_vacio_se_rechaza():
    actual = instrucciones.activa(1, "alquiler")
    resultado = instrucciones.guardar(1, actual["contenido"], "dueño", "alquiler")
    assert resultado["sin_cambios"] is True
    assert len(instrucciones.historial(1, "alquiler")) == 1

    with pytest.raises(ValueError, match="vacío"):
        instrucciones.guardar(1, "   ", "dueño", "alquiler")


def test_configuracion_convierte_horas_y_permite_apagar():
    fila = instrucciones.guardar_configuracion_recordatorios(
        1, False, 2, "horas", "dueño"
    )
    assert fila["habilitado"] is False
    assert fila["intervalo_minutos"] == 120


def test_la_pagina_muestra_una_tarjeta_y_modales_por_agente(sesion_cliente):
    instrucciones.guardar(1, "Otra versión del supervisor.", "dueño", "supervisor")
    html = sesion_cliente.get("/agente/instrucciones").text

    for meta in instrucciones.METADATOS.values():
        assert meta["nombre"] in html
        assert meta["codigo"] in html
    assert "Editar agente: Alquiler" in html
    assert "Historial: Supervisor" in html
    assert "Recordatorios automáticos activos" in html
    assert "Rollback" in html
    assert "CUÁNDO NO ENVIAR" in html


def test_las_rutas_guardan_prompt_y_configuracion(sesion_cliente):
    csrf = token_csrf(sesion_cliente)
    respuesta = sesion_cliente.post(
        "/agente/prompts/recordatorio",
        data={"contenido": "Un seguimiento cordial.", "csrf": csrf},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert instrucciones.activa(1, "recordatorio")["contenido"] == "Un seguimiento cordial."

    respuesta = sesion_cliente.post(
        "/agente/recordatorios/configuracion",
        data={"cantidad": "20", "unidad": "minutos", "csrf": csrf},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert instrucciones.configuracion_recordatorios(1)["habilitado"] is False
    assert instrucciones.configuracion_recordatorios(1)["intervalo_minutos"] == 20
