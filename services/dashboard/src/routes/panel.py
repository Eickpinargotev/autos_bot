"""Costos reales de operación, visibles exclusivamente para administración."""

from fastapi import APIRouter, Depends, Request

from src.core import security
from src.core.plantillas import render
from src.services import facturacion

router = APIRouter()


def _datos_reales() -> dict:
    return {
        "totales": facturacion.totales_reales(),
        "categorias": facturacion.desglose_real_por_categoria(),
        "origenes": facturacion.desglose_real_por_origen(),
        "cache": facturacion.ahorro_real_por_cache(),
    }


# El negocio no tiene ruta de consumo. Ocultar solo el enlace no sería una
# barrera: `/factura` dejó de existir para que tampoco se pueda abrir a mano.

@router.get("/admin/costos")
def costos(request: Request, usuario=Depends(security.requiere_admin)):
    return render(
        request,
        "costos.html",
        usuario,
        **_datos_reales(),
    )


@router.get("/admin/costos/totales")
def costos_totales(request: Request, usuario=Depends(security.requiere_admin)):
    return render(
        request,
        "_costos_totales.html",
        usuario,
        **_datos_reales(),
    )
