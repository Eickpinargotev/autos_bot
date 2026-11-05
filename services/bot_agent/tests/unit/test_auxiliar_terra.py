"""Compatibilidad de las tres llamadas auxiliares, sin consumir API real."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.application import publicidad_service, unified_agent
from src.application.rag_service import RagService
from src.core.config import settings
from src.core.modelos import (configuracion_rag, configuracion_recordatorios,
                             kwargs_de_auxiliar, kwargs_de_decision)
from src.infrastructure.repositories.conversation_state_repo import ConversationState


@pytest.fixture
def terra(monkeypatch):
    for nombre in ("OPENAI_MODEL_RAG", "OPENAI_REASONING_EFFORT_RAG",
                   "OPENAI_MODEL_RECORDATORIOS", "OPENAI_REASONING_EFFORT_RECORDATORIOS"):
        monkeypatch.setattr(settings, nombre, "")
    monkeypatch.setattr(settings, "OPENAI_MODEL_AUXILIAR", "gpt-6.1-terra")
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT_AUXILIAR", "none")
    # El auxiliar nunca debe heredar este esfuerzo de los especialistas.
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT", "high")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-prueba-sin-red")
    monkeypatch.setattr(unified_agent, "_record_model_trace", MagicMock())
    monkeypatch.setattr(unified_agent.seguimiento_service, "registrar_uso_llm", MagicMock())


def _completion(contenido):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(contenido)))], usage=None)


def _comprobar_llamada(mock, formato, modelo="gpt-6.1-terra", esfuerzo="none"):
    kwargs = mock.call_args.kwargs
    assert kwargs["model"] == modelo
    assert kwargs["reasoning_effort"] == esfuerzo
    if esfuerzo == "none":
        assert kwargs["temperature"] == 0
    else:
        assert "temperature" not in kwargs
    assert kwargs["response_format"]["type"] == formato
    traza = unified_agent._record_model_trace.call_args.kwargs
    assert traza["model"] == modelo
    assert traza["request"]["reasoning_effort"] == esfuerzo
    if esfuerzo == "none":
        assert traza["request"]["temperature"] == 0
    else:
        assert "temperature" not in traza["request"]


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


def test_modelos_y_esfuerzos_de_cada_tarea_son_independientes(terra, monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_MODEL_RAG", "gpt-6.1-sol")
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT_RAG", "low")
    monkeypatch.setattr(settings, "OPENAI_MODEL_RECORDATORIOS", "gpt-6.1-terra")
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT_RECORDATORIOS", "none")
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT_AUXILIAR", "high")
    assert configuracion_rag() == ("gpt-6.1-sol", {"reasoning_effort": "low"})
    assert configuracion_recordatorios() == ("gpt-6.1-terra", {"temperature": 0, "reasoning_effort": "none"})
    assert kwargs_de_auxiliar("gpt-6.1-terra") == {"reasoning_effort": "high"}


def test_rag_elige_sol_sin_cambiar_recordatorios(terra, monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_MODEL_RAG", "gpt-6.1-sol")
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT_RAG", "low")
    service = RagService.__new__(RagService)
    service.client = object()
    service.openai = MagicMock()
    service.sync_if_needed = MagicMock()
    service.search = MagicMock(return_value=[{"text": "Información", "score": 0.9}])
    service.openai.chat.completions.create.return_value = _completion({"has_answer": True, "answer": "Respuesta respaldada"})
    assert service.answer_question("Pregunta", client_id="506", canal="whatsapp").has_answer
    _comprobar_llamada(service.openai.chat.completions.create, "json_schema", "gpt-6.1-sol", "low")
    assert configuracion_recordatorios()[0] == "gpt-6.1-terra"
    assert unified_agent.seguimiento_service.registrar_uso_llm.call_args.kwargs["modelo"] == "gpt-6.1-sol"


def test_recordatorio_con_modelo_propio_no_hereda_rag_ni_auxiliar(terra, monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_MODEL_AUXILIAR", "gpt-5.4-nano")
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT_AUXILIAR", "high")
    monkeypatch.setattr(settings, "OPENAI_MODEL_RAG", "gpt-6.1-sol")
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT_RAG", "high")
    monkeypatch.setattr(settings, "OPENAI_MODEL_RECORDATORIOS", "gpt-6.1-terra")
    crear = MagicMock(return_value=_completion({"send": True, "message": "¿Desea continuar?"}))
    monkeypatch.setattr(unified_agent.client.chat.completions, "create", crear)
    assert unified_agent.FollowupAgent().decide(ConversationState(), "506", "whatsapp").send
    _comprobar_llamada(crear, "json_schema")


@pytest.mark.parametrize("resolver,nombre", [(configuracion_rag, "RAG"), (configuracion_recordatorios, "RECORDATORIOS")])
def test_sin_variables_nuevas_conserva_configuracion_anterior(terra, monkeypatch, resolver, nombre):
    monkeypatch.setattr(settings, "OPENAI_REASONING_EFFORT_AUXILIAR", "medium")
    assert resolver() == ("gpt-6.1-terra", {"reasoning_effort": "medium"})
    monkeypatch.setattr(settings, "OPENAI_MODEL_" + nombre, "gpt-6.1-sol")
    assert resolver() == ("gpt-6.1-sol", {"reasoning_effort": "low"})


def test_busquedas_separadas_conservan_chunks_de_cada_servicio_y_deduplican(terra):
    service = RagService.__new__(RagService)
    service.client = object()
    service.openai = MagicMock()
    service.sync_if_needed = MagicMock()
    service.search = MagicMock(side_effect=[
        [{"external_id": "dictamen", "text": "Dictamen: 22800", "score": .9},
         {"external_id": "compartido", "text": "Información común", "score": .6}],
        [{"external_id": "cita", "text": "Gestión de cita", "score": .7},
         {"external_id": "compartido", "text": "Información común", "score": .8}],
    ])
    service.openai.chat.completions.create.return_value = _completion({
        "has_answer": True, "answer": "El dictamen cuesta 22800. El costo de la cita requiere revisión.",
        "unanswered_queries": ["Costo de gestionar la cita A2"],
    })
    result = service.answer_question("Total de cita y dictamen", client_id="506", canal="whatsapp",
                                    search_queries=["Costo dictamen", "Costo cita A2", "  COSTO DICTAMEN  "])
    assert [c.args[0] for c in service.search.call_args_list] == ["Costo dictamen", "Costo cita A2"]
    assert {f["source_id"] for f in result.sources} == {"dictamen", "cita", "compartido"}
    assert len(result.sources) == 3
    assert next(f for f in result.sources if f["source_id"] == "compartido")["score"] == .8
    assert result.unanswered_queries == ["Costo de gestionar la cita A2"]
    assert result.has_answer
    prompt = service.openai.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert '"retrieval_queries": ["Costo dictamen", "Costo cita A2"]' in prompt


def test_busquedas_sin_resultados_no_inventan_respuesta_ni_llaman_modelo(terra):
    service = RagService.__new__(RagService)
    service.client = object()
    service.openai = MagicMock()
    service.sync_if_needed = MagicMock()
    service.search = MagicMock(return_value=[])
    result = service.answer_question("Consulta", search_queries=["Dictamen", "Cita"])
    assert not result.has_answer
    assert service.search.call_count == 2
    service.openai.chat.completions.create.assert_not_called()


def test_limites_de_consultas_estructuradas_y_compatibilidad():
    from src.core.consultas_rag import normalizar_consultas
    assert normalizar_consultas(["  Uno  ", "UNO", None, 42, "Dos", "Tres", "Cuatro"]) == ["Uno", "Dos", "Tres"]
    assert normalizar_consultas("Una consulta antigua") == []
    decision = unified_agent.SpecialistAgent("GENERAL")._validated_decision({
        "action": "reply", "rag_queries": ["Costo dictamen", "Costo cita"], "messages": [],
    }, "¿Cuánto sale?")
    assert decision.messages == ["[[rag]]"]
    assert decision.rag_queries == ["Costo dictamen", "Costo cita"]


def test_busqueda_vacia_de_un_servicio_no_descarta_el_resultado_del_otro(terra):
    service = RagService.__new__(RagService)
    service.client = object()
    service.openai = MagicMock()
    service.sync_if_needed = MagicMock()
    service.search = MagicMock(side_effect=[[], [{"text": "Dato del segundo servicio", "score": .8}]])
    service.openai.chat.completions.create.return_value = _completion({
        "has_answer": True, "answer": "Dato confirmado del segundo servicio.",
        "unanswered_queries": ["Costo del primer servicio"],
    })
    result = service.answer_question("Ambos servicios", search_queries=["Primer servicio", "Segundo servicio"])
    assert result.has_answer and len(result.sources) == 1
    assert result.unanswered_queries == ["Costo del primer servicio"]
    assert service.search.call_count == 2
    prompt = service.openai.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert '"consultas_especificas": ["Primer servicio", "Segundo servicio"]' in prompt
