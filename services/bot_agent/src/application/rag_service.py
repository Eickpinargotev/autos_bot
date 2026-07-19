import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from openai import OpenAI

from src.application import seguimiento_service
from src.core.config import settings
from src.domain.entities import Channel
from src.infrastructure.logging.tool_call_logger import ToolCallLogger


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
    chunks_table_id = "mlk30zxjzj4lfd8"
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
            seguimiento_service.registrar_uso_llm(client_id, canal, getattr(completion, "usage", None))
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

    def sync_chunk_event(self, event: dict[str, Any]) -> dict[str, int]:
        event_type = self._event_type(event)
        table_id = self._event_table_id(event)
        if table_id and table_id != self.chunks_table_id:
            return {"upserted": 0, "deleted": 0, "ignored": 1}

        rows = self._event_rows(event)
        if not rows:
            return {"upserted": 0, "deleted": 0, "ignored": 1}

        if "delete" in event_type:
            deleted = 0
            for row in rows:
                if self.delete_record(row, table_id or self.chunks_table_id):
                    deleted += 1
            return {"upserted": 0, "deleted": deleted, "ignored": 0}

        upserted = self.upsert_records(rows, table_id or self.chunks_table_id)
        return {"upserted": upserted, "deleted": 0, "ignored": 0}

    def fetch_chunk_records(self) -> list[dict[str, Any]]:
        if not settings.NOCODB_RAG_CHUNKS_URL:
            return []

        records: list[dict[str, Any]] = []
        page = 1
        page_size = 1000
        while True:
            url = self._url_with_params(settings.NOCODB_RAG_CHUNKS_URL, {"page": page, "pageSize": page_size})
            response = httpx.get(url, headers={"xc-token": settings.NOCODB_TOKEN}, timeout=20.0)
            response.raise_for_status()
            data = response.json()
            batch = data.get("list") or data.get("records") or data.get("data") or []
            if not isinstance(batch, list):
                break
            records.extend([record.get("fields", record) for record in batch if isinstance(record, dict)])

            page_info = data.get("pageInfo") or data.get("pagination") or {}
            total_rows = page_info.get("totalRows") or page_info.get("total")
            if len(batch) < page_size:
                break
            if total_rows and len(records) >= int(total_rows):
                break
            page += 1
        return records

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
        try:
            from qdrant_client.models import FieldCondition, Filter, MatchValue

            deleted = 0
            offset = None
            while True:
                points, offset = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=Filter(
                        must=[FieldCondition(key="table_id", match=MatchValue(value=table_id))]
                    ),
                    limit=1000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                stale_point_ids = [
                    point.id
                    for point in points
                    if str((point.payload or {}).get("record_id") or "") not in current_record_ids
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
        external_id = f"nocodb:{table_id}:{record_id}"
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
        ignored = {"Id", "id", "ID", "_id", "CreatedAt", "UpdatedAt", "created_at", "updated_at"}
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
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"nocodb:{table_id}:{record_id}"))

    def _answer_prompt(
        self,
        question: str,
        context: str,
        last_question: str,
        conversation_history: list[dict],
        chunks: list[dict[str, Any]],
    ) -> str:
        return f"""
Eres un asistente de una escuela de manejo. Responde solo si los chunks contienen evidencia suficiente.
Si falta información, devuelve has_answer=false.

Flujo y nodo actual: {context}
Última pregunta del flujo que se debe retomar después: {last_question}

Historial reciente cliente-bot:
{json.dumps(conversation_history[-settings.RAG_CONVERSATION_HISTORY_LIMIT:], ensure_ascii=False)}

Chunks recuperados:
{json.dumps(chunks, ensure_ascii=False)}

Pregunta actual del cliente:
{question}

Devuelve JSON estricto:
{{
  "has_answer": true|false,
  "answer": "respuesta clara para WhatsApp. Si hay una lista de requisitos o pasos, usa saltos de línea (\\n)    
  para separarlos y hacerla fácil de leer"
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

    def _event_type(self, event: dict[str, Any]) -> str:
        return str(event.get("type") or event.get("event") or event.get("Event") or "").lower()

    def _event_table_id(self, event: dict[str, Any]) -> str:
        data = event.get("data") or event.get("Data") or event
        return str(data.get("table_id") or data.get("tableId") or data.get("table") or event.get("table_id") or "")

    def _event_rows(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        data = event.get("data") or event.get("Data") or event
        rows = data.get("rows") or data.get("records") or data.get("list") or event.get("rows") or []
        if isinstance(rows, dict):
            rows = [rows]
        if not rows and isinstance(data.get("row"), dict):
            rows = [data["row"]]
        return [row.get("fields", row) for row in rows if isinstance(row, dict)]

    def _url_with_params(self, url: str, params: dict[str, Any]) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key, value in params.items():
            query[key] = [str(value)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
