"""Panel del NEGOCIO: lo que administra nuestro cliente, no nosotros.

Aquí «cliente» significa la persona que le escribe al bot. Todo lo de este
módulo depende de `requiere_negocio`: el administrador no ve estas páginas en
su menú porque no son su trabajo, y entra a ellas suplantando al negocio desde
`/admin/negocios/{id}` (que deja registro de quién entró).

Las conversaciones también viven aquí: son datos del proyecto. El administrador
solo llega a ellas entrando a la cuenta del negocio, con la suplantación auditada.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from src.core import security
from src.core.plantillas import render
from src.services import (
    bloqueos as bloqueos_temporales,
    bloqueos_permanentes,
    bot_interno,
    clientes_whatsapp,
    diagnostico_conversacion,
    instrucciones,
    trazabilidad,
)

router = APIRouter()


def _proyecto_del_usuario(usuario: dict) -> dict:
    proyecto = clientes_whatsapp.por_usuario(usuario["id"])
    if not proyecto:
        raise HTTPException(status_code=404, detail="Esta cuenta no está vinculada a un proyecto")
    return proyecto


# --- Instrucciones comerciales del agente -----------------------------------

@router.get("/agente/instrucciones")
def instrucciones_del_agente(request: Request, usuario=Depends(security.requiere_negocio)):
    proyecto = _proyecto_del_usuario(usuario)
    config_recordatorios = instrucciones.configuracion_recordatorios(proyecto["id"])
    minutos = int(config_recordatorios.get("intervalo_minutos") or 60)
    if minutos % 60 == 0:
        config_recordatorios["cantidad"] = minutos // 60
        config_recordatorios["unidad"] = "horas"
    else:
        config_recordatorios["cantidad"] = minutos
        config_recordatorios["unidad"] = "minutos"
    return render(
        request,
        "instrucciones.html",
        usuario,
        prompts={
            tipo: {
                "activa": instrucciones.activa(proyecto["id"], tipo),
                "historial": instrucciones.historial(proyecto["id"], tipo),
                "meta": instrucciones.METADATOS[tipo],
            }
            for tipo in instrucciones.TIPOS_EDITABLES
        },
        tipos=instrucciones.TIPOS_EDITABLES,
        config_recordatorios=config_recordatorios,
        limite=instrucciones.LIMITE,
    )


@router.post("/agente/prompts/{tipo}")
def guardar_instrucciones(
    request: Request,
    tipo: str,
    contenido: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto_del_usuario(usuario)
    try:
        resultado = instrucciones.guardar(proyecto["id"], contenido, usuario["usuario"], tipo)
    except ValueError as exc:
        return RedirectResponse(f"/agente/instrucciones?error={quote(str(exc))}", status_code=303)
    aviso = "Sin+cambios" if resultado.get("sin_cambios") else "Prompt+guardado"
    return RedirectResponse(f"/agente/instrucciones?aviso={aviso}", status_code=303)


@router.post("/agente/prompts/{tipo}/{version}/activar")
def restaurar_instrucciones(
    request: Request,
    tipo: str,
    version: int,
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto_del_usuario(usuario)
    try:
        restaurada = instrucciones.activar(proyecto["id"], version, usuario["usuario"], tipo)
    except ValueError:
        restaurada = None
    if not restaurada:
        raise HTTPException(status_code=404, detail="Esa versión no pertenece a tu proyecto")
    return RedirectResponse("/agente/instrucciones?aviso=Versión+restaurada", status_code=303)


@router.post("/agente/recordatorios/configuracion")
def guardar_configuracion_recordatorios(
    request: Request,
    habilitado: str = Form(""),
    cantidad: str = Form(""),
    unidad: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto_del_usuario(usuario)
    try:
        instrucciones.guardar_configuracion_recordatorios(
            proyecto["id"], bool(habilitado), cantidad, unidad, usuario["usuario"]
        )
    except ValueError as exc:
        return RedirectResponse(f"/agente/instrucciones?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(
        "/agente/instrucciones?aviso=Configuración+de+recordatorios+guardada",
        status_code=303,
    )


# --- Conversaciones del proyecto --------------------------------------------

def _datos_lista_conversaciones(request: Request, usuario: dict) -> dict:
    proyecto = _proyecto_del_usuario(usuario)
    busqueda = request.query_params.get("q", "")
    conversaciones = trazabilidad.listar_conversaciones(proyecto["id"], busqueda)
    return {
        "proyecto": proyecto,
        "negocio_id": proyecto["id"],
        "conversaciones": conversaciones,
        "busqueda": busqueda,
    }


@router.get("/conversaciones")
def conversaciones(request: Request, usuario=Depends(security.requiere_negocio)):
    return render(request, "conversaciones.html", usuario)


@router.get("/conversaciones/lista")
def conversaciones_lista(request: Request, usuario=Depends(security.requiere_negocio)):
    return render(
        request,
        "_conversaciones_negocio.html",
        usuario,
        **_datos_lista_conversaciones(request, usuario),
    )


@router.get("/conversaciones/{canal}/{client_id}")
def conversacion(
    request: Request,
    canal: str,
    client_id: str,
    usuario=Depends(security.requiere_negocio),
):
    proyecto = _proyecto_del_usuario(usuario)
    antes = request.query_params.get("antes", "")
    desde = request.query_params.get("desde", "")
    pagina = trazabilidad.mensajes_de(
        proyecto["id"],
        client_id,
        canal,
        antes_de=int(antes) if antes.isdigit() else None,
        desde_id=int(desde) if desde.isdigit() else None,
        incluir_internos=False,
    )

    if desde.isdigit():
        return render(
            request,
            "_conversacion_burbujas.html",
            usuario,
            proyecto=proyecto,
            mensajes=pagina["mensajes"],
            dia_previo=request.query_params.get("dia", ""),
            parcial=True,
        )

    resumen = trazabilidad.resumen_conversacion(proyecto["id"], client_id, canal)
    if not int(resumen.get("mensajes") or 0) and not int(resumen.get("eventos") or 0):
        raise HTTPException(status_code=404, detail="Esa conversación no pertenece a tu proyecto")
    return render(
        request,
        "_conversacion_hilo.html",
        usuario,
        proyecto=proyecto,
        en_el_final=not antes.isdigit(),
        client_id=client_id,
        canal=canal,
        bloqueo_permanente=bloqueos_permanentes.estado_de(proyecto["id"], canal, client_id),
        bloqueo_temporal=bloqueos_temporales.estado_de(proyecto["id"], canal, client_id),
        mensajes=pagina["mensajes"],
        hay_mas=pagina["hay_mas"],
        cursor=pagina["cursor"],
        resumen=resumen,
    )


@router.post("/conversaciones/{canal}/{client_id}/responder")
def responder_conversacion(
    request: Request,
    canal: str,
    client_id: str,
    texto: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto_del_usuario(usuario)
    texto = str(texto or "").strip()
    if canal != "whatsapp":
        error = "Solo se puede responder conversaciones de WhatsApp desde el panel."
    elif not texto:
        error = "El mensaje no puede estar vacío."
    elif len(texto) > 4000:
        error = "El mensaje no puede superar 4000 caracteres."
    else:
        resumen = trazabilidad.resumen_conversacion(proyecto["id"], client_id, canal)
        if not int(resumen.get("mensajes") or 0) and not int(resumen.get("eventos") or 0):
            raise HTTPException(status_code=404, detail="Esa conversación no pertenece a tu proyecto")
        error = bot_interno.responder_como_dueno(proyecto["id"], canal, client_id, texto)

    if request.headers.get("X-Fragmento") == "1":
        return JSONResponse({"ok": not bool(error), "error": error}, status_code=400 if error else 200)
    if error:
        return RedirectResponse(f"/conversaciones?error={quote(error)}", status_code=303)
    return RedirectResponse("/conversaciones?aviso=Mensaje+enviado", status_code=303)


@router.post("/conversaciones/{canal}/{client_id}/diagnostico")
def descargar_diagnostico(
    request: Request,
    canal: str,
    client_id: str,
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Exporta trazas solo para el administrador que está suplantando."""
    security.verificar_csrf(request, csrf)
    if not usuario.get("suplantado_por") or not usuario.get("admin_real_id"):
        raise HTTPException(status_code=403, detail="Esta descarga es exclusiva de administración")
    proyecto = _proyecto_del_usuario(usuario)
    archivo = diagnostico_conversacion.crear_zip(proyecto, client_id, canal)
    if archivo is None:
        raise HTTPException(status_code=404, detail="Esa conversación no pertenece a tu proyecto")
    diagnostico_conversacion.auditar(
        proyecto["id"],
        usuario["admin_real_id"],
        client_id,
        canal,
        request.client.host if request.client else "",
    )
    nombre = "".join(c for c in f"diagnostico-{canal}-{client_id}" if c.isalnum() or c in "-_")
    return StreamingResponse(
        diagnostico_conversacion.transmitir(archivo),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nombre}.zip"'},
    )


@router.post("/conversaciones/{canal}/{client_id}/bloqueo")
def bloquear_desde_conversacion(
    request: Request,
    canal: str,
    client_id: str,
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto_del_usuario(usuario)
    resumen = trazabilidad.resumen_conversacion(proyecto["id"], client_id, canal)
    if not int(resumen.get("mensajes") or 0) and not int(resumen.get("eventos") or 0):
        raise HTTPException(status_code=404, detail="Esa conversación no pertenece a tu proyecto")
    try:
        bloqueos_permanentes.agregar(proyecto["id"], client_id, usuario["usuario"], canal)
    except ValueError:
        pass
    if request.headers.get("X-Fragmento") == "1":
        return JSONResponse({"ok": True})
    return RedirectResponse("/conversaciones?aviso=Número+bloqueado", status_code=303)


@router.post("/conversaciones/{canal}/{client_id}/bloqueo/eliminar")
def desbloquear_desde_conversacion(
    request: Request,
    canal: str,
    client_id: str,
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto_del_usuario(usuario)
    bloqueos_permanentes.eliminar_numero(proyecto["id"], canal, client_id)
    if request.headers.get("X-Fragmento") == "1":
        return JSONResponse({"ok": True})
    return RedirectResponse("/conversaciones?aviso=Número+habilitado", status_code=303)


@router.post("/conversaciones/{canal}/{client_id}/eliminar")
def eliminar_conversacion(
    request: Request,
    canal: str,
    client_id: str,
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto_del_usuario(usuario)
    resumen = trazabilidad.resumen_conversacion(proyecto["id"], client_id, canal)
    if not int(resumen.get("mensajes") or 0) and not int(resumen.get("eventos") or 0):
        raise HTTPException(status_code=404, detail="Esa conversación no pertenece a tu proyecto")
    borrado = trazabilidad.eliminar_conversacion(proyecto["id"], client_id, canal)
    fallo = bot_interno.olvidar_conversacion(proyecto["id"], canal, client_id)
    if request.headers.get("X-Fragmento") == "1":
        return JSONResponse({
            "ok": not bool(fallo),
            "eliminada": True,
            "mensajes": borrado["mensajes"],
            "aviso": fallo or "Conversación eliminada",
        })
    clave = "error" if fallo else "aviso"
    texto = fallo or f"Conversación eliminada ({borrado['mensajes']} mensajes)"
    return RedirectResponse(f"/conversaciones?{clave}={quote(texto)}", status_code=303)


# --- Lista permanente de números bloqueados ----------------------------------

def _datos_bloqueos(request: Request, usuario: dict) -> dict:
    proyecto = _proyecto_del_usuario(usuario)
    busqueda = request.query_params.get("q", "")
    return {
        "proyecto": proyecto,
        "bloqueos": bloqueos_permanentes.listar(proyecto["id"], busqueda),
        "busqueda": busqueda,
    }


@router.get("/configuracion-proyecto/bloqueos")
def bloqueos(request: Request, usuario=Depends(security.requiere_negocio)):
    return render(
        request,
        "_configuracion_proyecto_bloqueos.html",
        usuario,
        **_datos_bloqueos(request, usuario),
    )


@router.post("/configuracion-proyecto/bloqueos")
def agregar_bloqueo(
    request: Request,
    numero: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto_del_usuario(usuario)
    try:
        fila = bloqueos_permanentes.agregar(proyecto["id"], numero, usuario["usuario"])
    except ValueError as exc:
        if request.headers.get("X-Fragmento") == "1":
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return RedirectResponse(f"/conversaciones?error={quote(str(exc))}", status_code=303)
    aviso = quote(f"Número {fila['numero']} bloqueado")
    if request.headers.get("X-Fragmento") == "1":
        return JSONResponse({"ok": True, "aviso": f"Número {fila['numero']} bloqueado"})
    return RedirectResponse(f"/conversaciones?aviso={aviso}", status_code=303)


@router.post("/configuracion-proyecto/bloqueos/{bloqueo_id}/eliminar")
def eliminar_bloqueo(
    request: Request,
    bloqueo_id: int,
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto_del_usuario(usuario)
    if not bloqueos_permanentes.eliminar(proyecto["id"], bloqueo_id):
        raise HTTPException(status_code=404, detail="Ese bloqueo no pertenece a tu proyecto")
    if request.headers.get("X-Fragmento") == "1":
        return JSONResponse({"ok": True, "aviso": "Número eliminado de la lista"})
    return RedirectResponse(
        f"/conversaciones?aviso={quote('Número eliminado de la lista')}", status_code=303
    )


# --- Reportes al asesor -------------------------------------------------------

def _datos_reportes(request: Request, usuario: dict) -> dict:
    proyecto = _proyecto_del_usuario(usuario)
    pendientes = request.query_params.get("pendientes") == "1"
    return {
        "reportes": trazabilidad.listar_reportes(proyecto["id"], solo_pendientes=pendientes),
        "solo_pendientes": pendientes,
        "retencion_dias": trazabilidad.REPORTES_RETENCION_DIAS,
    }


@router.get("/reportes")
def reportes(request: Request, usuario=Depends(security.requiere_negocio)):
    return render(request, "reportes.html", usuario, **_datos_reportes(request, usuario))


@router.get("/reportes/lista")
def reportes_lista(request: Request, usuario=Depends(security.requiere_negocio)):
    """La tabla sola, para que un reporte nuevo aparezca sin recargar.

    Es lo que más se espera de esta pantalla: la tienes abierta mientras
    trabajas y el bot deriva una conversación en cualquier momento.
    """
    return render(request, "_reportes_lista.html", usuario, **_datos_reportes(request, usuario))


@router.post("/reportes/{reporte_id}/revisado")
def marcar_reporte(
    request: Request, reporte_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto_del_usuario(usuario)
    trazabilidad.marcar_reporte_revisado(proyecto["id"], reporte_id)
    return RedirectResponse(url=f"/reportes?aviso={quote('Reporte marcado como revisado')}", status_code=303)


# --- Base de conocimiento del agente ------------------------------------------

@router.get("/conocimiento")
def conocimiento(request: Request, usuario=Depends(security.requiere_negocio)):
    busqueda = request.query_params.get("q", "")
    return render(
        request,
        "rag.html",
        usuario,
        chunks=trazabilidad.listar_chunks(_proyecto_del_usuario(usuario)["id"], busqueda),
        busqueda=busqueda,
        limite=trazabilidad.LIMITE_CHUNK,
    )


# --- Preguntas que el agente no supo responder --------------------------------

def _datos_preguntas(usuario: dict) -> dict:
    proyecto = _proyecto_del_usuario(usuario)
    return {
        "preguntas": trazabilidad.listar_preguntas_sin_respuesta(proyecto["id"]),
        "retencion_horas": trazabilidad.PREGUNTAS_RETENCION_HORAS,
    }


@router.get("/preguntas")
def preguntas(request: Request, usuario=Depends(security.requiere_negocio)):
    return render(request, "preguntas.html", usuario, **_datos_preguntas(usuario))


@router.get("/preguntas/lista")
def preguntas_lista(request: Request, usuario=Depends(security.requiere_negocio)):
    """La tabla sola: el agente se queda sin respuesta mientras nadie mira."""
    return render(request, "_preguntas_lista.html", usuario, **_datos_preguntas(usuario))


@router.post("/preguntas/{pregunta_id}/atendida")
def marcar_pregunta(
    request: Request, pregunta_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto_del_usuario(usuario)
    trazabilidad.marcar_pregunta_atendida(proyecto["id"], pregunta_id)
    aviso = f"Entendido. Se borra sola en {trazabilidad.PREGUNTAS_RETENCION_HORAS} horas."
    return RedirectResponse(url=f"/preguntas?aviso={quote(aviso)}", status_code=303)
