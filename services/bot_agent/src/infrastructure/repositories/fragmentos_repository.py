"""Lectura del catálogo de fragmentos vigente para el proyecto actual."""

from src.application.project_context import proyecto_actual
from src.infrastructure.repositories.postgres_conn import consultar, consultar_uno


def permitidos(agente: str) -> list[str] | None:
    """Ids autorizados; None significa que no pudo consultarse la fuente."""
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return None
    try:
        filas = consultar(
            """
            SELECT c.codigo || '.' || f.codigo AS fragment_id
            FROM agente_fragmentos f
            JOIN fragmento_categorias c ON c.id = f.categoria_id AND c.proyecto_id = f.proyecto_id
            JOIN agente_fragmento_asignaciones a
              ON a.fragmento_id = f.id AND a.proyecto_id = f.proyecto_id
            WHERE f.proyecto_id = %s AND c.activa AND f.activo
              AND f.variante_de_id IS NULL AND a.agente = %s
            ORDER BY c.codigo, f.codigo
            """,
            (proyecto_id, str(agente or "").upper()),
        )
        return [str(f["fragment_id"]) for f in filas]
    except Exception as exc:
        print(f"Error leyendo permisos de fragmentos: {exc}")
        return None


def obtener(fragment_id: str) -> dict | None | bool:
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return False
    try:
        return consultar_uno(
            """
            SELECT c.codigo || '.' || f.codigo AS fragment_id,
                   v.mensajes, v.reporte, v.retomar
            FROM agente_fragmentos f
            JOIN fragmento_categorias c ON c.id = f.categoria_id AND c.proyecto_id = f.proyecto_id
            JOIN agente_fragmento_versiones v ON v.id = f.version_activa_id
            WHERE f.proyecto_id = %s AND c.activa AND f.activo
              AND c.codigo || '.' || f.codigo = %s
            """,
            (proyecto_id, str(fragment_id or "")),
        )
    except Exception as exc:
        print(f"Error leyendo fragmento '{fragment_id}': {exc}")
        return False


def variante_de(fragment_id: str) -> str | None:
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return None
    try:
        fila = consultar_uno(
            """
            SELECT cv.codigo || '.' || fv.codigo AS fragment_id
            FROM agente_fragmentos base
            JOIN fragmento_categorias cb ON cb.id = base.categoria_id AND cb.proyecto_id = base.proyecto_id
            JOIN agente_fragmentos fv ON fv.variante_de_id = base.id AND fv.proyecto_id = base.proyecto_id
            JOIN fragmento_categorias cv ON cv.id = fv.categoria_id AND cv.proyecto_id = fv.proyecto_id
            WHERE base.proyecto_id = %s AND cb.codigo || '.' || base.codigo = %s
              AND cb.activa AND base.activo AND cv.activa AND fv.activo
              AND fv.condicion_variante = 'cliente_registrado'
            LIMIT 1
            """,
            (proyecto_id, str(fragment_id or "")),
        )
        return str((fila or {}).get("fragment_id") or "")
    except Exception as exc:
        print(f"Error leyendo variante de '{fragment_id}': {exc}")
        return None
