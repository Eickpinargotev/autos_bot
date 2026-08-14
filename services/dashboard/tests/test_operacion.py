"""Ciclo de vida de los envíos manuales."""

import pytest

from src.db import pool
from src.services import mensajeria
from src.services import envios as svc_envios


# --- Envíos ------------------------------------------------------------------

@pytest.fixture
def plantilla(monkeypatch):
    # No se comprueba el adjunto de verdad: eso se prueba aparte, en
    # test_media.py, y aquí solo interesa el ciclo del envío.
    monkeypatch.setattr(mensajeria.media, "verificar", lambda ref, tipo: (True, ""))
    creada = mensajeria.crear_plantilla("RECORDATORIO", "admin")
    mensajeria.guardar_parte(creada["id"], 1, "Hola", "imagen", "1abc")
    return creada


def _encolar(plantilla_id: int, canal: str = "telegram", destinos=("123",)) -> dict:
    """Crea una sesión y devuelve su primer envío."""
    lote = svc_envios.crear_lote(
        categoria="mensaje",
        referencia_id=plantilla_id,
        canal=canal,
        destinos=list(destinos),
        usuario="admin",
    )
    return {"lote": lote, "envios": svc_envios.destinos_de(lote["id"])}


def test_encolar_copia_el_contenido_de_la_plantilla(plantilla):
    """Editar el mensaje después no debe cambiar lo que ya se encoló."""
    envio_id = _encolar(plantilla["id"])["envios"][0]["id"]
    mensajeria.guardar_parte(plantilla["id"], 1, "TEXTO NUEVO", "", "")

    actual = mensajeria.obtener_envio(envio_id)
    assert actual["partes"][0]["texto"] == "Hola"
    assert actual["partes"][0]["media_ref"] == "1abc"


def test_encolar_copia_todas_las_partes_en_orden(plantilla):
    mensajeria.guardar_parte(plantilla["id"], 2, "Segunda", "", "")
    mensajeria.guardar_parte(plantilla["id"], 3, "Tercera", "", "")

    envio = mensajeria.obtener_envio(_encolar(plantilla["id"])["envios"][0]["id"])

    assert [p["texto"] for p in envio["partes"]] == ["Hola", "Segunda", "Tercera"]


def test_no_se_encola_un_mensaje_con_un_adjunto_roto(plantilla, monkeypatch):
    """Encolar algo que se sabe roto solo produce un cliente con medio mensaje."""
    monkeypatch.setattr(
        mensajeria.media, "verificar", lambda ref, tipo: (False, "El archivo no es público.")
    )
    mensajeria.guardar_parte(plantilla["id"], 1, "Hola", "imagen", "1abc")

    with pytest.raises(ValueError, match="no es público"):
        _encolar(plantilla["id"])


def test_no_se_encola_un_mensaje_sin_partes():
    vacia = mensajeria.crear_plantilla("VACIA", "admin")
    with pytest.raises(ValueError, match="ningún mensaje"):
        _encolar(vacia["id"])


def test_encolar_admite_varios_destinos(plantilla):
    creados = _encolar(plantilla["id"], canal="whatsapp", destinos=("1", "2", "3"))["envios"]
    assert len(creados) == 3
    assert all(e["estado"] == "pendiente" for e in creados)


def test_no_se_encola_a_un_canal_desconocido(plantilla):
    with pytest.raises(ValueError):
        _encolar(plantilla["id"], canal="telegrama")


def _fallar(envio_id: int, intentos: int = 1):
    pool.ejecutar(
        "UPDATE envios SET estado='error', intentos=%s, error_cliente='falló' WHERE id=%s",
        (intentos, envio_id),
    )


def test_el_reintento_se_corta_a_los_tres_intentos(plantilla):
    envio_id = _encolar(plantilla["id"])["envios"][0]["id"]

    _fallar(envio_id, intentos=1)
    assert mensajeria.reintentar(envio_id)[0] is True

    _fallar(envio_id, intentos=mensajeria.MAX_INTENTOS)
    ok, mensaje = mensajeria.reintentar(envio_id)
    assert ok is False
    assert "agotaron" in mensaje


def test_reintentar_limpia_el_error_anterior(plantilla):
    envio_id = _encolar(plantilla["id"])["envios"][0]["id"]
    _fallar(envio_id)

    mensajeria.reintentar(envio_id)
    actual = mensajeria.obtener_envio(envio_id)
    assert actual["estado"] == "pendiente"
    assert actual["error_cliente"] == ""


def test_solo_se_reintenta_lo_que_falló(plantilla):
    envio_id = _encolar(plantilla["id"])["envios"][0]["id"]
    ok, mensaje = mensajeria.reintentar(envio_id)
    assert ok is False
    assert "pendiente" in mensaje


def test_reportar_crea_la_incidencia_y_deja_el_envio_en_revision(plantilla):
    envio_id = _encolar(plantilla["id"])["envios"][0]["id"]
    pool.ejecutar(
        "UPDATE envios SET estado='error', error_tecnico='traza interna' WHERE id=%s", (envio_id,)
    )

    ok, _ = mensajeria.reportar(envio_id, "cliente_test")
    assert ok is True
    assert mensajeria.obtener_envio(envio_id)["estado"] == "en_revision"

    incidencia = mensajeria.listar_incidencias(solo_abiertas=True)[0]
    assert incidencia["reportado_por"] == "cliente_test"
    # El detalle técnico viaja a la incidencia para que el admin pueda arreglarlo.
    assert "traza interna" in str(incidencia["detalle"])


def test_el_detalle_tecnico_no_llega_a_la_pantalla_del_proyecto(plantilla):
    """Al cliente le sirve el mensaje accionable, no la traza. No se oculta en
    la plantilla: no se consulta."""
    resultado = _encolar(plantilla["id"])
    pool.ejecutar(
        "UPDATE envios SET error_tecnico='traza interna' WHERE id=%s",
        (resultado["envios"][0]["id"],),
    )

    fila = svc_envios.destinos_de(resultado["lote"]["id"])[0]

    assert "error_tecnico" not in fila


# --- Mensajes del negocio sembrados ------------------------------------------

def test_la_bienvenida_al_grupo_esta_en_el_panel():
    """El negocio tiene que poder editarla sin redeplegar.

    Antes vivía solo en `mensajes.json`, que es un archivo del repositorio:
    cambiar una palabra exigía tocar el código.
    """
    plantilla = mensajeria.buscar_por_clave("BIENVENIDA_GRUPO")

    assert plantilla, "la bienvenida al grupo no puede faltar"
    assert "curso teórico" in plantilla["partes"][0]["texto"]


def test_las_palabras_clave_ya_no_son_mensajes():
    """Se mudaron a su propia tabla (migración 016).

    Dejarlas aquí las mostraba en «Mensajes» como algo que se puede enviar a
    mano, cuando en realidad las dispara el cliente escribiéndolas.
    """
    claves = {p["clave"] for p in mensajeria.listar_plantillas()}

    assert not ({"TAREAS", "TRANSPORTE", "TAREAS_R1", "TAREAS_R2", "TAREAS_R3"} & claves)
