import json
import re
from dataclasses import dataclass

from openai import OpenAI

from src.core.config import settings
from src.core.prompts import (
    CATEGORIZATION_PROMPT,
    OFF_FLOW_QUESTION_PROMPT,
    REPLY_EVALUATION_PROMPT,
    REPORT_SUMMARY_PROMPT,
)


client = OpenAI(api_key=settings.OPENAI_API_KEY or "test")


@dataclass
class ReplyClassification:
    intent: str
    value: str = ""
    has_off_flow_question: bool = False
    off_flow_question: str = ""


class ResponseClassifier:
    initial_flow_map = {
        "DICTAMEN": "DICTAMEN",
        "CLASES": "CLASES",
        "ALQUILER": "Alquiler",
        "QUEJA": "QUEJA",
        "QUEJAS": "QUEJA",
        "WIN": "WIN",
        "GENERAL": "GENERAL",
    }
    positive_words = ("si", "sí", "claro", "correcto", "afirmativo", "ya tengo", "listo", "ok", "dale")
    negative_words = ("no", "todavia no", "todavía no", "aun no", "aún no", "negativo")
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

    def classify_initial_flow(self, text: str) -> str:
        flow = self._classify_initial_with_rules(text)
        if flow:
            return flow
        return self._classify_initial_with_llm(text)

    def classify_reply(self, text: str, flow: str, node: str, last_question: str) -> ReplyClassification:
        cleaned = self._normalize(text)

        if self.is_angry_or_complaint(text):
            return ReplyClassification("complaint")

        off_flow_question = self.extract_off_flow_question(text)
        primary_reply = self._detect_flow_reply(cleaned, node)
        if off_flow_question:
            if primary_reply.intent != "unknown":
                primary_reply.has_off_flow_question = True
                primary_reply.off_flow_question = off_flow_question
                return primary_reply
            return ReplyClassification("question", has_off_flow_question=True, off_flow_question=off_flow_question)

        if self._starts_with_question_phrase(cleaned) or self._contains_question_intent(cleaned):
            return ReplyClassification("question")

        if primary_reply.intent != "unknown":
            return primary_reply

        if self.is_question(text):
            return ReplyClassification("question")

        return self._classify_with_llm(text, flow, node, last_question)

    def extract_off_flow_question(self, text: str) -> str:
        cleaned = self._normalize(text)
        if not self.is_question(text) and not self._contains_question_intent(cleaned):
            return ""

        markers = (
            "pero tengo una consulta",
            "pero tengo consulta",
            "tengo una consulta",
            "tengo consulta",
            "una consulta",
            "me gustaria saber",
            "me gustaría saber",
            "quisiera saber",
            "quiero saber",
        )
        for marker in markers:
            idx = cleaned.find(marker)
            if idx >= 0:
                return text[idx:].strip(" ,.:;")

        split_match = re.search(r"\b(?:pero|aunque|y)\b\s*(.+)", text, re.IGNORECASE)
        if split_match and self._contains_question_intent(self._normalize(split_match.group(1))):
            return split_match.group(1).strip(" ,.:;")

        return text.strip()

    def _detect_flow_reply(self, cleaned: str, node: str) -> ReplyClassification:
        if node in {"G35", "C1", "G4"} and "liberia" in cleaned:
            return ReplyClassification("city", "liberia")
        if node in {"G35", "C1", "G4"} and self._looks_like_city(cleaned):
            return ReplyClassification("city", "other")
        if node in {"G11", "G12"}:
            license_value = self.detect_license_type(cleaned)
            if license_value:
                return ReplyClassification("license", license_value)
        if self._is_positive(cleaned):
            return ReplyClassification("positive")
        if self._is_negative(cleaned):
            return ReplyClassification("negative")

        license_value = self.detect_license_type(cleaned)
        if license_value:
            return ReplyClassification("license", license_value)
        if "liberia" in cleaned:
            return ReplyClassification("city", "liberia")
        return ReplyClassification("unknown")

    def is_angry_or_complaint(self, text: str) -> bool:
        cleaned = self._normalize(text)
        ascii_cleaned = self._strip_accents(cleaned)
        return any(word in cleaned or self._strip_accents(word) in ascii_cleaned for word in self.anger_words)

    def is_question(self, text: str) -> bool:
        cleaned = self._normalize(text)
        if "?" in text or "¿" in text:
            return True
        return self._starts_with_question_phrase(cleaned) or self._contains_question_intent(cleaned)

    def asks_for_human_help(self, text: str) -> bool:
        cleaned = self._normalize(text)
        return any(phrase in cleaned for phrase in ("asesor", "persona", "humano", "llamen", "llamar", "contacten", "contactar", "ayuda de alguien"))

    def summarize_for_report(self, text: str, flow: str, node: str) -> str:
        if not settings.OPENAI_API_KEY:
            return f"El usuario pide ayuda fuera del flujo {flow}.{node}: {text[:240]}"
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
            return data.get("resumen") or f"Duda fuera de flujo: {text[:240]}"
        except Exception:
            return f"Duda fuera de flujo: {text[:240]}"

    def detect_license_type(self, cleaned: str) -> str:
        if re.search(r"\b(b1|carro|auto|automovil|automóvil)\b", cleaned):
            return "car"
        if re.search(r"\b(a1|a2|a3|moto)\b", cleaned):
            return "moto"
        if re.search(r"\bb2\b", cleaned):
            return "b2"
        if re.search(r"\bb3\b", cleaned):
            return "b3"
        if re.search(r"\b(b4|trailer|triller)\b", cleaned):
            return "b4"
        if re.search(r"\b(c2|bus)\b", cleaned):
            return "bus"
        return ""

    def _classify_initial_with_rules(self, text: str) -> str:
        cleaned = self._normalize(text)
        ascii_cleaned = self._strip_accents(cleaned)

        if self.is_angry_or_complaint(text):
            return "QUEJA"
        if any(word in ascii_cleaned for word in ("dictamen", "examen medico", "prueba medica", "cita dictamen", "formulario dictamen")):
            return "DICTAMEN"
        if self._is_negative_win_message(ascii_cleaned):
            return "GENERAL"
        if self._is_win_message(ascii_cleaned):
            return "WIN"
        if any(word in ascii_cleaned for word in ("clases", "clase de manejo", "manejo", "lecciones", "practica", "conduccion")) and "teorico" not in ascii_cleaned:
            return "CLASES"
        if any(word in ascii_cleaned for word in ("alquiler", "alquilar", "auto", "carro", "moto", "bus", "camion", "trailer", "b1", "b2", "b3", "b4", "a1", "a2", "a3")):
            return "Alquiler"
        return ""

    def _is_win_message(self, ascii_cleaned: str) -> bool:
        if self._is_negative_win_message(ascii_cleaned):
            return False
        return any(
            win in ascii_cleaned
            for win in (
                "gane",
                "gané",
                "aprobo",
                "aprobe",
                "aprobé",
                "aprove",
                "pase",
                "pasé",
                "me fue bien",
                "ya gane",
                "ya aprobe",
                "ya pase",
            )
        )

    def _is_negative_win_message(self, ascii_cleaned: str) -> bool:
        return any(neg in ascii_cleaned for neg in ("no gan", "no aprob", "no aprobe", "no pase", "perdi", "reprobe", "reprob"))

    def _classify_initial_with_llm(self, text: str) -> str:
        if not settings.OPENAI_API_KEY:
            return "GENERAL"
        try:
            completion = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "Devuelve solo una categoría válida."},
                    {"role": "user", "content": CATEGORIZATION_PROMPT.format(mensaje=text)},
                ],
            )
            raw = (completion.choices[0].message.content or "").strip()
            raw = self._extract_initial_flow_label(raw)
            return self.initial_flow_map.get(raw, "GENERAL")
        except Exception:
            return "GENERAL"

    def _extract_initial_flow_label(self, raw: str) -> str:
        try:
            data = json.loads(raw)
            if isinstance(data, str):
                raw = data
            elif isinstance(data, dict):
                raw = str(data.get("categoria") or data.get("category") or data.get("flow") or data.get("intent") or "")
        except Exception:
            pass

        normalized = self._strip_accents(raw).upper()
        for category in self.initial_flow_map:
            if re.search(rf"\b{re.escape(category)}\b", normalized):
                return category
        return re.sub(r"[^A-Z]", "", normalized)

    def _classify_with_llm(self, text: str, flow: str, node: str, last_question: str) -> ReplyClassification:
        if not settings.OPENAI_API_KEY:
            return ReplyClassification("unknown")
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
            return ReplyClassification(
                data.get("intent", "unknown"),
                data.get("value", ""),
                bool(data.get("has_off_flow_question")),
                str(data.get("off_flow_question") or ""),
            )
        except Exception:
            return ReplyClassification("unknown")

    def _is_positive(self, cleaned: str) -> bool:
        if self._contains_question_intent(cleaned) or self._is_negative(cleaned):
            return False
        words = cleaned.split()
        if len(words) > 6:
            return False
        return any(self._contains_phrase(cleaned, word) for word in self.positive_words)

    def _is_negative(self, cleaned: str) -> bool:
        if self._contains_question_intent(cleaned):
            return False
        words = cleaned.split()
        if len(words) > 6:
            return False
        return any(self._contains_phrase(cleaned, word) for word in self.negative_words)

    def _contains_phrase(self, cleaned: str, phrase: str) -> bool:
        if " " in phrase:
            return phrase in cleaned
        return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", cleaned) is not None

    def _starts_with_question_phrase(self, cleaned: str) -> bool:
        question_starts = (
            "que ",
            "qué ",
            "como ",
            "cómo ",
            "cuando ",
            "cuándo ",
            "donde ",
            "dónde ",
            "cuanto ",
            "cuánto ",
            "puedo ",
            "ocupo ",
            "necesito ",
            "me puede",
            "me ayuda",
        )
        return cleaned.startswith(question_starts)

    def _contains_question_intent(self, cleaned: str) -> bool:
        patterns = (
            r"\bme gustar[ií]a saber\b",
            r"\bquisiera saber\b",
            r"\bquiero saber\b",
            r"\btengo (?:una )?consulta\b",
            r"\buna consulta\b",
            r"\bqu[eé] pasa si\b",
            r"\by si\b",
            r"\bsaber si\b",
            r"\btengo que\b",
            r"\bdebo\b",
            r"\bpuedo\b",
            r"\bpueden\b",
            r"\bme ayuda\b",
        )
        return any(re.search(pattern, cleaned) for pattern in patterns)

    def _looks_like_city(self, cleaned: str) -> bool:
        return bool(cleaned.strip()) and len(cleaned.split()) <= 5

    def _normalize(self, text: str) -> str:
        return " ".join(text.lower().strip().split())

    def _strip_accents(self, text: str) -> str:
        replacements = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
        return text.translate(replacements)
