"""Seguimiento por cliente y resumen mensual (tablas en Postgres).

Qué registra:
- Por cliente: conversaciones iniciadas (ventana de 24h desde el primer
  mensaje del cliente), primera/última interacción, derivaciones a asesor,
  costo acumulado del LLM (tokens de gpt-5.4-mini) y un historial simplificado
  del chat (hora + autor: cliente/bot/dueño + texto).
- Por mes: mensajes del bot, mensajes del cliente y costo total.

Diseño robusto (mismo espíritu que el buffer de mensajes):
1. Cada evento se ACUMULA primero en Redis con operaciones atómicas
   (RPUSH/HINCRBY). El camino caliente no necesita candados y una caída de la
   base no pierde datos: quedan en el buffer.
2. El volcado (flush) toma un candado no bloqueante por fila, lee la fila de
   Postgres, aplica los deltas y escribe. Solo tras escribir con éxito descuenta
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
from src.infrastructure.repositories import billing_repository, precios_repository
from src.infrastructure.repositories.fechas import parse_timestamp
from src.infrastructure.repositories.postgres_conn import ejecutar
from src.infrastructure.repositories.seguimiento_repository import SeguimientoRepository

HISTORIAL_PREFIX = "seguimiento_historial"
DELTAS_PREFIX = "seguimiento_deltas"
LOCK_PREFIX = "seguimiento_lock"
MES_DELTAS_PREFIX = "resumen_mensual_deltas"
MES_LOCK_PREFIX = "resumen_mensual_lock"

# Si un buffer queda huérfano (p. ej. la base caída días), se conserva un mes
# para que la tarea periódica pueda volcarlo; después expira solo.
_BUFFER_TTL_SECONDS = 30 * 24 * 3600
_LOCK_TTL_SECONDS = 30

_CAMPOS_DELTA = ("costo_microusd", "tokens_entrada", "tokens_salida", "derivaciones")
_MICRO = 1_000_000


# --- Costo de tokens ---------------------------------------------------------

def costo_microusd(
    prompt_tokens: int,
    cached_tokens: int,
    completion_tokens: int,
    modelo: str = "",
) -> int:
    """Costo en micro-USD de una llamada, con el precio del modelo que la atendió.

    El `modelo` es obligatorio en la práctica: desde que cada tipo de tarea usa
    el suyo, cobrar todo al mismo precio falsea la factura en los dos sentidos
    (una decisión del auxiliar cuesta 10 veces menos que una del supervisor).
    Se deja con valor por defecto solo para no romper llamadas antiguas, que
    caen en el precio de respaldo.
    """
    precio = precios_repository.precio_de(modelo or settings.OPENAI_MODEL)
    no_cacheados = max(int(prompt_tokens) - int(cached_tokens), 0)
    usd = (
        no_cacheados * precio.entrada_usd_1m
        + int(cached_tokens) * precio.cacheado_usd_1m
        + int(completion_tokens) * precio.salida_usd_1m
    ) / 1_000_000
    return round(usd * _MICRO)


def costo_audio_microusd(segundos: int, modelo: str = "") -> int:
    """Costo en micro-USD de transcribir N segundos de audio.

    Se cobra por minuto, pero se mide en SEGUNDOS y sin redondear hacia arriba:
    una nota de voz de 8 segundos no puede facturarse como un minuto. El
    redondeo final a micro-USD es el único, y ahí sí a entero, porque todo el
    dinero del sistema es entero en micro-USD.
    """
    precio = precios_repository.precio_de(modelo or settings.OPENAI_MODEL_TRANSCRIPCION)
    usd = (max(int(segundos), 0) / 60.0) * precio.audio_usd_minuto
    return round(usd * _MICRO)


# --- Registro de eventos (camino caliente, solo Redis) -----------------------

def registrar_uso_llm(
    client_id: str,
    canal: Channel | str,
    usage: Any,
    origen: str = "agente",
    modelo: str = "",
) -> None:
    """Acumula el costo/tokens de una llamada al LLM (objeto `usage` de OpenAI).

    Hace dos cosas con el mismo dato:
    1. Suma el delta en Redis, que alimenta el seguimiento por cliente y el
       resumen mensual (camino tolerante a caídas, se vuelca después).
    2. Anota el hecho facturable en `uso_eventos`, que es lo que el dashboard
       suma en vivo. `origen` dice qué parte del bot gastó (agente, recordatorio,
       rag, publicidad) para poder desglosarlo.

    El `modelo` lo pasa quien hizo la llamada, no se adivina aquí: cada tarea usa
    el suyo y su precio difiere hasta 10x. Tomarlo de la configuración global
    —como se hacía cuando había un solo modelo— le cobraría al cliente el precio
    del supervisor por una decisión del auxiliar.
    """
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
        modelo_usado = modelo or settings.OPENAI_MODEL
        micro = costo_microusd(prompt, cached, completion, modelo_usado)

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
        return

    # El libro mayor de facturación se escribe directo a Postgres (no pasa por
    # el buffer): el dashboard lo suma en vivo y debe reflejar el consumo al
    # instante. Su propio manejo de errores impide que un fallo aquí afecte
    # la respuesta al cliente.
    billing_repository.registrar_evento_llm(
        client_id=client_id,
        canal=canal,
        origen=origen,
        modelo=modelo_usado,
        tokens_entrada=prompt,
        tokens_cacheados=cached,
        tokens_salida=completion,
        costo_real_microusd=micro,
    )


def registrar_uso_audio(
    client_id: str,
    canal: Channel | str,
    segundos: int,
    modelo: str = "",
    origen: str = "transcripcion",
) -> int:
    """Anota la transcripción de una nota de voz. Devuelve el costo real.

    Suma al mismo buffer de costo por cliente que el LLM (el cliente ve UN
    total), pero en el libro mayor va con categoría propia para que el panel
    pueda desglosar tokens y audios por separado.
    """
    micro = costo_audio_microusd(segundos, modelo)
    try:
        pipe = redis_client.pipeline()
        if client_id and canal:
            key = scoped_key(DELTAS_PREFIX, canal, client_id)
            pipe.hincrby(key, "costo_microusd", micro)
            pipe.expire(key, _BUFFER_TTL_SECONDS)
        mes_key = _mes_key(_mes_actual())
        pipe.hincrby(mes_key, "costo_microusd", micro)
        pipe.expire(mes_key, _BUFFER_TTL_SECONDS)
        pipe.execute()
    except Exception as e:
        print(f"Error registrando uso de audio en seguimiento: {e}")

    billing_repository.registrar_evento_audio(
        client_id=client_id,
        canal=canal,
        origen=origen,
        modelo=modelo or settings.OPENAI_MODEL_TRANSCRIPCION,
        segundos=segundos,
        costo_real_microusd=micro,
    )
    return micro


def registrar_uso_codigo(
    client_id: str,
    canal: Channel | str,
    origen: str,
    mensajes: int = 1,
) -> None:
    """Anota mensajes entregados SIN pasar por el modelo.

    Son los que dispara el código: la palabra clave (`tareas`/`transporte`), la
    bienvenida al grupo, las secuencias programadas de publicidad y los envíos
    manuales del dashboard. No tienen costo de proveedor, pero sí se facturan
    con una tarifa fija por mensaje.
    """
    billing_repository.registrar_evento_codigo(
        client_id=client_id,
        canal=canal,
        origen=origen,
        mensajes=mensajes,
    )


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


def registrar_intervencion_humana(client_id: str, canal: Channel | str) -> None:
    """Cuenta que una persona del negocio entró a atender esta conversación.

    Se escribe directo a Postgres y no al buffer de Redis: es un hecho puntual y
    poco frecuente, y el panel del administrador debe reflejarlo enseguida.
    """
    try:
        ejecutar(
            """
            INSERT INTO seguimiento_clientes (client_id, canal, intervenciones_humano, ultima_intervencion_humano)
            VALUES (%s, %s, 1, NOW())
            ON CONFLICT (client_id, canal) DO UPDATE SET
                intervenciones_humano = seguimiento_clientes.intervenciones_humano + 1,
                ultima_intervencion_humano = NOW()
            """,
            (str(client_id), _canal_value(canal)),
        )
    except Exception as e:
        print(f"Error registrando intervención humana: {e}")


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


# --- Volcado a Postgres ------------------------------------------------------

def flush_cliente(client_id: str, canal: Channel | str) -> bool:
    """Vuelca los buffers del cliente a su fila de seguimiento_clientes."""
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
