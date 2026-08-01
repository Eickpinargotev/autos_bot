"""Catálogos editables: ciudades, plantillas de mensaje y base de conocimiento."""

import httpx
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.core import security
from src.core.config import settings
from src.core.plantillas import render
from src.services import ciudades as svc_ciudades
from src.services import mensajeria, trazabilidad

router = APIRouter()


# --- Ciudades ----------------------------------------------------------------

@router.get("/ciudades")
def listar_ciudades(request: Request, usuario=Depends(security.requiere_negocio)):
    busqueda = request.query_params.get("q", "")
    filas = svc_ciudades.listar(busqueda)
    return render(
        request,
        "ciudades.html",
        usuario,
        ciudades=[{**fila, "avisos": svc_ciudades.avisos(fila)} for fila in filas],
        busqueda=busqueda,
        campos=svc_ciudades.CAMPOS_EDITABLES,
    )


@router.post("/ciudades")
def crear_ciudad(request: Request, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)):
    security.verificar_csrf(request, csrf)
    fila = svc_ciudades.crear(usuario["usuario"])
    return RedirectResponse(
        url=f"/ciudades?aviso=Ciudad creada (inactiva hasta que la completes).#ciudad-{fila['id']}",
        status_code=303,
    )


@router.post("/ciudades/{ciudad_id}/campo")
def guardar_campo(
    request: Request,
    ciudad_id: int,
    campo: str = Form(...),
    valor: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Guarda una celda y devuelve la fila repintada (edición en línea)."""
    security.verificar_csrf(request, csrf)
    try:
        fila = svc_ciudades.actualizar_campo(ciudad_id, campo, valor, usuario["usuario"])
    except ValueError as e:
        return render(request, "_error_inline.html", usuario, detalle=str(e))

    return render(
        request,
        "_ciudad_fila.html",
        usuario,
        ciudad={**fila, "avisos": svc_ciudades.avisos(fila)},
        campos=svc_ciudades.CAMPOS_EDITABLES,
    )


@router.post("/ciudades/{ciudad_id}/activo")
def alternar_ciudad(
    request: Request, ciudad_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    svc_ciudades.alternar_activo(ciudad_id, usuario["usuario"])
    return RedirectResponse(url="/ciudades", status_code=303)


@router.post("/ciudades/{ciudad_id}/eliminar")
def eliminar_ciudad(
    request: Request, ciudad_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    svc_ciudades.eliminar(ciudad_id)
    return RedirectResponse(url="/ciudades?aviso=Ciudad eliminada.", status_code=303)


# --- Mensajes (plantillas en cadena) ----------------------------------------

@router.get("/mensajes")
def listar_plantillas(request: Request, usuario=Depends(security.requiere_negocio)):
    """La lista, con el mensaje que se acaba de tocar ya desplegado.

    `?abierto=<id>` existe para eso: tras guardar una parte, la redirección
    vuelve aquí y sin esto la lista aparecería toda plegada y habría que buscar
    de nuevo dónde se estaba.
    """
    abierto = request.query_params.get("abierto", "")
    return render(
        request,
        "plantillas.html",
        usuario,
        plantillas=mensajeria.listar_plantillas(),
        abierto=int(abierto) if abierto.isdigit() else None,
    )


@router.post("/mensajes")
def crear_plantilla(
    request: Request,
    clave: str = Form(...),
    nombre: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    if mensajeria.buscar_por_clave(clave):
        return RedirectResponse(url=f"/mensajes?error={quote('Ya existe un mensaje con esa clave')}", status_code=303)
    try:
        plantilla = mensajeria.crear_plantilla(clave, nombre, usuario["usuario"])
    except ValueError as e:
        return RedirectResponse(url=f"/mensajes?error={quote(str(e))}", status_code=303)
    mensajeria.agregar_parte(plantilla["id"])
    return RedirectResponse(url=f"/mensajes?abierto={plantilla['id']}", status_code=303)


@router.post("/mensajes/{plantilla_id}")
def renombrar_plantilla(
    request: Request,
    plantilla_id: int,
    clave: str = Form(...),
    nombre: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    mensajeria.renombrar_plantilla(plantilla_id, clave, nombre)
    return RedirectResponse(url=f"/mensajes?abierto={plantilla_id}&aviso=Mensaje+actualizado", status_code=303)


@router.post("/mensajes/{plantilla_id}/eliminar")
def eliminar_plantilla(
    request: Request, plantilla_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    mensajeria.eliminar_plantilla(plantilla_id)
    return RedirectResponse(url="/mensajes?aviso=Mensaje+eliminado", status_code=303)


@router.post("/mensajes/{plantilla_id}/parte")
def agregar_parte(
    request: Request, plantilla_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    mensajeria.agregar_parte(plantilla_id)
    return RedirectResponse(url=f"/mensajes?abierto={plantilla_id}", status_code=303)


@router.post("/mensajes/{plantilla_id}/parte/{orden}")
def guardar_parte(
    request: Request,
    plantilla_id: int,
    orden: int,
    texto: str = Form(""),
    media_tipo: str = Form(""),
    media_ref: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Guarda una parte y comprueba su adjunto en el momento.

    Si el archivo no se puede abrir, se avisa aquí mismo en vez de dejar que el
    envío falle después.
    """
    security.verificar_csrf(request, csrf)
    parte = mensajeria.guardar_parte(plantilla_id, orden, texto, media_tipo, media_ref)
    if parte["media_ok"] is False:
        return RedirectResponse(
            url=f"/mensajes?abierto={plantilla_id}&error={quote(parte['media_error'])}", status_code=303
        )
    return RedirectResponse(url=f"/mensajes?abierto={plantilla_id}&aviso=Guardado", status_code=303)


@router.post("/mensajes/parte/{parte_id}/eliminar")
def eliminar_parte(
    request: Request, parte_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    mensajeria.eliminar_parte(parte_id)
    return RedirectResponse(url="/mensajes?aviso=Parte+eliminada", status_code=303)


@router.post("/mensajes/{plantilla_id}/revisar")
def revisar_media(
    request: Request, plantilla_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    """Vuelve a comprobar los adjuntos, por si el permiso del archivo cambió."""
    security.verificar_csrf(request, csrf)
    mensajeria.revisar_media_de(plantilla_id)
    return RedirectResponse(url=f"/mensajes?abierto={plantilla_id}&aviso=Adjuntos+revisados", status_code=303)


# --- Base de conocimiento (la administra el NEGOCIO) -------------------------

@router.post("/conocimiento")
def crear_chunk(
    request: Request,
    titulo: str = Form(""),
    contenido: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    fila = trazabilidad.crear_chunk(titulo, contenido)
    _pedir_reindexado(fila["id"])
    return RedirectResponse(url="/conocimiento?aviso=Contenido agregado", status_code=303)


@router.post("/conocimiento/{chunk_id}")
def actualizar_chunk(
    request: Request,
    chunk_id: int,
    titulo: str = Form(""),
    contenido: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    trazabilidad.actualizar_chunk(chunk_id, titulo, contenido)
    _pedir_reindexado(chunk_id)
    return RedirectResponse(url="/conocimiento?aviso=Contenido actualizado", status_code=303)


@router.post("/conocimiento/{chunk_id}/activo")
def alternar_chunk(
    request: Request, chunk_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    trazabilidad.alternar_chunk_activo(chunk_id)
    _pedir_reindexado(chunk_id)
    return RedirectResponse(url="/conocimiento", status_code=303)


@router.post("/conocimiento/{chunk_id}/eliminar")
def eliminar_chunk(
    request: Request, chunk_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    trazabilidad.eliminar_chunk(chunk_id)
    _pedir_reindexado(chunk_id)
    return RedirectResponse(url="/conocimiento?aviso=Contenido eliminado", status_code=303)


def _pedir_reindexado(chunk_id: int) -> None:
    """Avisa al bot para que reindexe el chunk en Qdrant al instante.

    Es una optimización, no un requisito: si el bot no responde, el RAG se
    actualiza igual en la siguiente sincronización perezosa
    (RAG_SYNC_TTL_SECONDS). Por eso el fallo solo se registra y no se propaga.
    """
    if not settings.INTERNAL_API_TOKEN or not settings.BOT_WEBHOOK_URL:
        return
    try:
        httpx.post(
            f"{settings.BOT_WEBHOOK_URL.rstrip('/')}/internal/rag/sync/{chunk_id}",
            params={"token": settings.INTERNAL_API_TOKEN},
            timeout=5.0,
        )
    except Exception as e:
        print(f"No se pudo pedir el reindexado inmediato del chunk {chunk_id}: {e}")
