"""Mapa de navegación del panel: secciones, páginas y a quién le tocan.

Vive aquí y no en la plantilla porque es la única lista que sabe qué páginas
existen y quién puede verlas: el sidebar, la miga de pan y el título de cada
página se derivan de ella. Añadir una página es añadir una línea.

## El vocabulario: plataforma, proyecto, cliente

Tres cosas distintas que antes se llamaban todas «cliente»:

* **Base de Control** es la PLATAFORMA. Es la marca del panel y no cambia.
* Un **proyecto** es cada uno de nuestros clientes (hoy «Escuela de Manejo»):
  su número de WhatsApp, su bot y su conocimiento. Un proyecto tiene
  **un único usuario asignado** — no varios: quien lo administra es una persona,
  con su nombre, y esa cuenta es su llave.
* Un **cliente** es la persona que le escribe al bot. Solo aparece dentro del
  panel de un proyecto.

## Los dos menús no son el mismo con cosas ocultas

Son dos trabajos distintos:

* **Administrador** (nosotros). Ve la lista de **proyectos**. Su trabajo es
  controlar el costo real, ver qué falló y entrar a su cuenta mediante suplantación auditada
  cuando necesita soporte. No abre conversaciones ni bloqueos directamente.
* **Proyecto** (nuestro cliente). Administra su catálogo, su base de
  conocimiento, mensajes, conversaciones y bloqueos permanentes. No ve datos de
  consumo: el servicio se cobra mediante un valor fijo.

## Lo de la cuenta no se repite en el lateral, ni es una página

El menú de la cuenta (abajo del lateral, con el avatar) lleva a lo que es «del
sistema y de quien lo usa»: las cuentas de acceso y los ajustes. Tenerlas ADEMÁS
como una sección del lateral era la misma lista dos veces en la misma pantalla.

«Mi cuenta» ya no está en esta lista porque **dejó de ser una página**: ver con
qué cuenta estás dentro y cambiar la contraseña son cosas que se hacen sin salir
de donde estabas, así que viven en una ventana flotante declarada en
`templates/base.html`. Como página era un panel de «sesiones abiertas» y un rol
que decía «cliente», que no le servían a nadie.

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
            # La URL sigue siendo `/admin/negocios`: cambiar la etiqueta no
            # obliga a romper los enlaces que ya existen ni el histórico.
            {"etiqueta": "Proyectos", "url": "/admin/negocios", "icono": "clientes", "prefijo": True},
            # Conversaciones y bloqueos solo existen dentro de una sesión de
            # proyecto; soporte entra mediante la suplantación auditada.
            {"etiqueta": "Incidencias", "url": "/admin/incidencias", "icono": "alerta"},
        ],
    },
    {
        "titulo": "Costos internos",
        "paginas": [
            {"etiqueta": "Consumo real", "url": "/admin/costos", "icono": "panel"},
        ],
    },
    # La sección «Sistema» (cuentas, ajustes, mi cuenta) NO va aquí: vive en el
    # menú de la cuenta, al pie del lateral. Estaba en los dos sitios a la vez.
]

# Páginas que existen y se alcanzan desde el menú de la cuenta, no desde el
# lateral. Están declaradas igual porque la miga de pan y el título salen de
# esta misma lista: sin ellas, entrar a «Cuentas de acceso» dejaba la cabecera
# sin decir dónde estabas.
SECCIONES_DE_CUENTA: list[dict[str, Any]] = [
    {
        "titulo": "Sistema",
        "paginas": [
            {"etiqueta": "Cuentas de acceso", "url": "/admin/usuarios", "icono": "usuario"},
            {"etiqueta": "Ajustes del sistema", "url": "/admin/configuracion", "icono": "ajustes"},
        ],
    },
    {
        "titulo": "Cuenta",
        "paginas": [
            # Solo queda `/password`, que es la pantalla del cambio OBLIGATORIO
            # del primer ingreso (a la que redirige `/` cuando la contraseña es
            # provisional). El cambio voluntario ya no es una página: está en la
            # ventana de «Mi cuenta».
            {"etiqueta": "Mi cuenta", "url": "/password", "icono": "llave"},
        ],
    },
]

SECCIONES_NEGOCIO: list[dict[str, Any]] = [
    {
        "titulo": "Mi proyecto",
        "paginas": [
            {"etiqueta": "Conversaciones", "url": "/conversaciones", "icono": "chat", "prefijo": True},
            {"etiqueta": "Reportes", "url": "/reportes", "icono": "documento"},
        ],
    },
    {
        "titulo": "Agente IA",
        "paginas": [
            {"etiqueta": "Prompts", "url": "/agente/instrucciones", "icono": "ajustes"},
            {"etiqueta": "Fragmentos", "url": "/agente/fragmentos", "icono": "etiqueta"},
            {"etiqueta": "Conocimiento", "url": "/conocimiento", "icono": "libro"},
            {"etiqueta": "Preguntas", "url": "/preguntas", "icono": "pregunta"},
        ],
    },
    {
        "titulo": "Mensajería",
        "paginas": [
            # Aquí hubo una entrada «Ciudades» (oculta, como segunda pestaña de
            # Mensajes). Ya no existe: era un segundo catálogo con los mismos
            # textos y las mismas claves que «Mensajes».
            {"etiqueta": "Mensajes", "url": "/mensajes", "icono": "chat"},
            # «Palabras clave» SÍ es una entrada propia: no es otro catálogo de
            # textos, es otra cosa. Un mensaje lo mandas tú a quien elijas; una
            # palabra clave la dispara el cliente escribiéndola y arrastra el
            # bloqueo de la conversación y unos recordatorios a futuro.
            # Mezclarlas en la misma pantalla no dejaba ver cuál de las dos
            # cosas estabas tocando.
            {"etiqueta": "Palabras clave", "url": "/palabras-clave", "icono": "etiqueta"},
            {"etiqueta": "Registros", "url": "/registros", "icono": "clientes"},
            {"etiqueta": "Enviar", "url": "/enviar", "icono": "enviar"},
            {"etiqueta": "Envíos", "url": "/envios", "icono": "bandeja"},
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


def migas(es_admin: bool, ruta: str) -> list[dict[str, str]]:
    """Los tramos de la miga de pan, cada uno con su enlace.

    La miga era texto muerto: decía «Operación › Clientes» y no se podía volver
    a Operación ni a Clientes desde ahí. Ahora cada tramo lleva a algún sitio:

    * La **sección** no tiene página propia (es un título del menú), así que
      lleva a su primera página visible. Es lo que uno espera al pulsarla:
      «llévame a esta parte del panel».
    * La **página** lleva a sí misma, que es lo útil cuando estás en un detalle:
      desde el perfil de un cliente, «Clientes» te devuelve al listado.

    Las páginas del menú de la cuenta también se resuelven aquí; si no, entrar a
    «Cuentas de acceso» dejaba la cabecera muda.
    """
    propias = SECCIONES_ADMIN if es_admin else SECCIONES_NEGOCIO
    for seccion in [*propias, *SECCIONES_DE_CUENTA]:
        for pagina in seccion["paginas"]:
            if not _activa(pagina, ruta):
                continue
            primera = next((p for p in seccion["paginas"] if not p.get("oculta")), pagina)
            return [
                {"etiqueta": seccion["titulo"], "url": primera["url"]},
                {"etiqueta": pagina["etiqueta"], "url": pagina["url"]},
            ]
    return []
