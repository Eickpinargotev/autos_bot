"""Copias de seguridad y carga masiva de mensajes y conocimiento."""

from src.services import archivos_catalogo, mensajeria, trazabilidad
from tests.conftest import token_csrf


HTML_CIUDADES = b"""<!doctype html><table>
<thead><tr><th>A</th><th>B</th><th>C</th><th>D</th></tr></thead>
<tbody>
<tr><td>CIUDAD</td><td>PRIMER MENSAJE</td><td>SEGUNDO MENSAJE</td><td>CIUDAD_MAYUSCULA</td><td>FACEBOOK</td></tr>
<tr><td>Alajuela</td><td>Hola desde Alajuela</td><td>Mira el video<br><br>video=1AbC_defGHIjklMNO</td><td>ALAJUELA</td><td>https://facebook.example/no-importar</td></tr>
</tbody></table>"""


def test_el_html_de_google_se_lee_como_datos_y_descarta_facebook():
    plantillas = archivos_catalogo.analizar_mensajes(HTML_CIUDADES, "ciudades.html")

    assert len(plantillas) == 1
    assert plantillas[0]["clave"] == "ALAJUELA"
    assert [p["texto"] for p in plantillas[0]["partes"]] == ["Hola desde Alajuela", "Mira el video"]
    assert plantillas[0]["partes"][1]["media_tipo"] == "video"
    assert plantillas[0]["partes"][1]["media_ref"] == "1AbC_defGHIjklMNO"
    assert "facebook" not in repr(plantillas).lower()


def test_csv_con_comillas_dobles_dentro_del_mensaje_no_desplaza_la_clave():
    datos = (
        'CIUDAD,PRIMER MENSAJE,CIUDAD_MAYUSCULA,FACEBOOK\n'
        'Quepos,"LUGAR: ""Iglesia Cuadrangular"". Barrio Bella Vista",QUEPOS,'
        'https://facebook.example/anuncio\n'
    ).encode()

    plantillas = archivos_catalogo.analizar_mensajes(datos, "ciudades.csv")

    assert [p["clave"] for p in plantillas] == ["QUEPOS"]
    assert plantillas[0]["partes"][0]["texto"] == (
        'LUGAR: "Iglesia Cuadrangular". Barrio Bella Vista'
    )


def test_cargar_mensajes_crea_y_luego_actualiza_sin_duplicar(sesion_cliente, monkeypatch):
    monkeypatch.setattr("src.services.media.verificar", lambda ref, tipo: (True, ""))
    for _ in range(2):
        respuesta = sesion_cliente.post(
            "/mensajes/cargar",
            data={"csrf": token_csrf(sesion_cliente)},
            files={"archivo": ("ciudades.html", HTML_CIUDADES, "text/html")},
            follow_redirects=False,
        )
        assert respuesta.status_code == 303

    plantilla = mensajeria.buscar_por_clave(1, "ALAJUELA")
    assert len(plantilla["partes"]) == 2
    assert plantilla["partes"][1]["media_ok"] is True
    assert len([p for p in mensajeria.listar_plantillas(1) if p["clave"] == "ALAJUELA"]) == 1


def test_descarga_de_mensajes_se_puede_volver_a_cargar_y_no_tiene_facebook(sesion_cliente):
    plantilla = mensajeria.crear_plantilla(1, "HEREDIA", "prueba")
    mensajeria.guardar_parte(1, plantilla["id"], 1, "Hola", "", "")

    respuesta = sesion_cliente.get("/mensajes/descargar")

    assert respuesta.status_code == 200
    assert "attachment" in respuesta.headers["content-disposition"]
    assert b"HEREDIA" in respuesta.content
    assert b"FACEBOOK" not in respuesta.content.upper()
    assert archivos_catalogo.analizar_mensajes(respuesta.content, "mensajes.csv")


def test_conocimiento_se_descarga_y_se_carga_sin_duplicarse(sesion_cliente):
    trazabilidad.crear_chunk(1, "Las clases prácticas duran dos horas.")
    copia = sesion_cliente.get("/conocimiento/descargar")
    assert b"CONTENIDO" in copia.content

    for _ in range(2):
        respuesta = sesion_cliente.post(
            "/conocimiento/cargar",
            data={"csrf": token_csrf(sesion_cliente)},
            files={"archivo": ("base.csv", copia.content, "text/csv")},
            follow_redirects=False,
        )
        assert respuesta.status_code == 303

    assert [c["contenido"] for c in trazabilidad.listar_chunks(1)] == [
        "Las clases prácticas duran dos horas."
    ]


def test_las_paginas_muestran_los_dos_botones(sesion_cliente):
    mensajes = sesion_cliente.get("/mensajes").text
    conocimiento = sesion_cliente.get("/conocimiento").text

    assert "/mensajes/descargar" in mensajes and "/mensajes/cargar" in mensajes
    assert "/conocimiento/descargar" in conocimiento and "/conocimiento/cargar" in conocimiento
