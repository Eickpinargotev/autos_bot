"""Edición aislada y transaccional de los textos de publicidad."""

from html import unescape
import re

import pytest

from src.db import pool
from src.services import instrucciones, mensajeria, recordatorios_publicidad, tiempos_mensajes
from tests.conftest import token_csrf


def texto_mostrado(html, indice):
    return unescape(re.search(rf'<textarea id="publicidad-texto-{indice}"[^>]*>(.*?)</textarea>',
                              html, re.S).group(1))


def formulario(sesion):
    datos = {
        "csrf": token_csrf(sesion), "intervalo_mensajes_segundos": "5",
        "recordatorio_habilitado": "1", "recordatorio_cantidad": "1",
        "recordatorio_unidad": "horas",
        "publicidad_1_cantidad": "2", "publicidad_1_unidad": "horas",
        "publicidad_2_cantidad": "20", "publicidad_2_unidad": "horas",
        "publicidad_3_cantidad": "23", "publicidad_3_unidad": "horas",
    }
    datos.update({f"publicidad_{i}_texto": texto for i, texto in enumerate(
        ("👋 ¿Continuamos?", "¿Tiene dudas sobre {dia}?", "{dia}\n{hora}\n{valor} colones\n{enlace}"), 1)})
    return datos


def test_muestra_originales_y_editor_sin_cambiar_datos(sesion_cliente):
    antes = tiempos_mensajes.configuracion(1)
    respuesta = sesion_cliente.get("/mensajes")
    assert respuesta.status_code == 200
    assert [texto_mostrado(respuesta.text, i) for i in range(1, 4)] == list(recordatorios_publicidad.TEXTOS_PUBLICIDAD_ORIGINALES)
    assert 'data-inserta-variable="enlace"' in respuesta.text
    assert "Vista previa · datos de ejemplo" in respuesta.text
    assert tiempos_mensajes.configuracion(1) == antes


def test_guarda_textos_sin_cambiar_publicidades_ni_tiempos(sesion_cliente):
    antes = mensajeria.listar_plantillas(1)
    tiempos = tiempos_mensajes.configuracion(1)
    otro = pool.consultar_uno("""
        INSERT INTO clientes_whatsapp (nombre, slug, webhook_token)
        VALUES ('Otro', 'otro', 'token-otro') RETURNING id
    """)
    respuesta = sesion_cliente.post("/mensajes/configuracion", data=formulario(sesion_cliente), follow_redirects=False)
    assert respuesta.status_code == 303
    actual = tiempos_mensajes.configuracion(1)
    assert recordatorios_publicidad.textos_de(actual) == [formulario(sesion_cliente)[f"publicidad_{i}_texto"] for i in range(1, 4)]
    for clave in ("intervalo_mensajes_segundos", *(f"publicidad_recordatorio_{i}_segundos" for i in range(1, 4))):
        assert actual[clave] == tiempos[clave]
    assert mensajeria.listar_plantillas(1) == antes
    assert recordatorios_publicidad.textos_de(tiempos_mensajes.configuracion(otro["id"])) == list(recordatorios_publicidad.TEXTOS_PUBLICIDAD_ORIGINALES)


def test_cliente_anterior_no_borra_textos_y_conserva_maximo(sesion_cliente):
    instrucciones.guardar_configuracion_recordatorios(1, True, 1, "horas", "test", 4)
    datos = formulario(sesion_cliente)
    sesion_cliente.post("/mensajes/configuracion", data=datos)
    for i in range(1, 4):
        del datos[f"publicidad_{i}_texto"]
    datos["publicidad_1_cantidad"] = "3"
    respuesta = sesion_cliente.post("/mensajes/configuracion", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303
    assert tiempos_mensajes.configuracion(1)["publicidad_recordatorio_1_segundos"] == 10800
    assert recordatorios_publicidad.textos_de(tiempos_mensajes.configuracion(1))[0] == "👋 ¿Continuamos?"
    assert instrucciones.configuracion_recordatorios(1)["maximo_recordatorios"] == 4


@pytest.mark.parametrize("texto", ["", "  \n  ", "x" * 2001, "Hola {ciudad}", "Hola {dia", "{dia.__class__}", "{valor:,.2f}"])
def test_error_conserva_formulario_y_no_guarda_nada(sesion_cliente, texto):
    antes = tiempos_mensajes.configuracion(1)
    seguimiento = instrucciones.configuracion_recordatorios(1)
    datos = formulario(sesion_cliente)
    datos.update(publicidad_2_texto=texto, intervalo_mensajes_segundos="9", recordatorio_cantidad="2")
    respuesta = sesion_cliente.post("/mensajes/configuracion", data=datos)
    assert respuesta.status_code == 422
    assert texto_mostrado(respuesta.text, 2) == texto
    assert re.search(r'id="intervalo-mensajes"[^>]*value="9"', respuesta.text)
    assert 'id="error-publicidad_2_texto"' in respuesta.text
    assert 'aria-invalid="true"' in respuesta.text
    assert tiempos_mensajes.configuracion(1) == antes
    assert instrucciones.configuracion_recordatorios(1) == seguimiento


def test_rollback_si_falla_la_segunda_escritura(monkeypatch, sesion_cliente):
    antes = tiempos_mensajes.configuracion(1)

    def falla(*args, **kwargs):
        raise RuntimeError("Fallo de escritura simulado")

    monkeypatch.setattr(instrucciones, "guardar_configuracion_recordatorios", falla)
    with pytest.raises(RuntimeError, match="simulado"):
        recordatorios_publicidad.guardar_configuracion(1, 9, [(3, "horas"), (20, "horas"), (23, "horas")],
                                                       True, 1, "horas", "test", ["uno", "dos", "tres"])
    assert tiempos_mensajes.configuracion(1) == antes


def test_texto_literal_se_escapa_y_no_exige_variables(sesion_cliente):
    datos = formulario(sesion_cliente)
    datos["publicidad_3_texto"] = '<script>alert("hola")</script>\nTexto libre 🐱'
    respuesta = sesion_cliente.post("/mensajes/configuracion", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303
    respuesta = sesion_cliente.get("/mensajes")
    assert '<script>alert("hola")</script>' not in respuesta.text
    assert "&lt;script&gt;" in respuesta.text
    assert texto_mostrado(respuesta.text, 3) == datos["publicidad_3_texto"]


def test_no_guarda_sin_csrf(sesion_cliente):
    datos = formulario(sesion_cliente)
    datos["csrf"] = "invalido"
    assert sesion_cliente.post("/mensajes/configuracion", data=datos).status_code == 403


@pytest.mark.parametrize("cambio,campo", [
    ({"intervalo_mensajes_segundos": "0"}, "intervalo_mensajes_segundos"),
    ({"publicidad_2_cantidad": "1"}, "publicidad_2_cantidad"),
    ({"publicidad_3_unidad": "invalida"}, "publicidad_3_unidad"),
    ({"recordatorio_cantidad": "0"}, "recordatorio_cantidad"),
])
def test_senala_errores_de_tiempos_sin_perder_textos(sesion_cliente, cambio, campo):
    datos = formulario(sesion_cliente)
    datos.update(cambio)
    respuesta = sesion_cliente.post("/mensajes/configuracion", data=datos)
    assert respuesta.status_code == 422
    etiqueta = re.search(rf'<(?:input|select)[^>]*name="{campo}"[^>]*>', respuesta.text, re.S).group(0)
    assert "aria-invalid=true" in etiqueta
    assert texto_mostrado(respuesta.text, 1) == datos["publicidad_1_texto"]


def test_limite_de_dos_mil_caracteres_se_puede_guardar(sesion_cliente):
    datos = formulario(sesion_cliente)
    datos["publicidad_1_texto"] = "x" * 2000
    respuesta = sesion_cliente.post("/mensajes/configuracion", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303
    assert recordatorios_publicidad.textos_de(tiempos_mensajes.configuracion(1))[0] == "x" * 2000
