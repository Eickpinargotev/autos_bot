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

def _datos_clientes() -> dict:
    periodo = facturacion.periodo_abierto()
    filas = facturacion.actividad_por_cliente(periodo["id"], incluir_costo_real=False)
    return {
        "periodo": periodo,
        "filas": filas,
        "totales": facturacion.totales_de_actividad(filas),
    }


@router.get("/clientes")
def clientes(request: Request, usuario=Depends(security.requiere_negocio)):
    """Quién escribió y cuánto consumió, del más reciente al más antiguo.

    **Sin costo real.** Esta ruta es del proyecto (`requiere_negocio`), y el
    costo real es lo que nos cuesta a nosotros el proveedor: teniéndolo al lado
    de lo facturado, el margen se calcula solo. No se pide a la base, así que no
    llega ni al HTML.
    """
    return render(request, "clientes.html", usuario, **_datos_clientes())


@router.get("/clientes/lista")
def clientes_lista(request: Request, usuario=Depends(security.requiere_negocio)):
    """Las cifras y la tabla solas, para que la actividad se vea al momento."""
    return render(request, "_clientes_lista.html", usuario, **_datos_clientes())


# --- Reportes al asesor -------------------------------------------------------

def _datos_reportes(request: Request) -> dict:
    pendientes = request.query_params.get("pendientes") == "1"
    return {
        "reportes": trazabilidad.listar_reportes(solo_pendientes=pendientes),
        "solo_pendientes": pendientes,
        "retencion_dias": trazabilidad.REPORTES_RETENCION_DIAS,
    }


@router.get("/reportes")
def reportes(request: Request, usuario=Depends(security.requiere_negocio)):
    return render(request, "reportes.html", usuario, **_datos_reportes(request))


@router.get("/reportes/lista")
def reportes_lista(request: Request, usuario=Depends(security.requiere_negocio)):
    """La tabla sola, para que un reporte nuevo aparezca sin recargar.

    Es lo que más se espera de esta pantalla: la tienes abierta mientras
    trabajas y el bot deriva una conversación en cualquier momento.
    """
    return render(request, "_reportes_lista.html", usuario, **_datos_reportes(request))


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
        limite=trazabilidad.LIMITE_CHUNK,
    )


# --- Preguntas que el agente no supo responder --------------------------------

def _datos_preguntas() -> dict:
    return {
        "preguntas": trazabilidad.listar_preguntas_sin_respuesta(),
        "retencion_horas": trazabilidad.PREGUNTAS_RETENCION_HORAS,
    }


@router.get("/preguntas")
def preguntas(request: Request, usuario=Depends(security.requiere_negocio)):
    return render(request, "preguntas.html", usuario, **_datos_preguntas())


@router.get("/preguntas/lista")
def preguntas_lista(request: Request, usuario=Depends(security.requiere_negocio)):
    """La tabla sola: el agente se queda sin respuesta mientras nadie mira."""
    return render(request, "_preguntas_lista.html", usuario, **_datos_preguntas())


@router.post("/preguntas/{pregunta_id}/atendida")
def marcar_pregunta(
    request: Request, pregunta_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    trazabilidad.marcar_pregunta_atendida(pregunta_id)
    aviso = f"Entendido. Se borra sola en {trazabilidad.PREGUNTAS_RETENCION_HORAS} horas."
    return RedirectResponse(url=f"/preguntas?aviso={quote(aviso)}", status_code=303)
