import json
from unittest.mock import MagicMock, patch

from src.application.project_context import ambito_proyecto
from src.domain.entities import Channel, InboundMessage, MessageType
from src.infrastructure.repositories import conversation_audit_repository as audit
from src.application.conversation_orchestrator import ConversationOrchestrator
from src.infrastructure.channels.senders import _observed_send


def test_auditoria_descarta_contenido_y_errores_libres():
    with ambito_proyecto(1), patch.object(audit, 'consultar_uno', return_value={'id': 10}) as query:
        result = audit.record('506', Channel.WHATSAPP, 'message_received', metadata={
            'text': 'PRIVATE_MESSAGE', 'error': 'PRIVATE_ERROR', 'payload': {'text': 'PRIVATE'},
            'message_type': 'text', 'keyword_id': 3,
        })
    assert result == 10
    details = json.loads(query.call_args.args[1][-1])
    assert details == {'message_type': 'text', 'keyword_id': 3}
    assert 'PRIVATE' not in repr(query.call_args)


def test_fallo_de_auditoria_no_interrumpe_atencion(caplog):
    with ambito_proyecto(1), patch.object(audit, 'consultar_uno', side_effect=RuntimeError('db unavailable')):
        assert audit.record('506', 'whatsapp', 'message_received') is None
    assert 'No se pudo registrar auditoría' in caplog.text


def test_recepcion_y_silencio_no_dependen_del_log_del_chat():
    message = InboundMessage(Channel.WHATSAPP, '506', 'Ana', MessageType.TEXT, text='PRIVATE', message_id='abc')
    from src.infrastructure.repositories import bloqueos_permanentes_repository
    from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository
    with patch.object(audit, 'record', return_value=12) as record, patch.object(
        ConversationLogRepository, 'log_inbound', return_value=False
    ), patch.object(bloqueos_permanentes_repository, 'esta_bloqueado', return_value=True):
        assert ConversationOrchestrator().handle(message) == []
    assert [c.args[2] for c in record.call_args_list] == ['message_received', 'decision_blocked']
    assert record.call_args_list[0].kwargs['dedup_key'] == 'message:abc'
    assert 'PRIVATE' not in repr(record.call_args_list)


def test_envio_aceptado_o_fallido_no_guarda_contenido():
    with patch.object(audit, 'record') as record:
        assert _observed_send('whatsapp', '506', 'text', lambda: 'accepted') == 'accepted'
        assert [c.args[2] for c in record.call_args_list] == ['send_attempt', 'send_accepted']
    with patch.object(audit, 'record') as record:
        try:
            _observed_send('whatsapp', '506', 'image', MagicMock(side_effect=ValueError('PRIVATE_ERROR')))
        except ValueError:
            pass
        assert [c.args[2] for c in record.call_args_list] == ['send_attempt', 'send_failed']
        assert 'PRIVATE_ERROR' not in repr(record.call_args_list)


def test_contexto_se_propaga_sin_filtrarse_entre_peticiones():
    with audit.scope(correlation_id='message:123', actor={'kind': 'business_account'}):
        with audit.scope(source='worker'):
            assert audit.context()['correlation_id'] == 'message:123'
        assert 'source' not in audit.context()
    assert audit.context() == {}


def test_publicacion_propaga_referencia_sin_copiar_argumentos_del_chat():
    from src.infrastructure.tasks import celery_app as tasks
    headers = {"id": "task:123", "task": "send_single_message", "retries": 0}
    body = (("whatsapp", "506", "PRIVATE_MESSAGE"), {}, {})
    with ambito_proyecto(1), audit.scope(correlation_id="provider:123", event_id=42):
        tasks._propagar_proyecto(headers=headers, body=body)
    assert headers["conversation_audit"]["event_id"] == 42
    with patch.object(audit, 'record') as record:
        tasks._auditar_tarea_publicada(headers=headers, body=body)
    assert record.call_args.args[2] == "task_scheduled"
    assert record.call_args.kwargs['metadata']['task_id'] == 'task:123'
    assert 'PRIVATE_MESSAGE' not in repr(record.call_args)


def test_purga_usa_meses_calendario_sin_tocar_el_chat():
    with patch.object(audit, 'ejecutar', return_value=5) as execute:
        assert audit.purge() == 5
    assert "INTERVAL '3 months'" in execute.call_args.args[0]
    assert 'conversation_messages' not in execute.call_args.args[0]


def test_hora_del_proveedor_se_distingue_de_la_recepcion_sin_guardar_payload():
    message = InboundMessage(Channel.WHATSAPP, '506', 'Asesor', MessageType.TEXT,
                             text='PRIVATE', from_me=True, message_id='owner:1',
                             raw_payload={'data': {'messages': {'messageTimestamp': 1792841113, 'messageBody': 'PRIVATE'}}})
    with patch.object(audit, 'record') as record, patch.object(ConversationOrchestrator, '_handle_audited', return_value=[]):
        ConversationOrchestrator().handle(message)
    assert record.call_args.args[2] == 'human_message'
    assert record.call_args.kwargs['metadata']['provider_sent_at'] == '2026-10-24T11:25:13+00:00'
    assert 'PRIVATE' not in repr(record.call_args)
