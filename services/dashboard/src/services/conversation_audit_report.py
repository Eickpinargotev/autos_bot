"""Lectura y presentación de auditoría sin acceder al contenido del chat."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.db import pool

EVENTS = {
    'advertising_detected': 'Se reconoció una plantilla configurada del flujo de publicidad.',
    'flow_unavailable': 'El flujo no pudo iniciarse por falta de configuración o mensajes.',
    'message_received': 'El cliente envió un mensaje; el sistema lo recibió.',
    'group_event': 'Se recibió un evento de pertenencia al grupo.',
    'human_message': 'Se recibió un mensaje enviado desde la cuenta del negocio.',
    'human_intervention': 'Un asesor humano tomó la conversación.',
    'keyword_detected': 'Se reconoció una palabra clave configurada.',
    'decision': 'El sistema seleccionó una rama de atención.',
    'decision_blocked': 'La IA no respondió porque encontró un bloqueo vigente.',
    'provider_event': 'El proveedor notificó un evento.',
    'block_created': 'Se creó un bloqueo.',
    'block_updated': 'Se renovó el bloqueo o cambió su motivo.',
    'block_removed': 'Se levantó un bloqueo.',
    'block_expired': 'El sistema detectó el vencimiento y retiró el bloqueo.',
    'block_observed': 'Se observó un bloqueo anterior al inicio de la auditoría; no se conoce su origen histórico.',
    'history_deleted': 'Se eliminó el historial del chat; los bloqueos y la auditoría se conservaron.',
    'runtime_cleanup_started': 'Se inició la limpieza de la memoria y de las tareas del chat.',
    'runtime_cleanup_completed': 'Se completó la limpieza de la memoria y de las tareas del chat.',
    'runtime_cleanup_failed': 'Falló la limpieza de la memoria o de las tareas del chat.',
    'send_attempt': 'Se intentó enviar un mensaje al proveedor.',
    'send_accepted': 'El proveedor aceptó el envío; esto no confirma su entrega ni lectura.',
    'send_failed': 'Falló un intento de envío al proveedor.',
    'send_retry': 'Se reintentará el envío al proveedor.',
    'task_scheduled': 'Se publicó una tarea programada.',
    'task_started': 'Comenzó la ejecución de una tarea.',
    'task_completed': 'Terminó la ejecución de una tarea.',
    'task_failed': 'Falló la ejecución de una tarea.',
    'task_retry_requested': 'Se solicitó reintentar una tarea.',
    'task_cancel_requested': 'Se solicitó cancelar una tarea; podría estar ya en ejecución.',
    'task_skipped': 'Se omitió una tarea porque ya no se cumplían sus condiciones.',
}
REASONS = {
    'agent_handoff': 'Derivación a un asesor desde el agente',
    'scheduled_handoff': 'Derivación a un asesor desde una tarea programada',
    'media_review': 'Archivos reiterados que requieren revisión de un asesor',
    'human_intervention': 'Intervención de un asesor humano',
    'advertising': 'Flujo de publicidad', 'keyword': 'Flujo de palabra clave',
    'group_join': 'Ingreso al grupo', 'command': 'Comando de bloqueo',
    'manual_permanent': 'Bloqueo permanente desde el dashboard',
    'missing_group_link': 'Falta el enlace del grupo en la configuración',
    'missing_ad_fields': 'Faltan datos para programar publicidad',
    'handoff_or_legacy': 'Derivación a un asesor o bloqueo anterior sin causa estructurada',
}
BRANCHES = {
    'temporary_block': 'bloqueo temporal', 'permanent_block': 'bloqueo permanente',
    'advertising': 'flujo de publicidad', 'keyword': 'flujo de palabra clave',
    'human_intervention': 'intervención humana', 'advertising_wait': 'espera del flujo de publicidad',
    'buffer_queued': 'mensaje en espera para procesar junto con los mensajes recientes',
    'link_auto_reply': 'respuesta automática a un enlace',
    'sticker_ignored': 'sticker ignorado', 'media_auto_reply': 'respuesta automática a un archivo',
}


def local_time(value, zone):
    if not value:
        return None
    instant = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(zone).strftime('%d/%m/%Y %H:%M:%S %z')


def _describe(row):
    text = EVENTS.get(row['event_type'], 'Se registró un evento operativo.')
    data = row.get('metadata') or {}
    statuses = {'accepted': 'Mensaje aceptado para procesar', 'duplicate': 'Entrega duplicada del mismo mensaje, ignorada',
                'bot_echo': 'Eco de una salida del bot, ignorado', 'group_join': 'Ingreso al grupo',
                'group_leave': 'Salida del grupo', 'human_intervention': 'Intervención humana'}
    if data.get('status') in statuses:
        text += ' ' + statuses[data['status']] + '.'
    if data.get('branch'):
        text += ' Rama: ' + BRANCHES.get(data['branch'], data['branch']) + '.'
    state = data.get('after') or data.get('before') or {}
    if state.get('reason_code'):
        text += ' Motivo: ' + REASONS.get(state['reason_code'], 'Origen no determinado') + '.'
    if row['event_type'] == 'decision_blocked':
        for block in data.get('blocks', []):
            if block.get('active'):
                text += ' Motivo: ' + REASONS.get(block.get('reason_code'), 'Origen no determinado') + '.'
                if block.get('audit_event_id'):
                    text += f" Referencia de bloqueo: evento {block['audit_event_id']}."
                if not block.get('origin_event_available'):
                    text += ' El evento de origen no está disponible dentro del período conservado.'
    return text


def data(project, client_id, channel):
    project_id = int(project['id'])
    rows = pool.consultar(
        "SELECT * FROM conversation_audit WHERE proyecto_id=%s AND canal=%s AND client_id=%s "
        "AND occurred_at >= clock_timestamp() - INTERVAL '3 months' ORDER BY occurred_at,id",
        (project_id, channel, client_id),
    )
    coverage = pool.consultar_uno(
        "SELECT started_at, clock_timestamp() - INTERVAL '3 months' AS retention_start FROM conversation_audit_coverage WHERE singleton"
    ) or {}
    blocks = (pool.consultar_uno('SELECT conversation_audit_blocks(%s,%s,%s) AS blocks',
                               (project_id, channel, client_id)) or {}).get('blocks') or []
    zone_name = project.get('zona_horaria') or 'UTC'
    warnings = ['Los sucesos anteriores al inicio de la auditoría no se pueden reconstruir.',
                'stored_initial_at es la fecha almacenada inicialmente; no demuestra cuándo se renovó un bloqueo.']
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
        warnings.append('Zona horaria inválida; se muestran las fechas en UTC.')
        zone_name = 'UTC'
    ids = {row['id'] for row in rows}
    for row in rows:
        row['occurred_at_local'] = local_time(row['occurred_at'], zone)
        row['recorded_at_local'] = local_time(row['recorded_at'], zone)
        row['timezone'] = zone_name
        provider_time = (row.get('metadata') or {}).get('provider_sent_at')
        if provider_time:
            row['provider_sent_at_local'] = local_time(provider_time, zone)
        for block in (row.get('metadata') or {}).get('blocks', []):
            block['origin_event_available'] = block.get('audit_event_id') in ids
        row['description'] = _describe(row)
    for block in blocks:
        block['reason_description'] = REASONS.get(block.get('reason_code'), 'Origen no determinado')
        block['origin_event_available'] = block.get('audit_event_id') in ids
        block['expires_at_local'] = local_time(block.get('expires_at'), zone)
    return {
        'audit': rows,
        'block_state': blocks,
        'silences': [r for r in rows if r['event_type'] == 'decision_blocked'],
        'audit_coverage': {**coverage, 'retention': '3 calendar months per event',
                           'timezone': zone_name, 'warnings': warnings},
    }
