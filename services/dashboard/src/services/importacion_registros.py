"""Lectura e importación idempotente del histórico de palabras clave."""

import csv
import io
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg2.extras

from src.db import pool
from src.services.registros import normalizar_numero

MAX_ARCHIVO_BYTES = 5 * 1024 * 1024
CABECERAS_BASE = ("numero", "Nombre")
CABECERAS_FECHA = ("Fecha de registro (dia/mes//año)", "Fecha de registro")
_ROTULOS_MANUALES = {"solicitado agregar manual", "ingreso manual google sheet", "ingreso manual"}
_PUNTUACION_RELLENO = set(".,;:_-–—/\\ ")


@dataclass(frozen=True)
class RegistroCSV:
    numero: str
    canal: str
    nombre: str
    palabra_clave: str
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


def _leer(archivo) -> LecturaCSV:
    unicos: dict[tuple[str, str], RegistroCSV] = {}
    leidas = rechazadas = 0
    errores: list[str] = []
    lector = csv.DictReader(archivo)
    cabeceras = lector.fieldnames or []
    faltantes = [cabecera for cabecera in CABECERAS_BASE if cabecera not in cabeceras]
    cabecera_fecha = next((c for c in CABECERAS_FECHA if c in cabeceras), "")
    if not cabecera_fecha:
        faltantes.append(CABECERAS_FECHA[0])
    if faltantes:
        raise ValueError(f"Faltan columnas obligatorias: {', '.join(faltantes)}")

    for numero_fila, fila in enumerate(lector, start=2):
        leidas += 1
        numero = normalizar_numero(fila.get("numero", ""))
        nombre = " ".join(str(fila.get("Nombre") or "").split())
        canal = str(fila.get("Canal") or "whatsapp").strip().lower()
        palabra = str(fila.get("Palabra clave") or "").strip()
        if palabra.casefold() == "histórico":
            palabra = ""
        try:
            if len(numero) < 7:
                raise ValueError("número inválido")
            if canal not in {"whatsapp", "telegram"}:
                raise ValueError("canal inválido")
            if len(nombre) > 200:
                raise ValueError("nombre demasiado largo")
            if len(palabra) > 60:
                raise ValueError("palabra clave demasiado larga")
            fecha = datetime.strptime(str(fila.get(cabecera_fecha) or "").strip(), "%d/%m/%Y").date()
        except ValueError as exc:
            rechazadas += 1
            if len(errores) < 10:
                errores.append(f"Fila {numero_fila}: {exc}")
            continue

        nuevo = RegistroCSV(numero, canal, nombre, palabra, fecha)
        clave = (numero, canal)
        anterior = unicos.get(clave)
        if anterior is None:
            unicos[clave] = nuevo
            continue
        nombre_elegido = anterior.nombre
        if not _nombre_real(anterior.nombre) and _nombre_real(nuevo.nombre):
            nombre_elegido = nuevo.nombre
        unicos[clave] = RegistroCSV(
            numero,
            canal,
            nombre_elegido,
            anterior.palabra_clave or nuevo.palabra_clave,
            min(anterior.fecha, nuevo.fecha),
        )
    return LecturaCSV(leidas, tuple(unicos.values()), rechazadas, tuple(errores))


def leer_bytes(datos: bytes) -> LecturaCSV:
    if len(datos) > MAX_ARCHIVO_BYTES:
        raise ValueError("El CSV no puede superar 5 MB.")
    try:
        texto = datos.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("El CSV debe estar codificado en UTF-8.") from exc
    return _leer(io.StringIO(texto, newline=""))


def leer_ruta(ruta: str | Path) -> LecturaCSV:
    return leer_bytes(Path(ruta).read_bytes())


def importar(
    lectura: LecturaCSV, proyecto_id: int, zona_horaria: str, *, dry_run: bool = False
) -> dict[str, int]:
    existentes = pool.consultar(
        "SELECT registro, canal FROM keyword_registros WHERE proyecto_id = %s", (int(proyecto_id),)
    )
    claves_existentes = {(str(f["registro"]), str(f["canal"])) for f in existentes}
    nuevas = [r for r in lectura.registros if (r.numero, r.canal) not in claves_existentes]
    insertadas = 0
    if not dry_run and nuevas:
        try:
            zona = ZoneInfo(zona_horaria or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            zona = ZoneInfo("UTC")
        valores = [
            (int(proyecto_id), r.numero, r.canal, r.nombre, r.palabra_clave,
             datetime.combine(r.fecha, time(hour=12), tzinfo=zona))
            for r in nuevas
        ]
        with pool.conexion(autocommit=False) as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TEMP TABLE importar_keyword_registros (
                            proyecto_id INTEGER, registro VARCHAR(80), canal VARCHAR(20),
                            nombre VARCHAR(200), palabra_clave VARCHAR(60), creado_en TIMESTAMPTZ
                        ) ON COMMIT DROP
                        """
                    )
                    psycopg2.extras.execute_values(
                        cur, "INSERT INTO importar_keyword_registros VALUES %s", valores, page_size=1000
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
