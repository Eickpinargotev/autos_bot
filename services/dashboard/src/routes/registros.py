"""Administración del registro de palabras clave de cada proyecto."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse

from src.core import security
from src.core.plantillas import render, render_fragmento
from src.services import clientes_whatsapp, importacion_registros, registros as svc_registros

router = APIRouter()


def _proyecto(usuario: dict) -> dict:
    if usuario.get("_proyecto"):
        return usuario["_proyecto"]
    proyecto = clientes_whatsapp.por_usuario(usuario["id"])
    if not proyecto:
        raise HTTPException(status_code=404, detail="Esta cuenta no está vinculada a un proyecto")
    usuario["_proyecto"] = proyecto
    return proyecto


@router.get("/registros")
def registros(request: Request, usuario=Depends(security.requiere_negocio)):
    proyecto = _proyecto(usuario)
    busqueda = request.query_params.get("q", "").strip()
    if busqueda:
        datos = {
            "registros": svc_registros.buscar(proyecto["id"], busqueda),
            "siguiente_cursor": "",
        }
    else:
        datos = svc_registros.pagina(proyecto["id"])
    return render(
        request,
        "registros.html",
        usuario,
        proyecto=proyecto,
        busqueda=busqueda,
        mostrar_vacio=True,
        **datos,
    )


@router.post("/registros")
def agregar(
    request: Request,
    numero: str = Form(...),
    nombre: str = Form(""),
    palabra_clave: str = Form(""),
    canal: str = Form("whatsapp"),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto(usuario)
    try:
        fila = svc_registros.crear(proyecto["id"], numero, nombre, palabra_clave, canal)
    except ValueError as exc:
        return RedirectResponse(f"/registros?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(
        f"/registros?q={quote(fila['registro'])}&aviso=Registro+agregado", status_code=303
    )


@router.post("/registros/cargar")
def cargar(
    request: Request,
    archivo: UploadFile = File(...),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto(usuario)
    try:
        datos = archivo.file.read(importacion_registros.MAX_ARCHIVO_BYTES + 1)
        lectura = importacion_registros.leer_bytes(datos)
        resultado = importacion_registros.importar(
            lectura, proyecto["id"], proyecto.get("zona_horaria") or "UTC"
        )
    except ValueError as exc:
        return RedirectResponse(f"/registros?error={quote(str(exc))}", status_code=303)
    aviso = (
        f"CSV procesado: {resultado['insertadas']} agregados, "
        f"{resultado['existentes']} ya existían"
    )
    if resultado["rechazadas"]:
        aviso += f" y {resultado['rechazadas']} filas rechazadas"
    return RedirectResponse(f"/registros?aviso={quote(aviso)}", status_code=303)


@router.post("/registros/{registro_id}/eliminar")
def eliminar(
    request: Request,
    registro_id: int,
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    security.verificar_csrf(request, csrf)
    proyecto = _proyecto(usuario)
    if not svc_registros.eliminar(proyecto["id"], registro_id):
        raise HTTPException(status_code=404, detail="Ese registro no pertenece a tu proyecto")
    return RedirectResponse("/registros?aviso=Registro+eliminado", status_code=303)


@router.get("/registros/lista")
def lista(request: Request, usuario=Depends(security.requiere_negocio)):
    proyecto = _proyecto(usuario)
    cursor = request.query_params.get("cursor", "")
    try:
        datos = svc_registros.pagina(proyecto["id"], cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return render_fragmento(
        request,
        "_registros_filas.html",
        usuario,
        proyecto=proyecto,
        busqueda="",
        mostrar_vacio=not bool(cursor),
        **datos,
    )


@router.get("/registros/descargar")
def descargar(usuario=Depends(security.requiere_negocio)):
    proyecto = _proyecto(usuario)
    return StreamingResponse(
        svc_registros.exportar_csv(proyecto["id"], proyecto.get("zona_horaria") or "UTC"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="registros.csv"'},
    )
