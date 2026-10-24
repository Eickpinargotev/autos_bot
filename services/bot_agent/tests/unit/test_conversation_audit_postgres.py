"""Integración optativa, exclusivamente en una base desechable terminada en _test."""
import os
from datetime import datetime
from uuid import uuid4

import psycopg2
import pytest

from src.application.project_context import ambito_proyecto
from src.infrastructure.repositories import conversation_audit_repository as audit
from src.infrastructure.repositories import postgres_conn, postgres_user_repo
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository
import src.infrastructure.repositories.conversation_log_repository as chat_log


@pytest.mark.skipif(not os.getenv('AUDIT_TEST_DSN'), reason='Requiere PostgreSQL desechable migrado')
def test_repositorios_reales_propagan_contexto_y_conservan_auditoria(monkeypatch):
    connection = psycopg2.connect(os.environ['AUDIT_TEST_DSN'])
    assert connection.info.dbname.endswith('_test'), 'Prohibido usar una base de negocio'
    connection.autocommit = True
    project = None
    try:
        with connection.cursor() as cursor:
            reference = uuid4().hex
            cursor.execute("INSERT INTO clientes_whatsapp(nombre,slug,webhook_token) VALUES ('Audit integration',%s,%s) RETURNING id", (reference, reference))
            project = cursor.fetchone()[0]
        monkeypatch.setattr(postgres_conn._SharedConnection, 'get', lambda: connection)
        monkeypatch.setattr(postgres_user_repo, 'run_query', postgres_conn.run_query)
        monkeypatch.setattr(audit, 'consultar_uno', postgres_conn.consultar_uno)
        monkeypatch.setattr(chat_log, 'ejecutar', postgres_conn.ejecutar)
        with ambito_proyecto(project), audit.scope(correlation_id='provider:123', source='whatsapp'):
            received = audit.record('506', 'whatsapp', 'message_received', dedup_key='provider:123')
            with audit.scope(event_id=received, actor={'kind': 'business_account'}):
                repo = postgres_user_repo.PostgresUserRepo()
                repo.block_user('506', reason='Intervención de un asesor humano', days=12, channel='whatsapp')
                assert repo.is_blocked('506', channel='whatsapp') is True
                audit.record('506', 'whatsapp', 'decision_blocked')
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO conversation_messages(proyecto_id,client_id,canal,direction,author,text) VALUES (%s,'506','whatsapp','inbound','cliente','PRIVATE_CHAT')", (project,))
                assert ConversationLogRepository.delete_conversation('506', 'whatsapp') is True
                repo.unblock_user('506', channel='whatsapp')
                with connection.cursor() as cursor:
                    cursor.execute("SELECT event_type, actor, parent_event_id, metadata FROM conversation_audit WHERE proyecto_id=%s ORDER BY id", (project,))
                    events = cursor.fetchall()
                assert [row[0] for row in events] == ['message_received', 'block_created', 'decision_blocked', 'history_deleted', 'block_removed']
                assert events[1][1] == {'kind': 'business_account'}
                assert events[1][2] == received
                assert events[2][3]['blocks'][0]['reason_code'] == 'human_intervention'
                assert 'PRIVATE_CHAT' not in repr(events)
                # La configuración SQL local a la sentencia no queda en la conexión.
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_setting('app.conversation_audit',true)")
                    assert cursor.fetchone()[0] in ('', None)
                repo.block_user('506', reason='Ingreso a grupo', channel='whatsapp')
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE users_blocked SET expires_at=%s WHERE proyecto_id=%s AND user_id='whatsapp:506'", (datetime(2020, 1, 1), project))
                assert repo.is_blocked('506', channel='whatsapp') is False
                with connection.cursor() as cursor:
                    cursor.execute('SELECT event_type FROM conversation_audit WHERE proyecto_id=%s ORDER BY id DESC LIMIT 1', (project,))
                    assert cursor.fetchone()[0] == 'block_expired'
    finally:
        if project:
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM clientes_whatsapp WHERE id=%s', (project,))
        connection.close()
