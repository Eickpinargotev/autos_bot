"""Libro mayor del consumo facturable (`uso_eventos`).

Tres categorías, tal como se cobra:

- `llm`: el turno pasó por el modelo. El costo REAL sale de los tokens y de los
  precios del proveedor; al cliente se le cobra ese costo multiplicado por el
  margen de la tarifa vigente.
- `codigo`: mensaje disparado por un algoritmo, sin modelo de por medio (la
  palabra clave `tareas`/`transporte`, los flujos programados, los envíos
  manuales). No tiene costo real de proveedor y al cliente se le cobra una
  tarifa fija por mensaje.
- `audio`: transcripción de una nota de voz. Se separa de `llm` porque el
  negocio quiere ver aparte "lo que pago por tokens" y "lo que pago por audios",
  y porque no encaja en las otras dos: tiene costo real de proveedor (a
  diferencia de `codigo`) pero se mide en segundos y no en tokens.

Todo se resuelve en UNA sentencia: el periodo abierto y la tarifa vigente se
buscan dentro del propio INSERT. Así no hay caché que quede obsoleta ni ventana
de carrera tras un cierre de periodo o un cambio de precios — el evento queda
imputado exactamente donde corresponde en el instante en que ocurrió.

El costo se congela en la fila. Cambiar la tarifa mañana no reescribe lo de hoy.
"""

from src.domain.entities import Channel
from src.infrastructure.repositories.postgres_conn import ejecutar

CATEGORIA_LLM = "llm"
CATEGORIA_CODIGO = "codigo"
CATEGORIA_AUDIO = "audio"


def _canal(canal: Channel | str) -> str:
    return canal.value if isinstance(canal, Channel) else str(canal or "")


def registrar_evento_llm(
    *,
    client_id: str,
    canal: Channel | str,
    origen: str,
    modelo: str,
    tokens_entrada: int,
    tokens_cacheados: int,
    tokens_salida: int,
    costo_real_microusd: int,
) -> bool:
    """Anota un turno procesado por el LLM.

    El costo real llega ya calculado por `seguimiento_service.costo_microusd`,
    que es la única fuente de esa fórmula. Aquí solo se le aplica el margen de
    venta de la tarifa vigente.
    """
    try:
        ejecutar(
            """
            INSERT INTO uso_eventos (
                periodo_id, tarifa_id, client_id, canal, categoria, origen, modelo,
                tokens_entrada, tokens_cacheados, tokens_salida, mensajes,
                costo_real_microusd, costo_cliente_microusd
            )
            SELECT
                p.id,
                t.id,
                %s, %s, 'llm', %s, %s,
                %s, %s, %s, 1,
                %s,
                ROUND(%s * COALESCE(t.multiplicador_llm, 1))
            FROM (
                SELECT id FROM periodos_facturacion
                WHERE cerrado_en IS NULL ORDER BY id DESC LIMIT 1
            ) p
            LEFT JOIN LATERAL (
                SELECT id, multiplicador_llm FROM tarifas
                WHERE vigente_desde <= NOW() ORDER BY vigente_desde DESC, id DESC LIMIT 1
            ) t ON TRUE
            """,
            (
                str(client_id or ""),
                _canal(canal),
                origen or "",
                modelo or "",
                int(tokens_entrada or 0),
                int(tokens_cacheados or 0),
                int(tokens_salida or 0),
                int(costo_real_microusd or 0),
                int(costo_real_microusd or 0),
            ),
        )
        return True
    except Exception as e:
        # Perder una anotación de consumo no puede tumbar la atención al cliente.
        print(f"Error registrando evento de uso (llm) en Postgres: {e}")
        return False


def registrar_evento_audio(
    *,
    client_id: str,
    canal: Channel | str,
    origen: str,
    modelo: str,
    segundos: int,
    costo_real_microusd: int,
) -> bool:
    """Anota la transcripción de una nota de voz.

    Tercera categoría, separada de `llm` a pedido del negocio: el cliente quiere
    ver por un lado lo que paga en tokens y por otro lo que paga en audios. No
    encaja en las otras dos — tiene costo real de proveedor (a diferencia de
    `codigo`) pero no se mide en tokens (a diferencia de `llm`).

    Se le aplica el mismo margen que al LLM: es el margen del negocio sobre lo
    que le cuesta operar, no una tarifa por tecnología.
    """
    if segundos <= 0 and costo_real_microusd <= 0:
        return False
    try:
        ejecutar(
            """
            INSERT INTO uso_eventos (
                periodo_id, tarifa_id, client_id, canal, categoria, origen, modelo,
                segundos_audio, mensajes, costo_real_microusd, costo_cliente_microusd
            )
            SELECT
                p.id,
                t.id,
                %s, %s, 'audio', %s, %s,
                %s, 1,
                %s,
                ROUND(%s * COALESCE(t.multiplicador_llm, 1))
            FROM (
                SELECT id FROM periodos_facturacion
                WHERE cerrado_en IS NULL ORDER BY id DESC LIMIT 1
            ) p
            LEFT JOIN LATERAL (
                SELECT id, multiplicador_llm FROM tarifas
                WHERE vigente_desde <= NOW() ORDER BY vigente_desde DESC, id DESC LIMIT 1
            ) t ON TRUE
            """,
            (
                str(client_id or ""),
                _canal(canal),
                origen or "",
                modelo or "",
                int(segundos or 0),
                int(costo_real_microusd or 0),
                int(costo_real_microusd or 0),
            ),
        )
        return True
    except Exception as e:
        print(f"Error registrando evento de uso (audio) en Postgres: {e}")
        return False


def registrar_evento_codigo(
    *,
    client_id: str,
    canal: Channel | str,
    origen: str,
    mensajes: int = 1,
) -> bool:
    """Anota mensajes entregados sin pasar por el modelo."""
    if mensajes <= 0:
        return False
    try:
        ejecutar(
            """
            INSERT INTO uso_eventos (
                periodo_id, tarifa_id, client_id, canal, categoria, origen,
                mensajes, costo_real_microusd, costo_cliente_microusd
            )
            SELECT
                p.id,
                t.id,
                %s, %s, 'codigo', %s,
                %s, 0,
                %s * COALESCE(t.precio_mensaje_codigo_microusd, 0)
            FROM (
                SELECT id FROM periodos_facturacion
                WHERE cerrado_en IS NULL ORDER BY id DESC LIMIT 1
            ) p
            LEFT JOIN LATERAL (
                SELECT id, precio_mensaje_codigo_microusd FROM tarifas
                WHERE vigente_desde <= NOW() ORDER BY vigente_desde DESC, id DESC LIMIT 1
            ) t ON TRUE
            """,
            (
                str(client_id or ""),
                _canal(canal),
                origen or "",
                int(mensajes),
                int(mensajes),
            ),
        )
        return True
    except Exception as e:
        print(f"Error registrando evento de uso (codigo) en Postgres: {e}")
        return False
