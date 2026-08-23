"""Bloqueos permanentes propios del proyecto."""

from src.db import pool
from src.services import bloqueos_permanentes, clientes_whatsapp
from tests.conftest import token_csrf


def _proyecto_actual():
    return pool.consultar_uno("SELECT * FROM clientes_whatsapp ORDER BY id LIMIT 1")


def test_el_admin_no_tiene_pantalla_de_bloqueos(sesion_admin):
    assert sesion_admin.get("/admin/bloqueos").status_code == 404
    assert sesion_admin.get("/admin/bloqueos/lista").status_code == 404


def test_el_negocio_agrega_y_elimina_numeros_permanentes(sesion_cliente):
    proyecto = _proyecto_actual()
    respuesta = sesion_cliente.post(
        "/configuracion-proyecto/bloqueos",
        data={"numero": "+506 8888-7777", "csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    filas = bloqueos_permanentes.listar(proyecto["id"])
    assert [fila["numero"] for fila in filas] == ["50688887777"]
    assert "50688887777" in sesion_cliente.get("/configuracion-proyecto/bloqueos").text

    sesion_cliente.post(
        f"/configuracion-proyecto/bloqueos/{filas[0]['id']}/eliminar",
        data={"csrf": token_csrf(sesion_cliente)},
    )
    assert bloqueos_permanentes.listar(proyecto["id"]) == []


def test_un_negocio_no_puede_ver_ni_borrar_la_lista_de_otro(sesion_cliente):
    propio = _proyecto_actual()
    ajeno = clientes_whatsapp.crear("Proyecto ajeno")
    bloqueo_ajeno = bloqueos_permanentes.agregar(ajeno["id"], "50611112222")
    bloqueos_permanentes.agregar(propio["id"], "50699998888")

    cuerpo = sesion_cliente.get("/configuracion-proyecto/bloqueos").text
    assert "50699998888" in cuerpo
    assert "50611112222" not in cuerpo

    respuesta = sesion_cliente.post(
        f"/configuracion-proyecto/bloqueos/{bloqueo_ajeno['id']}/eliminar",
        data={"csrf": token_csrf(sesion_cliente)},
    )
    assert respuesta.status_code == 404
    assert bloqueos_permanentes.estado_de(ajeno["id"], "whatsapp", "50611112222")


def test_bloquear_desde_el_hilo_actualiza_configuracion(sesion_cliente):
    proyecto = _proyecto_actual()
    pool.ejecutar(
        """
        INSERT INTO conversation_messages
            (proyecto_id, client_id, canal, direction, author, text)
        VALUES (%s, '50677776666', 'whatsapp', 'inbound', 'cliente', 'hola')
        """,
        (proyecto["id"],),
    )
    respuesta = sesion_cliente.post(
        "/conversaciones/whatsapp/50677776666/bloqueo",
        data={"csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert "50677776666" in sesion_cliente.get("/configuracion-proyecto/bloqueos").text
