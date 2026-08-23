"""La base de conocimiento: un chunk es un trozo de texto, y nada más.

Nació con dos campos, `titulo` y `contenido`, como si fuera un FAQ. No lo es: lo
que se guarda es un trozo que se vectoriza ENTERO y que el RAG recupera por
parecido semántico. El «tema» no era un índice —nadie buscaba por él—, era un
pedazo más del mismo texto; lo único que conseguía era obligar a inventarle un
titular a cada trozo y, con la carga inicial, dejar 36 filas donde el tema
repetía la respuesta entera (migración 014).

Aquí se prueba lo que queda: el límite de tamaño y que la edición sigue de punta
a punta, avisando al bot para que vuelva a vectorizar.
"""

import pytest

from src.db import pool
from src.services import trazabilidad
from tests.conftest import token_csrf


# --- Un chunk es solo texto ---------------------------------------------------

def test_un_chunk_se_guarda_y_se_lee_sin_tema():
    trazabilidad.crear_chunk(1, "El curso teórico cuesta 45.000 colones y dura 12 horas.")

    chunk = trazabilidad.listar_chunks(1)[0]

    assert chunk["contenido"].startswith("El curso teórico")
    assert "titulo" not in chunk


def test_no_se_guarda_un_chunk_vacio():
    """Un punto sin texto en Qdrant no responde a nada; solo estorba."""
    with pytest.raises(ValueError):
        trazabilidad.crear_chunk(1, "   \n  ")


def test_se_busca_dentro_del_contenido():
    trazabilidad.crear_chunk(1, "El dictamen médico es obligatorio para renovar.")
    trazabilidad.crear_chunk(1, "Las clases prácticas son de dos horas.")

    assert len(trazabilidad.listar_chunks(1, "dictamen")) == 1
    assert len(trazabilidad.listar_chunks(1, "")) == 2


# --- El límite de caracteres --------------------------------------------------------

def test_no_se_guarda_un_chunk_que_pasa_del_limite():
    """Un trozo largo mezcla asuntos y su vector deja de parecerse a ninguna
    pregunta concreta."""
    with pytest.raises(ValueError) as fallo:
        trazabilidad.crear_chunk(1, "x" * (trazabilidad.LIMITE_CHUNK + 1))

    assert str(trazabilidad.LIMITE_CHUNK) in str(fallo.value)


def test_justo_en_el_limite_si_se_guarda():
    trazabilidad.crear_chunk(1, "x" * trazabilidad.LIMITE_CHUNK)

    assert trazabilidad.listar_chunks(1)[0]["largo"] == trazabilidad.LIMITE_CHUNK


def test_tampoco_se_edita_para_pasarse_del_limite():
    chunk = trazabilidad.crear_chunk(1, "corto")

    with pytest.raises(ValueError):
        trazabilidad.actualizar_chunk(1, chunk["id"], "y" * (trazabilidad.LIMITE_CHUNK + 1))

    assert trazabilidad.listar_chunks(1)[0]["contenido"] == "corto"


def test_lo_que_ya_estaba_largo_se_marca_pero_no_se_recorta():
    """Cortar el conocimiento de un negocio sin preguntarle podría dejar fuera
    justo el precio o el requisito que importaba."""
    largo = "x" * (trazabilidad.LIMITE_CHUNK + 50)
    fila = pool.consultar_uno(
        "INSERT INTO rag_chunks (contenido) VALUES (%s) RETURNING id", (largo,)
    )

    chunk = trazabilidad.listar_chunks(1)[0]

    assert chunk["id"] == fila["id"]
    assert chunk["demasiado_largo"] is True
    assert len(chunk["contenido"]) == trazabilidad.LIMITE_CHUNK + 50


def test_el_panel_avisa_de_los_que_hay_que_partir(sesion_cliente):
    pool.ejecutar(
        "INSERT INTO rag_chunks (contenido) VALUES (%s)",
        ("x" * (trazabilidad.LIMITE_CHUNK + 1),),
    )

    cuerpo = sesion_cliente.get("/conocimiento").text

    assert f"pasan de {trazabilidad.LIMITE_CHUNK} caracteres" in cuerpo


def test_sin_chunks_largos_no_aparece_el_aviso(sesion_cliente):
    trazabilidad.crear_chunk(1, "Cuesta 45.000 colones.")

    cuerpo = sesion_cliente.get("/conocimiento").text

    assert "pasan de" not in cuerpo


# --- La ruta, entera ----------------------------------------------------------

def test_el_proyecto_crea_un_chunk_desde_su_panel(sesion_cliente):
    respuesta = sesion_cliente.post(
        "/conocimiento",
        data={"contenido": "Las clases prácticas son de dos horas.", "csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    assert "vectorizar" in respuesta.headers["location"]
    assert trazabilidad.listar_chunks(1)[0]["contenido"].startswith("Las clases")


def test_pasarse_del_limite_desde_el_panel_avisa_y_no_guarda(sesion_cliente):
    respuesta = sesion_cliente.post(
        "/conocimiento",
        data={
            "contenido": "x" * (trazabilidad.LIMITE_CHUNK + 1),
            "csrf": token_csrf(sesion_cliente),
        },
        follow_redirects=False,
    )

    assert "error=" in respuesta.headers["location"]
    assert trazabilidad.listar_chunks(1) == []


def test_la_banda_entera_abre_la_edicion(sesion_cliente):
    """El botón «Editar» sobraba: si al pulsar la banda no pasa nada, lo que hay
    que explicar es por qué no."""
    chunk = trazabilidad.crear_chunk(1, "Cuesta 45.000 colones.")

    cuerpo = sesion_cliente.get("/conocimiento").text

    assert f'data-abre="editar-{chunk["id"]}"' in cuerpo
    assert f'id="editar-{chunk["id"]}"' in cuerpo
    assert ">Editar<" not in cuerpo


def test_borrar_un_chunk_pide_confirmacion(sesion_cliente):
    chunk = trazabilidad.crear_chunk(1, "Cuesta 45.000 colones.")

    cuerpo = sesion_cliente.get("/conocimiento").text

    assert f'id="borrar-chunk-{chunk["id"]}"' in cuerpo
    assert "¿Estás seguro de que quieres eliminar este chunk de información?" in cuerpo


def test_desactivar_un_chunk_no_lo_borra(sesion_cliente):
    chunk = trazabilidad.crear_chunk(1, "Cuesta 45.000 colones.")

    sesion_cliente.post(
        f"/conocimiento/{chunk['id']}/activo",
        data={"csrf": token_csrf(sesion_cliente)},
        follow_redirects=False,
    )

    quedan = trazabilidad.listar_chunks(1)
    assert len(quedan) == 1
    assert quedan[0]["activo"] is False
