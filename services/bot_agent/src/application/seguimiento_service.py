"""Seguimiento por cliente y resumen mensual (tablas en LOGs_Autos_Mensajes).

Qué registra:
- Por cliente: conversaciones iniciadas (ventana de 24h desde el primer
  mensaje del cliente), primera/última interacción, derivaciones a asesor,
  costo acumulado del LLM (tokens de gpt-5.4-mini) y un historial simplificado
  del chat (hora + autor: cliente/bot/dueño + texto).
- Por mes: mensajes del bot, mensajes del cliente y costo total.

Diseño robusto (mismo espíritu que el buffer de mensajes):
1. Cada evento se ACUMULA primero en Redis con operaciones atómicas
   (RPUSH/HINCRBY). El camino caliente no necesita candados y una caída de
   NocoDB no pierde datos: quedan en el buffer.
2. El volcado (flush) toma un candado no bloqueante por fila, lee la fila de
   NocoDB, aplica los deltas y escribe. Solo tras escribir con éxito descuenta
   del buffer EXACTAMENTE lo aplicado (HINCRBY negativo / LTRIM), preservando
   lo que otro proceso haya sumado entre medias.
3. El flush corre tras cada mensaje y, como red de seguridad, la tarea
   periódica `flush_seguimiento_pendiente` de Celery re-intenta lo pendiente.

El costo se maneja como entero en micro-USD: sumar enteros no acumula error de
punto flotante; el campo decimal legible se deriva del entero al escribir.
"""

import json
from datetime import datetime, timedelta
from typing import Any

from src.application.buffer_service import redis_client, scoped_key
from src.core.config import settings
from src.domain.entities import Channel
from src.infrastructure.repositories.nocodb_retention import parse_timestamp
from src.infrastructure.repositories.seguimiento_repository import SeguimientoRepository

HISTORIAL_PREFIX = "seguimiento_historial"
DELTAS_PREFIX = "seguimiento_deltas"
LOCK_PREFIX = "seguimiento_lock"
MES_DELTAS_PREFIX = "resumen_mensual_deltas"
MES_LOCK_PREFIX = "resumen_mensual_lock"

# Si un buffer queda huérfano (p. ej. NocoDB caído días), se conserva un mes
# para que la tarea periódica pueda volcarlo; después expira solo.
_BUFFER_TTL_SECONDS = 30 * 24 * 3600
_LOCK_TTL_SECONDS = 30

_CAMPOS_DELTA = ("costo_microusd", "tokens_entrada", "tokens_salida", "derivaciones")
_MICRO = 1_000_000


# --- Costo de tokens ---------------------------------------------------------

def costo_microusd(prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> int:
    """Costo en micro-USD de una llamada, según los precios configurados."""
    no_cacheados = max(int(prompt_tokens) - int(cached_tokens), 0)
    usd = (
        no_cacheados * settings.OPENAI_PRICE_INPUT_USD_PER_1M
        + int(cached_tokens) * settings.OPENAI_PRICE_CACHED_INPUT_USD_PER_1M
        + int(completion_tokens) * settings.OPENAI_PRICE_OUTPUT_USD_PER_1M
    ) / 1_000_000
    return round(usd * _MICRO)


# --- Registro de eventos (camino caliente, solo Redis) -----------------------

def registrar_uso_llm(client_id: str, canal: Channel | str, usage: Any) -> None:
    """Acumula el costo/tokens de una llamada al LLM (objeto `usage` de OpenAI)."""
    try:
        if usage is None:
            return
        # Solo enteros de verdad: un mock u objeto raro (p. ej. MagicMock, cuyo
        # int() devuelve 1) no debe contaminar los contadores.
        prompt = _entero_estricto(getattr(usage, "prompt_tokens", 0))
        completion = _entero_estricto(getattr(usage, "completion_tokens", 0))
        detalles = getattr(usage, "prompt_tokens_details", None)
        cached = _entero_estricto(getattr(detalles, "cached_tokens", 0))
        if not prompt and not completion:
            return
        micro = costo_microusd(prompt, cached, completion)

        pipe = redis_client.pipeline()
        if client_id and canal:
            key = scoped_key(DELTAS_PREFIX, canal, client_id)
            pipe.hincrby(key, "costo_microusd", micro)
            pipe.hincrby(key, "tokens_entrada", prompt)
            pipe.hincrby(key, "tokens_salida", completion)
            pipe.expire(key, _BUFFER_TTL_SECONDS)
        mes_key = _mes_key(_mes_actual())
        pipe.hincrby(mes_key, "costo_microusd", micro)
        pipe.hincrby(mes_key, "tokens_entrada", prompt)
        pipe.hincrby(mes_key, "tokens_salida", completion)
        pipe.expire(mes_key, _BUFFER_TTL_SECONDS)
        pipe.execute()
    except Exception as e:
        print(f"Error registrando uso de LLM en seguimiento: {e}")


def registrar_mensaje(
    client_id: str,
    canal: Channel | str,
    autor: str,
    texto: str,
    nombre: str = "",
) -> None:
    """Registra un mensaje del chat (autor: 'cliente', 'bot' o 'dueño') y vuelca."""
    try:
        if not client_id or not canal:
            return
        entry = {"hora": _now_iso(), "autor": autor, "texto": texto or ""}
        hist_key = scoped_key(HISTORIAL_PREFIX, canal, client_id)
        deltas_key = scoped_key(DELTAS_PREFIX, canal, client_id)
        pipe = redis_client.pipeline()
        pipe.rpush(hist_key, json.dumps(entry, ensure_ascii=False))
        pipe.expire(hist_key, _BUFFER_TTL_SECONDS)
        if nombre and nombre != "Desconocido":
            pipe.hset(deltas_key, "nombre", nombre)
            pipe.expire(deltas_key, _BUFFER_TTL_SECONDS)
        mes_key = _mes_key(_mes_actual())
        if autor == "cliente":
            pipe.hincrby(mes_key, "mensajes_cliente", 1)
        elif autor == "bot":
            pipe.hincrby(mes_key, "mensajes_bot", 1)
        pipe.expire(mes_key, _BUFFER_TTL_SECONDS)
        pipe.execute()
    except Exception as e:
        print(f"Error registrando mensaje en seguimiento: {e}")
        return

    flush_cliente(client_id, canal)
    flush_mes(_mes_actual())


def registrar_derivacion(client_id: str, canal: Channel | str) -> None:
    """Cuenta una derivación a asesor (reporte + bloqueo) para el cliente."""
    try:
        if not client_id or not canal:
            return
        key = scoped_key(DELTAS_PREFIX, canal, client_id)
        pipe = redis_client.pipeline()
        pipe.hincrby(key, "derivaciones", 1)
        pipe.expire(key, _BUFFER_TTL_SECONDS)
        pipe.execute()
    except Exception as e:
        print(f"Error registrando derivación en seguimiento: {e}")
        return

    flush_cliente(client_id, canal)


# --- Volcado a NocoDB --------------------------------------------------------

def flush_cliente(client_id: str, canal: Channel | str) -> bool:
    """Vuelca los buffers del cliente a su fila de seguimiento_clientes."""
    if not settings.NOCODB_SEGUIMIENTO_CLIENTES_URL:
        return False
    canal_value = _canal_value(canal)
    lock_key = scoped_key(LOCK_PREFIX, canal_value, client_id)
    if not redis_client.set(lock_key, "1", nx=True, ex=_LOCK_TTL_SECONDS):
        # Otro proceso está volcando: los datos quedan en el buffer y los
        # recoge el siguiente flush (o la tarea periódica).
        return False
    try:
        hist_key = scoped_key(HISTORIAL_PREFIX, canal_value, client_id)
        deltas_key = scoped_key(DELTAS_PREFIX, canal_value, client_id)
        entries_raw = redis_client.lrange(hist_key, 0, -1) or []
        deltas = redis_client.hgetall(deltas_key) or {}
        if not entries_raw and not _hay_deltas(deltas):
            return True

        entries = []
        for raw in entries_raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    entries.append(parsed)
            except Exception:
                continue

        record = SeguimientoRepository.find_cliente(client_id, canal_value)
        prev = SeguimientoRepository.record_fields(record) if record else {}
        fields = _aplicar_deltas_cliente(prev, entries, deltas)
        if record:
            SeguimientoRepository.update_cliente(SeguimientoRepository.record_id(record), fields)
        else:
            fields.update({"client_id": str(client_id), "canal": canal_value})
            SeguimientoRepository.create_cliente(fields)

        # Éxito: descontar exactamente lo aplicado (lo sumado entre la lectura
        # y este punto sobrevive en el buffer para el siguiente volcado).
        pipe = redis_client.pipeline()
        if entries_raw:
            pipe.ltrim(hist_key, len(entries_raw), -1)
        for campo in _CAMPOS_DELTA:
            valor = _entero(deltas.get(campo))
            if valor:
                pipe.hincrby(deltas_key, campo, -valor)
        if deltas.get("nombre"):
            pipe.hdel(deltas_key, "nombre")
        pipe.execute()
        return True
    except Exception as e:
        print(f"Error volcando seguimiento del cliente {client_id}: {e}")
        return False
    finally:
        redis_client.delete(lock_key)


def flush_mes(mes: str) -> bool:
    """Vuelca los deltas acumulados del mes a su fila de resumen_mensual."""
    if not settings.NOCODB_RESUMEN_MENSUAL_URL:
        return False
    lock_key = f"{MES_LOCK_PREFIX}:{mes}"
    if not redis_client.set(lock_key, "1", nx=True, ex=_LOCK_TTL_SECONDS):
        return False
    try:
        mes_key = _mes_key(mes)
        deltas = redis_client.hgetall(mes_key) or {}
        if not _hay_deltas(deltas):
            return True

        record = SeguimientoRepository.find_mes(mes)
        prev = SeguimientoRepository.record_fields(record) if record else {}
        fields = _aplicar_deltas_mes(prev, deltas)
        if record:
            SeguimientoRepository.update_mes(SeguimientoRepository.record_id(record), fields)
        else:
            fields["mes"] = mes
            SeguimientoRepository.create_mes(fields)

        pipe = redis_client.pipeline()
        for campo, valor in deltas.items():
            entero = _entero(valor)
            if entero:
                pipe.hincrby(mes_key, campo, -entero)
        pipe.execute()
        return True
    except Exception as e:
        print(f"Error volcando resumen mensual {mes}: {e}")
        return False
    finally:
        redis_client.delete(lock_key)


def flush_pendientes() -> int:
    """Red de seguridad periódica: vuelca todos los buffers con datos pendientes.

    Cubre los casos sin flush inline posterior (p. ej. un followup que decidió
    no enviar mensaje: su costo quedó en el buffer) y los volcado fallidos por
    caídas de NocoDB. Devuelve cuántos buffers se intentaron volcar.
    """
    intentos = 0
    vistos: set[tuple[str, str]] = set()
    for prefix in (DELTAS_PREFIX, HISTORIAL_PREFIX):
        for key in redis_client.scan_iter(match=f"{prefix}:*", count=200):
            partes = str(key).split(":", 2)
            if len(partes) != 3:
                continue
            canal, client_id = partes[1], partes[2]
            if (canal, client_id) in vistos:
                continue
            vistos.add((canal, client_id))
            flush_cliente(client_id, canal)
            intentos += 1
    for key in redis_client.scan_iter(match=f"{MES_DELTAS_PREFIX}:*", count=200):
        mes = str(key).split(":", 1)[1]
        flush_mes(mes)
        intentos += 1
    return intentos


# --- Aplicación de deltas (funciones puras, fáciles de testear) --------------

def _aplicar_deltas_cliente(
    prev: dict[str, Any],
    entries: list[dict[str, Any]],
    deltas: dict[str, Any],
) -> dict[str, Any]:
    """Combina la fila previa con los mensajes y deltas pendientes."""
    historial = _historial_previo(prev)
    historial.extend(entries)
    cap = settings.SEGUIMIENTO_HISTORIAL_MAX_MENSAJES
    if cap > 0:
        historial = historial[-cap:]

    conversaciones = _entero(prev.get("conversaciones_iniciadas"))
    inicio_iso = str(prev.get("conversacion_actual_inicio") or "")
    inicio_dt = parse_timestamp(inicio_iso)
    primera = str(prev.get("primera_interaccion") or "")
    ultima = str(prev.get("ultima_interaccion") or "")
    ventana = timedelta(hours=settings.SEGUIMIENTO_VENTANA_CONVERSACION_HORAS)

    for entry in entries:
        if entry.get("autor") != "cliente":
            continue
        hora_iso = str(entry.get("hora") or "")
        hora_dt = parse_timestamp(hora_iso) or datetime.now()
        if inicio_dt is None or (hora_dt - inicio_dt) > ventana:
            conversaciones += 1
            inicio_dt = hora_dt
            inicio_iso = hora_iso
        if not primera:
            primera = hora_iso
        ultima = hora_iso or ultima

    micro = _entero(prev.get("costo_microusd")) + _entero(deltas.get("costo_microusd"))
    fields: dict[str, Any] = {
        "conversaciones_iniciadas": conversaciones,
        "conversacion_actual_inicio": inicio_iso,
        "primera_interaccion": primera,
        "ultima_interaccion": ultima,
        "derivaciones_asesor": _entero(prev.get("derivaciones_asesor")) + _entero(deltas.get("derivaciones")),
        "costo_microusd": micro,
        "costo_acumulado_usd": round(micro / _MICRO, 6),
        "tokens_entrada": _entero(prev.get("tokens_entrada")) + _entero(deltas.get("tokens_entrada")),
        "tokens_salida": _entero(prev.get("tokens_salida")) + _entero(deltas.get("tokens_salida")),
        "historial": json.dumps({"mensajes": historial}, ensure_ascii=False),
    }
    nombre = str(deltas.get("nombre") or "").strip()
    if nombre:
        fields["nombre"] = nombre
    return fields


def _aplicar_deltas_mes(prev: dict[str, Any], deltas: dict[str, Any]) -> dict[str, Any]:
    micro = _entero(prev.get("costo_microusd")) + _entero(deltas.get("costo_microusd"))
    return {
        "mensajes_bot": _entero(prev.get("mensajes_bot")) + _entero(deltas.get("mensajes_bot")),
        "mensajes_cliente": _entero(prev.get("mensajes_cliente")) + _entero(deltas.get("mensajes_cliente")),
        "costo_microusd": micro,
        "costo_total_usd": round(micro / _MICRO, 6),
        "tokens_entrada": _entero(prev.get("tokens_entrada")) + _entero(deltas.get("tokens_entrada")),
        "tokens_salida": _entero(prev.get("tokens_salida")) + _entero(deltas.get("tokens_salida")),
        "actualizado_en": _now_iso(),
    }


# --- Helpers -----------------------------------------------------------------

def _historial_previo(prev: dict[str, Any]) -> list[dict[str, Any]]:
    raw = prev.get("historial")
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except Exception:
            return []
    elif isinstance(raw, dict):
        data = raw
    else:
        return []
    mensajes = data.get("mensajes")
    return [m for m in mensajes if isinstance(m, dict)] if isinstance(mensajes, list) else []


def _hay_deltas(deltas: dict[str, Any]) -> bool:
    if str(deltas.get("nombre") or "").strip():
        return True
    return any(_entero(deltas.get(campo)) for campo in set(_CAMPOS_DELTA) | {"mensajes_bot", "mensajes_cliente"})


def _entero_estricto(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _entero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _canal_value(canal: Channel | str) -> str:
    return canal.value if isinstance(canal, Channel) else str(canal)


def _mes_actual() -> str:
    return datetime.now().astimezone().strftime("%Y-%m")


def _mes_key(mes: str) -> str:
    return f"{MES_DELTAS_PREFIX}:{mes}"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
