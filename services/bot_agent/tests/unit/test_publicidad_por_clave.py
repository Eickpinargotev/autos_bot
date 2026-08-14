"""La publicidad reconoce el MENSAJE del panel, por su clave.

Antes había una tabla aparte (`invitaciones_ciudades`) con los mismos textos y
las mismas claves. Se eliminó: un mensaje se identifica por su clave, y esa
clave es lo que se reconoce cuando alguien llega por un anuncio preguntando por
su ciudad.

Reconocer una clave anunciada no es interpretar lenguaje natural (§5 de
CLAUDE.md): es lo mismo que hace una palabra clave. Lo que se prueba aquí es que
esa comparación no se equivoque de mensaje.
"""

import pytest

from src.application.publicidad_service import PublicidadService
from src.infrastructure.repositories import plantillas_repository


@pytest.fixture
def catalogo(monkeypatch):
    """Sustituye el catálogo de claves del panel."""

    def poner(*claves: str):
        ordenadas = sorted((c.upper() for c in claves), key=len, reverse=True)
        monkeypatch.setattr(plantillas_repository, "claves", lambda: ordenadas)

    return poner


def test_encuentra_la_clave_dentro_de_lo_que_escribio_el_cliente(catalogo):
    catalogo("ALAJUELA", "HEREDIA")

    assert PublicidadService._buscar_clave("quiero el curso en alajuela") == "ALAJUELA"


def test_las_tildes_y_las_mayusculas_dan_igual(catalogo):
    catalogo("CAÑAS", "JACÓ")

    assert PublicidadService._buscar_clave("JACO") == "JACÓ"
    assert PublicidadService._buscar_clave("canas") == "CAÑAS"


def test_gana_la_clave_mas_larga_y_no_el_trozo(catalogo):
    """Con «LIBERIA» y «LICENCIA EN LIBERIA», quien pide la segunda no puede
    recibir la primera: se prueban de la más larga a la más corta."""
    catalogo("LIBERIA", "LICENCIA EN LIBERIA")

    assert PublicidadService._buscar_clave("licencia en liberia") == "LICENCIA EN LIBERIA"
    assert PublicidadService._buscar_clave("liberia") == "LIBERIA"


def test_aguanta_un_error_de_tipeo(catalogo):
    catalogo("ALAJUELA")

    assert PublicidadService._buscar_clave("alajuel") == "ALAJUELA"


def test_una_clave_corta_no_se_traga_cualquier_texto(catalogo):
    """Una clave de tres letras aparecería dentro de casi todo y se llevaría por
    delante a la que de verdad se pidió."""
    catalogo("PAZ", "PUNTARENAS")

    assert PublicidadService._buscar_clave("curso de manejo en puntarenas") == "PUNTARENAS"
    assert PublicidadService._buscar_clave("capaz me apunto") == ""


def test_si_no_se_parece_a_ninguna_no_se_inventa_una(catalogo):
    """El que no encuentre nada es lo que dispara el reporte al asesor."""
    catalogo("ALAJUELA", "HEREDIA")

    assert PublicidadService._buscar_clave("Marruecos") == ""
    assert PublicidadService._buscar_clave("") == ""


# --- El enlace del grupo ------------------------------------------------------

def test_el_enlace_del_grupo_se_busca_en_toda_la_cadena():
    """Estaba atado al cuarto mensaje: agregar uno en medio rompía el flujo."""
    cadena = ["Hola", "Le cuento", "Únase aquí: https://chat.whatsapp.com/ABC123", "Gracias"]

    assert PublicidadService._enlace_de_grupo(cadena) == "https://chat.whatsapp.com/ABC123"


def test_una_cadena_sin_enlace_se_queda_sin_enlace():
    """Es lo que hace que se reporte y se bloquee en vez de agendar a ciegas."""
    assert PublicidadService._enlace_de_grupo(["Hola", "Nos vemos"]) == ""
