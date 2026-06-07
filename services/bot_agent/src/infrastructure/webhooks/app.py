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
    if settings.NOCODB_RAG_WEBHOOK_TOKEN and token != settings.NOCODB_RAG_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    result = rag_service.sync_chunk_event(payload)
    return {"status": "ok", **result}
