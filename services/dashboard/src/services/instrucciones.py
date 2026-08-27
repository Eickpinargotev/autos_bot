"""Capas comerciales editables y configuración de recordatorios por proyecto."""

from typing import Any

import psycopg2.extras

from src.db import pool

LIMITE = 20000
TIPOS_EDITABLES = (
    "supervisor",
    "general",
    "curso_teorico",
    "alquiler",
    "clases",
    "dictamen",
    "tramites",
    "recordatorio",
)
# `principal` se conserva para no perder el historial de la pantalla anterior,
# pero ya no se aplica al agente ni se muestra: cada playbook es independiente.
TIPOS = frozenset({"principal", *TIPOS_EDITABLES})
MAX_INTERVALO_MINUTOS = 20160
_INICIO_PLAYBOOK_RECORDATORIO = "═══ CUÁNDO NO ENVIAR"

METADATOS = {
    "supervisor": {
        "nombre": "Supervisor",
        "codigo": "supervisor_agent",
        "descripcion": "Coordina la conversación, atiende recepción y enruta cada caso al especialista correcto.",
    },
    "general": {
        "nombre": "Agente general",
        "codigo": "general_agent",
        "descripcion": "Ordena el proceso para obtener la licencia y entrega cada fase al área correspondiente.",
    },
    "curso_teorico": {
        "nombre": "Curso teórico",
        "codigo": "curso_teorico_agent",
        "descripcion": "Atiende matrícula, examen teórico, pagos, reingresos y acceso a la plataforma de estudio.",
    },
    "alquiler": {
        "nombre": "Alquiler",
        "codigo": "alquiler_agent",
        "descripcion": "Gestiona el alquiler del vehículo adecuado para la prueba práctica de manejo.",
    },
    "clases": {
        "nombre": "Clases prácticas",
        "codigo": "clases_agent",
        "descripcion": "Atiende consultas y solicitudes de lecciones prácticas de manejo.",
    },
    "dictamen": {
        "nombre": "Dictamen médico",
        "codigo": "dictamen_agent",
        "descripcion": "Guía al cliente para solicitar y completar el dictamen médico.",
    },
    "tramites": {
        "nombre": "Trámites",
        "codigo": "tramites_agent",
        "descripcion": "Informa y encamina renovaciones, homologaciones, permisos, citas y otros trámites.",
    },
    "recordatorio": {
        "nombre": "Recordatorios",
        "codigo": "followup_agent",
        "descripcion": "Decide cuándo retomar una conversación pendiente y redacta el seguimiento.",
    },
}

PROMPT_PRINCIPAL_INICIAL = (
    "Eres Enrique, asesor de una escuela de manejo en Costa Rica. Atiende con un tono "
    "directo, cálido y profesional. Trata siempre al cliente de usted; nunca lo tutees. "
    "Ayuda a entender qué servicio necesita y a avanzar al siguiente paso, sin inventar "
    "precios, horarios, enlaces ni requisitos."
)
PROMPT_RECORDATORIO_INICIAL = (
    "Retoma la conversación de manera breve, cordial y natural. Recuerda únicamente el "
    "paso que quedó pendiente y formula como máximo una pregunta. Usa “usted”, nunca "
    "tutees, y no presiones al cliente. Cuando envíes un recordatorio, inicia con: 📌 Hola!!!"
)


def _tipo_valido(tipo: str) -> str:
    tipo = str(tipo or "").strip().lower()
    if tipo not in TIPOS:
        raise ValueError("Tipo de prompt desconocido.")
    return tipo


def activa(proyecto_id: int, tipo: str = "principal") -> dict[str, Any]:
    tipo = _tipo_valido(tipo)
    return pool.consultar_uno(
        "SELECT * FROM proyecto_instrucciones "
        "WHERE proyecto_id = %s AND tipo = %s AND activa ORDER BY version DESC LIMIT 1",
        (int(proyecto_id), tipo),
    ) or {
        "version": 0,
        "tipo": tipo,
        "contenido": "",
        "activa": True,
    }


def historial(
    proyecto_id: int, tipo: str = "principal", limite: int = 20
) -> list[dict[str, Any]]:
    tipo = _tipo_valido(tipo)
    return pool.consultar(
        "SELECT * FROM proyecto_instrucciones WHERE proyecto_id = %s AND tipo = %s "
        "ORDER BY version DESC LIMIT %s",
        (int(proyecto_id), tipo, int(limite)),
    )


def todas_para_panel(proyecto_id: int, limite_historial: int = 20) -> dict[str, dict[str, Any]]:
    """Prompts activos e historiales de todos los agentes en una consulta."""
    filas = pool.consultar(
        """
        SELECT * FROM (
            SELECT i.*, ROW_NUMBER() OVER (
                PARTITION BY tipo ORDER BY version DESC
            ) AS posicion
            FROM proyecto_instrucciones i
            WHERE proyecto_id = %s AND tipo = ANY(%s)
        ) versiones
        WHERE posicion <= %s
        ORDER BY tipo, version DESC
        """,
        (int(proyecto_id), list(TIPOS_EDITABLES), int(limite_historial)),
    )
    salida: dict[str, dict[str, Any]] = {}
    for tipo in TIPOS_EDITABLES:
        historial_tipo = [fila for fila in filas if fila["tipo"] == tipo]
        activa_tipo = next((fila for fila in historial_tipo if fila["activa"]), None) or {
            "version": 0, "tipo": tipo, "contenido": "", "activa": True
        }
        salida[tipo] = {"activa": activa_tipo, "historial": historial_tipo}
    return salida


def activas_para_panel(proyecto_id: int) -> dict[str, dict[str, Any]]:
    """Los ocho prompts vigentes; los historiales se cargan al abrirlos."""
    filas = pool.consultar(
        "SELECT * FROM proyecto_instrucciones "
        "WHERE proyecto_id = %s AND activa AND tipo = ANY(%s)",
        (int(proyecto_id), list(TIPOS_EDITABLES)),
    )
    por_tipo = {fila["tipo"]: fila for fila in filas}
    return {
        tipo: por_tipo.get(tipo) or {
            "version": 0, "tipo": tipo, "contenido": "", "activa": True
        }
        for tipo in TIPOS_EDITABLES
    }


def guardar(
    proyecto_id: int, contenido: str, usuario: str, tipo: str = "principal"
) -> dict[str, Any]:
    tipo = _tipo_valido(tipo)
    contenido = str(contenido or "").strip()
    # Las primeras versiones del recordatorio incluían por delante el contrato
    # JSON hoy protegido. Un rollback histórico recupera solo el playbook, no
    # vuelve a exponer ni duplica esa capa técnica.
    if tipo == "recordatorio" and _INICIO_PLAYBOOK_RECORDATORIO in contenido:
        contenido = contenido[contenido.index(_INICIO_PLAYBOOK_RECORDATORIO):].strip()
    if not contenido:
        raise ValueError("El prompt no puede estar vacío.")
    if len(contenido) > LIMITE:
        raise ValueError(f"El prompt no puede superar {LIMITE} caracteres.")
    # Una etiqueta inválida terminaría descartada por el guardrail del bot y el
    # cliente recibiría una respuesta vacía. Se detecta mientras se edita.
    from src.services import fragmentos

    fragmentos.validar_referencias_prompt(proyecto_id, tipo, contenido)
    actual = activa(proyecto_id, tipo)
    if actual.get("version") and str(actual.get("contenido") or "").strip() == contenido:
        return {**actual, "sin_cambios": True}
    # El índice parcial exige que apagar la anterior e insertar la nueva sean
    # una transacción. El candado evita que dos guardados simultáneos calculen
    # la misma versión.
    with pool.conexion(autocommit=False) as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (int(proyecto_id), sorted(TIPOS).index(tipo) + 1),
                )
                cur.execute(
                    "UPDATE proyecto_instrucciones SET activa = FALSE "
                    "WHERE proyecto_id = %s AND tipo = %s AND activa",
                    (int(proyecto_id), tipo),
                )
                cur.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS version "
                    "FROM proyecto_instrucciones WHERE proyecto_id = %s AND tipo = %s",
                    (int(proyecto_id), tipo),
                )
                version = int(cur.fetchone()["version"])
                cur.execute(
                    "INSERT INTO proyecto_instrucciones "
                    "(proyecto_id, tipo, version, contenido, creado_por) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING *",
                    (
                        int(proyecto_id), tipo, version, contenido,
                        str(usuario)[:120],
                    ),
                )
                fila = dict(cur.fetchone())
            conn.commit()
            return fila
        except Exception:
            conn.rollback()
            raise


def activar(
    proyecto_id: int, version: int, usuario: str, tipo: str = "principal"
) -> dict[str, Any] | None:
    tipo = _tipo_valido(tipo)
    objetivo = pool.consultar_uno(
        "SELECT contenido FROM proyecto_instrucciones "
        "WHERE proyecto_id = %s AND tipo = %s AND version = %s",
        (int(proyecto_id), tipo, int(version)),
    )
    if not objetivo or not str(objetivo.get("contenido") or "").strip():
        return None
    # Restaurar crea una versión nueva: el historial nunca se reescribe.
    return guardar(proyecto_id, objetivo["contenido"], usuario, tipo)


def configuracion_recordatorios(proyecto_id: int) -> dict[str, Any]:
    return pool.consultar_uno(
        "SELECT * FROM proyecto_recordatorios WHERE proyecto_id = %s",
        (int(proyecto_id),),
    ) or {
        "proyecto_id": int(proyecto_id),
        "habilitado": True,
        "intervalo_minutos": 60,
        "maximo_recordatorios": 2,
    }


def guardar_configuracion_recordatorios(
    proyecto_id: int,
    habilitado: bool,
    cantidad: int,
    unidad: str,
    usuario: str,
    maximo_recordatorios: int | str | None = None,
) -> dict[str, Any]:
    minutos = validar_configuracion_recordatorios(cantidad, unidad)
    maximo = (
        validar_maximo_recordatorios(maximo_recordatorios)
        if maximo_recordatorios not in (None, "") else None
    )
    return pool.consultar_uno(
        """
        INSERT INTO proyecto_recordatorios
            (proyecto_id, habilitado, intervalo_minutos, maximo_recordatorios,
             actualizado_por, actualizado_en)
        VALUES (%s, %s, %s, COALESCE(%s, 2), %s, NOW())
        ON CONFLICT (proyecto_id) DO UPDATE SET
            habilitado = EXCLUDED.habilitado,
            intervalo_minutos = EXCLUDED.intervalo_minutos,
            maximo_recordatorios = COALESCE(%s, proyecto_recordatorios.maximo_recordatorios),
            actualizado_por = EXCLUDED.actualizado_por,
            actualizado_en = NOW()
        RETURNING *
        """,
        (
            int(proyecto_id), bool(habilitado), minutos, maximo,
            str(usuario)[:120], maximo,
        ),
    )


def validar_maximo_recordatorios(valor: int | str) -> int:
    try:
        maximo = int(valor)
    except (TypeError, ValueError):
        raise ValueError("La cantidad de recordatorios tiene que ser un número entero.") from None
    if maximo < 1 or maximo > 5:
        raise ValueError("La cantidad de recordatorios debe estar entre 1 y 5.")
    return maximo


def validar_configuracion_recordatorios(cantidad: int, unidad: str) -> int:
    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        raise ValueError("El intervalo tiene que ser un número entero.") from None
    unidad = str(unidad or "").strip().lower()
    if unidad not in {"minutos", "horas"}:
        raise ValueError("La unidad del intervalo no es válida.")
    minutos = cantidad * (60 if unidad == "horas" else 1)
    if minutos < 1 or minutos > MAX_INTERVALO_MINUTOS:
        raise ValueError("El intervalo debe estar entre 1 minuto y 14 días.")
    return minutos
