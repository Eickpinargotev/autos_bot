import json
import time
from dataclasses import dataclass

from openai import OpenAI

from src.core.config import settings
from src.core.prompts import (
    REPLY_EVALUATION_PROMPT,
    REPORT_SUMMARY_PROMPT,
)
from src.domain.entities import Channel
from src.infrastructure.logging.tool_call_logger import ToolCallLogger


client = OpenAI(api_key=settings.OPENAI_API_KEY or "test")


@dataclass
class ReplyClassification:
    intent: str
    value: str = ""
    has_off_flow_question: bool = False
    off_flow_question: str = ""


class ResponseClassifier:
    def classify_reply(
        self,
        text: str,
        flow: str,
        node: str,
        last_question: str,
        client_id: str = "",
        canal: Channel | str = "",
    ) -> ReplyClassification:
        if settings.OPENAI_API_KEY:
            return self._classify_with_llm(text, flow, node, last_question, client_id=client_id, canal=canal)
        return ReplyClassification("unknown")

    def summarize_for_report(
        self,
        text: str,
        flow: str,
        node: str,
        client_id: str = "",
        canal: Channel | str = "",
        conversation_history: list[dict] | None = None,
    ) -> str:
        if not settings.OPENAI_API_KEY:
            return f"El usuario pide ayuda fuera del flujo {flow}.{node}: {text[:240]}"
        started = time.monotonic()
        historial = json.dumps(conversation_history or [], ensure_ascii=False)
        input_data = {"text": text, "flow": flow, "node": node}
        try:
            completion = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Devuelve JSON estricto."},
                    {"role": "user", "content": REPORT_SUMMARY_PROMPT.format(mensaje=text, flujo=flow, nodo=node, historial=historial)},
                ],
            )
            data = json.loads(completion.choices[0].message.content)
            summary = data.get("resumen") or f"Duda fuera de flujo: {text[:240]}"
            self._log_tool_success(
                client_id,
                canal,
                "classifier.summarize_for_report",
                input_data,
                {"summary": summary},
                f"Resumen de reporte generado para {flow}.{node}",
                started,
            )
            return summary
        except Exception as exc:
            self._log_tool_error(
                client_id,
                canal,
                "classifier.summarize_for_report",
                input_data,
                exc,
                started,
            )
            return f"Duda fuera de flujo: {text[:240]}"

    def _classify_with_llm(
        self,
        text: str,
        flow: str,
        node: str,
        last_question: str,
        client_id: str = "",
        canal: Channel | str = "",
    ) -> ReplyClassification:
        if not settings.OPENAI_API_KEY:
            return ReplyClassification("unknown")
        started = time.monotonic()
        input_data = {
            "text": text,
            "flow": flow,
            "node": node,
            "last_question": last_question,
        }
        try:
            completion = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Devuelve JSON estricto."},
                    {
                        "role": "user",
                        "content": REPLY_EVALUATION_PROMPT.format(
                            mensaje=text,
                            flujo=flow,
                            nodo=node,
                            pregunta=last_question,
                        ),
                    },
                ],
            )
            data = json.loads(completion.choices[0].message.content)
            classification = self._normalize(
                ReplyClassification(
                    data.get("intent", "unknown"),
                    data.get("value", ""),
                    bool(data.get("has_off_flow_question")),
                    str(data.get("off_flow_question") or ""),
                )
            )
            self._log_tool_success(
                client_id,
                canal,
                "classifier.classify_reply",
                input_data,
                classification,
                f"Clasificación de respuesta: {classification.intent}",
                started,
            )
            return classification
        except Exception as exc:
            self._log_tool_error(
                client_id,
                canal,
                "classifier.classify_reply",
                input_data,
                exc,
                started,
            )
            return ReplyClassification("unknown")

    @staticmethod
    def _normalize(classification: ReplyClassification) -> ReplyClassification:
        # Coherencia con la tabla de decisión (estados imposibles de PD2):
        # F-i: un saludo, una queja o una derivación a humano NO llevan duda
        # lateral; esos casos se atienden por su propio intent (retomar o
        # humano), nunca por RAG. Evita el falso "tengo una pregunta".
        if classification.intent in {"greeting", "complaint", "human_handoff"}:
            classification.has_off_flow_question = False
            classification.off_flow_question = ""
        # F-iii: bandera de duda activada pero sin texto -> no hay duda real.
        if classification.has_off_flow_question and not classification.off_flow_question.strip():
            classification.has_off_flow_question = False
            classification.off_flow_question = ""
        return classification

    @staticmethod
    def _log_tool_success(
        client_id: str,
        canal: Channel | str,
        tool_name: str,
        input_data: dict,
        output_data,
        text: str,
        started: float,
    ):
        if not client_id or not canal:
            return
        ToolCallLogger.success(
            client_id=client_id,
            canal=canal,
            tool_name=tool_name,
            input_data=input_data,
            output_data=output_data,
            text=text,
            duration_ms=ToolCallLogger._duration_ms(started),
        )

    @staticmethod
    def _log_tool_error(
        client_id: str,
        canal: Channel | str,
        tool_name: str,
        input_data: dict,
        error: Exception,
        started: float,
    ):
        if not client_id or not canal:
            return
        ToolCallLogger.error(
            client_id=client_id,
            canal=canal,
            tool_name=tool_name,
            input_data=input_data,
            error=error,
            duration_ms=ToolCallLogger._duration_ms(started),
        )
