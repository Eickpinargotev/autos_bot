"""Listado ligero, búsqueda, descarga e importación del registro de keywords."""

import csv
import io
from datetime import datetime, timezone

import pytest

from src.commands import importar_registros
from src.db import pool
from src.services import clientes_whatsapp, registros


def _proyecto_principal() -> dict:
    return pool.consultar_uno("SELECT * FROM clientes_whatsapp ORDER BY id LIMIT 1")


def _agregar(proyecto_id: int, numero: str, *, fecha: datetime, nombre: str = "Persona") -> None:
    pool.ejecutar(
        """
        INSERT INTO keyword_registros
            (proyecto_id, registro, canal, nombre, palabra_clave, creado_en)
        VALUES (%s, %s, 'whatsapp', %s, '', %s)
        """,
        (proyecto_id, numero, nombre, fecha),
    )


def test_pagina_de_diez_usa_cursor_sin_saltos_aunque_las_fechas_coincidan():
    proyecto = _proyecto_principal()
    fecha = datetime(2026, 8, 24, 18, tzinfo=timezone.utc)
    for indice in range(25):
        _agregar(proyecto["id"], f"5068000{indice:04d}", fecha=fecha)

    primera = registros.pagina(proyecto["id"])
    segunda = registros.pagina(proyecto["id"], primera["siguiente_cursor"])
    tercera = registros.pagina(proyecto["id"], segunda["siguiente_cursor"])

    numeros = [
        fila["registro"]
        for pagina in (primera, segunda, tercera)
        for fila in pagina["registros"]
    ]
    assert [len(p["registros"]) for p in (primera, segunda, tercera)] == [10, 10, 5]
    assert len(numeros) == len(set(numeros)) == 25
    assert numeros[0] == "50680000024" and numeros[-1] == "50680000000"
    assert tercera["siguiente_cursor"] == ""


def test_busqueda_es_exacta_normaliza_formato_y_no_acepta_parciales():
    proyecto = _proyecto_principal()
    _agregar(
        proyecto["id"],
        "50688887777",
        fecha=datetime.now(timezone.utc),
        nombre="Exacta",
    )

    assert registros.buscar(proyecto["id"], "+506 8888-7777")[0]["nombre"] == "Exacta"
    assert registros.buscar(proyecto["id"], "88887777") == []
    assert registros.buscar(proyecto["id"], "sin número") == []


def test_registros_quedan_aislados_por_proyecto():
    propio = _proyecto_principal()
    ajeno = clientes_whatsapp.crear("Proyecto ajeno")
    fecha = datetime.now(timezone.utc)
    _agregar(propio["id"], "50611112222", fecha=fecha)
    _agregar(ajeno["id"], "50699998888", fecha=fecha)

    assert [r["registro"] for r in registros.pagina(propio["id"])["registros"]] == [
        "50611112222"
    ]
    assert registros.buscar(propio["id"], "50699998888") == []


def test_pantalla_inicial_solo_pinta_diez_y_ofrece_siguiente_tanda(sesion_cliente):
    proyecto = _proyecto_principal()
    fecha = datetime.now(timezone.utc)
    for indice in range(12):
        _agregar(proyecto["id"], f"5067000{indice:04d}", fecha=fecha)

    cuerpo = sesion_cliente.get("/registros").text

    assert cuerpo.count("data-registro-id=") == 10
    assert "data-registros-siguiente=" in cuerpo
    assert 'href="/registros/descargar"' in cuerpo
    assert 'href="/registros"' in cuerpo  # entrada del lateral


def test_busqueda_http_muestra_solo_la_coincidencia(sesion_cliente):
    proyecto = _proyecto_principal()
    fecha = datetime.now(timezone.utc)
    _agregar(proyecto["id"], "50688887777", fecha=fecha, nombre="Buscada")
    _agregar(proyecto["id"], "50688886666", fecha=fecha, nombre="Otra")

    cuerpo = sesion_cliente.get("/registros?q=%2B506+8888-7777").text

    assert "Buscada" in cuerpo and "Otra" not in cuerpo
    assert "data-registros-siguiente=" not in cuerpo


def test_cursor_http_invalido_se_rechaza(sesion_cliente):
    assert sesion_cliente.get("/registros/lista?cursor=no-es-valido").status_code == 400


def test_descarga_incluye_todo_y_marca_el_historico(sesion_cliente):
    proyecto = _proyecto_principal()
    fecha = datetime(2026, 8, 24, 18, tzinfo=timezone.utc)
    for indice in range(12):
        _agregar(proyecto["id"], f"5066000{indice:04d}", fecha=fecha)

    respuesta = sesion_cliente.get("/registros/descargar")
    texto = respuesta.content.decode("utf-8-sig")
    filas = list(csv.reader(io.StringIO(texto)))

    assert respuesta.status_code == 200
    assert respuesta.content.startswith(b"\xef\xbb\xbf")
    assert len(filas) == 13
    assert filas[0] == ["numero", "Nombre", "Palabra clave", "Canal", "Fecha de registro"]
    assert all(fila[2] == "Histórico" for fila in filas[1:])


def test_importador_deduplica_prefiere_nombre_real_y_es_idempotente(tmp_path):
    ruta = tmp_path / "registros.csv"
    ruta.write_text(
        "numero,Nombre,Fecha de registro (dia/mes//año)\n"
        "50688887777,solicitado agregar manual,02/11/2025\n"
        "50688887777,Ana Pérez,28/10/2025\n"
        "50677776666,.,03/11/2025\n"
        "abc,Inválida,03/11/2025\n",
        encoding="utf-8",
    )
    lectura = importar_registros.leer_csv(ruta)

    assert lectura.leidas == 4
    assert len(lectura.registros) == 2
    assert lectura.rechazadas == 1
    ana = next(r for r in lectura.registros if r.numero == "50688887777")
    assert ana.nombre == "Ana Pérez"
    assert ana.fecha.isoformat() == "2025-10-28"

    primero = importar_registros.importar(lectura, "proyecto-de-pruebas")
    segundo = importar_registros.importar(lectura, "proyecto-de-pruebas")
    filas = pool.consultar(
        "SELECT * FROM keyword_registros WHERE registro IN ('50688887777', '50677776666')"
    )

    assert primero["insertadas"] == 2
    assert segundo["insertadas"] == 0 and segundo["existentes"] == 2
    assert len(filas) == 2
    assert all(f["canal"] == "whatsapp" and f["palabra_clave"] == "" for f in filas)


def test_importador_valida_cabeceras(tmp_path):
    ruta = tmp_path / "mal.csv"
    ruta.write_text("numero,Nombre\n50688887777,Ana\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Faltan columnas"):
        importar_registros.leer_csv(ruta)
