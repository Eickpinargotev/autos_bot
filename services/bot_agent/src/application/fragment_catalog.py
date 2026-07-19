"""Catálogo de fragmentos literales del negocio.

Los textos curados (precios, formularios, guiones con su estilo y emojis)
viven en `mensajes.json` y se envían al cliente SIN reescribir. El agente
único no redacta esa información: la referencia con etiquetas
`[[frag:FLUJO.NODO]]` y este módulo las expande al texto literal.

`mensajes.json` sigue siendo la única fuente de verdad: editar un texto ahí
cambia lo que recibe el cliente sin tocar código ni prompts.
"""

from dataclasses import dataclass, field

from src.application.message_catalog import mensajes_db
from src.domain.entities import Channel


# Categorías que NO son fragmentos del agente conversacional: las maneja el
# orquestador por fuera (keywords, publicidad, bienvenida a grupos).
_EXCLUDED_CATEGORIES = {"KEYWORD", "PUBLICIDAD", "WELCOME"}

# Variantes por registro de keyword ("tareas"/"transporte"): si el cliente ya
# está registrado, ciertos fragmentos usan otro sinpe/formulario. El agente ve
# UN solo id; la variante la resuelve el código al expandir, igual que hacía
# el router determinista.
_KEYWORD_VARIANTS = {
    "DICTAMEN.D1": "DICTAMEN.D1_1",
    "GENERAL.G16": "GENERAL.G16_1",
    "GENERAL.G28": "GENERAL.G28_1",
}
_VARIANT_IDS = set(_KEYWORD_VARIANTS.values())

# Partición del catálogo por agente (arquitectura supervisor/workers): cada
# agente solo VE y solo PUEDE ENVIAR los fragmentos de su área. El pipeline
# rechaza etiquetas fuera del set del agente activo (guardrail determinista
# contra alucinación cruzada). Las variantes _1 se resuelven por código, así
# que no se listan.
AREA_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "SUPERVISOR": ("QUEJA.Q1", "WIN.W1"),
    "GENERAL": ("GENERAL.G1", "GENERAL.G3", "GENERAL.G7"),
    "CURSO_TEORICO": ("GENERAL.G4",),
    "ALQUILER": (
        "Alquiler.A1", "GENERAL.G7", "GENERAL.G35", "GENERAL.G11",
        "GENERAL.G13", "GENERAL.G16", "GENERAL.G19", "GENERAL.G20",
        "GENERAL.G21", "GENERAL.G22", "GENERAL.G25", "GENERAL.G28",
        "GENERAL.G29", "GENERAL.G30", "GENERAL.G31", "GENERAL.G32",
    ),
    "CLASES": ("CLASES.C1", "CLASES.C2", "CLASES.C5"),
    "DICTAMEN": ("DICTAMEN.D1",),
    # TRAMITES informa 100 % vía RAG: no tiene textos curados propios.
    "TRAMITES": (),
}

SPECIALIST_AREAS = ("GENERAL", "CURSO_TEORICO", "ALQUILER", "CLASES", "DICTAMEN", "TRAMITES")


def allowed_fragments(role: str) -> set[str]:
    """Fragmentos que el agente `role` (SUPERVISOR o área) puede enviar."""
    return set(AREA_FRAGMENTS.get(role, ()))


@dataclass
class Fragment:
    fragment_id: str
    messages: list[str] = field(default_factory=list)
    report: str = ""
    retake: str = ""


def _build_fragments() -> dict[str, Fragment]:
    fragments: dict[str, Fragment] = {}
    for category, nodes in mensajes_db.items():
        if category in _EXCLUDED_CATEGORIES:
            continue
        for node, data in nodes.items():
            fragment_id = f"{category}.{node}"
            fragments[fragment_id] = Fragment(
                fragment_id=fragment_id,
                messages=list(data.get("mensajes", [])),
                report=str(data.get("reporte") or "").strip(),
                retake=str(data.get("retomar") or "").strip(),
            )
    return fragments


_fragments = _build_fragments()


def get_fragment(fragment_id: str) -> Fragment | None:
    return _fragments.get(fragment_id)


def resolve_variant(fragment_id: str, user_id: str, channel: Channel | str) -> str:
    """Cambia al fragmento variante cuando el cliente está en el registro de keywords."""
    variant = _KEYWORD_VARIANTS.get(fragment_id)
    if not variant or variant not in _fragments:
        return fragment_id
    from src.infrastructure.repositories.keyword_registry_repository import KeywordRegistryRepository

    try:
        if KeywordRegistryRepository.exists(user_id, channel):
            return variant
    except Exception:
        return fragment_id
    return fragment_id


def visible_fragment_ids() -> list[str]:
    """Ids que el agente puede usar (las variantes _1 quedan ocultas)."""
    return [fid for fid in _fragments if fid not in _VARIANT_IDS]


def catalog_for_prompt(fragment_ids: list[str] | tuple[str, ...] | None = None) -> str:
    """Render del catálogo para el mensaje system de un agente.

    Incluye el texto LITERAL de cada fragmento para que el modelo sepa
    exactamente qué recibe el cliente (y no lo contradiga ni lo duplique).
    Si se pasa `fragment_ids`, solo se renderiza esa partición (el catálogo
    del área). Es contenido estable entre llamadas, así que cachea bien.
    """
    ids = list(fragment_ids) if fragment_ids is not None else visible_fragment_ids()
    blocks: list[str] = []
    for fid in ids:
        frag = _fragments.get(fid)
        if frag is None:
            continue
        if not frag.messages:
            continue
        lines = [f"### [[frag:{fid}]]"]
        if frag.report:
            lines.append(
                "(Al enviar este fragmento, la respuesta siguiente del cliente queda pendiente de revisión del equipo humano.)"
            )
        for i, msg in enumerate(frag.messages, start=1):
            lines.append(f"— mensaje {i} —\n{msg}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
