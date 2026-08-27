"""Catálogo versionado de fragmentos literales por proyecto."""

import pytest

from src.services import clientes_whatsapp, fragmentos, instrucciones
from tests.conftest import token_csrf


def _categoria(codigo: str) -> dict:
    return next(c for c in fragmentos.listar(1) if c["codigo"] == codigo)


def test_la_semilla_conserva_queja_y_sus_permisos():
    queja = next(f for f in _categoria("QUEJA")["fragmentos"] if f["codigo"] == "Q1")

    assert queja["fragment_id"] == "QUEJA.Q1"
    assert queja["agentes"] == ["SUPERVISOR"]
    assert "lujo de detalles" in queja["mensajes"][0]
    assert "queja" in queja["reporte"].lower()


def test_crear_editar_y_restaurar_un_fragmento():
    categoria = _categoria("QUEJA")
    creado = fragmentos.crear_fragmento(
        1, categoria["id"], "Q2", ["Primero", "Segundo"],
        "Respondió la segunda queja", ["SUPERVISOR"], "dueño",
    )
    fragmentos.guardar_fragmento(
        1, creado["id"], ["Texto nuevo"], "Nuevo reporte",
        ["SUPERVISOR", "GENERAL"], "dueño",
    )

    editado = fragmentos.obtener(1, creado["id"])
    assert editado["version"] == 2
    assert editado["mensajes"] == ["Texto nuevo"]
    assert set(editado["agentes"]) == {"SUPERVISOR", "GENERAL"}

    restaurado = fragmentos.restaurar(1, creado["id"], 1, "dueño")
    assert restaurado["version"] == 3
    assert restaurado["mensajes"] == ["Primero", "Segundo"]


def test_prompt_rechaza_fragmento_inexistente_archivado_o_ajeno():
    with pytest.raises(ValueError, match="no existe"):
        instrucciones.guardar(1, "Envía [[frag:QUEJA.NO_EXISTE]]", "dueño", "supervisor")

    queja = next(f for f in _categoria("QUEJA")["fragmentos"] if f["codigo"] == "Q1")
    with pytest.raises(ValueError, match="no está asignado"):
        instrucciones.guardar(1, "Envía [[frag:QUEJA.Q1]]", "dueño", "general")

    # Primero se quita la referencia del prompt vigente; entonces puede archivarse.
    instrucciones.guardar(1, "Atiende las quejas con prudencia.", "dueño", "supervisor")
    fragmentos.archivar_fragmento(1, queja["id"])
    with pytest.raises(ValueError, match="archivado"):
        instrucciones.guardar(1, "Envía [[frag:QUEJA.Q1]]", "dueño", "supervisor")


def test_no_se_archiva_ni_desasigna_si_un_prompt_activo_lo_usa():
    queja = next(f for f in _categoria("QUEJA")["fragmentos"] if f["codigo"] == "Q1")

    with pytest.raises(ValueError, match="Primero quite"):
        fragmentos.archivar_fragmento(1, queja["id"])
    with pytest.raises(ValueError, match="Primero quite"):
        fragmentos.guardar_fragmento(
            1, queja["id"], queja["mensajes"], queja["reporte"], ["GENERAL"], "dueño"
        )


def test_catalogos_de_proyectos_distintos_no_se_mezclan():
    otro = clientes_whatsapp.crear("Otro catálogo")
    categoria = _categoria("QUEJA")
    creado = fragmentos.crear_fragmento(
        1, categoria["id"], "SOLO_UNO", ["Privado"], "", ["SUPERVISOR"], "dueño"
    )

    assert fragmentos.obtener(1, creado["id"])["mensajes"] == ["Privado"]
    assert all(
        f["fragment_id"] != "QUEJA.SOLO_UNO"
        for c in fragmentos.listar(otro["id"])
        for f in c["fragmentos"]
    )


def test_la_pagina_y_las_rutas_permiten_administrar_fragmentos(sesion_cliente):
    html = sesion_cliente.get("/agente/fragmentos").text
    assert "[[frag:QUEJA.Q1]]" in html
    assert "Nueva categoría" in html
    assert "Agentes autorizados" in html

    csrf = token_csrf(sesion_cliente)
    respuesta = sesion_cliente.post(
        f"/agente/fragmentos/categorias/{_categoria('QUEJA')['id']}/fragmentos",
        data={
            "codigo": "Q2", "mensajes": ["Uno", "Dos"],
            "agentes": ["SUPERVISOR"], "reporte": "Revisar respuesta", "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert "abierto=" in respuesta.headers["location"]
    creado = next(f for f in _categoria("QUEJA")["fragmentos"] if f["codigo"] == "Q2")
    detalle = sesion_cliente.get(f"/agente/fragmentos/{creado['id']}/detalle").text
    assert "Guardar nueva versión" in detalle
    assert "Revisar respuesta" in detalle
