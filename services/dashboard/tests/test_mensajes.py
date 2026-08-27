"""Los mensajes del proyecto: una clave, una cadena, y el verde que promete algo.

La pantalla era un acordeón: cada mensaje un `<details>` que al abrirse volcaba
en mitad de la lista todas sus partes, cada una con su formulario abierto y su
«Guardar». Ahora la lista solo se lee y lo que se edita vive en su ventana.

Lo que se prueba aquí es la lógica, no el CSS: que la clave sea única de verdad
(también al renombrar, que era por donde se colaba), que el switch del adjunto
mande sobre el campo del ID, y que el verde signifique «esto se puede enviar tal
cual» y no «alguien lo escribió».
"""

import pytest

from src.services import media, mensajeria, tiempos_mensajes
from tests.conftest import ServicioDeProyecto, token_csrf

mensajeria = ServicioDeProyecto(mensajeria, {
    "listar_plantillas", "obtener_plantilla", "buscar_por_clave", "partes_de",
    "crear_plantilla", "renombrar_plantilla", "eliminar_plantilla", "guardar_parte",
    "agregar_parte", "eliminar_parte", "revisar_media_de", "revisar_todos_los_adjuntos",
})


# --- La clave es lo único que identifica un mensaje ---------------------------

def test_la_clave_se_guarda_en_mayusculas():
    """«alajuela» y «ALAJUELA» tienen que ser la misma, no dos que chocan."""
    creada = mensajeria.crear_plantilla("alajuela", "admin")

    assert creada["clave"] == "ALAJUELA"


def test_no_se_crean_dos_mensajes_con_la_misma_clave():
    mensajeria.crear_plantilla("ALAJUELA", "admin")

    with pytest.raises(ValueError, match="Ya existe"):
        mensajeria.crear_plantilla("alajuela", "admin")


def test_tampoco_se_RENOMBRA_a_una_clave_que_ya_existe():
    """Solo se comprobaba al crear: renombrar llegaba al índice único y salía un
    500 sin explicación."""
    mensajeria.crear_plantilla("ALAJUELA", "admin")
    otra = mensajeria.crear_plantilla("HEREDIA", "admin")

    with pytest.raises(ValueError, match="Ya existe"):
        mensajeria.renombrar_plantilla(otra["id"], "ALAJUELA")

    assert mensajeria.obtener_plantilla(otra["id"])["clave"] == "HEREDIA"


def test_renombrarse_a_si_mismo_no_es_un_choque():
    """Guardar sin tocar la clave no puede dar «ya existe»."""
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")

    mensajeria.renombrar_plantilla(plantilla["id"], "ALAJUELA")

    assert mensajeria.obtener_plantilla(plantilla["id"])["clave"] == "ALAJUELA"


def test_una_clave_vacia_no_vale():
    with pytest.raises(ValueError, match="necesita una clave"):
        mensajeria.crear_plantilla("   ", "admin")


# --- El verde: «esto se puede enviar tal cual» --------------------------------

def test_un_mensaje_de_solo_texto_esta_listo():
    """No hay nada que comprobar: no puede quedarse en «sin comprobar»."""
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.guardar_parte(plantilla["id"], 1, "Nos vemos el lunes.", "", "")

    parte = mensajeria.partes_de(plantilla["id"])[0]
    assert mensajeria.parte_lista(parte)
    assert parte["problema"] == ""


def test_un_mensaje_vacio_no_esta_listo():
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.guardar_parte(plantilla["id"], 1, "", "", "")

    parte = mensajeria.partes_de(plantilla["id"])[0]
    assert not mensajeria.parte_lista(parte)
    assert "vac" in parte["problema"].lower()


def test_un_adjunto_que_no_se_pudo_abrir_no_esta_listo(monkeypatch):
    monkeypatch.setattr(media, "verificar", lambda ref, tipo: (False, "El archivo no es público."))
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.guardar_parte(plantilla["id"], 1, "Mira esto", "imagen", "1AbC_defGHIjklMNO")

    parte = mensajeria.partes_de(plantilla["id"])[0]
    assert not mensajeria.parte_lista(parte)
    assert parte["problema"] == "El archivo no es público."


def test_un_adjunto_comprobado_si_esta_listo(monkeypatch):
    monkeypatch.setattr(media, "verificar", lambda ref, tipo: (True, ""))
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.guardar_parte(plantilla["id"], 1, "Mira esto", "imagen", "1AbC_defGHIjklMNO")

    parte = mensajeria.partes_de(plantilla["id"])[0]
    assert parte["media_ok"] is True
    assert mensajeria.parte_lista(parte)


def test_un_adjunto_sin_comprobar_tampoco_esta_listo():
    """No pasa por el panel (se comprueba en cada guardado), pero una fila
    cargada a mano sí puede quedar así. Sin comprobar no se promete nada."""
    parte = {"texto": "Mira", "media_ref": "abc", "media_ok": None}

    assert not mensajeria.parte_lista(parte)


# --- El ID de Drive se comprueba SIEMPRE al guardar ---------------------------

def test_guardar_comprueba_el_adjunto(monkeypatch):
    """La comprobación va aquí y no al enviar: un enlace roto se descubre
    mientras se escribe, no cuando el cliente ya recibió media cadena."""
    llamadas = []
    monkeypatch.setattr(
        media, "verificar", lambda ref, tipo: (llamadas.append((ref, tipo)), (True, ""))[1]
    )
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")

    mensajeria.guardar_parte(plantilla["id"], 1, "Mira", "imagen", "1AbC_defGHIjklMNO")

    assert llamadas == [("1AbC_defGHIjklMNO", "imagen")]


def test_el_enlace_de_drive_se_reduce_al_id(monkeypatch):
    monkeypatch.setattr(media, "verificar", lambda ref, tipo: (True, ""))
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")

    mensajeria.guardar_parte(
        plantilla["id"],
        1,
        "Mira",
        "imagen",
        "https://drive.google.com/file/d/1AbC_defGHIjklMNO/view?usp=sharing",
    )

    assert mensajeria.partes_de(plantilla["id"])[0]["media_ref"] == "1AbC_defGHIjklMNO"


# --- El switch manda sobre el campo del ID ------------------------------------

def test_apagar_el_switch_quita_el_adjunto_aunque_el_id_siga_escrito(sesion_cliente, monkeypatch):
    """Apagarlo no puede obligar además a borrar el ID a mano."""
    monkeypatch.setattr(media, "verificar", lambda ref, tipo: (True, ""))
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.guardar_parte(plantilla["id"], 1, "Mira", "imagen", "1AbC_defGHIjklMNO")

    sesion_cliente.post(
        f"/mensajes/{plantilla['id']}/parte/1",
        data={
            "texto": "Mira",
            # El switch apagado no se envía; el campo del ID sí, con su valor.
            "media_tipo": "imagen",
            "media_ref": "1AbC_defGHIjklMNO",
            "csrf": token_csrf(sesion_cliente),
        },
        follow_redirects=False,
    )

    parte = mensajeria.partes_de(plantilla["id"])[0]
    assert parte["media_ref"] == ""
    assert parte["media_tipo"] == ""


def test_con_el_switch_encendido_si_se_guarda(sesion_cliente, monkeypatch):
    monkeypatch.setattr(media, "verificar", lambda ref, tipo: (True, ""))
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.agregar_parte(plantilla["id"])

    sesion_cliente.post(
        f"/mensajes/{plantilla['id']}/parte/1",
        data={
            "texto": "Mira",
            "con_media": "1",
            "media_tipo": "video",
            "media_ref": "1AbC_defGHIjklMNO",
            "csrf": token_csrf(sesion_cliente),
        },
        follow_redirects=False,
    )

    parte = mensajeria.partes_de(plantilla["id"])[0]
    assert parte["media_tipo"] == "video"
    assert parte["media_ref"] == "1AbC_defGHIjklMNO"


# --- La página: se lee, no se despliega ---------------------------------------

def test_la_lista_no_despliega_nada_y_cada_clave_abre_su_ventana(sesion_cliente):
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.guardar_parte(plantilla["id"], 1, "Nos vemos el lunes.", "", "")

    cuerpo = sesion_cliente.get("/mensajes").text

    assert f'data-abre="msg-{plantilla["id"]}"' in cuerpo
    assert f'id="msg-{plantilla["id"]}"' in cuerpo
    # El acordeón se fue: ya no hay <details> en la lista.
    assert "<details" not in cuerpo
    # Y no queda ningún campo «nombre» que rellenar.
    assert 'name="nombre"' not in cuerpo


def test_cada_mensaje_de_la_cadena_es_un_boton_con_su_ventana(sesion_cliente):
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.guardar_parte(plantilla["id"], 1, "Primero", "", "")
    mensajeria.guardar_parte(plantilla["id"], 2, "Segundo", "", "")

    cuerpo = sesion_cliente.get("/mensajes").text
    assert f'data-carga="/mensajes/{plantilla["id"]}/detalle"' in cuerpo

    # Los editores ya no inflan la página inicial: aparecen al abrir esta
    # plantilla y conservan los mismos botones y ventanas.
    cuerpo = sesion_cliente.get(f"/mensajes/{plantilla['id']}/detalle").text

    assert "Mensaje 1" in cuerpo and "Mensaje 2" in cuerpo
    assert f'data-abre="parte-{plantilla["id"]}-1"' in cuerpo
    assert f'id="parte-{plantilla["id"]}-2"' in cuerpo


def test_tras_guardar_se_vuelve_a_la_ventana_donde_estabas(sesion_cliente):
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.agregar_parte(plantilla["id"])

    respuesta = sesion_cliente.post(
        f"/mensajes/{plantilla['id']}/parte/1",
        data={"texto": "Nos vemos", "csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )

    destino = respuesta.headers["location"]
    assert f"abierto={plantilla['id']}" in destino and "parte=1" in destino
    assert "data-abrir-al-cargar" in sesion_cliente.get(destino).text


def test_crear_una_clave_repetida_desde_el_panel_avisa(sesion_cliente):
    mensajeria.crear_plantilla("ALAJUELA", "admin")

    respuesta = sesion_cliente.post(
        "/mensajes",
        data={"clave": "alajuela", "csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )

    assert "error=" in respuesta.headers["location"]
    assert len([p for p in mensajeria.listar_plantillas() if p["clave"] == "ALAJUELA"]) == 1


# --- Revisar TODOS los adjuntos de una vez ------------------------------------
#
# El estado de un adjunto no depende solo de nosotros: a un archivo de Drive le
# pueden quitar el permiso público sin que nadie toque el panel. Comprobarlos
# mensaje por mensaje eran decenas de clics.

def test_revisar_todos_vuelve_a_comprobar_los_adjuntos_de_todo_el_catalogo(monkeypatch):
    monkeypatch.setattr(media, "verificar", lambda ref, tipo: (True, ""))
    primera = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.guardar_parte(primera["id"], 1, "Con foto", "imagen", "1abc")
    segunda = mensajeria.crear_plantilla("HEREDIA", "admin")
    mensajeria.guardar_parte(segunda["id"], 1, "Con foto", "imagen", "2def")

    # Al archivo de la segunda le quitan el permiso público.
    monkeypatch.setattr(
        media, "verificar", lambda ref, tipo: (True, "") if ref == "1abc" else (False, "No es público.")
    )
    revisados, con_problema = mensajeria.revisar_todos_los_adjuntos()

    assert (revisados, con_problema) == (2, 1)
    assert mensajeria.obtener_plantilla(primera["id"])["problemas"] == []
    assert "No es público." in mensajeria.obtener_plantilla(segunda["id"])["problemas"][0]


def test_revisar_todos_no_toca_el_texto_ni_los_mensajes_sin_adjunto(monkeypatch):
    """Solo se escriben las columnas del adjunto: revisar no puede estropear lo
    que alguien acaba de escribir."""
    monkeypatch.setattr(media, "verificar", lambda ref, tipo: (True, ""))
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.guardar_parte(plantilla["id"], 1, "Solo texto", "", "")

    revisados, con_problema = mensajeria.revisar_todos_los_adjuntos()

    assert (revisados, con_problema) == (0, 0)
    assert mensajeria.partes_de(plantilla["id"])[0]["texto"] == "Solo texto"


def test_el_boton_de_revisar_no_se_confunde_con_el_id_de_un_mensaje(sesion_cliente, monkeypatch):
    """`/mensajes/revisar` va declarada ANTES que `/mensajes/{id}`: al revés,
    «revisar» entraría por la ruta con parámetro y saldría un 422."""
    monkeypatch.setattr(media, "verificar", lambda ref, tipo: (True, ""))
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "admin")
    mensajeria.guardar_parte(plantilla["id"], 1, "Con foto", "imagen", "1abc")

    respuesta = sesion_cliente.post(
        "/mensajes/revisar",
        data={"csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    assert "1%20adjuntos%20revisados" in respuesta.headers["location"]
    # Y la clave sigue siendo la que era: no se renombró ningún mensaje.
    assert mensajeria.obtener_plantilla(plantilla["id"])["clave"] == "ALAJUELA"


# --- Configuración del ritmo -------------------------------------------------

def test_mensajes_muestra_la_configuracion_en_una_ventana(sesion_cliente):
    cuerpo = sesion_cliente.get("/mensajes").text

    assert 'data-abre="configuracion-tiempos"' in cuerpo
    assert 'id="configuracion-tiempos"' in cuerpo
    assert 'name="intervalo_mensajes_segundos"' in cuerpo
    assert 'name="publicidad_3_cantidad"' in cuerpo


def test_guarda_todos_los_tiempos_por_proyecto(sesion_cliente):
    respuesta = sesion_cliente.post(
        "/mensajes/configuracion",
        data={
            "csrf": token_csrf(sesion_cliente),
            "intervalo_mensajes_segundos": "5",
            "recordatorio_habilitado": "1",
            "recordatorio_cantidad": "2",
            "recordatorio_unidad": "horas",
            "publicidad_1_cantidad": "10",
            "publicidad_1_unidad": "minutos",
            "publicidad_2_cantidad": "2",
            "publicidad_2_unidad": "horas",
            "publicidad_3_cantidad": "1",
            "publicidad_3_unidad": "dias",
        },
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    guardada = tiempos_mensajes.configuracion(1)
    assert guardada["intervalo_mensajes_segundos"] == 5
    assert guardada["publicidad_recordatorio_1_segundos"] == 600
    assert guardada["publicidad_recordatorio_2_segundos"] == 7200
    assert guardada["publicidad_recordatorio_3_segundos"] == 86400


def test_rechaza_recordatorios_de_publicidad_fuera_de_orden(sesion_cliente):
    respuesta = sesion_cliente.post(
        "/mensajes/configuracion",
        data={
            "csrf": token_csrf(sesion_cliente),
            "intervalo_mensajes_segundos": "5",
            "recordatorio_cantidad": "1",
            "recordatorio_unidad": "horas",
            "publicidad_1_cantidad": "3",
            "publicidad_1_unidad": "horas",
            "publicidad_2_cantidad": "2",
            "publicidad_2_unidad": "horas",
            "publicidad_3_cantidad": "1",
            "publicidad_3_unidad": "dias",
        },
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    assert "configuracion=1" in respuesta.headers["location"]
    assert tiempos_mensajes.configuracion(1)["publicidad_recordatorio_1_segundos"] == 7200
