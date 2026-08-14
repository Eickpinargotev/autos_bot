"""Consumo y facturación: lo que ve el cliente y lo que ve el administrador."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.core import security
from src.core.plantillas import render
from src.services import facturacion

router = APIRouter()


def _datos_periodo(periodo: dict) -> dict:
    return {
        "periodo": periodo,
        "totales": facturacion.totales_de_periodo(periodo["id"]),
        "categorias": facturacion.desglose_por_categoria(periodo["id"]),
        "serie": facturacion.serie_diaria(periodo["id"]),
        # Solo se muestra en la vista del administrador: al negocio no le dice
        # nada un porcentaje de tokens reutilizados, y ya ve el efecto en su
        # total. A nosotros nos avisa si el prompt dejó de ser cacheable.
        "cache": facturacion.ahorro_por_cache(periodo["id"]),
    }


# --- Vista del cliente -------------------------------------------------------

@router.get("/factura")
def factura(request: Request, usuario=Depends(security.requiere_sesion)):
    """Cuánto lleva consumido el cliente en el periodo abierto.

    Aquí NUNCA se muestra el costo real del proveedor: solo el precio de venta.
    """
    return render(request, "factura.html", usuario, **_datos_periodo(facturacion.periodo_abierto()))


@router.get("/factura/totales")
def factura_totales(request: Request, usuario=Depends(security.requiere_sesion)):
    """Fragmento que el navegador vuelve a pedir cada pocos segundos.

    Es "tiempo real" sin websockets ni estado en el servidor: una consulta
    agregada por índice sobre `uso_eventos`, que es barata.
    """
    return render(request, "_factura_totales.html", usuario, **_datos_periodo(facturacion.periodo_abierto()))


# --- Vista del administrador -------------------------------------------------

@router.get("/admin/costos")
def costos(request: Request, usuario=Depends(security.requiere_admin)):
    periodo = facturacion.periodo_abierto()
    return render(
        request,
        "costos.html",
        usuario,
        tarifa=facturacion.tarifa_vigente(),
        origenes=facturacion.desglose_por_origen(periodo["id"]),
        **_datos_periodo(periodo),
    )


@router.get("/admin/costos/totales")
def costos_totales(request: Request, usuario=Depends(security.requiere_admin)):
    periodo = facturacion.periodo_abierto()
    return render(
        request,
        "_costos_totales.html",
        usuario,
        origenes=facturacion.desglose_por_origen(periodo["id"]),
        **_datos_periodo(periodo),
    )


@router.get("/admin/periodos")
def periodos(request: Request, usuario=Depends(security.requiere_admin)):
    return render(
        request,
        "periodos.html",
        usuario,
        periodos=facturacion.listar_periodos(),
        abierto=facturacion.periodo_abierto(),
    )


@router.post("/admin/periodos/cerrar")
def cerrar_periodo(
    request: Request,
    nota: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_admin),
):
    """Reinicia el contador del cliente SIN borrar nada.

    El periodo se cierra con sus totales congelados y queda en el historial: si
    el clic fue por error, se puede reincorporar al periodo abierto.
    """
    security.verificar_csrf(request, csrf)
    nuevo = facturacion.cerrar_periodo(usuario["usuario"], nota)
    return RedirectResponse(
        url=f"/admin/periodos?aviso=Periodo cerrado. El cliente ve el periodo {nuevo['id']} desde cero.",
        status_code=303,
    )


@router.post("/admin/periodos/{periodo_id}/reincorporar")
def reincorporar(
    request: Request,
    periodo_id: int,
    csrf: str = Form(""),
    usuario=Depends(security.requiere_admin),
):
    security.verificar_csrf(request, csrf)
    if facturacion.reincorporar_periodo(periodo_id):
        mensaje = f"aviso=El periodo {periodo_id} se sumó al periodo actual."
    else:
        mensaje = "error=Ese periodo no se puede reincorporar (ya lo está, o es el periodo abierto)."
    return RedirectResponse(url=f"/admin/periodos?{mensaje}", status_code=303)


# --- Tarifas -----------------------------------------------------------------

@router.get("/admin/tarifas")
def tarifas(request: Request, usuario=Depends(security.requiere_admin)):
    return render(
        request,
        "tarifas.html",
        usuario,
        tarifas=facturacion.listar_tarifas(),
        vigente=facturacion.tarifa_vigente(),
    )


@router.post("/admin/tarifas")
def crear_tarifa(
    request: Request,
    modelo: str = Form(...),
    precio_input_usd_1m: float = Form(...),
    precio_cached_input_usd_1m: float = Form(...),
    precio_output_usd_1m: float = Form(...),
    multiplicador_llm: float = Form(...),
    precio_mensaje_codigo_microusd: int = Form(...),
    nota: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_admin),
):
    """Registra una tarifa nueva; nunca edita la anterior.

    Lo ya facturado no se toca: cada evento guardó su costo con la tarifa que
    regía en su momento.
    """
    security.verificar_csrf(request, csrf)
    facturacion.crear_tarifa(
        {
            "modelo": modelo,
            "precio_input_usd_1m": precio_input_usd_1m,
            "precio_cached_input_usd_1m": precio_cached_input_usd_1m,
            "precio_output_usd_1m": precio_output_usd_1m,
            "multiplicador_llm": multiplicador_llm,
            "precio_mensaje_codigo_microusd": precio_mensaje_codigo_microusd,
            "nota": nota,
        },
        usuario["usuario"],
    )
    return RedirectResponse(
        url="/admin/tarifas?aviso=Tarifa nueva vigente desde ahora. Lo facturado antes no cambia.",
        status_code=303,
    )
