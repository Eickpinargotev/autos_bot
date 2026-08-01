"""Envíos manuales: preparar, mandar, reintentar y reportar."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.core import security
from src.core.plantillas import render
from src.services import mensajeria

router = APIRouter()


@router.get("/enviar")
def formulario(request: Request, usuario=Depends(security.requiere_negocio)):
    return render(
        request,
        "enviar.html",
        usuario,
        plantillas=mensajeria.listar_plantillas(),
        canales=mensajeria.CANALES,
    )


@router.post("/enviar")
def encolar(
    request: Request,
    plantilla_id: int = Form(...),
    canal: str = Form(...),
    destinos: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Deja los envíos en la cola; el bot los manda en segundo plano.

    Se acepta un destino por línea (o separados por coma) para poder mandar el
    mismo mensaje a varias personas de una vez.
    """
    security.verificar_csrf(request, csrf)

    lista = [
        parte.strip()
        for linea in destinos.replace(",", "\n").splitlines()
        for parte in [linea]
        if parte.strip()
    ]
    if not lista:
        return RedirectResponse(url="/enviar?error=Escribe al menos un ID de destino.", status_code=303)

    try:
        creados = mensajeria.encolar_envios(plantilla_id, canal, lista, usuario["usuario"])
    except ValueError as e:
        return RedirectResponse(url=f"/enviar?error={e}", status_code=303)

    return RedirectResponse(
        url=f"/envios?aviso={len(creados)} envío(s) en cola. Aquí verás cómo van.",
        status_code=303,
    )


@router.get("/envios")
def historial(request: Request, usuario=Depends(security.requiere_negocio)):
    """Histórico de envíos.

    El detalle técnico del error solo se carga si quien mira es administrador:
    al cliente le sirve el mensaje accionable, no la traza.
    """
    es_admin = usuario["rol"] == security.ROL_ADMIN
    return render(
        request,
        "envios.html",
        usuario,
        envios=mensajeria.listar_envios(incluir_tecnico=es_admin),
        max_intentos=mensajeria.MAX_INTENTOS,
    )


@router.post("/envios/{envio_id}/reintentar")
def reintentar(
    request: Request, envio_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    ok, mensaje = mensajeria.reintentar(envio_id)
    clave = "aviso" if ok else "error"
    return RedirectResponse(url=f"/envios?{clave}={mensaje}", status_code=303)


@router.post("/envios/{envio_id}/reportar")
def reportar(
    request: Request, envio_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    ok, mensaje = mensajeria.reportar(envio_id, usuario["usuario"])
    clave = "aviso" if ok else "error"
    return RedirectResponse(url=f"/envios?{clave}={mensaje}", status_code=303)


@router.post("/envios/{envio_id}/eliminar")
def eliminar(
    request: Request, envio_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    mensajeria.eliminar_envio(envio_id)
    return RedirectResponse(url="/envios?aviso=Envío eliminado.", status_code=303)
