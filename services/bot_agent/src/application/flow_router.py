from src.application.response_classifier import ReplyClassification, ResponseClassifier


class FlowRouter:
    def __init__(self):
        self.classifier = ResponseClassifier()

    def initial_node(self, flow: str, channel, user_id: str) -> str:
        if flow == "GENERAL":
            return "G1"
        if flow == "Alquiler":
            return "A1"
        if flow == "CLASES":
            return "C1"
        if flow == "QUEJA":
            return "Q1"
        if flow == "WIN":
            return "W1"
        if flow == "DICTAMEN":
            from src.infrastructure.repositories.keyword_registry_repository import KeywordRegistryRepository
            return "D1_1" if KeywordRegistryRepository.exists(user_id, channel) else "D1"
        return "G1"

    def next_node(self, flow: str, node: str, classification: ReplyClassification, user_id: str, channel) -> tuple[str, str]:
        intent = classification.intent
        value = classification.value

        if flow == "GENERAL":
            return self._general_next(node, intent, value, user_id, channel)
        if flow == "Alquiler" and node == "A1":
            if intent == "positive":
                return "GENERAL", "G35"
            if intent == "negative":
                return "GENERAL", "G7"
        if flow == "CLASES" and node == "C1":
            if intent == "city" and value == "liberia":
                return "CLASES", "C2"
            if intent in {"city", "negative"}:
                return "CLASES", "C5"

        return flow, ""

    def _general_next(self, node: str, intent: str, value: str, user_id: str, channel) -> tuple[str, str]:
        if node == "G1":
            if intent == "positive":
                return "GENERAL", "G3"
            if intent == "negative":
                return "GENERAL", "G4"
        if node == "G3":
            if intent == "positive":
                return "GENERAL", "G35"
            if intent == "negative":
                return "GENERAL", "G7"
        if node == "G4" and intent == "city":
            return "PUBLICIDAD", "CITY_INVITATION"
        if node == "G35":
            if intent == "city" and value == "liberia":
                return "GENERAL", "G11"
            if intent == "city":
                return "GENERAL", "G12"
        if node in {"G11", "G12"} and intent == "license":
            return "GENERAL", self._license_node(node, value, user_id, channel)
        return "GENERAL", ""

    def _license_node(self, current_node: str, value: str, user_id: str, channel) -> str:
        liberia = current_node == "G11"
        if value == "car":
            return "G13" if liberia else "G25"
        if value == "moto":
            from src.infrastructure.repositories.keyword_registry_repository import KeywordRegistryRepository
            is_registered = KeywordRegistryRepository.exists(user_id, channel)
            if liberia:
                return "G16_1" if is_registered else "G16"
            else:
                return "G28_1" if is_registered else "G28"
        if value == "b2":
            return "G19" if liberia else "G29"
        if value == "b3":
            return "G20" if liberia else "G30"
        if value == "b4":
            return "G21" if liberia else "G31"
        if value == "bus":
            return "G22" if liberia else "G32"
        return ""

