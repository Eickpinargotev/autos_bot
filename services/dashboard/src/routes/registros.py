"""Consulta y descarga del registro de palabras clave de cada proyecto."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.core import security
from src.core.plantillas import render
from src.services import clientes_whatsapp, registros as svc_registros

router = APIRouter()


def _proyecto(usuario: dict) -> dict:
    proyecto = clientes_whatsapp.por_usuario(usuario["id"])
    if not proyecto:
        raise HTTPException(status_code=404, detail="Esta cuenta no está vinculada a un proyecto")
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


@router.get("/registros/lista")
def lista(request: Request, usuario=Depends(security.requiere_negocio)):
    proyecto = _proyecto(usuario)
    cursor = request.query_params.get("cursor", "")
    try:
        datos = svc_registros.pagina(proyecto["id"], cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return render(
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
