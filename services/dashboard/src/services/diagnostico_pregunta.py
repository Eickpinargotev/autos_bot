"""Evidencias de la interacción que originó una pregunta sin respuesta."""

from datetime import datetime, timezone

from src.db import pool
from src.services.diagnostico_conversacion import sanitizar


def datos(proyecto: dict, pregunta_id: int) -> dict | None:
    pregunta = pool.consultar_uno(
        "SELECT * FROM preguntas_sin_respuesta WHERE proyecto_id = %s AND id = %s",
        (proyecto["id"], pregunta_id),
    )
    if not pregunta:
        return None
    guardado = pregunta.get("diagnostico") or {}
    eventos = {e["id"]: e for e in guardado.get("eventos", []) if "id" in e}
    # Completar con la salida enviada inmediatamente después de crear la
    # pregunta. Nunca seleccionar por número solamente ni exportar todo el chat.
    if pregunta["client_id"] and pregunta["canal"] and pregunta["correlation_id"]:
        for evento in pool.consultar(
            "SELECT * FROM conversation_messages WHERE proyecto_id = %s "
            "AND client_id = %s AND canal = %s AND correlation_id = %s "
            "AND created_at BETWEEN %s - INTERVAL '5 minutes' AND %s + INTERVAL '1 minute' "
            "ORDER BY id",
            (proyecto["id"], pregunta["client_id"], pregunta["canal"],
             pregunta["correlation_id"], pregunta["creado_en"], pregunta["creado_en"]),
        ):
            eventos.setdefault(evento["id"], dict(evento))
    limitaciones = [
        "Solo incluye la interacción asociada a esta pregunta; no exporta el historial completo.",
        "Los prompts pueden contener el contexto previo que recibió el modelo.",
        "No incluye razonamiento interno privado. Las trazas pueden contener recortes explícitos.",
        "Secretos y datos binarios se omiten.",
    ]
    if not guardado:
        limitaciones.append("Pregunta histórica sin copia de evidencias; solo se incluyen las trazas conservadas.")
    if not pregunta["client_id"] or not pregunta["correlation_id"]:
        limitaciones.append("No se pudo identificar inequívocamente la interacción original.")
    return sanitizar({
        "schema_version": "1.0",
        "scope": "unanswered_question_interaction",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": {"id": proyecto["id"], "name": proyecto["nombre"],
                    "timezone": proyecto.get("zona_horaria", "")},
        "question": {"id": pregunta["id"], "text": pregunta["pregunta"],
                     "created_at": pregunta["creado_en"],
                     "client_id": pregunta["client_id"], "channel": pregunta["canal"],
                     "correlation_id": pregunta["correlation_id"]},
        "client_query": guardado.get("consulta_cliente", ""),
        "knowledge_query": guardado.get("consulta_conocimiento", pregunta["pregunta"]),
        "planned_response": guardado.get("respuesta_prevista", ""),
        "planned_fallback": guardado.get("respuesta_prevista", ""),
        "events": [sanitizar(eventos[k]) for k in sorted(eventos)],
        "limitations": limitaciones,
    }, max_bytes=16 * 1024 * 1024)
