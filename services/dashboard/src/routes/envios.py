"""Envíos manuales: preparar una tanda y verla avanzar."""

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.core import security
from src.core.plantillas import render, render_fragmento
from src.services import envios as svc_envios
from src.services import clientes_whatsapp, mensajeria

router = APIRouter()


def _proyecto_id(usuario: dict) -> int:
    if usuario.get("_proyecto"):
        return int(usuario["_proyecto"]["id"])
    proyecto = clientes_whatsapp.por_usuario(usuario["id"])
    if not proyecto:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Esta cuenta no está vinculada a un proyecto")
    usuario["_proyecto"] = proyecto
    return int(proyecto["id"])


@router.get("/enviar")
def formulario(request: Request, usuario=Depends(security.requiere_negocio)):
    """El formulario, con las tres categorías cargadas de una vez.

    Se mandan las tres al navegador y el desplegable de «qué enviar» se rellena
    solo al cambiar de categoría: recargar la página por elegir entre tres
    opciones sería perder lo que ya se escribió en la lista de números.
    """
    return render(
        request,
        "enviar.html",
        usuario,
        categorias=svc_envios.CATEGORIAS,
        opciones={
            clave: svc_envios.opciones(_proyecto_id(usuario), clave)
            for clave in svc_envios.CATEGORIAS
        },
        canales=mensajeria.CANALES,
    )


@router.post("/enviar")
def encolar(
    request: Request,
    categoria: str = Form(...),
    referencia_id: int = Form(...),
    canal: str = Form(...),
    destinos: str = Form(""),
    empieza_en: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_negocio),
):
    """Crea la sesión de envío. El bot la va soltando a su ritmo."""
    security.verificar_csrf(request, csrf)

    validos, rechazados = svc_envios.numeros(destinos)
    if not validos:
        detalle = (
            "Ninguno de los números tiene código de país."
            if rechazados
            else "Escribe al menos un número, uno por línea."
        )
        return RedirectResponse(url=f"/enviar?error={quote(detalle)}", status_code=303)

    try:
        lote = svc_envios.crear_lote(
            proyecto_id=_proyecto_id(usuario),
            categoria=categoria,
            referencia_id=referencia_id,
            canal=canal,
            destinos=validos,
            usuario=usuario["usuario"],
            empieza_en=_momento(empieza_en),
        )
    except ValueError as e:
        return RedirectResponse(url=f"/enviar?error={quote(str(e))}", status_code=303)

    aviso = f"{len(validos)} envío(s) en cola."
    if rechazados:
        # No se calla: si se pegaron cien números y tres iban sin código de país,
        # hay que saber CUÁLES quedaron fuera, no solo que faltan tres.
        aviso += f" Sin código de país, no se envían: {', '.join(rechazados[:5])}"
        if len(rechazados) > 5:
            aviso += f" y {len(rechazados) - 5} más"
    return RedirectResponse(url=f"/envios?aviso={quote(aviso)}#lote-{lote['id']}", status_code=303)


def _momento(valor: str) -> datetime | None:
    """Convierte el `datetime-local` del formulario. Vacío = ahora."""
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return None


@router.get("/envios")
def historial(request: Request, usuario=Depends(security.requiere_negocio)):
    return render(request, "envios.html", usuario, **_datos_de_envios(usuario))


@router.get("/envios/sesiones")
def sesiones(request: Request, usuario=Depends(security.requiere_negocio)):
    """Fragmento que el navegador vuelve a pedir cada pocos segundos.

    Es como avanza la barra de progreso sin websockets ni estado en el servidor:
    una consulta agregada por índice, igual que la de la factura.
    """
    return render_fragmento(request, "_envios_sesiones.html", usuario, **_datos_de_envios(usuario))


def _datos_de_envios(usuario: dict) -> dict:
    lotes = [
        svc_envios.con_progreso(lote)
        for lote in svc_envios.listar_lotes(_proyecto_id(usuario))
    ]
    return {
        "lotes": lotes,
        "retencion_dias": svc_envios.RETENCION_DIAS,
        "max_intentos": mensajeria.MAX_INTENTOS,
    }


@router.get("/envios/{lote_id}/destinos")
def destinos(
    request: Request,
    lote_id: int,
    usuario=Depends(security.requiere_negocio),
):
    """Los números de una sesión, para la ventana de detalle.

    Se puede abrir en cualquier momento, no solo al terminar: si de los primeros
    veinte fallan quince, más vale enterarse antes de que salgan los ochenta.
    """
    solo_fallidos = request.query_params.get("fallidos") == "1"
    return render_fragmento(
        request,
        "_envio_destinos.html",
        usuario,
        lote=svc_envios.obtener_lote(_proyecto_id(usuario), lote_id),
        destinos=svc_envios.destinos_de(
            _proyecto_id(usuario), lote_id, solo_fallidos=solo_fallidos
        ),
        solo_fallidos=solo_fallidos,
        max_intentos=mensajeria.MAX_INTENTOS,
    )


@router.post("/envios/{lote_id}/cancelar")
def cancelar(
    request: Request, lote_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    quitados = svc_envios.cancelar(_proyecto_id(usuario), lote_id)
    aviso = f"Sesión cancelada: {quitados} envío(s) no saldrán. Lo ya enviado no se puede deshacer."
    return RedirectResponse(url=f"/envios?aviso={quote(aviso)}", status_code=303)


@router.post("/envios/{lote_id}/eliminar")
def eliminar(
    request: Request, lote_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    svc_envios.eliminar_lote(_proyecto_id(usuario), lote_id)
    return RedirectResponse(url="/envios?aviso=Sesión+eliminada+del+historial", status_code=303)


# --- Un envío suelto dentro de una sesión ------------------------------------

@router.post("/envios/mensaje/{envio_id}/reintentar")
def reintentar(
    request: Request, envio_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    ok, mensaje = mensajeria.reintentar(_proyecto_id(usuario), envio_id)
    clave = "aviso" if ok else "error"
    return RedirectResponse(url=f"/envios?{clave}={quote(mensaje)}", status_code=303)


@router.post("/envios/mensaje/{envio_id}/reportar")
def reportar(
    request: Request, envio_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_negocio)
):
    security.verificar_csrf(request, csrf)
    ok, mensaje = mensajeria.reportar(_proyecto_id(usuario), envio_id, usuario["usuario"])
    clave = "aviso" if ok else "error"
    return RedirectResponse(url=f"/envios?{clave}={quote(mensaje)}", status_code=303)
