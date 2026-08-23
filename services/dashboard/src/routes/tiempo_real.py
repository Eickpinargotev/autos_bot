"""El canal por el que el panel se entera de que algo cambió.

Una sola ruta, y la ÚNICA `async def` del proyecto. Es a propósito: no toca la
base ni el pool de conexiones, solo espera en una cola. Si fuera síncrona
ocuparía uno de los hilos de Starlette durante toda la vida de la conexión, que
es justo lo que no queremos de una conexión abierta durante horas.

Lo que se manda es una lista de nombres («reportes,uso»), nunca datos. El
navegador la traduce a «vuelve a pedir estos fragmentos», y cada fragmento pasa
por su propia puerta de permisos.
"""

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from src.core import eventos, navegacion, security
from src.core.plantillas import render

router = APIRouter()

# Cada cuánto se manda un comentario si no hay novedades. Hace dos trabajos:
# evita que un proxy (Traefik, nginx) corte la conexión por inactividad, y es lo
# que DETECTA que el navegador se fue —escribir en un socket muerto falla y
# cierra el generador, liberando la suscripción—. Sin latido, una pestaña
# cerrada de golpe dejaría su cola colgada.
#
# Diez segundos y no treinta por lo segundo: es el techo de lo que tarda el
# servidor en soltar una conexión cuyo navegador ya no está (se cerró de golpe,
# se cayó la red). Escribir diez bytes cada diez segundos no le cuesta nada a
# nadie, y a cambio nada se queda colgado. Sigue muy por debajo del tiempo de
# inactividad que corta cualquier proxy.
LATIDO = 10.0

CABECERAS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Sin esto nginx acumula el stream en un buffer y no llega nada hasta que se
    # llena. Traefik no bufferiza, pero la cabecera no le molesta.
    "X-Accel-Buffering": "no",
}


@router.get("/eventos")
async def eventos_del_panel(usuario=Depends(security.requiere_sesion)):
    """Flujo SSE con los temas que van cambiando."""
    permitidos = eventos.topics_para(usuario)
    ambito = eventos.ambito_para(usuario)

    async def flujo():
        async with eventos.suscribirse(ambito) as cola:
            # Un primer comentario cierra la negociación y le confirma al
            # navegador que la conexión está viva (algunos proxies no la dan por
            # establecida hasta el primer byte).
            yield ": conectado\n\n"
            while True:
                try:
                    temas = await asyncio.wait_for(cola.get(), timeout=LATIDO)
                except asyncio.TimeoutError:
                    yield ": latido\n\n"
                    continue

                visibles = sorted(temas & permitidos)
                if visibles:
                    yield f"data: {','.join(visibles)}\n\n"

    return StreamingResponse(flujo(), media_type="text/event-stream", headers=CABECERAS)


@router.get("/pendientes")
def menu_con_pendientes(request: Request, usuario=Depends(security.requiere_sesion)):
    """El menú lateral con sus pastillas al día.

    Es el único fragmento que no pertenece a una página: vive en el armazón, y
    por eso hace falta decirle en QUÉ ruta está el usuario (`?en=`) para que
    siga marcando el enlace de la página abierta. Sin eso, el primer refresco
    apagaba el resaltado y el menú dejaba de decir dónde estabas.
    """
    en = request.query_params.get("en", "")
    es_admin = usuario["rol"] == security.ROL_ADMIN
    return render(
        request,
        "_lateral_nav.html",
        usuario,
        secciones=navegacion.secciones_para(es_admin, en),
    )
