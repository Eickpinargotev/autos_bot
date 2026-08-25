"""Importa en privado el histórico de palabras clave desde un CSV.

Uso dentro del contenedor del dashboard::

    python -m src.commands.importar_registros --archivo /tmp/registros.csv \
        --proyecto-slug escuela-de-manejo --dry-run
"""

import argparse
import csv
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg2.extras

from src.db import pool
from src.services.registros import normalizar_numero

CABECERAS = ("numero", "Nombre", "Fecha de registro (dia/mes//año)")
_ROTULOS_MANUALES = {
    "solicitado agregar manual",
    "ingreso manual google sheet",
    "ingreso manual",
}
_PUNTUACION_RELLENO = set(".,;:_-–—/\\ ")


@dataclass(frozen=True)
class RegistroCSV:
    numero: str
    nombre: str
    fecha: date


@dataclass(frozen=True)
class LecturaCSV:
    leidas: int
    registros: tuple[RegistroCSV, ...]
    rechazadas: int
    errores: tuple[str, ...]


def _nombre_real(nombre: str) -> bool:
    limpio = " ".join(str(nombre or "").split())
    if not limpio:
        return False
    normalizado = unicodedata.normalize("NFKC", limpio).casefold()
    if normalizado in _ROTULOS_MANUALES:
        return False
    return any(c not in _PUNTUACION_RELLENO for c in limpio)


def leer_csv(ruta: str | Path) -> LecturaCSV:
    """Valida, normaliza y consolida el archivo sin tocar la base."""
    unicos: dict[str, RegistroCSV] = {}
    leidas = 0
    rechazadas = 0
    errores: list[str] = []

    with Path(ruta).open("r", encoding="utf-8-sig", newline="") as archivo:
        lector = csv.DictReader(archivo)
        faltantes = [cabecera for cabecera in CABECERAS if cabecera not in (lector.fieldnames or [])]
        if faltantes:
            raise ValueError(f"Faltan columnas obligatorias: {', '.join(faltantes)}")

        for numero_fila, fila in enumerate(lector, start=2):
            leidas += 1
            numero = normalizar_numero(fila.get("numero", ""))
            nombre = " ".join(str(fila.get("Nombre") or "").split())
            try:
                if len(numero) < 7:
                    raise ValueError("número inválido")
                fecha = datetime.strptime(
                    str(fila.get("Fecha de registro (dia/mes//año)") or "").strip(),
                    "%d/%m/%Y",
                ).date()
            except ValueError as exc:
                rechazadas += 1
                if len(errores) < 10:
                    errores.append(f"Fila {numero_fila}: {exc}")
                continue

            nuevo = RegistroCSV(numero=numero, nombre=nombre, fecha=fecha)
            anterior = unicos.get(numero)
            if anterior is None:
                unicos[numero] = nuevo
                continue

            fecha_elegida = min(anterior.fecha, nuevo.fecha)
            nombre_elegido = anterior.nombre
            if not _nombre_real(anterior.nombre) and _nombre_real(nuevo.nombre):
                nombre_elegido = nuevo.nombre
            unicos[numero] = RegistroCSV(numero, nombre_elegido, fecha_elegida)

    return LecturaCSV(
        leidas=leidas,
        registros=tuple(unicos.values()),
        rechazadas=rechazadas,
        errores=tuple(errores),
    )


def importar(lectura: LecturaCSV, proyecto_slug: str, *, dry_run: bool = False) -> dict[str, int]:
    proyecto = pool.consultar_uno(
        "SELECT id, zona_horaria FROM clientes_whatsapp WHERE slug = %s",
        (str(proyecto_slug),),
    )
    if not proyecto:
        raise ValueError(f"No existe el proyecto con slug «{proyecto_slug}».")

    existentes = pool.consultar(
        "SELECT registro FROM keyword_registros WHERE proyecto_id = %s AND canal = 'whatsapp'",
        (proyecto["id"],),
    )
    numeros_existentes = {str(fila["registro"]) for fila in existentes}
    nuevas = [r for r in lectura.registros if r.numero not in numeros_existentes]

    insertadas = 0
    if not dry_run and nuevas:
        try:
            zona = ZoneInfo(proyecto.get("zona_horaria") or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            zona = ZoneInfo("UTC")
        valores = [
            (
                proyecto["id"],
                registro.numero,
                "whatsapp",
                registro.nombre,
                "",
                datetime.combine(registro.fecha, time(hour=12), tzinfo=zona),
            )
            for registro in nuevas
        ]
        with pool.conexion(autocommit=False) as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TEMP TABLE importar_keyword_registros (
                            proyecto_id INTEGER,
                            registro VARCHAR(80),
                            canal VARCHAR(20),
                            nombre VARCHAR(200),
                            palabra_clave VARCHAR(60),
                            creado_en TIMESTAMPTZ
                        ) ON COMMIT DROP
                        """
                    )
                    psycopg2.extras.execute_values(
                        cur,
                        "INSERT INTO importar_keyword_registros VALUES %s",
                        valores,
                        page_size=1000,
                    )
                    cur.execute(
                        """
                        INSERT INTO keyword_registros
                            (proyecto_id, registro, canal, nombre, palabra_clave, creado_en)
                        SELECT proyecto_id, registro, canal, nombre, palabra_clave, creado_en
                        FROM importar_keyword_registros
                        ON CONFLICT (proyecto_id, registro, canal) DO NOTHING
                        """
                    )
                    insertadas = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    return {
        "leidas": lectura.leidas,
        "unicas": len(lectura.registros),
        "rechazadas": lectura.rechazadas,
        "existentes": len(lectura.registros) - len(nuevas),
        "insertadas": insertadas,
        "por_insertar": len(nuevas) if dry_run else 0,
    }


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
