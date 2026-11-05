"""Compatibilidad de las tres llamadas auxiliares, sin consumir API real."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.application import publicidad_service, unified_agent
from src.application.rag_service import RagService
from src.core.config import settings
from src.core.modelos import kwargs_de_auxiliar, kwargs_de_decision
from src.infrastructure.repositories.conversation_state_repo import ConversationState


@pytest.fixture
def terra(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_MODEL_AUXILIAR", "gpt-6.1-terra")
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT_AUXILIAR", "none")
    # El auxiliar nunca debe heredar este esfuerzo de los especialistas.
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT", "high")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-prueba-sin-red")
    monkeypatch.setattr(unified_agent, "_record_model_trace", MagicMock())
    monkeypatch.setattr(unified_agent.seguimiento_service, "registrar_uso_llm", MagicMock())


def _completion(contenido):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(contenido)))], usage=None)


def _comprobar_llamada(mock, formato):
    kwargs = mock.call_args.kwargs
    assert kwargs["model"] == "gpt-6.1-terra"
    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["temperature"] == 0
    assert kwargs["response_format"]["type"] == formato
    traza = unified_agent._record_model_trace.call_args.kwargs
    assert traza["request"]["reasoning_effort"] == "none"
    assert traza["request"]["temperature"] == 0


def test_rag_usa_terra_none_y_conserva_respuesta_y_fuentes(terra):
    service = RagService.__new__(RagService)
    service.client = object()
    service.openai = MagicMock()
    service.sync_if_needed = MagicMock()
    service.search = MagicMock(return_value=[{"external_id": "pg:rag_chunks:1", "text": "El curso cuesta 15000", "score": 0.9}])
    service.openai.chat.completions.create.return_value = _completion({"has_answer": True, "answer": "El curso cuesta 15000."})
    result = service.answer_question("¿Cuánto cuesta?", client_id="506", canal="whatsapp")
    assert result.has_answer is True and result.answer == "El curso cuesta 15000."
    assert result.sources[0]["source_id"] == "pg:rag_chunks:1"
    service.search.assert_called_once_with("¿Cuánto cuesta?")
    _comprobar_llamada(service.openai.chat.completions.create, "json_schema")


def test_recordatorio_usa_terra_none(terra, monkeypatch):
    crear = MagicMock(return_value=_completion({"send": True, "message": "¿Desea continuar?"}))
    monkeypatch.setattr(unified_agent.client.chat.completions, "create", crear)
    result = unified_agent.FollowupAgent().decide(ConversationState(last_question="¿Desea continuar?"), "506", "whatsapp")
    assert result.send and result.message == "¿Desea continuar?"
    _comprobar_llamada(crear, "json_schema")


@pytest.mark.parametrize("flujo", ["rag", "recordatorio"])
def test_error_de_api_auxiliar_conserva_fallback_y_parametros_en_traza(terra, monkeypatch, flujo):
    crear = MagicMock(side_effect=RuntimeError("Error de API simulado"))
    if flujo == "rag":
        service = RagService.__new__(RagService)
        service.client = object()
        service.openai = MagicMock()
        service.openai.chat.completions.create = crear
        service.sync_if_needed = MagicMock()
        service.search = MagicMock(return_value=[{"text": "Información", "score": 0.9}])
        assert service.answer_question("Pregunta", client_id="506", canal="whatsapp").has_answer is False
    else:
        monkeypatch.setattr(unified_agent.client.chat.completions, "create", crear)
        assert unified_agent.FollowupAgent().decide(ConversationState(), "506", "whatsapp").send is False
    traza = unified_agent._record_model_trace.call_args.kwargs
    assert traza["status"] == "error"
    assert traza["request"]["reasoning_effort"] == "none"
    assert crear.call_args.kwargs["reasoning_effort"] == "none"


def test_extraccion_publicitaria_usa_terra_none(terra, monkeypatch):
    crear = MagicMock(return_value=_completion({"dia": "05/11/2026", "hora": "18:00", "valor": "15000"}))
    monkeypatch.setattr(publicidad_service.client.chat.completions, "create", crear)
    monkeypatch.setattr(publicidad_service.PublicidadService, "_buscar_clave", lambda _: "LIBERIA")
    monkeypatch.setattr(publicidad_service.plantillas_repository, "textos_de", lambda _: ["Curso jueves a las 18:00, 15000", "https://chat.whatsapp.com/ABC123"])
    monkeypatch.setattr(publicidad_service, "PostgresUserRepo", MagicMock())
    monkeypatch.setattr(publicidad_service, "register_ad_context", MagicMock())
    monkeypatch.setattr(publicidad_service, "schedule_ad_programmed_messages", MagicMock())
    assert publicidad_service.PublicidadService.handle_invitation_by_city("506", "LIBERIA", channel="whatsapp")
    _comprobar_llamada(crear, "json_object")
    publicidad_service.schedule_ad_programmed_messages.apply_async.assert_called_once()


@pytest.mark.parametrize("esfuerzo", ["low", "medium", "high", "xhigh", "max"])
def test_terra_con_razonamiento_omite_temperature(esfuerzo):
    assert kwargs_de_decision("gpt-6.1-terra", reasoning_effort=esfuerzo) == {"reasoning_effort": esfuerzo}


def test_terra_con_esfuerzo_vacio_no_hereda_medium():
    assert kwargs_de_decision("gpt-6.1-terra", reasoning_effort="") == {"reasoning_effort": "none", "temperature": 0}


def test_auxiliar_historico_conserva_parametros(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT_AUXILIAR", "none")
    assert kwargs_de_auxiliar("gpt-5.4-nano") == {"reasoning_effort": "none", "temperature": 0}
    assert kwargs_de_auxiliar("gpt-4o-mini") == {"temperature": 0}
