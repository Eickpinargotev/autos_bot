"""Lectura de todo lo que el bot registra: conversaciones, reportes, RAG.

Es lo que sustituye a entrar a NocoDB a mirar tablas.
"""

from typing import Any

from src.db import pool


# --- Conversaciones ----------------------------------------------------------

def normalizar_numero(busqueda: str) -> str:
    """Deja solo los dígitos de lo que se escribió en el buscador.

    La búsqueda es POR NÚMERO, no por texto: buscar dentro del contenido de los
    mensajes obliga a recorrer la tabla entera (o a montar un índice de texto
    completo) y el panel se vuelve lento justo cuando hay historial de sobra.
    Así, '+506 8888-8888' y '50688888888' encuentran lo mismo.
    """
    return "".join(c for c in str(busqueda or "") if c.isdigit())


def listar_conversaciones(busqueda: str = "", limite: int = 100) -> list[dict[str, Any]]:
    """Una fila por (cliente, canal) con su última actividad."""
    numero = normalizar_numero(busqueda)
    if busqueda and not numero:
        # Se escribió algo que no es un número (un nombre, una palabra del chat).
        # Devolver la lista entera haría creer que eso es el resultado de la
        # búsqueda; devolver nada deja claro que aquí solo se busca por número.
        return []

    condiciones = []
    params: list[Any] = []
    if numero:
        condiciones.append("m.client_id LIKE %s")
        params.append(f"%{numero}%")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    params.append(int(limite))
    return pool.consultar(
        f"""
        SELECT m.client_id,
               m.canal,
               MAX(m.created_at) AS ultima_actividad,
               COUNT(*)          AS mensajes,
               MAX(m.sender_name) FILTER (WHERE m.direction = 'inbound') AS nombre
        FROM conversation_messages m
        {where}
        GROUP BY m.client_id, m.canal
        ORDER BY ultima_actividad DESC
        LIMIT %s
        """,
        tuple(params),
    )


# Cuántos mensajes trae cada tanda del visor. Una conversación larga puede tener
# miles de filas (cada llamada a una herramienta es una); cargarlas todas de
# golpe hace la página pesada y lenta sin que nadie las lea.
MENSAJES_POR_PAGINA = 60


def mensajes_de(
    client_id: str,
    canal: str,
    limite: int = MENSAJES_POR_PAGINA,
    antes_de: int | None = None,
    incluir_internos: bool = False,
) -> dict[str, Any]:
    """Una tanda de mensajes, del más antiguo al más reciente.

    Pagina hacia ATRÁS con un cursor por `id` (`antes_de`), no con OFFSET: el
    chat se lee desde el final y con OFFSET habría que contar y descartar todas
    las filas nuevas en cada tanda. Además el cursor no se descoloca si llegan
    mensajes mientras el administrador va leyendo.

    `incluir_internos` decide si vienen también los eventos de herramientas
    (`direction = 'internal'`), que son diagnóstico y no conversación.

    Devuelve `{mensajes, hay_mas, cursor}`: `cursor` es el id desde el que pedir
    la siguiente tanda hacia atrás.
    """
    limite = max(1, min(int(limite), 500))
    condiciones = ["client_id = %s", "canal = %s"]
    params: list[Any] = [client_id, canal]

    if not incluir_internos:
        condiciones.append("direction <> 'internal'")
    if antes_de:
        condiciones.append("id < %s")
        params.append(int(antes_de))

    # Se pide una fila de más solo para saber si queda historial detrás, sin
    # tener que contar la conversación entera.
    params.append(limite + 1)
    filas = pool.consultar(
        f"""
        SELECT * FROM conversation_messages
        WHERE {' AND '.join(condiciones)}
        ORDER BY id DESC
        LIMIT %s
        """,
        tuple(params),
    )

    hay_mas = len(filas) > limite
    filas = filas[:limite]
    cursor = filas[-1]["id"] if filas else None
    filas.reverse()
    return {"mensajes": filas, "hay_mas": hay_mas, "cursor": cursor}


def resumen_conversacion(client_id: str, canal: str) -> dict[str, Any]:
    """Cabecera del visor: nombre, cuántos mensajes hay y de cuándo son."""
    fila = pool.consultar_uno(
        """
        SELECT COUNT(*) FILTER (WHERE direction <> 'internal') AS mensajes,
               COUNT(*) FILTER (WHERE direction = 'internal')  AS eventos,
               MIN(created_at) AS primera,
               MAX(created_at) AS ultima,
               MAX(sender_name) FILTER (WHERE direction = 'inbound') AS nombre
        FROM conversation_messages
        WHERE client_id = %s AND canal = %s
        """,
        (client_id, canal),
    )
    return fila or {}


# --- Reportes al asesor ------------------------------------------------------

def listar_reportes(solo_pendientes: bool = False, limite: int = 200) -> list[dict[str, Any]]:
    where = "WHERE NOT revisado" if solo_pendientes else ""
    return pool.consultar(
        f"SELECT * FROM reportes {where} ORDER BY creado_en DESC LIMIT %s", (int(limite),)
    )


def marcar_reporte_revisado(reporte_id: int) -> int:
    return pool.ejecutar("UPDATE reportes SET revisado = TRUE WHERE id = %s", (reporte_id,))


def contar_reportes_pendientes() -> int:
    fila = pool.consultar_uno("SELECT COUNT(*) AS total FROM reportes WHERE NOT revisado")
    return int((fila or {}).get("total") or 0)


# --- Base de conocimiento del RAG --------------------------------------------

def listar_chunks(busqueda: str = "") -> list[dict[str, Any]]:
    if busqueda:
        return pool.consultar(
            "SELECT * FROM rag_chunks WHERE titulo ILIKE %s OR contenido ILIKE %s ORDER BY id DESC",
            (f"%{busqueda}%", f"%{busqueda}%"),
        )
    return pool.consultar("SELECT * FROM rag_chunks ORDER BY id DESC")


def crear_chunk(titulo: str, contenido: str) -> dict[str, Any]:
    return pool.consultar_uno(
        "INSERT INTO rag_chunks (titulo, contenido) VALUES (%s, %s) RETURNING *",
        (titulo, contenido),
    )


def actualizar_chunk(chunk_id: int, titulo: str, contenido: str) -> dict[str, Any] | None:
    return pool.consultar_uno(
        """
        UPDATE rag_chunks
        SET titulo = %s, contenido = %s, actualizado_en = NOW()
        WHERE id = %s
        RETURNING *
        """,
        (titulo, contenido, chunk_id),
    )


def alternar_chunk_activo(chunk_id: int) -> dict[str, Any] | None:
    return pool.consultar_uno(
        "UPDATE rag_chunks SET activo = NOT activo, actualizado_en = NOW() WHERE id = %s RETURNING *",
        (chunk_id,),
    )


def eliminar_chunk(chunk_id: int) -> int:
    return pool.ejecutar("DELETE FROM rag_chunks WHERE id = %s", (chunk_id,))


# --- Preguntas sin respuesta -------------------------------------------------

def listar_preguntas_sin_respuesta(limite: int = 200) -> list[dict[str, Any]]:
    return pool.consultar(
        "SELECT * FROM preguntas_sin_respuesta ORDER BY atendida, creado_en DESC LIMIT %s",
        (int(limite),),
    )


def marcar_pregunta_atendida(pregunta_id: int) -> int:
    return pool.ejecutar(
        "UPDATE preguntas_sin_respuesta SET atendida = TRUE WHERE id = %s", (pregunta_id,)
    )
