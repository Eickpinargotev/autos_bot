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


def listar_conversaciones(
    proyecto_id: int, busqueda: str = "", limite: int = 100
) -> list[dict[str, Any]]:
    """Una fila por (cliente, canal) con su última actividad.

    Con `negocio_id` solo salen las conversaciones de ESE negocio. La
    pertenencia está en `conversacion_negocio`, que anota el webhook al entrar
    cada mensaje (ver migración 009). Sin filtrar, se mezclarían los clientes de
    todos los negocios en una sola lista, que es justo lo que no sirve: un chat
    solo se entiende dentro del negocio al que le escribieron.

    Las conversaciones sin pertenencia anotada (anteriores a la 009, o de
    Telegram, que no pasa por el webhook de WhatsApp) NO aparecen al filtrar por
    negocio. Es deliberado: mostrarlas en el perfil de un negocio sería afirmar
    que son suyas sin saberlo.
    """
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
    condiciones.append("m.proyecto_id = %s")
    params.append(int(proyecto_id))
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    params.append(int(limite))
    return pool.consultar(
        f"""
        SELECT m.client_id,
               m.canal,
               MAX(m.created_at) AS ultima_actividad,
               COUNT(*) FILTER (WHERE m.direction <> 'internal') AS mensajes,
               COUNT(*) FILTER (
                   WHERE m.direction <> 'internal' AND m.author = 'ia'
               ) AS respuestas_bot,
               COUNT(*) FILTER (
                   WHERE m.direction <> 'internal' AND m.author = 'dueño'
               ) AS respuestas_dueno,
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
    proyecto_id: int,
    client_id: str,
    canal: str,
    limite: int = MENSAJES_POR_PAGINA,
    antes_de: int | None = None,
    desde_id: int | None = None,
    incluir_internos: bool = False,
) -> dict[str, Any]:
    """Una tanda de mensajes, del más antiguo al más reciente.

    Se lee en dos direcciones y cada una tiene su motivo:

    * **Hacia atrás** (`antes_de`), que es como se lee un chat: se carga la
      tanda más reciente y se va pidiendo historial. El cursor es por `id` y no
      un OFFSET porque con OFFSET habría que contar y descartar todas las filas
      nuevas en cada tanda, y el sitio se descolocaría si llega un mensaje
      mientras el administrador va leyendo.
    * **Hacia adelante** (`desde_id`), para el chat en vivo: solo lo que entró
      después del último mensaje que ya está pintado. Sin esto, enterarse de un
      mensaje nuevo obligaba a volver a pedir el hilo entero y repintarlo, con
      lo que se perdía el sitio en el que ibas leyendo.

    `incluir_internos` decide si vienen también los eventos de herramientas
    (`direction = 'internal'`), que son diagnóstico y no conversación.

    Devuelve `{mensajes, hay_mas, cursor}`: `cursor` es el id desde el que pedir
    la siguiente tanda hacia atrás. Leyendo hacia adelante `hay_mas` es siempre
    falso — no hay «anteriores» que cargar detrás de lo que acaba de llegar.
    """
    limite = max(1, min(int(limite), 500))
    condiciones = ["proyecto_id = %s", "client_id = %s", "canal = %s"]
    params: list[Any] = [int(proyecto_id), client_id, canal]

    if not incluir_internos:
        condiciones.append("(direction <> 'internal' OR event_type = 'report_created')")
    if desde_id:
        condiciones.append("id > %s")
        params.append(int(desde_id))
    elif antes_de:
        condiciones.append("id < %s")
        params.append(int(antes_de))

    if desde_id:
        # Lo nuevo se pide en orden natural y ya viene colocado: son pocos
        # mensajes (los de los últimos segundos) y se añaden al final tal cual.
        params.append(limite)
        filas = pool.consultar(
            f"""
            SELECT * FROM conversation_messages
            WHERE {' AND '.join(condiciones)}
            ORDER BY id ASC
            LIMIT %s
            """,
            tuple(params),
        )
        return {"mensajes": filas, "hay_mas": False, "cursor": filas[-1]["id"] if filas else None}

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


def eliminar_conversacion(proyecto_id: int, client_id: str, canal: str) -> dict[str, int]:
    """Borra el historial durable de una conversación.

    Se lleva las tres tablas que SON la conversación: el log de mensajes, los
    shots de sus turnos y la anotación de a qué negocio pertenecía. Devuelve
    cuántas filas cayó en cada una, para poder decirlo en el aviso.

    Lo que NO borra, a propósito:

    * `uso_eventos` — el libro mayor. Nunca se recalcula el pasado: el consumo
      ya ocurrido se factura aunque el chat se borre. Borrarlo aquí cambiaría
      una factura ya emitida.
    * `seguimiento_clientes` — la ficha de la persona. Se borra el chat, no el
      cliente.

    Falta la mitad que vive en Redis (el hilo que el bot tiene en memoria y sus
    recordatorios agendados): esa la pide `routes/admin.py` al bot, que es el
    único que conoce el esquema de claves y los ids de tarea.
    """
    return {
        "mensajes": pool.ejecutar(
            "DELETE FROM conversation_messages WHERE proyecto_id = %s AND client_id = %s AND canal = %s",
            (int(proyecto_id), client_id, canal),
        ),
        "shots": pool.ejecutar(
            "DELETE FROM conversation_shots WHERE proyecto_id = %s AND id_user = %s AND canal = %s",
            (int(proyecto_id), client_id, canal),
        ),
        "pertenencia": pool.ejecutar(
            "DELETE FROM conversacion_negocio WHERE proyecto_id = %s AND client_id = %s AND canal = %s",
            (int(proyecto_id), client_id, canal),
        ),
    }


def resumen_conversacion(proyecto_id: int, client_id: str, canal: str) -> dict[str, Any]:
    """Cabecera del visor: nombre, cuántos mensajes hay y de cuándo son."""
    fila = pool.consultar_uno(
        """
        SELECT COUNT(*) FILTER (WHERE direction <> 'internal') AS mensajes,
               COUNT(*) FILTER (WHERE direction = 'internal')  AS eventos,
               MIN(created_at) AS primera,
               MAX(created_at) AS ultima,
               MAX(sender_name) FILTER (WHERE direction = 'inbound') AS nombre
        FROM conversation_messages
        WHERE proyecto_id = %s AND client_id = %s AND canal = %s
        """,
        (int(proyecto_id), client_id, canal),
    )
    return fila or {}


# --- Reportes al asesor ------------------------------------------------------

# Cuánto sobrevive un reporte YA REVISADO. Lo pendiente no caduca nunca: un
# cliente esperando respuesta no deja de esperar porque pase una semana.
REPORTES_RETENCION_DIAS = 7


def listar_reportes(proyecto_id: int, solo_pendientes: bool = False, limite: int = 200) -> list[dict[str, Any]]:
    """Los reportes, lo que falta por atender primero.

    El orden es `revisado, creado_en DESC`: lo pendiente arriba y, dentro de cada
    grupo, lo más reciente primero. Antes se ordenaba solo por fecha, así que un
    reporte sin revisar de ayer quedaba debajo de tres ya resueltos de hoy —
    justo al revés de para qué se abre esta pantalla.
    """
    where = "WHERE proyecto_id = %s" + (" AND NOT revisado" if solo_pendientes else "")
    return pool.consultar(
        f"""
        SELECT *,
               revisado_en + (%s || ' days')::interval AS caduca_en
        FROM reportes {where}
        ORDER BY revisado, creado_en DESC
        LIMIT %s
        """,
        (REPORTES_RETENCION_DIAS, int(proyecto_id), int(limite)),
    )


def purgar_reportes_revisados(dias: int = REPORTES_RETENCION_DIAS) -> int:
    """Borra los reportes revisados que ya cumplieron su plazo.

    Un reporte revisado ya hizo su trabajo: alguien lo leyó y contactó a esa
    persona. Guardarlo para siempre solo alarga la lista con cosas resueltas
    hasta que lo que importa deja de verse.

    Lo pendiente NUNCA se borra, sea de cuando sea: que nadie lo haya mirado en
    un mes no lo hace menos urgente, lo hace más. El plazo cuenta desde
    `revisado_en`, no desde que llegó (ver migración 013).
    """
    return pool.ejecutar(
        """
        DELETE FROM reportes
        WHERE revisado
          AND revisado_en IS NOT NULL
          AND revisado_en < NOW() - (%s || ' days')::interval
        """,
        (int(dias),),
    )


def marcar_reporte_revisado(proyecto_id: int, reporte_id: int) -> int:
    """Lo baja al final de la lista y le arranca el plazo de caducidad.

    `revisado_en` solo se pone la PRIMERA vez (`WHERE NOT revisado`): volver a
    pulsar el botón no debe regalarle otros 7 días a algo ya resuelto.
    """
    return pool.ejecutar(
        "UPDATE reportes SET revisado = TRUE, revisado_en = NOW() "
        "WHERE proyecto_id = %s AND id = %s AND NOT revisado",
        (int(proyecto_id), reporte_id),
    )


def contar_reportes_pendientes(proyecto_id: int) -> int:
    fila = pool.consultar_uno(
        "SELECT COUNT(*) AS total FROM reportes WHERE proyecto_id = %s AND NOT revisado",
        (int(proyecto_id),),
    )
    return int((fila or {}).get("total") or 0)


def contar_preguntas_pendientes(proyecto_id: int) -> int:
    fila = pool.consultar_uno(
        "SELECT COUNT(*) AS total FROM preguntas_sin_respuesta "
        "WHERE proyecto_id = %s AND NOT atendida",
        (int(proyecto_id),),
    )
    return int((fila or {}).get("total") or 0)


# --- Base de conocimiento del RAG --------------------------------------------
#
# Un chunk es UN TROZO DE TEXTO. No tiene tema ni título: se vectoriza entero y
# el RAG lo recupera por parecido semántico con lo que preguntó el cliente. El
# campo `titulo` que hubo aquí no era un índice —nadie buscaba por él—, era un
# pedazo más del mismo texto que se embebía pegado al resto; lo único que lograba
# era obligar a inventarle un titular a cada trozo (ver migración 014).

# Cuánto puede medir un chunk. El tope no es de la base ni del modelo: es de la
# BÚSQUEDA. Un trozo largo mezcla varios asuntos, su vector queda en el promedio
# de todos ellos y deja de parecerse mucho a ninguna pregunta concreta; además,
# cuando se recupera, arrastra al agente párrafos que no venían al caso. Dos
# trozos cortos y precisos funcionan mejor que uno largo.
LIMITE_CHUNK = 2000


def _validar_chunk(contenido: str) -> str:
    contenido = str(contenido or "").strip()
    if not contenido:
        raise ValueError("El chunk no puede estar vacío.")
    if len(contenido) > LIMITE_CHUNK:
        raise ValueError(
            f"El chunk tiene {len(contenido)} caracteres y el máximo son {LIMITE_CHUNK}. "
            "Pártelo en dos: cada trozo se busca por separado y así los dos se encuentran mejor."
        )
    return contenido


def listar_chunks(proyecto_id: int, busqueda: str = "") -> list[dict[str, Any]]:
    """Los chunks, del más nuevo al más viejo.

    `demasiado_largo` marca los que ya existían por encima del límite (la carga
    inicial dejó varios). No se recortan solos: cortar el conocimiento de un
    negocio sin preguntarle podría dejar fuera justo el precio o el requisito que
    importaba. Se señalan para que su dueño decida por dónde partirlos.
    """
    if busqueda:
        filas = pool.consultar(
            "SELECT * FROM rag_chunks WHERE proyecto_id = %s AND contenido ILIKE %s ORDER BY id DESC",
            (int(proyecto_id), f"%{busqueda}%"),
        )
    else:
        filas = pool.consultar(
            "SELECT * FROM rag_chunks WHERE proyecto_id = %s ORDER BY id DESC",
            (int(proyecto_id),),
        )
    for fila in filas:
        fila["largo"] = len(str(fila.get("contenido") or ""))
        fila["demasiado_largo"] = fila["largo"] > LIMITE_CHUNK
    return filas


def crear_chunk(proyecto_id: int, contenido: str) -> dict[str, Any]:
    return pool.consultar_uno(
        "INSERT INTO rag_chunks (proyecto_id, contenido) VALUES (%s, %s) RETURNING *",
        (int(proyecto_id), _validar_chunk(contenido)),
    )


def actualizar_chunk(proyecto_id: int, chunk_id: int, contenido: str) -> dict[str, Any] | None:
    return pool.consultar_uno(
        """
        UPDATE rag_chunks
        SET contenido = %s, actualizado_en = NOW()
        WHERE proyecto_id = %s AND id = %s
        RETURNING *
        """,
        (_validar_chunk(contenido), int(proyecto_id), chunk_id),
    )


def alternar_chunk_activo(proyecto_id: int, chunk_id: int) -> dict[str, Any] | None:
    return pool.consultar_uno(
        "UPDATE rag_chunks SET activo = NOT activo, actualizado_en = NOW() "
        "WHERE proyecto_id = %s AND id = %s RETURNING *",
        (int(proyecto_id), chunk_id),
    )


def eliminar_chunk(proyecto_id: int, chunk_id: int) -> int:
    return pool.ejecutar(
        "DELETE FROM rag_chunks WHERE proyecto_id = %s AND id = %s",
        (int(proyecto_id), chunk_id),
    )


# --- Preguntas sin respuesta -------------------------------------------------
#
# Es una bandeja de trabajo, no un archivo: lo que un cliente preguntó y el bot
# no supo contestar, para que el dueño del negocio le cree el chunk que faltaba.
# Cerrada la pregunta, ya cumplió.

# Cuánto se queda a la vista una pregunta ya entendida. Es corto a propósito: lo
# único que hace falta después de marcarla es poder deshacer el clic si fue sin
# querer.
PREGUNTAS_RETENCION_HORAS = 24


def listar_preguntas_sin_respuesta(proyecto_id: int, limite: int = 200) -> list[dict[str, Any]]:
    return pool.consultar(
        """
        SELECT *,
               atendida_en + (%s || ' hours')::interval AS caduca_en
        FROM preguntas_sin_respuesta
        WHERE proyecto_id = %s
        ORDER BY atendida, creado_en DESC
        LIMIT %s
        """,
        (PREGUNTAS_RETENCION_HORAS, int(proyecto_id), int(limite)),
    )


def marcar_pregunta_atendida(proyecto_id: int, pregunta_id: int) -> int:
    """La pone en verde y le arranca las 24 horas.

    Como en los reportes, solo la primera vez: volver a pulsar no le regala otro
    día a algo ya resuelto.
    """
    return pool.ejecutar(
        "UPDATE preguntas_sin_respuesta SET atendida = TRUE, atendida_en = NOW() "
        "WHERE proyecto_id = %s AND id = %s AND NOT atendida",
        (int(proyecto_id), pregunta_id),
    )


def purgar_preguntas_atendidas(horas: int = PREGUNTAS_RETENCION_HORAS) -> int:
    """Borra las preguntas ya entendidas que cumplieron su plazo.

    Lo pendiente no se toca: una pregunta que nadie ha mirado sigue siendo un
    agujero en la base de conocimiento, tenga la edad que tenga.
    """
    return pool.ejecutar(
        """
        DELETE FROM preguntas_sin_respuesta
        WHERE atendida
          AND atendida_en IS NOT NULL
          AND atendida_en < NOW() - (%s || ' hours')::interval
        """,
        (int(horas),),
    )
