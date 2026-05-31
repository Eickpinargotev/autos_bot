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
