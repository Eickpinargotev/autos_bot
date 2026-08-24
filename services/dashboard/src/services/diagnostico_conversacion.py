"""Exportación administrativa autocontenida de una conversación.

No expone razonamiento privado del modelo. Reúne lo que realmente se guardó:
entradas, decisiones estructuradas, herramientas, estados, salidas y errores.
"""

import html
import json
import re
import tempfile
import zipfile
from datetime import date, datetime
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from src.db import pool

SCHEMA_VERSION = "1.0"
MAX_PROVIDER_BYTES = 256 * 1024
MAX_MODEL_BYTES = 128 * 1024
_SECRET_KEY = re.compile(
    r"(?:authorization|cookie|access[_-]?token|refresh[_-]?token|webhook[_-]?token|(?:^|[_-])token(?:$|[_-])|secret|password|passwd|api[_-]?key|media[_-]?key|signature|credential)",
    re.I,
)
_BASE64 = re.compile(r"^[A-Za-z0-9+/=_-]{512,}$")
_SIGNED_QUERY = re.compile(r"(?:signature|sig|token|key|credential|expires|x-amz-)", re.I)


def _json_default(valor: Any) -> str:
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return str(valor)


def sanitizar(valor: Any, *, max_bytes: int = MAX_PROVIDER_BYTES) -> Any:
    """Redacta secretos de forma recursiva y aplica un límite serializado."""
    def recorrer(actual: Any, clave: str = "") -> Any:
        if _SECRET_KEY.search(clave):
            return "[REDACTADO]"
        if isinstance(actual, dict):
            return {str(k): recorrer(v, str(k)) for k, v in actual.items()}
        if isinstance(actual, (list, tuple)):
            return [recorrer(v, clave) for v in actual]
        if isinstance(actual, bytes):
            return f"[BINARIO OMITIDO: {len(actual)} bytes]"
        if isinstance(actual, str):
            texto = actual
            if texto.startswith(("http://", "https://")):
                partes = urlsplit(texto)
                if _SIGNED_QUERY.search(partes.query):
                    texto = urlunsplit((partes.scheme, partes.netloc, partes.path, "[REDACTADO]", ""))
            if len(texto) >= 512 and _BASE64.fullmatch(texto):
                return f"[BASE64 OMITIDO: {len(texto)} caracteres]"
            return texto
        return actual

    limpio = recorrer(valor)
    crudo = json.dumps(limpio, ensure_ascii=False, default=_json_default).encode("utf-8")
    if len(crudo) <= max_bytes:
        return limpio
    return {
        "truncado": True,
        "limite_bytes": max_bytes,
        "tamano_original_bytes": len(crudo),
        "vista_previa": crudo[: max_bytes - 256].decode("utf-8", errors="ignore"),
    }


def _fila(fila: dict[str, Any]) -> dict[str, Any]:
    resultado = dict(fila)
    limite = MAX_MODEL_BYTES if str(resultado.get("tool_name", "")).startswith(("agent.", "llm.")) else MAX_PROVIDER_BYTES
    resultado["entrada"] = sanitizar(resultado.get("entrada"), max_bytes=limite)
    resultado["salida"] = sanitizar(resultado.get("salida"), max_bytes=limite)
    return resultado


def datos(proyecto: dict[str, Any], client_id: str, canal: str) -> dict[str, Any] | None:
    mensajes = pool.consultar(
        """SELECT * FROM conversation_messages
           WHERE proyecto_id = %s AND client_id = %s AND canal = %s
           ORDER BY id ASC""",
        (int(proyecto["id"]), client_id, canal),
    )
    shots = pool.consultar(
        """SELECT id, fecha_hora, revisado, shot FROM conversation_shots
           WHERE proyecto_id = %s AND id_user = %s AND canal = %s
           ORDER BY fecha_hora ASC, id ASC""",
        (int(proyecto["id"]), client_id, canal),
    )
    if not mensajes and not shots:
        return None
    visibles = [_fila(m) for m in mensajes if m.get("direction") != "internal"]
    eventos = [_fila(m) for m in mensajes if m.get("direction") == "internal"]
    turnos = [
        {**{k: v for k, v in s.items() if k != "shot"}, "shot": sanitizar(s.get("shot"), max_bytes=MAX_MODEL_BYTES)}
        for s in shots
    ]
    timeline = (
        [{"kind": "message", **item} for item in visibles]
        + [{"kind": "internal_event", **item} for item in eventos]
        + [{"kind": "turn", **item} for item in turnos]
    )
    timeline.sort(key=lambda item: str(item.get("created_at") or item.get("fecha_hora") or ""))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "project": {
            "id": proyecto["id"],
            "name": proyecto.get("nombre", ""),
            "slug": proyecto.get("slug", ""),
            "timezone": proyecto.get("zona_horaria", ""),
        },
        "conversation": {"client_id": client_id, "channel": canal},
        "messages": visibles,
        "internal_events": eventos,
        "turns": turnos,
        "timeline": timeline,
        "limitations": [
            "No contiene razonamiento interno privado del modelo.",
            "Los payloads completos solo existen para eventos capturados después del despliegue de trazas completas.",
            "Secretos, binarios, base64 y parámetros firmados fueron redactados.",
        ],
    }


def _reporte_html(info: dict[str, Any]) -> str:
    def bloque(titulo: str, filas: list[dict[str, Any]]) -> str:
        elementos = []
        for fila in filas:
            momento = html.escape(str(fila.get("created_at") or fila.get("fecha_hora") or ""))
            nombre = html.escape(str(fila.get("tool_name") or fila.get("author") or fila.get("event_type") or "evento"))
            contenido = html.escape(json.dumps(fila, ensure_ascii=False, indent=2, default=_json_default))
            elementos.append(f"<article><h3>{momento} · {nombre}</h3><pre>{contenido}</pre></article>")
        return f"<section><h2>{html.escape(titulo)}</h2>{''.join(elementos) or '<p>Sin datos retenidos.</p>'}</section>"

    conv = info["conversation"]
    return """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Diagnóstico de conversación</title><style>body{font:14px system-ui;margin:0;background:#f5f5f5;color:#171717}main{max-width:1100px;margin:auto;padding:32px}header,article{background:white;border:1px solid #ddd;border-radius:12px;padding:16px;margin:12px 0}h1,h2,h3{line-height:1.2}h3{font-size:14px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f7f7;padding:12px;border-radius:8px;font-size:12px}</style></head><body><main>""" + f"<header><h1>Diagnóstico de conversación</h1><p>Proyecto: {html.escape(str(info['project']['name']))}</p><p>{html.escape(str(conv['channel']))} · {html.escape(str(conv['client_id']))}</p><p>Generado: {html.escape(info['generated_at'])}</p></header>" + bloque("Cronología completa", info["timeline"]) + "</main></body></html>"


def crear_zip(proyecto: dict[str, Any], client_id: str, canal: str):
    info = datos(proyecto, client_id, canal)
    if info is None:
        return None
    archivo = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode="w+b")
    with zipfile.ZipFile(archivo, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr("diagnostico.json", json.dumps(info, ensure_ascii=False, indent=2, default=_json_default))
        zipf.writestr("reporte.html", _reporte_html(info))
        zipf.writestr(
            "README.txt",
            "Diagnóstico autocontenido de una conversación.\n\n"
            "diagnostico.json: datos estructurados (schema_version 1.0).\n"
            "reporte.html: vista cronológica para abrir sin conexión.\n\n"
            "Seguridad: se redactaron credenciales, tokens, encabezados sensibles, base64, binarios y URLs firmadas. "
            "No se incluye razonamiento privado del modelo; sí entradas, decisiones estructuradas, herramientas y salidas guardadas. "
            "Los datos anteriores al despliegue pueden no tener payloads completos.\n",
        )
    archivo.seek(0)
    return archivo


def transmitir(archivo) -> Iterator[bytes]:
    try:
        while True:
            bloque = archivo.read(64 * 1024)
            if not bloque:
                break
            yield bloque
    finally:
        archivo.close()


def auditar(proyecto_id: int, admin_id: int, client_id: str, canal: str, ip: str) -> None:
    pool.ejecutar(
        """INSERT INTO diagnostico_descargas
           (proyecto_id, administrador_id, client_id, canal, ip)
           VALUES (%s, %s, %s, %s, %s)""",
        (int(proyecto_id), int(admin_id), client_id, canal, (ip or "")[:64]),
    )
