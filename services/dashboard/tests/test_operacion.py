"""Catálogo de ciudades y ciclo de vida de los envíos manuales."""

import pytest

from src.db import pool
from src.services import ciudades, mensajeria


# --- Ciudades ----------------------------------------------------------------

def test_una_ciudad_nueva_nace_inactiva():
    """Publicarla a medias haría que el bot mande mensajes incompletos."""
    fila = ciudades.crear("admin")
    assert fila["activo"] is False


def test_avisa_si_falta_el_enlace_del_grupo():
    """Sin ese enlace el flujo de publicidad se corta en silencio."""
    fila = ciudades.crear("admin")
    ciudades.actualizar_campo(fila["id"], "ciudad", "ALAJUELA", "admin")
    ciudades.actualizar_campo(fila["id"], "mensaje_1", "Curso en Alajuela", "admin")

    actual = ciudades.obtener(fila["id"])
    avisos = ciudades.avisos(actual)
    assert any("enlace del grupo" in a for a in avisos)

    ciudades.actualizar_campo(
        fila["id"], "mensaje_4", "Únase: https://chat.whatsapp.com/ABC", "admin"
    )
    assert ciudades.avisos(ciudades.obtener(fila["id"])) == []


def test_no_se_puede_escribir_en_una_columna_arbitraria():
    """El nombre de columna llega del formulario: sin lista blanca sería inyección."""
    fila = ciudades.crear("admin")
    with pytest.raises(ValueError):
        ciudades.actualizar_campo(fila["id"], "activo = TRUE; DROP TABLE envios; --", "x", "admin")


def test_guardar_registra_quien_y_cuando():
    fila = ciudades.crear("admin")
    actualizada = ciudades.actualizar_campo(fila["id"], "ciudad", "HEREDIA", "erick")
    assert actualizada["actualizado_por"] == "erick"
    assert actualizada["actualizado_en"] is not None


# --- Envíos ------------------------------------------------------------------

@pytest.fixture
def plantilla(monkeypatch):
    # No se comprueba el adjunto de verdad: eso se prueba aparte, en
    # test_media.py, y aquí solo interesa el ciclo del envío.
    monkeypatch.setattr(mensajeria.media, "verificar", lambda ref, tipo: (True, ""))
    creada = mensajeria.crear_plantilla("RECORDATORIO", "Recordatorio", "admin")
    mensajeria.guardar_parte(creada["id"], 1, "Hola", "imagen", "1abc")
    return creada


def test_encolar_copia_el_contenido_de_la_plantilla(plantilla):
    """Editar el mensaje después no debe cambiar lo que ya se encoló."""
    envio = mensajeria.encolar_envios(plantilla["id"], "telegram", ["123"], "admin")[0]
    mensajeria.guardar_parte(plantilla["id"], 1, "TEXTO NUEVO", "", "")

    actual = mensajeria.obtener_envio(envio["id"])
    assert actual["partes"][0]["texto"] == "Hola"
    assert actual["partes"][0]["media_ref"] == "1abc"


def test_encolar_copia_todas_las_partes_en_orden(plantilla):
    mensajeria.guardar_parte(plantilla["id"], 2, "Segunda", "", "")
    mensajeria.guardar_parte(plantilla["id"], 3, "Tercera", "", "")

    envio = mensajeria.encolar_envios(plantilla["id"], "telegram", ["123"], "admin")[0]

    assert [p["texto"] for p in envio["partes"]] == ["Hola", "Segunda", "Tercera"]


def test_no_se_encola_un_mensaje_con_un_adjunto_roto(plantilla, monkeypatch):
    """Encolar algo que se sabe roto solo produce un cliente con medio mensaje."""
    monkeypatch.setattr(
        mensajeria.media, "verificar", lambda ref, tipo: (False, "El archivo no es público.")
    )
    mensajeria.guardar_parte(plantilla["id"], 1, "Hola", "imagen", "1abc")

    with pytest.raises(ValueError, match="no es público"):
        mensajeria.encolar_envios(plantilla["id"], "telegram", ["123"], "admin")


def test_no_se_encola_un_mensaje_sin_partes(monkeypatch):
    vacia = mensajeria.crear_plantilla("VACIA", "Vacía", "admin")
    with pytest.raises(ValueError, match="ninguna parte"):
        mensajeria.encolar_envios(vacia["id"], "telegram", ["123"], "admin")


def test_encolar_admite_varios_destinos(plantilla):
    creados = mensajeria.encolar_envios(plantilla["id"], "whatsapp", ["1", "2", "3"], "admin")
    assert len(creados) == 3
    assert all(e["estado"] == "pendiente" for e in creados)


def test_no_se_encola_a_un_canal_desconocido(plantilla):
    with pytest.raises(ValueError):
        mensajeria.encolar_envios(plantilla["id"], "telegrama", ["123"], "admin")


def _fallar(envio_id: int, intentos: int = 1):
    pool.ejecutar(
        "UPDATE envios SET estado='error', intentos=%s, error_cliente='falló' WHERE id=%s",
        (intentos, envio_id),
    )


def test_el_reintento_se_corta_a_los_tres_intentos(plantilla):
    envio = mensajeria.encolar_envios(plantilla["id"], "telegram", ["123"], "admin")[0]

    _fallar(envio["id"], intentos=1)
    assert mensajeria.reintentar(envio["id"])[0] is True

    _fallar(envio["id"], intentos=mensajeria.MAX_INTENTOS)
    ok, mensaje = mensajeria.reintentar(envio["id"])
    assert ok is False
    assert "agotaron" in mensaje


def test_reintentar_limpia_el_error_anterior(plantilla):
    envio = mensajeria.encolar_envios(plantilla["id"], "telegram", ["123"], "admin")[0]
    _fallar(envio["id"])

    mensajeria.reintentar(envio["id"])
    actual = mensajeria.obtener_envio(envio["id"])
    assert actual["estado"] == "pendiente"
    assert actual["error_cliente"] == ""


def test_solo_se_reintenta_lo_que_falló(plantilla):
    envio = mensajeria.encolar_envios(plantilla["id"], "telegram", ["123"], "admin")[0]
    ok, mensaje = mensajeria.reintentar(envio["id"])
    assert ok is False
    assert "pendiente" in mensaje


def test_reportar_crea_la_incidencia_y_deja_el_envio_en_revision(plantilla):
    envio = mensajeria.encolar_envios(plantilla["id"], "telegram", ["123"], "admin")[0]
    pool.ejecutar(
        "UPDATE envios SET estado='error', error_tecnico='traza interna' WHERE id=%s", (envio["id"],)
    )

    ok, _ = mensajeria.reportar(envio["id"], "cliente_test")
    assert ok is True
    assert mensajeria.obtener_envio(envio["id"])["estado"] == "en_revision"

    incidencia = mensajeria.listar_incidencias(solo_abiertas=True)[0]
    assert incidencia["reportado_por"] == "cliente_test"
    # El detalle técnico viaja a la incidencia para que el admin pueda arreglarlo.
    assert "traza interna" in str(incidencia["detalle"])


def test_el_detalle_tecnico_solo_se_carga_para_el_admin(plantilla):
    """Al cliente le sirve el mensaje accionable, no la traza."""
    envio = mensajeria.encolar_envios(plantilla["id"], "telegram", ["123"], "admin")[0]
    pool.ejecutar("UPDATE envios SET error_tecnico='traza interna' WHERE id=%s", (envio["id"],))

    del_cliente = mensajeria.listar_envios(incluir_tecnico=False)[0]
    del_admin = mensajeria.listar_envios(incluir_tecnico=True)[0]

    assert "error_tecnico" not in del_cliente
    assert del_admin["error_tecnico"] == "traza interna"


# --- Mensajes del negocio sembrados ------------------------------------------

def test_los_mensajes_de_las_palabras_clave_estan_en_el_panel():
    """El negocio tiene que poder editar «tareas» y «transporte» sin redeplegar.

    Antes vivían solo en `mensajes.json`, que es un archivo del repositorio:
    cambiar una palabra exigía tocar el código. La migración los siembra para
    que estén ahí desde el primer arranque.
    """
    claves = {p["clave"] for p in mensajeria.listar_plantillas()}
    assert {"TAREAS", "TRANSPORTE", "BIENVENIDA_GRUPO"} <= claves


def test_los_mensajes_sembrados_traen_su_contenido():
    plantilla = mensajeria.buscar_por_clave("TAREAS")
    partes = mensajeria.partes_de(plantilla["id"])

    assert partes, "«tareas» no puede llegar vacío al panel"
    assert "curso teórico" in partes[0]["texto"]


def test_los_recordatorios_de_tareas_tambien_son_editables():
    claves = {p["clave"] for p in mensajeria.listar_plantillas()}
    assert {"TAREAS_R1", "TAREAS_R2", "TAREAS_R3"} <= claves
