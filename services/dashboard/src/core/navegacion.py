"""Mapa de navegación del panel: secciones, páginas y a quién le tocan.

Vive aquí y no en la plantilla porque es la única lista que sabe qué páginas
existen y quién puede verlas: el sidebar, la miga de pan y el título de cada
página se derivan de ella. Añadir una página es añadir una línea.

## Los dos menús no son el mismo con cosas ocultas

Son dos trabajos distintos, y la palabra «Clientes» significa algo distinto en
cada uno:

* **Administrador** (nosotros). Sus clientes son los **negocios** a los que les
  prestamos el servicio. Su trabajo es facturarles, ver qué falló y entrar a su
  perfil cuando reclaman algo. Por eso ve incidencias técnicas y conversaciones
  —para resolver problemas—, y NO ve el conocimiento, las preguntas ni los
  reportes: ese es el trabajo del negocio, no el nuestro.
* **Negocio** (nuestro cliente). Sus clientes son las **personas que le escriben
  al bot**. Administra su catálogo, su base de conocimiento y sus mensajes.
  **No ve las conversaciones**: son de sus clientes, y para lo que él necesita ya
  están los reportes y las preguntas sin responder.

OJO: esto solo decide qué se *muestra*. Quién puede *entrar* lo deciden las
dependencias de la ruta (`requiere_admin` / `requiere_negocio`); ocultar un
enlace nunca es una medida de seguridad.
"""

from typing import Any

# Cada página: etiqueta, url, icono (ver templates/_iconos.html) y, si su ruta
# tiene subpáginas, `prefijo=True` para que el enlace siga marcado como activo
# dentro de ellas.

SECCIONES_ADMIN: list[dict[str, Any]] = [
    {
        "titulo": "Operación",
        "paginas": [
            {"etiqueta": "Clientes", "url": "/admin/negocios", "icono": "clientes", "prefijo": True},
            {"etiqueta": "Conversaciones", "url": "/admin/logs", "icono": "chat", "prefijo": True},
            {"etiqueta": "Incidencias", "url": "/admin/incidencias", "icono": "alerta"},
        ],
    },
    {
        "titulo": "Facturación",
        "paginas": [
            {"etiqueta": "Costos", "url": "/admin/costos", "icono": "panel"},
            {"etiqueta": "Periodos", "url": "/admin/periodos", "icono": "calendario"},
            {"etiqueta": "Tarifas", "url": "/admin/tarifas", "icono": "etiqueta"},
        ],
    },
    {
        "titulo": "Sistema",
        "paginas": [
            {"etiqueta": "Cuentas de acceso", "url": "/admin/usuarios", "icono": "usuario"},
            {"etiqueta": "Ajustes del sistema", "url": "/admin/configuracion", "icono": "ajustes"},
            {"etiqueta": "Mi cuenta", "url": "/password", "icono": "llave"},
        ],
    },
]

SECCIONES_NEGOCIO: list[dict[str, Any]] = [
    {
        "titulo": "Mi negocio",
        "paginas": [
            {"etiqueta": "Clientes", "url": "/clientes", "icono": "clientes"},
            {"etiqueta": "Reportes", "url": "/reportes", "icono": "documento"},
            {"etiqueta": "Mi consumo", "url": "/factura", "icono": "dinero"},
        ],
    },
    {
        "titulo": "Agente IA",
        "paginas": [
            {"etiqueta": "Conocimiento", "url": "/conocimiento", "icono": "libro"},
            {"etiqueta": "Preguntas", "url": "/preguntas", "icono": "pregunta"},
        ],
    },
    {
        "titulo": "Mensajería",
        "paginas": [
            {"etiqueta": "Mensajes", "url": "/mensajes", "icono": "chat"},
            {"etiqueta": "Ciudades", "url": "/ciudades", "icono": "mapa"},
            {"etiqueta": "Enviar", "url": "/enviar", "icono": "enviar"},
            {"etiqueta": "Envíos", "url": "/envios", "icono": "bandeja"},
        ],
    },
    {
        "titulo": "Cuenta",
        "paginas": [
            {"etiqueta": "Mi cuenta", "url": "/password", "icono": "llave"},
        ],
    },
]


def _activa(pagina: dict[str, Any], ruta: str) -> bool:
    if pagina.get("prefijo"):
        return ruta.startswith(pagina["url"])
    return ruta == pagina["url"]


def secciones_para(es_admin: bool, ruta: str) -> list[dict[str, Any]]:
    """El menú del rol, ya marcada la página activa."""
    origen = SECCIONES_ADMIN if es_admin else SECCIONES_NEGOCIO
    return [
        {
            "titulo": seccion["titulo"],
            "paginas": [{**pagina, "activa": _activa(pagina, ruta)} for pagina in seccion["paginas"]],
        }
        for seccion in origen
    ]


def ubicacion(secciones: list[dict[str, Any]]) -> tuple[str, str]:
    """(sección, página) de la ruta actual, para la miga de pan del encabezado."""
    for seccion in secciones:
        for pagina in seccion["paginas"]:
            if pagina["activa"]:
                return seccion["titulo"], pagina["etiqueta"]
    return "", ""
