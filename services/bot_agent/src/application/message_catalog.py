import json
import os


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
MENSAJES_PATHS = [
    "/mensajes.json",
    os.path.join(BASE_DIR, "mensajes.json"),
    os.path.join(PROJECT_ROOT, "mensajes.json"),
]


def load_mensajes():
    for path in MENSAJES_PATHS:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            continue
    return {}


mensajes_db = load_mensajes()


def get_node_data(category: str, node: str) -> dict:
    return mensajes_db.get(category, {}).get(node, {})


def get_messages_for_node(category: str, node: str) -> list[str]:
    return get_node_data(category, node).get("mensajes", [])


# Qué mensaje del panel («clave» de `plantillas_mensaje`) sustituye a cada nodo
# del archivo. Los textos del agente conversacional NO entran aquí: son
# fragmentos que el prompt referencia por id y cambiarlos desde el panel rompería
# el contrato con el modelo.
#
# Los nodos KEYWORD (T1, H1, T2, T3, T4) estaban aquí y se fueron: las palabras
# clave dejaron de ser textos de este archivo y son filas de `palabras_clave`,
# con su propia tabla, sus recordatorios y sus minutos (migración 016). Las lee
# `palabras_clave_repository`, no esto.
CLAVES_EDITABLES = {
    ("WELCOME", "W"): "BIENVENIDA_GRUPO",
}


def mensajes_del_negocio(category: str, node: str) -> list[str]:
    """Los mensajes de ese nodo, dando prioridad a lo editado en el panel.

    Si el negocio lo editó, mandan sus textos. Si no hay nada en la base (base
    recién creada, plantilla borrada), se cae a `mensajes.json`: el bot no puede
    quedarse mudo delante de un cliente por una fila que falta.
    """
    clave = CLAVES_EDITABLES.get((category, node))
    if clave:
        from src.infrastructure.repositories import plantillas_repository

        desde_panel = plantillas_repository.textos_de(clave)
        if desde_panel:
            return desde_panel
    return get_messages_for_node(category, node)
