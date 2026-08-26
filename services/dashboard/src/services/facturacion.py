"""Consultas históricas y control interno de costos reales.

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


# --- Costos reales (modalidad de cobro fijo) ---------------------------------

def totales_reales(proyecto_id: int | None = None) -> dict[str, Any]:
    """Costo real acumulado, sin tarifas de venta ni cortes de facturación."""
    fila = pool.consultar_uno(
        """
        SELECT COALESCE(SUM(costo_real_microusd), 0) AS real_microusd,
               COALESCE(SUM(mensajes), 0)            AS mensajes,
               COALESCE(SUM(tokens_entrada), 0)      AS tokens_entrada,
               COALESCE(SUM(tokens_salida), 0)       AS tokens_salida,
               COUNT(*)                              AS eventos
        FROM uso_eventos
        WHERE (%s IS NULL OR proyecto_id = %s)
        """,
        (proyecto_id, proyecto_id),
    ) or {}
    real = int(fila.get("real_microusd") or 0)
    return {
        "real_microusd": real,
        "real_usd": usd(real),
        "mensajes": int(fila.get("mensajes") or 0),
        "tokens_entrada": int(fila.get("tokens_entrada") or 0),
        "tokens_salida": int(fila.get("tokens_salida") or 0),
        "eventos": int(fila.get("eventos") or 0),
    }


def desglose_real_por_categoria(proyecto_id: int | None = None) -> list[dict[str, Any]]:
    """Costo real acumulado de cada componente técnico."""
    filas = pool.consultar(
        """
        SELECT categoria,
               COUNT(*)                              AS eventos,
               COALESCE(SUM(mensajes), 0)            AS mensajes,
               COALESCE(SUM(tokens_entrada), 0)      AS tokens_entrada,
               COALESCE(SUM(tokens_cacheados), 0)    AS tokens_cacheados,
               COALESCE(SUM(tokens_salida), 0)       AS tokens_salida,
               COALESCE(SUM(segundos_audio), 0)      AS segundos_audio,
               COALESCE(SUM(costo_real_microusd), 0) AS real_microusd
        FROM uso_eventos
        WHERE (%s IS NULL OR proyecto_id = %s)
        GROUP BY categoria
        """,
        (proyecto_id, proyecto_id),
    )
    resultado = [
        {
            **fila,
            "etiqueta": ETIQUETAS_CATEGORIA.get(fila["categoria"], fila["categoria"]),
            "real_usd": usd(fila["real_microusd"]),
            "minutos_audio": round(int(fila["segundos_audio"] or 0) / 60, 1),
        }
        for fila in filas
    ]
    resultado.sort(key=lambda f: _ORDEN_CATEGORIA.get(f["categoria"], 9))
    return resultado


def desglose_real_por_origen(proyecto_id: int | None = None) -> list[dict[str, Any]]:
    filas = pool.consultar(
        """
        SELECT origen, categoria, COUNT(*) AS eventos,
               COALESCE(SUM(costo_real_microusd), 0) AS real_microusd
        FROM uso_eventos
        WHERE (%s IS NULL OR proyecto_id = %s)
        GROUP BY origen, categoria
        ORDER BY real_microusd DESC
        """,
        (proyecto_id, proyecto_id),
    )
    return [{**fila, "real_usd": usd(fila["real_microusd"])} for fila in filas]


def ahorro_real_por_cache(proyecto_id: int | None = None) -> dict[str, Any]:
    fila = pool.consultar_uno(
        """
        SELECT COALESCE(SUM(tokens_entrada), 0) AS entrada,
               COALESCE(SUM(tokens_cacheados), 0) AS cacheados
        FROM uso_eventos
        WHERE categoria = 'llm' AND (%s IS NULL OR proyecto_id = %s)
        """,
        (proyecto_id, proyecto_id),
    ) or {}
    entrada = int(fila.get("entrada") or 0)
    cacheados = int(fila.get("cacheados") or 0)
    return {
        "tokens_entrada": entrada,
        "tokens_cacheados": cacheados,
        "porcentaje": round(cacheados / entrada * 100, 1) if entrada else 0.0,
    }


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

def totales_de_periodo(periodo_id: int, proyecto_id: int | None = None) -> dict[str, Any]:
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
        WHERE periodo_id = ANY(%s) AND (%s IS NULL OR proyecto_id = %s)
        """,
        (ids, proyecto_id, proyecto_id),
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


# Cómo se le nombra cada categoría al NEGOCIO. Él no tiene por qué saber qué es
# un token ni un modelo: lo que necesita saber es por qué le cobran.
ETIQUETAS_CATEGORIA = {
    "llm": "Conversaciones atendidas",
    "audio": "Notas de voz transcritas",
    "codigo": "Mensajes automáticos",
}

# Orden en el que se muestran, de mayor a menor peso en la factura. Sin esto
# saldrían alfabéticos y "audio" abriría el listado, que es lo que menos pesa.
_ORDEN_CATEGORIA = {"llm": 0, "audio": 1, "codigo": 2}


def desglose_por_categoria(periodo_id: int, proyecto_id: int | None = None) -> list[dict[str, Any]]:
    """Consumo separado por categoría: conversaciones, audios y automáticos.

    Las tres se cobran distinto y el negocio las quiere ver por separado: los
    turnos del modelo se miden en tokens, las notas de voz en segundos, y los
    mensajes automáticos por unidad entregada. Sumarlas en una sola cifra
    escondería de dónde sale el gasto.
    """
    ids = ids_del_periodo(periodo_id)
    filas = pool.consultar(
        """
        SELECT categoria,
               COUNT(*)                                 AS eventos,
               COALESCE(SUM(mensajes), 0)               AS mensajes,
               COALESCE(SUM(tokens_entrada), 0)         AS tokens_entrada,
               COALESCE(SUM(tokens_cacheados), 0)       AS tokens_cacheados,
               COALESCE(SUM(tokens_salida), 0)          AS tokens_salida,
               COALESCE(SUM(segundos_audio), 0)         AS segundos_audio,
               COALESCE(SUM(costo_real_microusd), 0)    AS real_microusd,
               COALESCE(SUM(costo_cliente_microusd), 0) AS cliente_microusd
        FROM uso_eventos
        WHERE periodo_id = ANY(%s) AND (%s IS NULL OR proyecto_id = %s)
        GROUP BY categoria
        """,
        (ids, proyecto_id, proyecto_id),
    )
    resultado = [
        {
            **fila,
            "etiqueta": ETIQUETAS_CATEGORIA.get(fila["categoria"], fila["categoria"]),
            "real_usd": usd(fila["real_microusd"]),
            "cliente_usd": usd(fila["cliente_microusd"]),
            "minutos_audio": round(int(fila["segundos_audio"] or 0) / 60, 1),
        }
        for fila in filas
    ]
    resultado.sort(key=lambda f: _ORDEN_CATEGORIA.get(f["categoria"], 9))
    return resultado


def ahorro_por_cache(periodo_id: int, proyecto_id: int | None = None) -> dict[str, Any]:
    """Cuánto del prompt se reutilizó desde la caché del proveedor, y qué ahorró.

    El prompt del sistema es grande (>2900 tokens) y se repite en cada turno.
    OpenAI cobra los tokens ya vistos al 10%, así que el porcentaje cacheado es
    la palanca más directa sobre la factura — y la primera señal de que algo se
    rompió: si el prefijo del prompt deja de ser estable, este número se
    desploma y el gasto se triplica sin que nada más falle.
    """
    ids = ids_del_periodo(periodo_id)
    fila = pool.consultar_uno(
        """
        SELECT COALESCE(SUM(tokens_entrada), 0)   AS entrada,
               COALESCE(SUM(tokens_cacheados), 0) AS cacheados
        FROM uso_eventos
        WHERE periodo_id = ANY(%s) AND categoria = 'llm'
          AND (%s IS NULL OR proyecto_id = %s)
        """,
        (ids, proyecto_id, proyecto_id),
    ) or {}
    entrada = int(fila.get("entrada") or 0)
    cacheados = int(fila.get("cacheados") or 0)
    return {
        "tokens_entrada": entrada,
        "tokens_cacheados": cacheados,
        "porcentaje": round(cacheados / entrada * 100, 1) if entrada else 0.0,
    }


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


def serie_diaria(
    periodo_id: int, dias: int = 30, proyecto_id: int | None = None
) -> list[dict[str, Any]]:
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
          AND (%s IS NULL OR proyecto_id = %s)
        GROUP BY DATE(ts)
        ORDER BY dia
        """,
        (ids, str(dias), proyecto_id, proyecto_id),
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

def actividad_por_cliente(
    proyecto_id: int, periodo_id: int, limite: int = 200, incluir_costo_real: bool = False
) -> list[dict[str, Any]]:
    """Una fila por conversación con la actividad y el consumo de cada cliente.

    Junta dos fuentes: el consumo facturable (`uso_eventos`, del periodo actual)
    y el seguimiento acumulado (`seguimiento_clientes`, que no se reinicia con
    los periodos). Así se ve de un vistazo quién gasta, quién necesitó a una
    persona y quién quedó atendido por un humano, sin entrar a cada perfil.

    **`incluir_costo_real` es una puerta, no un adorno.** El costo real es lo que
    nos cuesta a NOSOTROS el proveedor; el proyecto solo puede ver lo facturado,
    que es su precio de venta. Estaba saliendo en su tabla de clientes: con los
    dos números al lado, cualquiera calcula el margen. La columna no se oculta en
    la plantilla —eso deja el dato viajando en el HTML— sino que no se consulta.

    Ordena por **última actividad**, no por gasto: esta tabla se lee para saber
    quién escribió hace poco, y con el orden por dinero lo de hoy aparecía en
    mitad de la lista. Las filas sin fecha (consumo sin seguimiento) van al
    final: no se sabe de cuándo son.
    """
    ids = ids_del_periodo(periodo_id)
    columna_real = (
        "COALESCE(u.real_microusd, 0)" if incluir_costo_real else "NULL::BIGINT"
    )
    filas = pool.consultar(
        f"""
        SELECT COALESCE(s.client_id, u.client_id)       AS client_id,
               COALESCE(s.canal, u.canal)               AS canal,
               s.nombre,
               s.conversaciones_iniciadas,
               s.derivaciones_asesor,
               s.intervenciones_humano,
               s.ultima_intervencion_humano,
               s.ultima_interaccion,
               {columna_real}                            AS real_microusd,
               COALESCE(u.cliente_microusd, 0)          AS cliente_microusd,
               COALESCE(u.mensajes_llm, 0)              AS mensajes_llm,
               COALESCE(u.mensajes_codigo, 0)           AS mensajes_codigo
        FROM seguimiento_clientes s
        FULL OUTER JOIN (
            SELECT proyecto_id, client_id, canal,
                   SUM(costo_real_microusd)                                  AS real_microusd,
                   SUM(costo_cliente_microusd)                               AS cliente_microusd,
                   SUM(mensajes) FILTER (WHERE categoria = 'llm')            AS mensajes_llm,
                   SUM(mensajes) FILTER (WHERE categoria = 'codigo')         AS mensajes_codigo
            FROM uso_eventos
            WHERE proyecto_id = %s AND periodo_id = ANY(%s)
            GROUP BY proyecto_id, client_id, canal
        ) u ON u.proyecto_id = s.proyecto_id AND u.client_id = s.client_id AND u.canal = s.canal
        WHERE COALESCE(s.proyecto_id, u.proyecto_id) = %s
        ORDER BY s.ultima_interaccion DESC NULLS LAST, COALESCE(u.cliente_microusd, 0) DESC
        LIMIT %s
        """,
        (int(proyecto_id), ids, int(proyecto_id), int(limite)),
    )
    return [
        {
            **fila,
            # Sin costo real, `real_usd` no se calcula: un "$0.00" en esa columna
            # sería un dato falso, no un dato ausente.
            "real_usd": usd(fila["real_microusd"]) if fila["real_microusd"] is not None else None,
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
