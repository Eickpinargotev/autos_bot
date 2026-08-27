"""Catálogo versionado de fragmentos literales de los agentes IA."""

import json
import re
from pathlib import Path
from typing import Any

import psycopg2.extras

from src.db import pool

AGENTES = (
    "SUPERVISOR", "GENERAL", "CURSO_TEORICO", "ALQUILER",
    "CLASES", "DICTAMEN", "TRAMITES",
)
NOMBRES_AGENTES = {
    "SUPERVISOR": "Supervisor",
    "GENERAL": "Agente general",
    "CURSO_TEORICO": "Curso teórico",
    "ALQUILER": "Alquiler",
    "CLASES": "Clases prácticas",
    "DICTAMEN": "Dictamen médico",
    "TRAMITES": "Trámites",
}

# Catálogo vigente antes de la migración a Postgres. También define la semilla
# de permisos; las variantes son técnicas y heredan la selección de su base.
ASIGNACIONES_INICIALES: dict[str, tuple[str, ...]] = {
    "SUPERVISOR": ("QUEJA.Q1", "WIN.W1"),
    "GENERAL": ("GENERAL.G1", "GENERAL.G3", "GENERAL.G7"),
    "CURSO_TEORICO": ("GENERAL.G4",),
    "ALQUILER": (
        "Alquiler.A1", "GENERAL.G7", "GENERAL.G35", "GENERAL.G11",
        "GENERAL.G13", "GENERAL.G16", "GENERAL.G19", "GENERAL.G20",
        "GENERAL.G21", "GENERAL.G22", "GENERAL.G25", "GENERAL.G28",
        "GENERAL.G29", "GENERAL.G30", "GENERAL.G31", "GENERAL.G32",
    ),
    "CLASES": ("CLASES.C1", "CLASES.C2", "CLASES.C5"),
    "DICTAMEN": ("DICTAMEN.D1",),
    "TRAMITES": (),
}
VARIANTES_INICIALES = {
    "DICTAMEN.D1": "DICTAMEN.D1_1",
    "GENERAL.G16": "GENERAL.G16_1",
    "GENERAL.G28": "GENERAL.G28_1",
}

_CODIGO_RE = re.compile(r"^[A-Za-z0-9_]+$")
_FRAG_RE = re.compile(r"\[\[frag:([A-Za-z0-9_.]+)\]\]")


def _codigo(valor: str, etiqueta: str) -> str:
    valor = str(valor or "").strip()
    if not valor or not _CODIGO_RE.fullmatch(valor):
        raise ValueError(f"{etiqueta} solo puede contener letras, números y guion bajo.")
    return valor


def _agentes_validos(agentes: list[str] | tuple[str, ...]) -> list[str]:
    salida = list(dict.fromkeys(str(a or "").strip().upper() for a in agentes))
    if not salida:
        raise ValueError("El fragmento debe estar asignado al menos a un agente.")
    if any(a not in AGENTES for a in salida):
        raise ValueError("Se indicó un agente desconocido.")
    return salida


def _mensajes_validos(mensajes: list[str] | tuple[str, ...]) -> list[str]:
    salida = [str(m or "").strip() for m in mensajes if str(m or "").strip()]
    if not salida:
        raise ValueError("El fragmento necesita al menos un mensaje con texto.")
    return salida


def listar(proyecto_id: int, incluir_archivados: bool = True) -> list[dict[str, Any]]:
    condicion = "" if incluir_archivados else "AND c.activa AND f.activo"
    filas = pool.consultar(
        f"""
        SELECT c.id AS categoria_id, c.codigo AS categoria_codigo,
               c.nombre AS categoria_nombre, c.activa AS categoria_activa,
               f.id, f.codigo, f.activo, f.variante_de_id, f.condicion_variante,
               v.version, v.mensajes, v.reporte, v.retomar, v.creado_por,
               v.creado_en,
               COALESCE(array_agg(a.agente ORDER BY a.agente)
                   FILTER (WHERE a.agente IS NOT NULL), ARRAY[]::varchar[]) AS agentes
        FROM fragmento_categorias c
        LEFT JOIN agente_fragmentos f
          ON f.categoria_id = c.id AND f.proyecto_id = c.proyecto_id
        LEFT JOIN agente_fragmento_versiones v ON v.id = f.version_activa_id
        LEFT JOIN agente_fragmento_asignaciones a
          ON a.fragmento_id = f.id AND a.proyecto_id = f.proyecto_id
        WHERE c.proyecto_id = %s {condicion}
        GROUP BY c.id, f.id, v.id
        ORDER BY c.codigo, f.codigo
        """,
        (int(proyecto_id),),
    )
    categorias: dict[int, dict[str, Any]] = {}
    for fila in filas:
        cid = int(fila["categoria_id"])
        categoria = categorias.setdefault(cid, {
            "id": cid,
            "codigo": fila["categoria_codigo"],
            "nombre": fila["categoria_nombre"],
            "activa": fila["categoria_activa"],
            "fragmentos": [],
        })
        if fila.get("id") is None:
            continue
        fila["fragment_id"] = f"{fila['categoria_codigo']}.{fila['codigo']}"
        fila["mensajes"] = list(fila.get("mensajes") or [])
        fila["agentes"] = list(fila.get("agentes") or [])
        categoria["fragmentos"].append(fila)
    return list(categorias.values())


def obtener(proyecto_id: int, fragmento_id: int) -> dict[str, Any] | None:
    fila = pool.consultar_uno(
        """
        SELECT f.*, c.codigo AS categoria_codigo, c.nombre AS categoria_nombre,
               c.activa AS categoria_activa, v.version, v.mensajes, v.reporte,
               v.retomar, v.creado_por AS version_creada_por, v.creado_en AS version_creada_en
        FROM agente_fragmentos f
        JOIN fragmento_categorias c ON c.id = f.categoria_id AND c.proyecto_id = f.proyecto_id
        LEFT JOIN agente_fragmento_versiones v ON v.id = f.version_activa_id
        WHERE f.proyecto_id = %s AND f.id = %s
        """,
        (int(proyecto_id), int(fragmento_id)),
    )
    if not fila:
        return None
    fila["fragment_id"] = f"{fila['categoria_codigo']}.{fila['codigo']}"
    fila["mensajes"] = list(fila.get("mensajes") or [])
    fila["agentes"] = [r["agente"] for r in pool.consultar(
        "SELECT agente FROM agente_fragmento_asignaciones "
        "WHERE proyecto_id = %s AND fragmento_id = %s ORDER BY agente",
        (int(proyecto_id), int(fragmento_id)),
    )]
    return fila


def historial(proyecto_id: int, fragmento_id: int) -> list[dict[str, Any]]:
    if not obtener(proyecto_id, fragmento_id):
        return []
    filas = pool.consultar(
        "SELECT * FROM agente_fragmento_versiones WHERE proyecto_id = %s "
        "AND fragmento_id = %s ORDER BY version DESC",
        (int(proyecto_id), int(fragmento_id)),
    )
    for fila in filas:
        fila["mensajes"] = list(fila.get("mensajes") or [])
    return filas


def crear_categoria(proyecto_id: int, codigo: str, nombre: str, usuario: str) -> dict[str, Any]:
    codigo = _codigo(codigo, "El código de categoría")
    nombre = str(nombre or "").strip() or codigo
    if pool.consultar_uno(
        "SELECT id FROM fragmento_categorias WHERE proyecto_id = %s AND lower(codigo) = lower(%s)",
        (int(proyecto_id), codigo),
    ):
        raise ValueError(f"Ya existe la categoría «{codigo}».")
    return pool.consultar_uno(
        "INSERT INTO fragmento_categorias (proyecto_id, codigo, nombre, creado_por) "
        "VALUES (%s, %s, %s, %s) RETURNING *",
        (int(proyecto_id), codigo, nombre[:120], str(usuario)[:120]),
    )


def renombrar_categoria(proyecto_id: int, categoria_id: int, nombre: str) -> dict[str, Any] | None:
    nombre = str(nombre or "").strip()
    if not nombre:
        raise ValueError("El nombre visible no puede estar vacío.")
    return pool.consultar_uno(
        "UPDATE fragmento_categorias SET nombre = %s, actualizado_en = NOW() "
        "WHERE proyecto_id = %s AND id = %s RETURNING *",
        (nombre[:120], int(proyecto_id), int(categoria_id)),
    )


def _insertar_version(cur, proyecto_id: int, fragmento_id: int, mensajes: list[str],
                      reporte: str, retomar: str, usuario: str) -> dict[str, Any]:
    cur.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM agente_fragmento_versiones "
        "WHERE proyecto_id = %s AND fragmento_id = %s",
        (int(proyecto_id), int(fragmento_id)),
    )
    version = int(cur.fetchone()["version"])
    cur.execute(
        "INSERT INTO agente_fragmento_versiones "
        "(proyecto_id, fragmento_id, version, mensajes, reporte, retomar, creado_por) "
        "VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s) RETURNING *",
        (int(proyecto_id), int(fragmento_id), version, json.dumps(mensajes),
         str(reporte or "").strip(), str(retomar or "").strip(), str(usuario)[:120]),
    )
    nueva = dict(cur.fetchone())
    cur.execute(
        "UPDATE agente_fragmentos SET version_activa_id = %s, actualizado_en = NOW() "
        "WHERE proyecto_id = %s AND id = %s",
        (nueva["id"], int(proyecto_id), int(fragmento_id)),
    )
    return nueva


def crear_fragmento(proyecto_id: int, categoria_id: int, codigo: str, mensajes: list[str],
                     reporte: str, agentes: list[str], usuario: str) -> dict[str, Any]:
    codigo = _codigo(codigo, "El código del fragmento")
    mensajes = _mensajes_validos(mensajes)
    agentes = _agentes_validos(agentes)
    categoria = pool.consultar_uno(
        "SELECT * FROM fragmento_categorias WHERE proyecto_id = %s AND id = %s AND activa",
        (int(proyecto_id), int(categoria_id)),
    )
    if not categoria:
        raise ValueError("La categoría no existe o está archivada.")
    if pool.consultar_uno(
        "SELECT id FROM agente_fragmentos WHERE proyecto_id = %s AND categoria_id = %s "
        "AND lower(codigo) = lower(%s)",
        (int(proyecto_id), int(categoria_id), codigo),
    ):
        raise ValueError(f"Ya existe el fragmento «{categoria['codigo']}.{codigo}».")
    with pool.conexion(autocommit=False) as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO agente_fragmentos "
                    "(proyecto_id, categoria_id, codigo, creado_por) VALUES (%s, %s, %s, %s) RETURNING *",
                    (int(proyecto_id), int(categoria_id), codigo, str(usuario)[:120]),
                )
                fragmento = dict(cur.fetchone())
                _insertar_version(cur, proyecto_id, fragmento["id"], mensajes, reporte, "", usuario)
                for agente in agentes:
                    cur.execute(
                        "INSERT INTO agente_fragmento_asignaciones (proyecto_id, fragmento_id, agente) "
                        "VALUES (%s, %s, %s)",
                        (int(proyecto_id), fragmento["id"], agente),
                    )
            conn.commit()
            return obtener(proyecto_id, fragmento["id"])
        except Exception:
            conn.rollback()
            raise


def guardar_fragmento(proyecto_id: int, fragmento_id: int, mensajes: list[str], reporte: str,
                       agentes: list[str], usuario: str) -> dict[str, Any]:
    actual = obtener(proyecto_id, fragmento_id)
    if not actual:
        raise ValueError("Ese fragmento no existe.")
    mensajes = _mensajes_validos(mensajes)
    es_variante = bool(actual.get("variante_de_id"))
    agentes = [] if es_variante else _agentes_validos(agentes)
    retirados = set(actual["agentes"]) - set(agentes)
    referencias = referencias_activas(proyecto_id, actual["fragment_id"])
    conflicto = sorted(retirados & set(referencias))
    if conflicto:
        nombres = ", ".join(NOMBRES_AGENTES.get(a, a) for a in conflicto)
        raise ValueError(f"Primero quite {actual['fragment_id']} de los prompts activos: {nombres}.")
    with pool.conexion(autocommit=False) as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (int(proyecto_id), int(fragmento_id)))
                _insertar_version(cur, proyecto_id, fragmento_id, mensajes, reporte, actual.get("retomar", ""), usuario)
                if not es_variante:
                    cur.execute(
                        "DELETE FROM agente_fragmento_asignaciones WHERE proyecto_id = %s AND fragmento_id = %s",
                        (int(proyecto_id), int(fragmento_id)),
                    )
                    for agente in agentes:
                        cur.execute(
                            "INSERT INTO agente_fragmento_asignaciones (proyecto_id, fragmento_id, agente) VALUES (%s, %s, %s)",
                            (int(proyecto_id), int(fragmento_id), agente),
                        )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return obtener(proyecto_id, fragmento_id)


def restaurar(proyecto_id: int, fragmento_id: int, version: int, usuario: str) -> dict[str, Any] | None:
    anterior = pool.consultar_uno(
        "SELECT * FROM agente_fragmento_versiones WHERE proyecto_id = %s "
        "AND fragmento_id = %s AND version = %s",
        (int(proyecto_id), int(fragmento_id), int(version)),
    )
    actual = obtener(proyecto_id, fragmento_id)
    if not anterior or not actual:
        return None
    with pool.conexion(autocommit=False) as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (int(proyecto_id), int(fragmento_id)))
                _insertar_version(cur, proyecto_id, fragmento_id, list(anterior["mensajes"]),
                                  anterior["reporte"], anterior["retomar"], usuario)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return obtener(proyecto_id, fragmento_id)


def referencias_activas(proyecto_id: int, fragment_id: str) -> list[str]:
    referencia = f"[[frag:{fragment_id}]]"
    roles = []
    for fila in pool.consultar(
        "SELECT tipo, contenido FROM proyecto_instrucciones WHERE proyecto_id = %s AND activa",
        (int(proyecto_id),),
    ):
        if referencia in str(fila.get("contenido") or ""):
            roles.append(str(fila["tipo"]).upper())
    return roles


def archivar_fragmento(proyecto_id: int, fragmento_id: int) -> None:
    actual = obtener(proyecto_id, fragmento_id)
    if not actual:
        raise ValueError("Ese fragmento no existe.")
    referencias = referencias_activas(proyecto_id, actual["fragment_id"])
    if referencias:
        nombres = ", ".join(NOMBRES_AGENTES.get(a, a) for a in referencias)
        raise ValueError(f"Primero quite {actual['fragment_id']} de los prompts activos: {nombres}.")
    pool.ejecutar(
        "UPDATE agente_fragmentos SET activo = FALSE, actualizado_en = NOW() "
        "WHERE proyecto_id = %s AND id = %s",
        (int(proyecto_id), int(fragmento_id)),
    )


def reactivar_fragmento(proyecto_id: int, fragmento_id: int) -> None:
    pool.ejecutar(
        "UPDATE agente_fragmentos f SET activo = TRUE, actualizado_en = NOW() "
        "FROM fragmento_categorias c WHERE f.categoria_id = c.id AND c.activa "
        "AND f.proyecto_id = %s AND f.id = %s",
        (int(proyecto_id), int(fragmento_id)),
    )


def archivar_categoria(proyecto_id: int, categoria_id: int) -> None:
    fragmentos = [f for c in listar(proyecto_id) if c["id"] == int(categoria_id) for f in c["fragmentos"]]
    conflictos = []
    for fragmento in fragmentos:
        if refs := referencias_activas(proyecto_id, fragmento["fragment_id"]):
            conflictos.append(f"{fragmento['fragment_id']} ({', '.join(refs)})")
    if conflictos:
        raise ValueError("Primero quite de los prompts activos: " + "; ".join(conflictos) + ".")
    with pool.conexion(autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE agente_fragmentos SET activo = FALSE WHERE proyecto_id = %s AND categoria_id = %s",
                            (int(proyecto_id), int(categoria_id)))
                cur.execute("UPDATE fragmento_categorias SET activa = FALSE, actualizado_en = NOW() "
                            "WHERE proyecto_id = %s AND id = %s", (int(proyecto_id), int(categoria_id)))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def reactivar_categoria(proyecto_id: int, categoria_id: int) -> None:
    with pool.conexion(autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE fragmento_categorias SET activa = TRUE, actualizado_en = NOW() "
                    "WHERE proyecto_id = %s AND id = %s",
                    (int(proyecto_id), int(categoria_id)),
                )
                cur.execute(
                    "UPDATE agente_fragmentos SET activo = TRUE, actualizado_en = NOW() "
                    "WHERE proyecto_id = %s AND categoria_id = %s",
                    (int(proyecto_id), int(categoria_id)),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def validar_referencias_prompt(proyecto_id: int, tipo: str, contenido: str) -> None:
    agente = str(tipo or "").upper()
    if agente not in AGENTES:
        return
    referencias = list(dict.fromkeys(_FRAG_RE.findall(str(contenido or ""))))
    if not referencias:
        return
    filas = pool.consultar(
        """
        SELECT c.codigo || '.' || f.codigo AS fragment_id, c.activa, f.activo,
               EXISTS (SELECT 1 FROM agente_fragmento_asignaciones a
                       WHERE a.proyecto_id = f.proyecto_id AND a.fragmento_id = f.id AND a.agente = %s) AS permitido
        FROM agente_fragmentos f
        JOIN fragmento_categorias c ON c.id = f.categoria_id AND c.proyecto_id = f.proyecto_id
        WHERE f.proyecto_id = %s AND (c.codigo || '.' || f.codigo) = ANY(%s)
        """,
        (agente, int(proyecto_id), referencias),
    )
    por_id = {fila["fragment_id"]: fila for fila in filas}
    errores = []
    for ref in referencias:
        fila = por_id.get(ref)
        if not fila:
            errores.append(f"{ref} no existe")
        elif not fila["activa"] or not fila["activo"]:
            errores.append(f"{ref} está archivado")
        elif not fila["permitido"]:
            errores.append(f"{ref} no está asignado a {NOMBRES_AGENTES[agente]}")
    if errores:
        raise ValueError("Referencias de fragmentos inválidas: " + "; ".join(errores) + ".")


def _ruta_mensajes() -> Path | None:
    candidatos = (Path("/mensajes.json"), Path("mensajes.json"), Path("../../mensajes.json"))
    return next((ruta for ruta in candidatos if ruta.is_file()), None)


def sembrar_catalogos_faltantes(proyecto_id: int | None = None) -> int:
    """Copia la semilla histórica del JSON sin sobrescribir ediciones del panel."""
    ruta = _ruta_mensajes()
    if not ruta:
        return 0
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    proyectos = ([{"id": int(proyecto_id)}] if proyecto_id else
                 pool.consultar("SELECT id FROM clientes_whatsapp ORDER BY id"))
    asignaciones_por_id: dict[str, list[str]] = {}
    for agente, ids in ASIGNACIONES_INICIALES.items():
        for fid in ids:
            asignaciones_por_id.setdefault(fid, []).append(agente)
    ids_semilla = set(asignaciones_por_id) | set(VARIANTES_INICIALES.values())
    creados = 0
    for proyecto in proyectos:
        pid = int(proyecto["id"])
        fragmentos_creados: dict[str, int] = {}
        for fid in sorted(ids_semilla):
            categoria_codigo, codigo = fid.split(".", 1)
            nodo = datos.get(categoria_codigo, {}).get(codigo)
            if not nodo:
                continue
            categoria = pool.consultar_uno(
                "SELECT id FROM fragmento_categorias WHERE proyecto_id = %s AND codigo = %s",
                (pid, categoria_codigo),
            ) or pool.consultar_uno(
                "INSERT INTO fragmento_categorias (proyecto_id, codigo, nombre, creado_por) "
                "VALUES (%s, %s, %s, 'sistema') RETURNING id",
                (pid, categoria_codigo, categoria_codigo.replace("_", " ").title()),
            )
            existente = pool.consultar_uno(
                "SELECT id FROM agente_fragmentos WHERE proyecto_id = %s AND categoria_id = %s AND codigo = %s",
                (pid, categoria["id"], codigo),
            )
            if existente:
                fragmentos_creados[fid] = int(existente["id"])
                continue
            with pool.conexion(autocommit=False) as conn:
                try:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute(
                            "INSERT INTO agente_fragmentos (proyecto_id, categoria_id, codigo, creado_por) "
                            "VALUES (%s, %s, %s, 'sistema') RETURNING id",
                            (pid, categoria["id"], codigo),
                        )
                        fragmento = dict(cur.fetchone())
                        _insertar_version(cur, pid, fragmento["id"], list(nodo.get("mensajes") or []),
                                          nodo.get("reporte", ""), nodo.get("retomar", ""), "sistema")
                        for agente in asignaciones_por_id.get(fid, []):
                            cur.execute(
                                "INSERT INTO agente_fragmento_asignaciones (proyecto_id, fragmento_id, agente) "
                                "VALUES (%s, %s, %s)", (pid, fragmento["id"], agente),
                            )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            fragmentos_creados[fid] = int(fragmento["id"])
            creados += 1
        for base, variante in VARIANTES_INICIALES.items():
            base_id = fragmentos_creados.get(base)
            variante_id = fragmentos_creados.get(variante)
            if base_id and variante_id:
                pool.ejecutar(
                    "UPDATE agente_fragmentos SET variante_de_id = %s, condicion_variante = 'cliente_registrado' "
                    "WHERE proyecto_id = %s AND id = %s",
                    (base_id, pid, variante_id),
                )
    return creados
