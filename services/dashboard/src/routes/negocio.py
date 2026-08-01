"""Panel del NEGOCIO: lo que administra nuestro cliente, no nosotros.

Aquí «cliente» significa la persona que le escribe al bot. Todo lo de este
módulo depende de `requiere_negocio`: el administrador no ve estas páginas en
su menú porque no son su trabajo, y entra a ellas suplantando al negocio desde
`/admin/negocios/{id}` (que deja registro de quién entró).

Lo que NO está aquí, a propósito: las **conversaciones**. Son de los clientes
del negocio y solo las ve el administrador, para resolver problemas. Para lo que
el negocio necesita del día a día ya están los reportes y las preguntas sin
responder.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.core import security
from src.core.plantillas import render
from src.services import facturacion, trazabilidad

router = APIRouter()


# --- Clientes del negocio (las personas que le escriben) ----------------------

@router.get("/clientes")
def clientes(request: Request, usuario=Depends(security.requiere_negocio)):
    periodo = facturacion.periodo_abierto()
    filas = facturacion.actividad_por_cliente(periodo["id"])
    return render(
        request,
        "clientes.html",
        usuario,
        periodo=periodo,
        filas=filas,
        totales=facturacion.totales_de_actividad(filas),
    )


# --- Reportes al asesor -------------------------------------------------------

@router.get("/reportes")
def reportes(request: Request, usuario=Depends(security.requiere_negocio)):
    pendientes = request.query_params.get("pendientes") == "1"
    return render(
        request,
        "reportes.html",
        usuario,
        reportes=trazabilidad.listar_reportes(solo_pendientes=pendientes),
        solo_pendientes=pendientes,
    )


@router.post("/reportes/{reporte_id}/revisado")
def marcar_reporte(
    request: Request, reporte_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    trazabilidad.marcar_reporte_revisado(reporte_id)
    return RedirectResponse(url=f"/reportes?aviso={quote('Reporte marcado como revisado')}", status_code=303)


# --- Base de conocimiento del agente ------------------------------------------

@router.get("/conocimiento")
def conocimiento(request: Request, usuario=Depends(security.requiere_negocio)):
    busqueda = request.query_params.get("q", "")
    return render(
        request,
        "rag.html",
        usuario,
        chunks=trazabilidad.listar_chunks(busqueda),
        busqueda=busqueda,
    )


# --- Preguntas que el agente no supo responder --------------------------------

@router.get("/preguntas")
def preguntas(request: Request, usuario=Depends(security.requiere_negocio)):
    return render(
        request,
        "preguntas.html",
        usuario,
        preguntas=trazabilidad.listar_preguntas_sin_respuesta(),
    )


@router.post("/preguntas/{pregunta_id}/atendida")
def marcar_pregunta(
    request: Request, pregunta_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    trazabilidad.marcar_pregunta_atendida(pregunta_id)
    return RedirectResponse(url=f"/preguntas?aviso={quote('Pregunta marcada como atendida')}", status_code=303)
