"""Libro mayor del costo real de operación (`uso_eventos`).

Tres categorías, tal como se mide:

- `llm`: el turno pasó por el modelo. El costo real sale de los tokens y de los
  precios del proveedor.
- `codigo`: mensaje disparado por un algoritmo, sin modelo de por medio (la
  palabra clave `tareas`/`transporte`, los flujos programados, los envíos
  manuales). No tiene costo real de modelo, pero se conserva la cantidad para
  auditar cada componente.
- `audio`: transcripción de una nota de voz. Se separa de `llm` porque el
  administrador necesita ver aparte tokens y audios,
  y porque no encaja en las otras dos: tiene costo real de proveedor (a
  diferencia de `codigo`) pero se mide en segundos y no en tokens.

El costo real se congela en la fila. Las columnas históricas de tarifa y precio
de venta se escriben como NULL y cero: siguen en el esquema para permitir una
actualización sin destruir el historial anterior, pero ya no intervienen en el
cobro fijo.
"""

from src.domain.entities import Channel
from src.infrastructure.repositories.postgres_conn import ejecutar
from src.application.project_context import proyecto_actual

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
    que es la única fuente de esa fórmula.
    """
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return False
    try:
        ejecutar(
            """
            INSERT INTO uso_eventos (
                proyecto_id, periodo_id, tarifa_id, client_id, canal, categoria, origen, modelo,
                tokens_entrada, tokens_cacheados, tokens_salida, mensajes,
                costo_real_microusd, costo_cliente_microusd
            )
            SELECT
                %s, p.id,
                NULL,
                %s, %s, 'llm', %s, %s,
                %s, %s, %s, 1,
                %s, 0
            FROM (
                SELECT id FROM periodos_facturacion
                WHERE cerrado_en IS NULL ORDER BY id DESC LIMIT 1
            ) p
            """,
            (
                proyecto_id, str(client_id or ""),
                _canal(canal),
                origen or "",
                modelo or "",
                int(tokens_entrada or 0),
                int(tokens_cacheados or 0),
                int(tokens_salida or 0),
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

    Tiene costo real de proveedor (a diferencia de `codigo`) y se mide en
    segundos, por eso se conserva como categoría propia.
    """
    proyecto_id = proyecto_actual()
    if not proyecto_id or (segundos <= 0 and costo_real_microusd <= 0):
        return False
    try:
        ejecutar(
            """
            INSERT INTO uso_eventos (
                proyecto_id, periodo_id, tarifa_id, client_id, canal, categoria, origen, modelo,
                segundos_audio, mensajes, costo_real_microusd, costo_cliente_microusd
            )
            SELECT
                %s, p.id,
                NULL,
                %s, %s, 'audio', %s, %s,
                %s, 1,
                %s, 0
            FROM (
                SELECT id FROM periodos_facturacion
                WHERE cerrado_en IS NULL ORDER BY id DESC LIMIT 1
            ) p
            """,
            (
                proyecto_id, str(client_id or ""),
                _canal(canal),
                origen or "",
                modelo or "",
                int(segundos or 0),
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
    proyecto_id = proyecto_actual()
    if not proyecto_id or mensajes <= 0:
        return False
    try:
        ejecutar(
            """
            INSERT INTO uso_eventos (
                proyecto_id, periodo_id, tarifa_id, client_id, canal, categoria, origen,
                mensajes, costo_real_microusd, costo_cliente_microusd
            )
            SELECT
                %s, p.id,
                NULL,
                %s, %s, 'codigo', %s,
                %s, 0, 0
            FROM (
                SELECT id FROM periodos_facturacion
                WHERE cerrado_en IS NULL ORDER BY id DESC LIMIT 1
            ) p
            """,
            (
                proyecto_id, str(client_id or ""),
                _canal(canal),
                origen or "",
                int(mensajes),
            ),
        )
        return True
    except Exception as e:
        print(f"Error registrando evento de uso (codigo) en Postgres: {e}")
        return False
