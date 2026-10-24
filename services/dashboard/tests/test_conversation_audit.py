"""Auditoría real en Postgres: supervivencia, causalidad y privacidad."""
import io
import json
import zipfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.db import pool
from src.services import diagnostico_conversacion as diagnostic, trazabilidad
from src.services import conversation_audit_report as report
from src.services.conversation_audit import actor_from_request

CLIENT = '50672757453'
PROJECT = {'id': 1, 'nombre': 'Escuela', 'zona_horaria': 'America/Costa_Rica'}


def append(kind, dedup=None, metadata=None):
    return pool.consultar_uno(
        "SELECT conversation_audit_append(1,'whatsapp',%s,%s,'test','{}',NULL,NULL,%s,%s::jsonb) AS id",
        (CLIENT, kind, dedup, json.dumps(metadata or {})),
    )['id']


def block(reason, expires=None):
    pool.ejecutar(
        "INSERT INTO users_blocked(proyecto_id,user_id,reason,expires_at) VALUES (1,%s,%s,%s) "
        "ON CONFLICT(proyecto_id,user_id) DO UPDATE SET reason=EXCLUDED.reason,expires_at=EXCLUDED.expires_at",
        ('whatsapp:' + CLIENT, reason, expires),
    )


def test_renovacion_y_silencio_sobreviven_al_borrado_sin_texto_del_chat():
    block('Flujo keyword TEXTO_SECRETO')
    block('Intervención de un asesor humano')
    pool.ejecutar("INSERT INTO conversation_messages(client_id,canal,direction,author,text) VALUES (%s,'whatsapp','inbound','cliente','MENSAJE_PRIVADO')", (CLIENT,))
    trazabilidad.eliminar_conversacion(1, CLIENT, 'whatsapp')
    append('message_received', 'incoming:2')
    append('decision_blocked')
    data = diagnostic.datos(PROJECT, CLIENT, 'whatsapp')
    assert data['messages'] == []
    assert [r['event_type'] for r in data['audit']] == [
        'block_created', 'block_updated', 'history_deleted', 'message_received', 'decision_blocked']
    assert data['audit'][1]['metadata']['before']['reason_code'] == 'keyword'
    silence = data['silences'][0]
    assert silence['metadata']['blocks'][0]['audit_event_id'] == data['audit'][1]['id']
    assert 'Intervención de un asesor humano' in silence['description']
    serialized = json.dumps(data, default=str)
    assert 'MENSAJE_PRIVADO' not in serialized
    assert 'TEXTO_SECRETO' not in serialized
    assert data['block_state'][0]['active'] is True
    with zipfile.ZipFile(diagnostic.crear_zip(PROJECT, CLIENT, 'whatsapp')) as archive:
        html = archive.read('reporte.html').decode()
        assert html.index('Auditoría descriptiva') < html.index('Cronología del chat')
        assert 'America/Costa_Rica' in html
        assert archive.read('diagnostico.json')


def test_actor_dashboard_y_admin_real_se_conservan_en_mutaciones():
    @actor_from_request
    def mutate(usuario):
        block('Bloqueado por comando')
        pool.ejecutar('DELETE FROM users_blocked WHERE proyecto_id=1 AND user_id=%s', ('whatsapp:' + CLIENT,))
    mutate(usuario={'id': 7, 'usuario': 'asesora', 'admin_real_id': 9})
    rows = report.data(PROJECT, CLIENT, 'whatsapp')['audit']
    assert [r['event_type'] for r in rows] == ['block_created', 'block_removed']
    assert all(r['actor']['username'] == 'asesora' and r['actor']['real_admin_id'] == 9 for r in rows)
    block('Flujo de publicidad')
    assert report.data(PROJECT, CLIENT, 'whatsapp')['audit'][-1]['actor'] == {'kind': 'system'}


def test_vencimiento_distingue_fecha_limite_y_fecha_de_deteccion():
    block('Ingreso a grupo', datetime(2020, 1, 1))
    pool.ejecutar('DELETE FROM users_blocked WHERE proyecto_id=1 AND user_id=%s', ('whatsapp:' + CLIENT,))
    rows = report.data(PROJECT, CLIENT, 'whatsapp')['audit']
    event = rows[-1]
    assert event['event_type'] == 'block_expired'
    assert event['metadata']['before']['expires_at'].startswith('2020-01-01')
    assert event['occurred_at'].year > 2020


def test_bloqueo_permanente_tiene_creacion_retiro_y_responsable():
    pool.ejecutar("INSERT INTO bloqueos_permanentes(proyecto_id,canal,numero,creado_por) VALUES(1,'whatsapp',%s,'dueno')", (CLIENT,))
    append('decision_blocked')
    data = report.data(PROJECT, CLIENT, 'whatsapp')
    assert data['block_state'][0]['actor']['username'] == 'dueno'
    assert data['silences'][0]['metadata']['blocks'][0]['block_kind'] == 'permanent'
    pool.ejecutar('DELETE FROM bloqueos_permanentes WHERE proyecto_id=1 AND numero=%s', (CLIENT,))
    assert report.data(PROJECT, CLIENT, 'whatsapp')['audit'][-1]['event_type'] == 'block_removed'


def test_reintentos_no_duplican_evento_con_clave_estable():
    append('message_received', 'provider:123')
    append('message_received', 'provider:123')
    assert len(report.data(PROJECT, CLIENT, 'whatsapp')['audit']) == 1
    assert diagnostic.datos({**PROJECT, 'id': 999}, CLIENT, 'whatsapp') is None


def test_tres_meses_calendario_y_antecedente_fuera_de_cobertura():
    block('Flujo de publicidad')
    pool.ejecutar("UPDATE conversation_audit SET occurred_at=clock_timestamp()-INTERVAL '3 months 1 second'")
    # La misma condición que usa la limpieza diaria; no toca el bloqueo vigente.
    pool.ejecutar("DELETE FROM conversation_audit WHERE occurred_at < clock_timestamp()-INTERVAL '3 months'")
    append('decision_blocked')
    data = report.data(PROJECT, CLIENT, 'whatsapp')
    assert data['silences'][0]['metadata']['blocks'][0]['reason_code'] == 'advertising'
    assert data['silences'][0]['metadata']['blocks'][0]['origin_event_available'] is False
    assert data['block_state'][0]['active'] is True
    cutoff = pool.consultar_uno("SELECT TIMESTAMPTZ '2026-05-31 10:11:12+00' - INTERVAL '3 months' AS cutoff")['cutoff']
    assert cutoff.day == 28 and cutoff.month == 2


def test_fechas_completas_con_segundos_y_zona_horaria():
    assert report.local_time(datetime(2026, 10, 24, 11, 25, 14, tzinfo=timezone.utc), ZoneInfo('America/Costa_Rica')) == '24/10/2026 05:25:14 -0600'


def test_motivo_libre_de_reporte_no_se_copia():
    block('El cliente dice MI_INFORMACION_PRIVADA')
    serialized = json.dumps(report.data(PROJECT, CLIENT, 'whatsapp'), default=str)
    assert 'MI_INFORMACION_PRIVADA' not in serialized
    assert 'handoff_or_legacy' in serialized


def test_el_admin_puede_descargar_solo_auditoria_despues_de_borrar(sesion_admin):
    from tests.conftest import token_csrf
    from src.services import usuarios, clientes_whatsapp
    account = usuarios.crear('auditoria_negocio', 'clave-segura-123', 'cliente', debe_cambiar=False)
    clientes_whatsapp.vincular_cuenta(1, account['id'])
    block('Flujo de publicidad')
    sesion_admin.post(f"/admin/usuarios/{account['id']}/entrar", data={'csrf': token_csrf(sesion_admin)})
    response = sesion_admin.post(f'/conversaciones/whatsapp/{CLIENT}/diagnostico', data={'csrf': token_csrf(sesion_admin)})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        data = json.loads(archive.read('diagnostico.json'))
    assert data['messages'] == [] and data['audit']
    assert pool.consultar_uno('SELECT COUNT(*) AS n FROM diagnostico_descargas')['n'] == 1


def test_referencia_explicita_enlaza_las_trazas_con_el_evento():
    pool.ejecutar(
        "INSERT INTO conversation_messages(client_id,canal,direction,author,entrada) "
        "VALUES (%s,'whatsapp','internal','tool','{\"audit_correlation_id\":\"provider:123\"}')", (CLIENT,))
    event = pool.consultar_uno(
        "SELECT conversation_audit_append(1,'whatsapp',%s,'decision','bot','{}','provider:123') AS id", (CLIENT,))
    data = diagnostic.datos(PROJECT, CLIENT, 'whatsapp')
    assert data['audit'][0]['id'] == event['id']
    assert data['audit'][0]['related_trace_ids'] == [data['internal_events'][0]['id']]


def test_fallo_del_almacen_de_auditoria_no_impide_bloquear():
    # La transacción se revierte íntegra: no se altera el esquema de otros tests.
    with pool.conexion(autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute('ALTER TABLE conversation_audit RENAME TO audit_temporarily_unavailable')
                cur.execute("INSERT INTO users_blocked(proyecto_id,user_id,reason) VALUES(1,%s,'Flujo de publicidad')", ('whatsapp:' + CLIENT,))
                cur.execute('SELECT reason FROM users_blocked WHERE proyecto_id=1 AND user_id=%s', ('whatsapp:' + CLIENT,))
                assert cur.fetchone()[0] == 'Flujo de publicidad'
                assert any('conversation_audit_' in warning for warning in conn.notices)
        finally:
            conn.rollback()


def test_semilla_de_bloqueos_previos_no_inventa_fecha_de_creacion():
    from pathlib import Path
    block('Intervención de un asesor humano')
    pool.ejecutar("UPDATE users_blocked SET blocked_at='2020-01-01 00:00:00' WHERE proyecto_id=1")
    migration = (Path(__file__).parents[1] / 'src/db/migrations/036_auditoria_conversaciones.sql').read_text()
    baseline = migration.split('-- Observaciones de estado, no reconstrucciones de hechos anteriores al despliegue.')[1]
    pool.ejecutar(baseline)
    observed = report.data(PROJECT, CLIENT, 'whatsapp')['audit'][-1]
    assert observed['event_type'] == 'block_observed'
    assert observed['occurred_at'].year > 2020
    assert observed['metadata']['after']['stored_initial_at'].startswith('2020-01-01')
    assert observed['metadata']['historical_origin_unknown'] is True
