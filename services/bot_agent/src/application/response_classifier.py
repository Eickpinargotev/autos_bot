import json
import re
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
    SERVICE_DISCOVERY_QUESTION = "¿Lo necesita para prueba de manejo, clases, alquiler, dictamen o información de licencia?"

    anger_words = (
        "queja",
        "molest",
        "devolucion",
        "devolución",
        "problema",
        "enojo",
        "frustr",
        "insatisf",
        "estafa",
        "reclamo",
        "reclamar",
        "mal servicio",
        "pesimo",
        "pésimo",
        "horrible",
        "indign",
        "irrespet",
        "mentira",
        "engañ",
        "engan",
        "robo",
        "ladron",
        "ladrón",
        "denuncia",
        "demand",
        "me bloquearon",
        "me dejaron",
        "no responden",
        "nadie responde",
        "quiero solucion",
        "quiero solución",
        "solucionen",
        "me siento",
        "terrible",
        "fatal",
        "no puedo ingresar",
        "no me deja ingresar",
        "no funciona",
        "no sirve",
        "seccion de tareas",
        "sección de tareas",
        "no se que hacer",
        "no sé qué hacer",
    )

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

    def is_angry_or_complaint(self, text: str) -> bool:
        cleaned = self._normalize(text)
        ascii_cleaned = self._strip_accents(cleaned)
        return any(word in cleaned or self._strip_accents(word) in ascii_cleaned for word in self.anger_words)

    def asks_for_human_help(self, text: str) -> bool:
        cleaned = self._normalize(text)
        return any(phrase in cleaned for phrase in ("asesor", "persona", "humano", "llamen", "llamar", "contacten", "contactar", "ayuda de alguien"))

    def needs_manual_handoff(self, text: str) -> bool:
        cleaned = self._strip_accents(self._normalize(text))
        manual_patterns = (
            r"\b(ya|hoy|ayer|te|le)\s+(deposite|pague|transferi|mande|envie)\b",
            r"\b(deposito|pago|transferencia)\s+(hecho|realizado|enviado|lista|listo)\b",
            r"\b(comprobante|recibo|factura|revisar|revise|confirmar|confirmame|verificar|validar)\b",
            r"\b(hace|desde) \d+ (mes|meses|dia|dias|semana|semanas)\b",
            r"\b(estado|seguimiento|pendiente|tramite|tr[aá]mite)\b",
            r"\b(hablar|comunicarme|contactar|ocupo|necesito)\s+(con\s+)?(enrique|asesor|persona|humano)\b",
        )
        return any(re.search(pattern, cleaned) for pattern in manual_patterns)

    def summarize_for_report(self, text: str, flow: str, node: str, client_id: str = "", canal: Channel | str = "") -> str:
        if not settings.OPENAI_API_KEY:
            return f"El usuario pide ayuda fuera del flujo {flow}.{node}: {text[:240]}"
        started = time.monotonic()
        input_data = {"text": text, "flow": flow, "node": node}
        try:
            completion = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Devuelve JSON estricto."},
                    {"role": "user", "content": REPORT_SUMMARY_PROMPT.format(mensaje=text, flujo=flow, nodo=node)},
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
            classification = ReplyClassification(
                data.get("intent", "unknown"),
                data.get("value", ""),
                bool(data.get("has_off_flow_question")),
                str(data.get("off_flow_question") or ""),
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

    def _normalize(self, text: str) -> str:
        return " ".join(text.lower().strip().split())

    def _strip_accents(self, text: str) -> str:
        replacements = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
        return text.translate(replacements)
