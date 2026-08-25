"""Importa en privado el histórico de palabras clave desde un CSV."""

import argparse
import sys
from typing import Iterable

from src.db import pool
from src.services import importacion_registros


def leer_csv(ruta):
    return importacion_registros.leer_ruta(ruta)


def importar(lectura, proyecto_slug: str, *, dry_run: bool = False) -> dict[str, int]:
    proyecto = pool.consultar_uno(
        "SELECT id, zona_horaria FROM clientes_whatsapp WHERE slug = %s", (str(proyecto_slug),)
    )
    if not proyecto:
        raise ValueError(f"No existe el proyecto con slug «{proyecto_slug}».")
    return importacion_registros.importar(
        lectura,
        proyecto["id"],
        proyecto.get("zona_horaria") or "UTC",
        dry_run=dry_run,
    )


def _imprimir(resumen: dict[str, int], errores: Iterable[str], dry_run: bool) -> None:
    estado = "SIMULACIÓN" if dry_run else "IMPORTACIÓN"
    print(f"{estado} COMPLETADA")
    for clave in ("leidas", "unicas", "rechazadas", "existentes", "por_insertar", "insertadas"):
        print(f"{clave}: {resumen[clave]}")
    for error in errores:
        print(error, file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archivo", required=True, help="Ruta privada al CSV")
    parser.add_argument("--proyecto-slug", required=True, help="Slug exacto del proyecto")
    parser.add_argument("--dry-run", action="store_true", help="Valida y resume sin insertar")
    args = parser.parse_args()
    try:
        lectura = leer_csv(args.archivo)
        resumen = importar(lectura, args.proyecto_slug, dry_run=args.dry_run)
        _imprimir(resumen, lectura.errores, args.dry_run)
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
