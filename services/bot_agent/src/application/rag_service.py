import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from src.application import seguimiento_service
from src.core.config import settings
from src.domain.entities import Channel
from src.infrastructure.logging.tool_call_logger import ToolCallLogger
from src.infrastructure.repositories.postgres_conn import consultar, consultar_uno


# Structured Outputs (json_schema estricto): OpenAI garantiza la forma exacta
# de la salida. La validación semántica (respuesta vacía = sin respaldo) sigue
# en código.
ANSWER_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "rag_answer",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "has_answer": {"type": "boolean"},
                "answer": {"type": "string"},
            },
            "required": ["has_answer", "answer"],
        },
    },
}


@dataclass
class RagAnswer:
    has_answer: bool
    answer: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RagChunk:
    point_id: str
    external_id: str
    record_id: str
    table_id: str
    text: str
    payload: dict[str, Any]


class RagService:
    collection_name = "escuela_manejo_kb"
    # Identifica el origen de los puntos en Qdrant. Cambió al migrar de NocoDB
    # a Postgres; los puntos viejos se limpian solos en la primera sincronización
    # completa (delete_stale_records borra todo lo que no esté en la base).
    chunks_table_id = "rag_chunks"
    vector_size = 1536
    score_threshold = 0.25
    source_text_limit = 700

    def __init__(self):
        self.qdrant_url = settings.QDRANT_URL
        self.openai = (
            OpenAI(
                api_key=settings.OPENAI_API_KEY,
                timeout=settings.OPENAI_TIMEOUT_SECONDS,
                max_retries=settings.OPENAI_MAX_RETRIES,
            )
            if settings.OPENAI_API_KEY
            else None
        )
        self.client = None
        self._last_sync = 0.0
        try:
            from qdrant_client import QdrantClient

            self.client = QdrantClient(url=self.qdrant_url)
        except Exception as e:
            print(f"Error conectando Qdrant para RAG: {e}")
            self.client = None

    def answer_question(
        self,
        question: str,
        context: str = "",
        last_question: str = "",
        conversation_history: list[dict] | None = None,
        client_id: str = "",
        canal: Channel | str = "",
    ) -> RagAnswer:
        if not self.client or not self.openai:
            return RagAnswer(has_answer=False)

        if client_id and canal:
            ToolCallLogger.record(
                client_id=client_id,
                canal=canal,
                tool_name="rag.sync_if_needed",
                input_data={"collection": self.collection_name},
                output_mapper=lambda result: {"completed": True},
                text_mapper=lambda result: "Sincronización lazy RAG revisada",
                call=self.sync_if_needed,
            )
            chunks = ToolCallLogger.record(
                client_id=client_id,
                canal=canal,
                tool_name="rag.search",
                input_data={"question": question, "limit": 3, "score_threshold": self.score_threshold},
                output_mapper=self._search_log_output,
                text_mapper=lambda result: f"RAG encontró chunks: {len(result)}",
                call=lambda: self.search(question),
            )
        else:
            self.sync_if_needed()
            chunks = self.search(question)
        if not chunks:
            return RagAnswer(has_answer=False)

        prompt = self._answer_prompt(question, context, last_question, conversation_history or [], chunks)
        try:
            started = time.monotonic()
            completion = self.openai.chat.completions.create(
                model=settings.OPENAI_MODEL,
                response_format=ANSWER_RESPONSE_FORMAT,
                messages=[
                    {"role": "system", "content": "Devuelve JSON estricto. Responde solo con información respaldada por el contexto."},
                    {"role": "user", "content": prompt},
                ],
            )
            seguimiento_service.registrar_uso_llm(
                client_id, canal, getattr(completion, "usage", None), origen="rag"
            )
            data = json.loads(completion.choices[0].message.content or "{}")
            has_answer = bool(data.get("has_answer"))
            answer = str(data.get("answer") or "").strip()
            if not has_answer or not answer:
                self._log_answer_generation(
                    client_id,
                    canal,
                    question,
                    context,
                    chunks,
                    {"has_answer": False},
                    started,
                )
                return RagAnswer(has_answer=False)
            result = RagAnswer(
                has_answer=True,
                answer=answer,
                sources=self._source_summaries(chunks),
            )
            self._log_answer_generation(
                client_id,
                canal,
                question,
                context,
                chunks,
                result,
                started,
            )
            return result
        except Exception as e:
            if client_id and canal:
                ToolCallLogger.error(
                    client_id=client_id,
                    canal=canal,
                    tool_name="rag.generate_answer",
                    input_data={
                        "question": question,
                        "context": context,
                        "chunk_count": len(chunks),
                    },
                    error=e,
                )
            print(f"Error generando respuesta RAG: {e}")
            return RagAnswer(has_answer=False)

    def sync_if_needed(self):
        if time.time() - self._last_sync < settings.RAG_SYNC_TTL_SECONDS:
            return
        try:
            self.ensure_collection()
            count = self.client.count(collection_name=self.collection_name, exact=True).count
            if count == 0 or time.time() - self._last_sync >= settings.RAG_SYNC_TTL_SECONDS:
                self.sync_all_chunks()
                self._last_sync = time.time()
        except Exception as e:
            print(f"Error sincronizando RAG lazy: {e}")

    def sync_all_chunks(self) -> dict[str, int]:
        records = self.fetch_chunk_records()
        current_ids = {self.record_id(record.get("fields", record)) for record in records if isinstance(record, dict)}
        current_ids.discard("")
        upserted = self.upsert_records(records, self.chunks_table_id)
        deleted = self.delete_stale_records(self.chunks_table_id, current_ids)
        return {"upserted": upserted, "deleted": deleted}

    def sync_chunk_id(self, chunk_id: int) -> dict[str, int]:
        """Re-sincroniza un chunk concreto tras editarlo en el dashboard.

        Si el chunk ya no existe o quedó inactivo, se borra su punto de Qdrant.
        Es la sustitución del antiguo webhook de NocoDB: ahora el disparo viene
        del propio dashboard, que es quien edita la base de conocimiento.
        """
        registro = consultar_uno(
            "SELECT id, titulo, contenido FROM rag_chunks WHERE id = %s AND activo",
            (int(chunk_id),),
        )
        if not registro:
            borrados = 1 if self.delete_record({"id": chunk_id}, self.chunks_table_id) else 0
            return {"upserted": 0, "deleted": borrados, "ignored": 0}

        return {"upserted": self.upsert_records([registro], self.chunks_table_id), "deleted": 0, "ignored": 0}

    def fetch_chunk_records(self) -> list[dict[str, Any]]:
        """Lee la base de conocimiento desde Postgres.

        Solo entran los chunks activos: desactivar uno en el dashboard lo saca
        del RAG sin borrarlo, y la limpieza de puntos obsoletos se encarga de
        quitarlo de Qdrant en la siguiente sincronización.
        """
        try:
            return consultar(
                "SELECT id, titulo, contenido FROM rag_chunks WHERE activo ORDER BY id"
            )
        except Exception as e:
            print(f"Error leyendo chunks del RAG en Postgres: {e}")
            return []

    def upsert_records(self, records: list[dict[str, Any]], table_id: str) -> int:
        self.ensure_collection()
        chunks = [self.chunk_from_record(record, table_id) for record in records]
        chunks = [chunk for chunk in chunks if chunk and chunk.text.strip()]
        if not chunks:
            return 0

        vectors = self.embed([chunk.text for chunk in chunks])
        from qdrant_client.models import PointStruct

        points = [
            PointStruct(
                id=chunk.point_id,
                vector=vector,
                payload={
                    **chunk.payload,
                    "external_id": chunk.external_id,
                    "record_id": chunk.record_id,
                    "table_id": chunk.table_id,
                    "text": chunk.text,
                },
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        self.client.upsert(collection_name=self.collection_name, points=points)
        self._last_sync = time.time()
        return len(points)

    def delete_record(self, record: dict[str, Any], table_id: str) -> bool:
        record_id = self.record_id(record)
        if not record_id:
            return False
        try:
            self.ensure_collection()
            self.delete_point_ids([self.point_id(table_id, record_id)])
            self._last_sync = time.time()
            return True
        except Exception as e:
            print(f"Error eliminando chunk RAG de Qdrant: {e}")
            return False

    def delete_stale_records(self, table_id: str, current_record_ids: set[str]) -> int:
        """Borra de Qdrant todo punto que ya no exista (o no esté activo) en la base.

        Recorre la colección ENTERA, sin filtrar por `table_id`. Así una misma
        pasada limpia tanto los chunks borrados en el dashboard como los puntos
        que quedaron del origen anterior (NocoDB), que tenían otro `table_id` y
        de otro modo permanecerían para siempre contaminando las búsquedas.
        """
        try:
            deleted = 0
            offset = None
            while True:
                points, offset = self.client.scroll(
                    collection_name=self.collection_name,
                    limit=1000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                stale_point_ids = [
                    point.id
                    for point in points
                    if str((point.payload or {}).get("record_id") or "") not in current_record_ids
                    or str((point.payload or {}).get("table_id") or "") != table_id
                ]
                if stale_point_ids:
                    self.delete_point_ids(stale_point_ids)
                    deleted += len(stale_point_ids)
                if offset is None:
                    break
            return deleted
        except Exception as e:
            print(f"Error limpiando chunks RAG obsoletos: {e}")
            return 0

    def delete_point_ids(self, point_ids: list[str]):
        from qdrant_client.models import PointIdsList

        self.client.delete(
            collection_name=self.collection_name,
            points_selector=PointIdsList(points=point_ids),
        )

    def search(self, question: str) -> list[dict[str, Any]]:
        try:
            vector = self.embed([question])[0]
            try:
                result = self.client.query_points(
                    collection_name=self.collection_name,
                    query=vector,
                    limit=3,
                    with_payload=True,
                )
                points = result.points
            except Exception:
                points = self.client.search(
                    collection_name=self.collection_name,
                    query_vector=vector,
                    limit=3,
                    with_payload=True,
                )
            chunks = []
            for point in points:
                score = float(getattr(point, "score", 0) or 0)
                if score < self.score_threshold:
                    continue
                payload = getattr(point, "payload", {}) or {}
                chunks.append({"score": score, **payload})
            return chunks
        except Exception as e:
            print(f"Error buscando en RAG: {e}")
            return []

    def ensure_collection(self):
        from qdrant_client.models import Distance, VectorParams

        exists = False
        try:
            exists = bool(self.client.collection_exists(self.collection_name))
        except Exception:
            try:
                self.client.get_collection(self.collection_name)
                exists = True
            except Exception:
                exists = False

        if not exists:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.openai.embeddings.create(model=settings.RAG_EMBEDDING_MODEL, input=texts)
        return [item.embedding for item in response.data]

    def chunk_from_record(self, record: dict[str, Any], table_id: str) -> RagChunk | None:
        record_data = record.get("fields", record)
        record_id = self.record_id(record_data)
        text = self.record_text(record_data)
        if not record_id or not text:
            return None
        external_id = f"pg:{table_id}:{record_id}"
        return RagChunk(
            point_id=self.point_id(table_id, record_id),
            external_id=external_id,
            record_id=record_id,
            table_id=table_id,
            text=text,
            payload={"raw": record_data},
        )

    def record_id(self, record: dict[str, Any]) -> str:
        for key in ("Id", "id", "ID", "_id", "ncRecordId", "rowId"):
            value = record.get(key)
            if value:
                return str(value)
        text = self.record_text(record)
        return str(uuid.uuid5(uuid.NAMESPACE_URL, text)) if text else ""

    def record_text(self, record: dict[str, Any]) -> str:
        """Texto que se embebe: el título y el contenido del chunk.

        Se mantiene el recorrido genérico como respaldo para filas con otra
        forma (p. ej. durante la migración), pero el caso normal es
        titulo + contenido.
        """
        if "titulo" in record or "contenido" in record:
            titulo = str(record.get("titulo") or "").strip()
            contenido = str(record.get("contenido") or "").strip()
            return "\n".join(parte for parte in (titulo, contenido) if parte).strip()

        ignored = {"Id", "id", "ID", "_id", "CreatedAt", "UpdatedAt", "created_at", "updated_at", "activo"}
        parts = []
        for key, value in record.items():
            if key in ignored or value is None:
                continue
            if isinstance(value, str) and value.strip():
                parts.append(f"{key}: {value.strip()}")
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                parts.append(f"{key}: {value}")
        return "\n".join(parts).strip()

    def point_id(self, table_id: str, record_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pg:{table_id}:{record_id}"))

    def _answer_prompt(
        self,
        question: str,
        context: str,
        last_question: str,
        conversation_history: list[dict],
        chunks: list[dict[str, Any]],
    ) -> str:
        return f"""
Eres la recepcionista de una escuela de manejo y estás redactando la respuesta que se
enviará TAL CUAL al cliente por WhatsApp/Telegram.

Reglas:
- "answer" es el mensaje exacto que leerá el cliente: háblale directamente y en el mismo
  tono cercano del historial. No es un informe ni un análisis.
- Responde SOLO con información respaldada por la base de conocimiento de abajo. Si no
  respalda una respuesta completa a la pregunta, devuelve has_answer=false; nunca inventes.
- La mecánica interna es invisible para el cliente: jamás menciones de dónde sacas la
  información ni con qué la comparas (nada de "chunks", "fragmentos", "el sistema",
  "la información disponible", "según los datos"). Simplemente responde como quien se
  sabe la respuesta.
- No narres el proceso en tercera persona ("se le envía el formulario…"): dile al cliente
  directamente qué sigue o qué necesitas de él.
- Si hay una lista de requisitos o pasos, sepárala con saltos de línea (\\n) para que sea
  fácil de leer.

Flujo y nodo actual (contexto interno, no lo menciones): {context}
Última pregunta del flujo que se debe retomar después: {last_question}

Historial reciente cliente-bot:
{json.dumps(conversation_history[-settings.RAG_CONVERSATION_HISTORY_LIMIT:], ensure_ascii=False)}

Base de conocimiento (contexto interno, no la menciones):
{json.dumps(chunks, ensure_ascii=False)}

Pregunta actual del cliente:
{question}

Devuelve JSON estricto:
{{
  "has_answer": true|false,
  "answer": "el mensaje que se enviará literal al cliente"
}}
"""

    def _search_log_output(self, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "chunk_count": len(chunks),
            "sources": self._source_summaries(chunks),
            "scores": [round(float(chunk.get("score") or 0), 4) for chunk in chunks],
        }

    def _source_summaries(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        summaries = []
        for chunk in chunks:
            content = str(chunk.get("text") or "").strip()
            if not content and isinstance(chunk.get("raw"), dict):
                content = self.record_text(chunk["raw"])
            summaries.append(
                {
                    "content": self._truncate_source_text(content),
                    "score": round(float(chunk.get("score") or 0), 4),
                    "source_id": str(chunk.get("external_id") or ""),
                }
            )
        return summaries

    def _truncate_source_text(self, text: str) -> str:
        if len(text) <= self.source_text_limit:
            return text
        return f"{text[: self.source_text_limit]}...[truncated]"

    def _log_answer_generation(
        self,
        client_id: str,
        canal: Channel | str,
        question: str,
        context: str,
        chunks: list[dict[str, Any]],
        result: RagAnswer | dict[str, Any],
        started: float,
    ):
        if not client_id or not canal:
            return
        if isinstance(result, RagAnswer):
            output_data = {
                "has_answer": result.has_answer,
                "answer": result.answer,
                "sources": result.sources,
            }
            has_answer = result.has_answer
        else:
            output_data = result
            has_answer = bool(result.get("has_answer"))
        ToolCallLogger.success(
            client_id=client_id,
            canal=canal,
            tool_name="rag.generate_answer",
            input_data={
                "question": question,
                "context": context,
                "chunk_count": len(chunks),
                "sources": self._source_summaries(chunks),
            },
            output_data=output_data,
            text=f"RAG generó respuesta: {has_answer}",
            duration_ms=ToolCallLogger._duration_ms(started),
        )
