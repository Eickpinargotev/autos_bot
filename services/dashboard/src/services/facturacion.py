"""Consultas y operaciones de facturación.

Reglas del modelo:

- Un evento facturable es una fila de `uso_eventos`, con el costo YA congelado
  (real y de venta). Aquí solo se suma; nunca se recalcula el pasado.
- El cliente ve el periodo abierto. El administrador ve todo.
- "Resetear" cierra el periodo abierto y abre otro. No borra nada.
- Un periodo cerrado puede reincorporarse al abierto: se marca
  `reincorporado_en_periodo_id` y las sumas del periodo abierto lo incluyen.
"""

from typing import Any

from src.db import pool

MICRO = 1_000_000


def usd(microusd: int | None) -> float:
    """Convierte micro-USD (entero) a USD legible."""
    return round(int(microusd or 0) / MICRO, 6)


# --- Periodos ----------------------------------------------------------------

def periodo_abierto() -> dict[str, Any]:
    """Periodo de facturación en curso, creándolo si por algún motivo no existe."""
    periodo = pool.consultar_uno(
        "SELECT * FROM periodos_facturacion WHERE cerrado_en IS NULL ORDER BY id DESC LIMIT 1"
    )
    if periodo:
        return periodo

    # El índice parcial `idx_periodo_unico_abierto` garantiza que solo uno gane
    # esta carrera; el resto vuelve a leer el que quedó.
    try:
        return pool.consultar_uno(
            "INSERT INTO periodos_facturacion (nota) VALUES (%s) RETURNING *",
            ("Periodo recreado automáticamente.",),
        )
    except Exception:
        return pool.consultar_uno(
            "SELECT * FROM periodos_facturacion WHERE cerrado_en IS NULL ORDER BY id DESC LIMIT 1"
        )


def ids_del_periodo(periodo_id: int) -> list[int]:
    """El periodo dado más los periodos cerrados que se le reincorporaron."""
    filas = pool.consultar(
        "SELECT id FROM periodos_facturacion WHERE id = %s OR reincorporado_en_periodo_id = %s",
        (periodo_id, periodo_id),
    )
    return [fila["id"] for fila in filas]


def cerrar_periodo(usuario: str, nota: str = "") -> dict[str, Any]:
    """Cierra el periodo abierto congelando sus totales y abre uno nuevo.

    Los totales se guardan en la fila del periodo para que el histórico sea
    estable aunque después se purguen eventos viejos.
    """
    actual = periodo_abierto()
    totales = totales_de_periodo(actual["id"])

    pool.ejecutar(
        """
        UPDATE periodos_facturacion
        SET cerrado_en = NOW(),
            cerrado_por = %s,
            total_real_microusd = %s,
            total_cliente_microusd = %s,
            total_eventos = %s,
            nota = %s
        WHERE id = %s AND cerrado_en IS NULL
        """,
        (
            usuario,
            totales["real_microusd"],
            totales["cliente_microusd"],
            totales["eventos"],
            nota,
            actual["id"],
        ),
    )
    return pool.consultar_uno(
        "INSERT INTO periodos_facturacion (nota) VALUES (%s) RETURNING *",
        (f"Abierto tras el cierre del periodo {actual['id']} por {usuario}.",),
    )


def reincorporar_periodo(periodo_id: int) -> bool:
    """Suma un periodo cerrado al periodo abierto (deshace un cierre por error).

    Solo aplica a periodos cerrados y aún no reincorporados.
    """
    abierto = periodo_abierto()
    if periodo_id == abierto["id"]:
        return False
    filas = pool.ejecutar(
        """
        UPDATE periodos_facturacion
        SET reincorporado_en_periodo_id = %s
        WHERE id = %s AND cerrado_en IS NOT NULL AND reincorporado_en_periodo_id IS NULL
        """,
        (abierto["id"], periodo_id),
    )
    return filas > 0


def listar_periodos() -> list[dict[str, Any]]:
    return pool.consultar(
        """
        SELECT p.*,
               r.id AS reincorporado_a
        FROM periodos_facturacion p
        LEFT JOIN periodos_facturacion r ON r.id = p.reincorporado_en_periodo_id
        ORDER BY p.id DESC
        """
    )


# --- Totales y desgloses -----------------------------------------------------

def totales_de_periodo(periodo_id: int) -> dict[str, Any]:
    ids = ids_del_periodo(periodo_id)
    fila = pool.consultar_uno(
        """
        SELECT COALESCE(SUM(costo_real_microusd), 0)    AS real_microusd,
               COALESCE(SUM(costo_cliente_microusd), 0) AS cliente_microusd,
               COALESCE(SUM(mensajes), 0)               AS mensajes,
               COALESCE(SUM(tokens_entrada), 0)         AS tokens_entrada,
               COALESCE(SUM(tokens_salida), 0)          AS tokens_salida,
               COUNT(*)                                 AS eventos
        FROM uso_eventos
        WHERE periodo_id = ANY(%s)
        """,
        (ids,),
    ) or {}
    real = int(fila.get("real_microusd") or 0)
    cliente = int(fila.get("cliente_microusd") or 0)
    return {
        "real_microusd": real,
        "cliente_microusd": cliente,
        "margen_microusd": cliente - real,
        "real_usd": usd(real),
        "cliente_usd": usd(cliente),
        "margen_usd": usd(cliente - real),
        "mensajes": int(fila.get("mensajes") or 0),
        "tokens_entrada": int(fila.get("tokens_entrada") or 0),
        "tokens_salida": int(fila.get("tokens_salida") or 0),
        "eventos": int(fila.get("eventos") or 0),
    }


def desglose_por_categoria(periodo_id: int) -> list[dict[str, Any]]:
    """Consumo separado en turnos con LLM vs mensajes disparados por código."""
    ids = ids_del_periodo(periodo_id)
    filas = pool.consultar(
        """
        SELECT categoria,
               COUNT(*)                                 AS eventos,
               COALESCE(SUM(mensajes), 0)               AS mensajes,
               COALESCE(SUM(costo_real_microusd), 0)    AS real_microusd,
               COALESCE(SUM(costo_cliente_microusd), 0) AS cliente_microusd
        FROM uso_eventos
        WHERE periodo_id = ANY(%s)
        GROUP BY categoria
        ORDER BY categoria
        """,
        (ids,),
    )
    return [
        {
            **fila,
            "real_usd": usd(fila["real_microusd"]),
            "cliente_usd": usd(fila["cliente_microusd"]),
        }
        for fila in filas
    ]


def desglose_por_origen(periodo_id: int) -> list[dict[str, Any]]:
    """De dónde vino el consumo (agente, recordatorio, RAG, keyword, envíos...)."""
    ids = ids_del_periodo(periodo_id)
    filas = pool.consultar(
        """
        SELECT origen,
               categoria,
               COUNT(*)                                 AS eventos,
               COALESCE(SUM(costo_real_microusd), 0)    AS real_microusd,
               COALESCE(SUM(costo_cliente_microusd), 0) AS cliente_microusd
        FROM uso_eventos
        WHERE periodo_id = ANY(%s)
        GROUP BY origen, categoria
        ORDER BY cliente_microusd DESC
        """,
        (ids,),
    )
    return [
        {
            **fila,
            "real_usd": usd(fila["real_microusd"]),
            "cliente_usd": usd(fila["cliente_microusd"]),
        }
        for fila in filas
    ]


def serie_diaria(periodo_id: int, dias: int = 30) -> list[dict[str, Any]]:
    """Consumo por día, para la gráfica de barras del panel."""
    ids = ids_del_periodo(periodo_id)
    filas = pool.consultar(
        """
        SELECT DATE(ts) AS dia,
               COALESCE(SUM(costo_real_microusd), 0)    AS real_microusd,
               COALESCE(SUM(costo_cliente_microusd), 0) AS cliente_microusd,
               COALESCE(SUM(mensajes), 0)               AS mensajes
        FROM uso_eventos
        WHERE periodo_id = ANY(%s) AND ts >= NOW() - (%s || ' days')::interval
        GROUP BY DATE(ts)
        ORDER BY dia
        """,
        (ids, str(dias)),
    )
    return [
        {
            **fila,
            "real_usd": usd(fila["real_microusd"]),
            "cliente_usd": usd(fila["cliente_microusd"]),
        }
        for fila in filas
    ]


# --- Tarifas -----------------------------------------------------------------

def tarifa_vigente() -> dict[str, Any] | None:
    return pool.consultar_uno(
        "SELECT * FROM tarifas WHERE vigente_desde <= NOW() ORDER BY vigente_desde DESC, id DESC LIMIT 1"
    )


def listar_tarifas() -> list[dict[str, Any]]:
    return pool.consultar("SELECT * FROM tarifas ORDER BY vigente_desde DESC, id DESC")


def crear_tarifa(datos: dict[str, Any], usuario: str) -> dict[str, Any]:
    """Registra una tarifa nueva. Nunca edita la anterior: el histórico es la auditoría."""
    return pool.consultar_uno(
        """
        INSERT INTO tarifas (
            modelo, precio_input_usd_1m, precio_cached_input_usd_1m, precio_output_usd_1m,
            multiplicador_llm, precio_mensaje_codigo_microusd, creado_por, nota
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            datos["modelo"],
            datos["precio_input_usd_1m"],
            datos["precio_cached_input_usd_1m"],
            datos["precio_output_usd_1m"],
            datos["multiplicador_llm"],
            datos["precio_mensaje_codigo_microusd"],
            usuario,
            datos.get("nota", ""),
        ),
    )


# --- Actividad por cliente ---------------------------------------------------

def actividad_por_cliente(periodo_id: int, limite: int = 200) -> list[dict[str, Any]]:
    """Una fila por conversación con todo lo que el administrador necesita ver.

    Junta dos fuentes: el consumo facturable (`uso_eventos`, del periodo actual)
    y el seguimiento acumulado (`seguimiento_clientes`, que no se reinicia con
    los periodos). Así se ve de un vistazo quién gasta, quién necesitó a una
    persona y quién quedó atendido por un humano, sin entrar a cada perfil.
    """
    ids = ids_del_periodo(periodo_id)
    filas = pool.consultar(
        """
        SELECT COALESCE(s.client_id, u.client_id)       AS client_id,
               COALESCE(s.canal, u.canal)               AS canal,
               s.nombre,
               s.conversaciones_iniciadas,
               s.derivaciones_asesor,
               s.intervenciones_humano,
               s.ultima_intervencion_humano,
               s.ultima_interaccion,
               COALESCE(u.real_microusd, 0)             AS real_microusd,
               COALESCE(u.cliente_microusd, 0)          AS cliente_microusd,
               COALESCE(u.mensajes_llm, 0)              AS mensajes_llm,
               COALESCE(u.mensajes_codigo, 0)           AS mensajes_codigo
        FROM seguimiento_clientes s
        FULL OUTER JOIN (
            SELECT client_id, canal,
                   SUM(costo_real_microusd)                                  AS real_microusd,
                   SUM(costo_cliente_microusd)                               AS cliente_microusd,
                   SUM(mensajes) FILTER (WHERE categoria = 'llm')            AS mensajes_llm,
                   SUM(mensajes) FILTER (WHERE categoria = 'codigo')         AS mensajes_codigo
            FROM uso_eventos
            WHERE periodo_id = ANY(%s)
            GROUP BY client_id, canal
        ) u ON u.client_id = s.client_id AND u.canal = s.canal
        ORDER BY COALESCE(u.cliente_microusd, 0) DESC, s.ultima_interaccion DESC NULLS LAST
        LIMIT %s
        """,
        (ids, int(limite)),
    )
    return [
        {
            **fila,
            "real_usd": usd(fila["real_microusd"]),
            "cliente_usd": usd(fila["cliente_microusd"]),
        }
        for fila in filas
    ]


def totales_de_actividad(filas: list[dict[str, Any]]) -> dict[str, int]:
    """Suma de las columnas contables de la tabla de actividad."""
    def suma(campo: str) -> int:
        return sum(int(fila.get(campo) or 0) for fila in filas)

    return {
        "clientes": len(filas),
        "mensajes_llm": suma("mensajes_llm"),
        "mensajes_codigo": suma("mensajes_codigo"),
        "derivaciones": suma("derivaciones_asesor"),
        "intervenciones": suma("intervenciones_humano"),
        "conversaciones": suma("conversaciones_iniciadas"),
    }
