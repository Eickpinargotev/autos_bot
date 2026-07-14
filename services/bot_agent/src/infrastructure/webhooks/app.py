import hmac
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from src.application.rag_service import RagService
from src.core.config import settings

app = FastAPI(title="Bot Agent Webhooks")
rag_service = RagService()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhooks/nocodb-rag-chunks")
async def nocodb_rag_chunks_webhook(payload: dict[str, Any], token: str = Query(default="")):
    # Sin token configurado el webhook queda DESHABILITADO (503), no abierto:
    # este endpoint escribe en la base de conocimiento del RAG y el servicio
    # puede quedar expuesto por el reverse proxy. El RAG sigue actualizándose
    # por la sincronización lazy (RAG_SYNC_TTL_SECONDS); configurar
    # NOCODB_RAG_WEBHOOK_TOKEN solo hace la actualización instantánea.
    if not settings.NOCODB_RAG_WEBHOOK_TOKEN:
        raise HTTPException(status_code=503, detail="Webhook disabled: set NOCODB_RAG_WEBHOOK_TOKEN")
    if not hmac.compare_digest(token, settings.NOCODB_RAG_WEBHOOK_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    result = rag_service.sync_chunk_event(payload)
    return {"status": "ok", **result}
