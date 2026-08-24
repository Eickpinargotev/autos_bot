"""Catálogos editables: mensajes, palabras clave y base de conocimiento."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from src.core import security
from src.core.plantillas import render
from src.services import bot_interno
from src.services import (
    archivos_catalogo,
    clientes_whatsapp,
    instrucciones,
    mensajeria,
    palabras_clave,
    tiempos_mensajes,
    trazabilidad,
)

router = APIRouter()


def _proyecto_id(usuario: dict) -> int:
    proyecto = clientes_whatsapp.por_usuario(usuario["id"])
    if not proyecto:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Esta cuenta no está vinculada a un proyecto")
    return int(proyecto["id"])


# --- Mensajes (plantillas en cadena) ----------------------------------------
#
# Aquí había además un catálogo de «Publicidad por ciudad»: cinco columnas de
# texto por ciudad, en su propia tabla y su propia pestaña. Era el mismo
# contenido que estos mensajes —las mismas claves, los mismos textos— pero con
# el adjunto escrito dentro del texto en vez de comprobado. Un mensaje se
# identifica por su CLAVE, y esa clave es lo que el bot reconoce cuando alguien
# llega por un anuncio preguntando por su ciudad; no hacía falta un segundo
# catálogo para lo mismo.

@router.get("/mensajes")
def listar_plantillas(request: Request, usuario=Depends(security.requiere_negocio)):
    """La lista, con el mensaje que se acaba de tocar ya abierto.

    `?abierto=<id>` existe para eso: tras guardar, la redirección vuelve aquí y
    sin esto habría que buscar otra vez dónde se estaba. `?parte=<orden>` hace lo
    mismo con la segunda ventana, la del mensaje suelto.
    """
    abierto = request.query_params.get("abierto", "")
    parte = request.query_params.get("parte", "")
    proyecto_id = _proyecto_id(usuario)
    tiempos = tiempos_mensajes.configuracion(proyecto_id)
    publicidad = [
        tiempos_mensajes.para_formulario(
            tiempos[f"publicidad_recordatorio_{indice}_segundos"]
        )
        for indice in range(1, 4)
    ]
    recordatorios = instrucciones.configuracion_recordatorios(proyecto_id)
    minutos = int(recordatorios.get("intervalo_minutos") or 60)
    recordatorios["cantidad"] = minutos // 60 if minutos % 60 == 0 else minutos
    recordatorios["unidad"] = "horas" if minutos % 60 == 0 else "minutos"
    return render(
        request,
        "plantillas.html",
        usuario,
        plantillas=mensajeria.listar_plantillas(proyecto_id),
        tiempos=tiempos,
        tiempos_publicidad=publicidad,
        config_recordatorios=recordatorios,
        abierto=int(abierto) if abierto.isdigit() else None,
        parte_abierta=int(parte) if parte.isdigit() else None,
    )


@router.post("/mensajes/configuracion")
def guardar_configuracion_de_tiempos(
    request: Request,
    intervalo_mensajes_segundos: str = Form(""),
    recordatorio_habilitado: str = Form(""),
    recordatorio_cantidad: str = Form(""),
    recordatorio_unidad: str = Form(""),
    publicidad_1_cantidad: str = Form(""),
    publicidad_1_unidad: str = Form(""),
    publicidad_2_cantidad: str = Form(""),
    publicidad_2_unidad: str = Form(""),
    publicidad_3_cantidad: str = Form(""),
    publicidad_3_unidad: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Guarda en una sola acción todos los tiempos visibles en Mensajes."""
    security.verificar_csrf(request, csrf)
    proyecto_id = _proyecto_id(usuario)
    recordatorios_publicidad = [
        (publicidad_1_cantidad, publicidad_1_unidad),
        (publicidad_2_cantidad, publicidad_2_unidad),
        (publicidad_3_cantidad, publicidad_3_unidad),
    ]
    try:
        # Se valida todo antes de escribir: un error en el último campo no debe
        # guardar silenciosamente la primera mitad del formulario.
        tiempos_mensajes.validar(intervalo_mensajes_segundos, recordatorios_publicidad)
        instrucciones.validar_configuracion_recordatorios(
            recordatorio_cantidad, recordatorio_unidad
        )
        tiempos_mensajes.guardar(
            proyecto_id,
            intervalo_mensajes_segundos,
            recordatorios_publicidad,
            usuario["usuario"],
        )
        instrucciones.guardar_configuracion_recordatorios(
            proyecto_id,
            bool(recordatorio_habilitado),
            recordatorio_cantidad,
            recordatorio_unidad,
            usuario["usuario"],
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/mensajes?configuracion=1&error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(
        url="/mensajes?aviso=Configuración+de+tiempos+guardada", status_code=303
    )


@router.post("/mensajes")
def crear_plantilla(
    request: Request,
    clave: str = Form(...),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Un mensaje nace con su clave y un primer mensaje vacío que rellenar."""
    security.verificar_csrf(request, csrf)
    try:
        proyecto_id = _proyecto_id(usuario)
        plantilla = mensajeria.crear_plantilla(proyecto_id, clave, usuario["usuario"])
    except ValueError as e:
        return RedirectResponse(url=f"/mensajes?error={quote(str(e))}", status_code=303)
    mensajeria.agregar_parte(proyecto_id, plantilla["id"])
    return RedirectResponse(url=f"/mensajes?abierto={plantilla['id']}", status_code=303)


@router.post("/mensajes/revisar")
def revisar_media_de_todos(
    request: Request, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    """Comprueba los adjuntos de TODO el catálogo de una vez.

    Se declara ANTES que `/mensajes/{plantilla_id}` a propósito: registrada
    después, «revisar» entraría por la ruta con parámetro y saldría un 422 por
    no ser un número. FastAPI resuelve por orden de declaración.
    """
    security.verificar_csrf(request, csrf)
    revisados, con_problema = mensajeria.revisar_todos_los_adjuntos(_proyecto_id(usuario))
    if not revisados:
        aviso = "Ningún mensaje tiene adjuntos que revisar."
    elif con_problema:
        aviso = f"{revisados} adjuntos revisados; {con_problema} con algo que arreglar."
    else:
        aviso = f"{revisados} adjuntos revisados: todos se abren bien."
    return RedirectResponse(url=f"/mensajes?aviso={quote(aviso)}", status_code=303)


@router.get("/mensajes/descargar")
def descargar_mensajes(usuario=Depends(security.requiere_negocio)):
    """Copia completa importable; por diseño no contiene enlaces de Facebook."""
    contenido = archivos_catalogo.exportar_mensajes(_proyecto_id(usuario))
    return Response(
        contenido,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mensajes.csv"'},
    )


@router.post("/mensajes/cargar")
def cargar_mensajes(
    request: Request,
    archivo: UploadFile = File(...),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Acepta el HTML de Google Sheets o un CSV descargado desde este panel."""
    security.verificar_csrf(request, csrf)
    try:
        datos = archivo.file.read(archivos_catalogo.MAX_ARCHIVO_BYTES + 1)
        resultado = archivos_catalogo.importar_mensajes(
            _proyecto_id(usuario), datos, archivo.filename or "", usuario["usuario"]
        )
    except ValueError as e:
        return RedirectResponse(url=f"/mensajes?error={quote(str(e))}", status_code=303)
    aviso = (
        f"Carga terminada: {resultado['creadas']} claves nuevas, "
        f"{resultado['actualizadas']} actualizadas y {resultado['partes']} mensajes."
    )
    if resultado["problemas"]:
        aviso += f" {resultado['problemas']} adjuntos necesitan revisión."
    return RedirectResponse(url=f"/mensajes?aviso={quote(aviso)}", status_code=303)


@router.post("/mensajes/{plantilla_id}")
def renombrar_plantilla(
    request: Request,
    plantilla_id: int,
    clave: str = Form(...),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    try:
        mensajeria.renombrar_plantilla(_proyecto_id(usuario), plantilla_id, clave)
    except ValueError as e:
        return RedirectResponse(
            url=f"/mensajes?abierto={plantilla_id}&error={quote(str(e))}", status_code=303
        )
    return RedirectResponse(url=f"/mensajes?abierto={plantilla_id}&aviso=Clave+guardada", status_code=303)


@router.post("/mensajes/{plantilla_id}/eliminar")
def eliminar_plantilla(
    request: Request, plantilla_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    mensajeria.eliminar_plantilla(_proyecto_id(usuario), plantilla_id)
    return RedirectResponse(url="/mensajes?aviso=Mensaje+eliminado", status_code=303)


@router.post("/mensajes/{plantilla_id}/parte")
def agregar_parte(
    request: Request, plantilla_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    mensajeria.agregar_parte(_proyecto_id(usuario), plantilla_id)
    return RedirectResponse(url=f"/mensajes?abierto={plantilla_id}", status_code=303)


@router.post("/mensajes/{plantilla_id}/parte/{orden}")
def guardar_parte(
    request: Request,
    plantilla_id: int,
    orden: int,
    texto: str = Form(""),
    con_media: str = Form(""),
    media_tipo: str = Form(""),
    media_ref: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Guarda un mensaje y comprueba su adjunto EN EL MOMENTO.

    La comprobación se hace aquí y no al enviar: un enlace mal copiado o un
    archivo de Drive sin permiso público no fallarían hasta el envío, y para
    entonces el cliente ya recibió media cadena.

    `con_media` es el switch «incluye imagen». Apagado significa que no hay
    adjunto, aunque el campo del ID conserve lo que había escrito: el switch es
    lo que manda, y así apagarlo no obliga además a borrar el texto a mano.
    """
    security.verificar_csrf(request, csrf)
    if con_media != "1":
        media_tipo, media_ref = "", ""

    parte = mensajeria.guardar_parte(
        _proyecto_id(usuario), plantilla_id, orden, texto, media_tipo, media_ref
    )
    destino = f"/mensajes?abierto={plantilla_id}&parte={orden}"
    if parte["media_ok"] is False:
        return RedirectResponse(url=f"{destino}&error={quote(parte['media_error'])}", status_code=303)

    aviso = "Guardado y adjunto comprobado" if parte["media_ref"] else "Guardado"
    return RedirectResponse(url=f"{destino}&aviso={quote(aviso)}", status_code=303)


@router.post("/mensajes/parte/{parte_id}/eliminar")
def eliminar_parte(
    request: Request,
    parte_id: int,
    plantilla_id: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Borra un mensaje de la cadena y vuelve a la ventana de donde salió."""
    security.verificar_csrf(request, csrf)
    mensajeria.eliminar_parte(_proyecto_id(usuario), parte_id)
    volver = f"?abierto={plantilla_id}&" if plantilla_id.isdigit() else "?"
    return RedirectResponse(url=f"/mensajes{volver}aviso=Mensaje+eliminado", status_code=303)


@router.post("/mensajes/{plantilla_id}/revisar")
def revisar_media(
    request: Request, plantilla_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    """Vuelve a comprobar los adjuntos, por si el permiso del archivo cambió."""
    security.verificar_csrf(request, csrf)
    mensajeria.revisar_media_de(_proyecto_id(usuario), plantilla_id)
    return RedirectResponse(url=f"/mensajes?abierto={plantilla_id}&aviso=Adjuntos+revisados", status_code=303)


# --- Palabras clave ----------------------------------------------------------
#
# Sección aparte de «Mensajes» a propósito. Se parecen (los dos son una cadena de
# textos con adjunto), pero un mensaje lo mandas TÚ a quien elijas y una palabra
# clave la dispara el CLIENTE escribiéndola, y arrastra el bloqueo de la
# conversación y unos recordatorios a futuro. Mezcladas, no había forma de saber
# cuál de las dos cosas estabas tocando.

@router.get("/palabras-clave")
def listar_palabras(request: Request, usuario=Depends(security.requiere_negocio)):
    abierta = request.query_params.get("abierta", "")
    pieza = request.query_params.get("pieza", "")
    return render(
        request,
        "palabras_clave.html",
        usuario,
        palabras=palabras_clave.listar(_proyecto_id(usuario)),
        abierta=int(abierta) if abierta.isdigit() else None,
        pieza_abierta=int(pieza) if pieza.isdigit() else None,
        max_minutos=palabras_clave.MAX_MINUTOS,
    )


@router.post("/palabras-clave")
def crear_palabra(
    request: Request,
    palabra: str = Form(...),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    try:
        creada = palabras_clave.crear(_proyecto_id(usuario), palabra, usuario["usuario"])
    except ValueError as e:
        return RedirectResponse(url=f"/palabras-clave?error={quote(str(e))}", status_code=303)
    return RedirectResponse(url=f"/palabras-clave?abierta={creada['id']}", status_code=303)


@router.post("/palabras-clave/{palabra_id}")
def renombrar_palabra(
    request: Request,
    palabra_id: int,
    palabra: str = Form(...),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    try:
        palabras_clave.renombrar(_proyecto_id(usuario), palabra_id, palabra)
    except ValueError as e:
        return _volver_a_palabra(palabra_id, error=str(e))
    return _volver_a_palabra(palabra_id, aviso="Palabra guardada")


@router.post("/palabras-clave/{palabra_id}/activa")
def alternar_palabra(
    request: Request, palabra_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    fila = palabras_clave.alternar_activa(_proyecto_id(usuario), palabra_id)
    estado = "activada" if (fila or {}).get("activa") else "desactivada; el bot deja de reconocerla"
    return RedirectResponse(url=f"/palabras-clave?aviso={quote(f'Palabra {estado}')}", status_code=303)


@router.post("/palabras-clave/{palabra_id}/eliminar")
def eliminar_palabra(
    request: Request, palabra_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    palabras_clave.eliminar(_proyecto_id(usuario), palabra_id)
    return RedirectResponse(url="/palabras-clave?aviso=Palabra+clave+eliminada", status_code=303)


@router.post("/palabras-clave/{palabra_id}/pieza")
def agregar_pieza(
    request: Request,
    palabra_id: int,
    tipo: str = Form(...),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    if tipo not in ("mensaje", "recordatorio"):
        return _volver_a_palabra(palabra_id, error="Eso no es ni un mensaje ni un recordatorio.")
    palabras_clave.agregar_pieza(_proyecto_id(usuario), palabra_id, tipo)
    return _volver_a_palabra(palabra_id)


@router.post("/palabras-clave/{palabra_id}/pieza/{pieza_id}")
def guardar_pieza(
    request: Request,
    palabra_id: int,
    pieza_id: int,
    texto: str = Form(""),
    con_media: str = Form(""),
    media_tipo: str = Form(""),
    media_ref: str = Form(""),
    minutos: str = Form(""),
    activo: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Guarda una pieza y comprueba su adjunto en el momento.

    `con_media` es el switch del adjunto y `activo` el del recordatorio: los dos
    mandan sobre los campos que revelan, así que apagarlos no obliga además a
    vaciar lo que había escrito.
    """
    security.verificar_csrf(request, csrf)
    if con_media != "1":
        media_tipo, media_ref = "", ""

    try:
        pieza = palabras_clave.guardar_pieza(
            _proyecto_id(usuario), pieza_id,
            texto=texto,
            media_tipo=media_tipo,
            media_ref=media_ref,
            minutos=minutos.strip() or None,
            activo=activo == "1",
        )
    except ValueError as e:
        return _volver_a_palabra(palabra_id, error=str(e), pieza=pieza_id)

    if pieza["media_ok"] is False:
        return _volver_a_palabra(palabra_id, error=pieza["media_error"], pieza=pieza_id)
    aviso = "Guardado y adjunto comprobado" if pieza["media_ref"] else "Guardado"
    return _volver_a_palabra(palabra_id, aviso=aviso, pieza=pieza_id)


@router.post("/palabras-clave/{palabra_id}/pieza/{pieza_id}/eliminar")
def eliminar_pieza(
    request: Request,
    palabra_id: int,
    pieza_id: int,
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    palabras_clave.eliminar_pieza(_proyecto_id(usuario), pieza_id)
    return _volver_a_palabra(palabra_id, aviso="Eliminado")


@router.post("/palabras-clave/{palabra_id}/revisar")
def revisar_media_palabra(
    request: Request, palabra_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    palabras_clave.revisar_media_de(_proyecto_id(usuario), palabra_id)
    return _volver_a_palabra(palabra_id, aviso="Adjuntos revisados")


def _volver_a_palabra(
    palabra_id: int, aviso: str = "", error: str = "", pieza: int | None = None
) -> RedirectResponse:
    """Devuelve a la ventana de la que salió el formulario, no a la lista."""
    destino = f"/palabras-clave?abierta={palabra_id}"
    if pieza:
        destino += f"&pieza={pieza}"
    if aviso:
        destino += f"&aviso={quote(aviso)}"
    elif error:
        destino += f"&error={quote(error)}"
    return RedirectResponse(url=destino, status_code=303)


# --- Base de conocimiento (la administra el NEGOCIO) -------------------------

@router.post("/conocimiento")
def crear_chunk(
    request: Request,
    contenido: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Un chunk es solo texto: ni tema ni título (ver migración 014)."""
    security.verificar_csrf(request, csrf)
    try:
        proyecto_id = _proyecto_id(usuario)
        fila = trazabilidad.crear_chunk(proyecto_id, contenido)
    except ValueError as e:
        return RedirectResponse(url=f"/conocimiento?error={quote(str(e))}", status_code=303)
    _pedir_reindexado(proyecto_id, fila["id"])
    return RedirectResponse(url="/conocimiento?aviso=Chunk agregado y enviado a vectorizar", status_code=303)


@router.get("/conocimiento/descargar")
def descargar_conocimiento(usuario=Depends(security.requiere_negocio)):
    contenido = archivos_catalogo.exportar_conocimiento(_proyecto_id(usuario))
    return Response(
        contenido,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="base_conocimiento.csv"'},
    )


@router.post("/conocimiento/cargar")
def cargar_conocimiento(
    request: Request,
    archivo: UploadFile = File(...),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    try:
        datos = archivo.file.read(archivos_catalogo.MAX_ARCHIVO_BYTES + 1)
        resultado = archivos_catalogo.importar_conocimiento(
            _proyecto_id(usuario), datos, archivo.filename or ""
        )
    except ValueError as e:
        return RedirectResponse(url=f"/conocimiento?error={quote(str(e))}", status_code=303)
    aviso = (
        f"Carga terminada: {resultado['creados']} contenidos nuevos y "
        f"{resultado['actualizados']} actualizados."
    )
    return RedirectResponse(url=f"/conocimiento?aviso={quote(aviso)}", status_code=303)


@router.post("/conocimiento/{chunk_id}")
def actualizar_chunk(
    request: Request,
    chunk_id: int,
    contenido: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    try:
        proyecto_id = _proyecto_id(usuario)
        trazabilidad.actualizar_chunk(proyecto_id, chunk_id, contenido)
    except ValueError as e:
        return RedirectResponse(url=f"/conocimiento?error={quote(str(e))}", status_code=303)
    _pedir_reindexado(proyecto_id, chunk_id)
    return RedirectResponse(url="/conocimiento?aviso=Chunk actualizado y vuelto a vectorizar", status_code=303)


@router.post("/conocimiento/{chunk_id}/activo")
def alternar_chunk(
    request: Request, chunk_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    proyecto_id = _proyecto_id(usuario)
    trazabilidad.alternar_chunk_activo(proyecto_id, chunk_id)
    _pedir_reindexado(proyecto_id, chunk_id)
    return RedirectResponse(url="/conocimiento", status_code=303)


@router.post("/conocimiento/{chunk_id}/eliminar")
def eliminar_chunk(
    request: Request, chunk_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    proyecto_id = _proyecto_id(usuario)
    trazabilidad.eliminar_chunk(proyecto_id, chunk_id)
    _pedir_reindexado(proyecto_id, chunk_id)
    return RedirectResponse(url="/conocimiento?aviso=Contenido eliminado", status_code=303)


def _pedir_reindexado(proyecto_id: int, chunk_id: int) -> None:
    """Avisa al bot para que reindexe el chunk en Qdrant al instante.

    Es una optimización, no un requisito: si el bot no responde, el RAG se
    actualiza igual en la siguiente sincronización perezosa
    (RAG_SYNC_TTL_SECONDS). Por eso el fallo solo se registra y no se propaga.
    """
    bot_interno.reindexar_chunk(proyecto_id, chunk_id)
