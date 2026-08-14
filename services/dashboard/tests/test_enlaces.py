"""Ningún enlace ni formulario de las plantillas apunta a una ruta que no existe.

Nace de un fallo real: al mover el conocimiento, los reportes y las preguntas al
panel del negocio (`/conocimiento`, `/reportes`, `/preguntas`), las plantillas
siguieron apuntando a las rutas viejas `/admin/*`. Los botones «Marcar
revisado», «Marcar atendida» y todo el formulario de la base de conocimiento
respondían 404 sin que nada fallara al arrancar: Jinja no sabe qué rutas existen
y FastAPI no sabe qué escriben las plantillas.

Esto cierra el hueco: recorre las plantillas, saca cada `action=`/`href=` que
apunte dentro de la aplicación y comprueba que alguna ruta lo atienda, con el
método que usa el formulario.
"""

import re
from pathlib import Path

from src.main import app

_PLANTILLAS = Path("src/templates")

# `<form ...>` y `<a ...>` enteros: hace falta la etiqueta completa, no solo la
# URL, porque el método (`method="post"`) vive en el mismo sitio que el destino.
_ETIQUETAS = re.compile(r"<(form|a)\b[^>]*>", re.IGNORECASE | re.DOTALL)
_ATRIBUTO = re.compile(r"\b(action|href|method)\s*=\s*\"([^\"]*)\"", re.IGNORECASE)

# Una expresión de Jinja dentro de la URL se sustituye por un valor cualquiera:
# lo que se comprueba es la FORMA de la ruta, no el id concreto.
_EXPRESION = re.compile(r"\{\{.*?\}\}")


def _urls_de(texto: str) -> list[tuple[str, str]]:
    """(método, ruta) de cada enlace o formulario interno de una plantilla."""
    encontradas = []
    for etiqueta in _ETIQUETAS.finditer(texto):
        atributos = {n.lower(): v for n, v in _ATRIBUTO.findall(etiqueta.group(0))}
        destino = atributos.get("action", atributos.get("href", ""))
        metodo = atributos.get("method", "GET").upper()

        destino = _EXPRESION.sub("1", destino).split("?")[0].split("#")[0]
        if not destino.startswith("/"):
            # Enlaces externos, anclas y `mailto:`: no son cosa nuestra.
            continue
        if destino.startswith("/static") or "{" in destino or "}" in destino:
            # `/static` lo sirve un Mount, y lo que sigue teniendo llaves es una
            # URL que se arma entera desde el contexto (`{{ r.link_whatsapp }}`).
            continue
        encontradas.append((metodo, destino))
    return encontradas


# El inventario de rutas se saca del esquema OpenAPI y no de `app.routes`: según
# la versión, FastAPI guarda los routers incluidos envueltos y sin resolver, así
# que recorrer `app.routes` deja fuera casi todo el panel. El esquema siempre
# tiene la lista completa, con su método.
def _rutas_declaradas() -> list[tuple[str, re.Pattern[str]]]:
    rutas = []
    for plantilla, operaciones in app.openapi()["paths"].items():
        # "/admin/negocios/{negocio_id}/config" -> ^/admin/negocios/[^/]+/config$
        partes = re.split(r"\{[^}]+\}", plantilla)
        patron = re.compile("^" + "[^/]+".join(re.escape(p) for p in partes) + "$")
        for metodo in operaciones:
            rutas.append((metodo.upper(), patron))
    return rutas


_RUTAS = _rutas_declaradas()


def _atendida(metodo: str, ruta: str) -> bool:
    return any(
        metodo == declarado and patron.match(ruta) for declarado, patron in _RUTAS
    )


def test_ningun_enlace_de_las_plantillas_apunta_a_una_ruta_inexistente():
    rotos = []
    for plantilla in sorted(_PLANTILLAS.glob("*.html")):
        for metodo, ruta in _urls_de(plantilla.read_text(encoding="utf-8")):
            if not _atendida(metodo, ruta):
                rotos.append(f"{plantilla.name}: {metodo} {ruta}")

    assert not rotos, "Enlaces a rutas que no existen:\n  " + "\n  ".join(rotos)
