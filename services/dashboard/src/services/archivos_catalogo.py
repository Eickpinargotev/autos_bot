"""Importar y exportar catálogos del negocio sin depender de una hoja concreta.

Se aceptan CSV y las tablas HTML que descarga Google Sheets. El HTML se trata
exclusivamente como datos: no se ejecutan scripts, estilos, enlaces ni ninguna
otra instrucción incluida en el archivo.
"""

import csv
import io
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any

from src.db import pool
from src.services import media, trazabilidad

MAX_ARCHIVO_BYTES = 5 * 1024 * 1024


class _LectorTablaHTML(HTMLParser):
    """Extrae solamente el texto de las celdas de la primera tabla útil."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.filas: list[list[str]] = []
        self._fila: list[str] | None = None
        self._celda: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._fila = []
        elif tag in ("td", "th") and self._fila is not None:
            self._celda = []
        elif tag == "br" and self._celda is not None:
            self._celda.append("\n")

    def handle_data(self, data: str) -> None:
        if self._celda is not None:
            self._celda.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._celda is not None:
            assert self._fila is not None
            self._fila.append("".join(self._celda).strip())
            self._celda = None
        elif tag == "tr" and self._fila is not None:
            if any(celda.strip() for celda in self._fila):
                self.filas.append(self._fila)
            self._fila = None


def _texto(datos: bytes) -> str:
    if len(datos) > MAX_ARCHIVO_BYTES:
        raise ValueError("El archivo supera el máximo de 5 MB.")
    if not datos:
        raise ValueError("El archivo está vacío.")
    for codificacion in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            pass
    raise ValueError("No se pudo leer la codificación del archivo.")


def leer_tabla(datos: bytes, nombre: str = "") -> list[list[str]]:
    texto = _texto(datos)
    parece_html = nombre.lower().endswith((".html", ".htm")) or "<table" in texto[:5000].lower()
    if parece_html:
        lector = _LectorTablaHTML()
        lector.feed(texto)
        filas = lector.filas
    else:
        muestra = texto[:8192]
        try:
            dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t")
        except csv.Error:
            dialecto = csv.excel
        filas = [[celda.strip() for celda in fila] for fila in csv.reader(io.StringIO(texto), dialecto)]
        filas = [fila for fila in filas if any(celda for celda in fila)]
    if not filas:
        raise ValueError("No se encontró ninguna tabla con datos.")
    return filas


def _normalizar_titulo(valor: str) -> str:
    valor = unicodedata.normalize("NFKD", valor or "")
    valor = "".join(c for c in valor if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", "_", valor.upper()).strip("_")


_ORDINALES = {
    "PRIMER": 1, "PRIMERO": 1, "SEGUNDO": 2, "TERCER": 3, "TERCERO": 3,
    "CUARTO": 4, "QUINTO": 5, "SEXTO": 6, "SEPTIMO": 7, "OCTAVO": 8,
    "NOVENO": 9, "DECIMO": 10,
}


def _orden_mensaje(titulo: str) -> int | None:
    limpio = _normalizar_titulo(titulo)
    if "MENSAJE" not in limpio:
        return None
    numero = re.search(r"(?:MENSAJE_?|_)(\d+)(?:_|$)", limpio)
    if numero:
        return int(numero.group(1))
    for palabra, orden in _ORDINALES.items():
        if limpio.startswith(palabra + "_MENSAJE"):
            return orden
    return None


_MARCADOR_MEDIA = re.compile(
    r"(?:^|\n)\s*(imagen|video)\s*=\s*(\S+)\s*$", re.IGNORECASE
)


def _separar_media(valor: str) -> dict[str, Any]:
    valor = (valor or "").strip()
    encontrado = _MARCADOR_MEDIA.search(valor)
    if not encontrado:
        return {"texto": valor, "media_tipo": "", "media_ref": ""}
    return {
        "texto": valor[: encontrado.start()].rstrip(),
        "media_tipo": encontrado.group(1).lower(),
        "media_ref": media.extraer_referencia(encontrado.group(2)),
    }


def analizar_mensajes(datos: bytes, nombre: str = "") -> list[dict[str, Any]]:
    filas = leer_tabla(datos, nombre)
    indice_encabezado = next(
        (
            i for i, fila in enumerate(filas)
            if any(_normalizar_titulo(x) in ("CLAVE", "CIUDAD", "CIUDAD_MAYUSCULA") for x in fila)
            and any(_orden_mensaje(x) is not None for x in fila)
        ),
        None,
    )
    if indice_encabezado is None:
        raise ValueError(
            "El archivo necesita una columna CLAVE o CIUDAD_MAYUSCULA y columnas de mensajes."
        )
    encabezados = [_normalizar_titulo(x) for x in filas[indice_encabezado]]
    indices = {titulo: i for i, titulo in enumerate(encabezados)}
    indice_clave = indices.get("CIUDAD_MAYUSCULA")
    if indice_clave is None:
        indice_clave = indices.get("CLAVE", indices.get("CIUDAD"))
    columnas = sorted(
        (orden, i) for i, titulo in enumerate(filas[indice_encabezado])
        if (orden := _orden_mensaje(titulo)) is not None
    )
    if indice_clave is None or not columnas:
        raise ValueError(
            "El archivo necesita una columna CLAVE o CIUDAD_MAYUSCULA y columnas de mensajes."
        )

    resultado: list[dict[str, Any]] = []
    claves: set[str] = set()
    for numero_fila, fila in enumerate(filas[indice_encabezado + 1 :], start=indice_encabezado + 2):
        clave = fila[indice_clave].strip().upper() if indice_clave < len(fila) else ""
        if not clave:
            continue
        if clave in claves:
            raise ValueError(f"La clave «{clave}» está repetida (fila {numero_fila}).")
        claves.add(clave)
        partes = []
        for _, indice in columnas:
            valor = fila[indice] if indice < len(fila) else ""
            if valor.strip():
                partes.append(_separar_media(valor))
        if not partes:
            raise ValueError(f"La clave «{clave}» no tiene ningún mensaje.")
        resultado.append({"clave": clave, "partes": partes})
    if not resultado:
        raise ValueError("El archivo no contiene mensajes para cargar.")
    return resultado


def importar_mensajes(proyecto_id: int, datos: bytes, nombre: str, usuario: str) -> dict[str, int]:
    plantillas = analizar_mensajes(datos, nombre)

    # Los adjuntos repetidos se comprueban una sola vez. La hoja de ciudades
    # suele repetir el mismo video y la misma imagen decenas de veces.
    comprobaciones: dict[tuple[str, str], tuple[bool, str]] = {}
    for plantilla in plantillas:
        for parte in plantilla["partes"]:
            if parte["media_ref"]:
                llave = (parte["media_tipo"], parte["media_ref"])
                if llave not in comprobaciones:
                    comprobaciones[llave] = media.verificar(parte["media_ref"], parte["media_tipo"])

    creadas = actualizadas = piezas = problemas = 0
    with pool.conexion(autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                for plantilla in plantillas:
                    cur.execute(
                        "SELECT id FROM plantillas_mensaje WHERE proyecto_id = %s AND clave = %s",
                        (int(proyecto_id), plantilla["clave"]),
                    )
                    fila = cur.fetchone()
                    if fila:
                        plantilla_id = fila[0]
                        actualizadas += 1
                        cur.execute(
                            "UPDATE plantillas_mensaje SET actualizado_en = NOW() WHERE id = %s",
                            (plantilla_id,),
                        )
                    else:
                        cur.execute(
                            "INSERT INTO plantillas_mensaje (proyecto_id, clave, creado_por) "
                            "VALUES (%s, %s, %s) RETURNING id",
                            (int(proyecto_id), plantilla["clave"], usuario),
                        )
                        plantilla_id = cur.fetchone()[0]
                        creadas += 1
                    cur.execute(
                        "DELETE FROM plantilla_partes WHERE proyecto_id = %s AND plantilla_id = %s",
                        (int(proyecto_id), plantilla_id),
                    )
                    for orden, parte in enumerate(plantilla["partes"], start=1):
                        ok = None
                        error = ""
                        if parte["media_ref"]:
                            ok, error = comprobaciones[(parte["media_tipo"], parte["media_ref"])]
                            problemas += int(not ok)
                        cur.execute(
                            """
                            INSERT INTO plantilla_partes
                                (proyecto_id, plantilla_id, orden, texto, media_tipo, media_ref,
                                 media_ok, media_error, media_revisada_en)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                                    CASE WHEN %s <> '' THEN NOW() ELSE NULL END)
                            """,
                            (int(proyecto_id), plantilla_id, orden, parte["texto"],
                             parte["media_tipo"], parte["media_ref"], ok, error, parte["media_ref"]),
                        )
                        piezas += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"creadas": creadas, "actualizadas": actualizadas, "partes": piezas, "problemas": problemas}


def _parte_a_celda(parte: dict[str, Any]) -> str:
    valor = (parte.get("texto") or "").strip()
    if parte.get("media_ref"):
        marcador = f"{parte.get('media_tipo') or 'imagen'}={parte['media_ref']}"
        valor = f"{valor}\n\n{marcador}" if valor else marcador
    return valor


def exportar_mensajes(proyecto_id: int) -> bytes:
    from src.services import mensajeria

    plantillas = mensajeria.listar_plantillas(proyecto_id)
    max_partes = max((len(p["partes"]) for p in plantillas), default=1)
    salida = io.StringIO(newline="")
    escritor = csv.writer(salida)
    escritor.writerow(["CLAVE", *[f"MENSAJE {n}" for n in range(1, max_partes + 1)]])
    for plantilla in plantillas:
        celdas = [_parte_a_celda(parte) for parte in plantilla["partes"]]
        escritor.writerow([plantilla["clave"], *celdas, *([""] * (max_partes - len(celdas)))])
    return ("\ufeff" + salida.getvalue()).encode("utf-8")


def analizar_conocimiento(datos: bytes, nombre: str = "") -> list[dict[str, Any]]:
    filas = leer_tabla(datos, nombre)
    indice_encabezado = next(
        (i for i, fila in enumerate(filas) if any(
            _normalizar_titulo(x) in ("CONTENIDO", "CONOCIMIENTO", "TEXTO") for x in fila
        )),
        None,
    )
    if indice_encabezado is None:
        raise ValueError("El archivo necesita una columna CONTENIDO.")
    encabezados = [_normalizar_titulo(x) for x in filas[indice_encabezado]]
    indices = {titulo: i for i, titulo in enumerate(encabezados)}
    indice_contenido = indices.get("CONTENIDO", indices.get("CONOCIMIENTO", indices.get("TEXTO")))
    indice_activo = indices.get("ACTIVO")
    if indice_contenido is None:
        raise ValueError("El archivo necesita una columna CONTENIDO.")
    resultado = []
    vistos = set()
    for numero_fila, fila in enumerate(filas[indice_encabezado + 1 :], start=indice_encabezado + 2):
        contenido = fila[indice_contenido].strip() if indice_contenido < len(fila) else ""
        if not contenido:
            continue
        try:
            contenido = trazabilidad._validar_chunk(contenido)
        except ValueError as e:
            raise ValueError(f"Fila {numero_fila}: {e}") from e
        if contenido in vistos:
            continue
        vistos.add(contenido)
        activo = True
        if indice_activo is not None and indice_activo < len(fila):
            activo = _normalizar_titulo(fila[indice_activo]) not in ("NO", "FALSE", "0", "INACTIVO")
        resultado.append({"contenido": contenido, "activo": activo})
    if not resultado:
        raise ValueError("El archivo no contiene conocimiento para cargar.")
    return resultado


def importar_conocimiento(proyecto_id: int, datos: bytes, nombre: str) -> dict[str, int]:
    entradas = analizar_conocimiento(datos, nombre)
    existentes = {
        fila["contenido"]: fila for fila in pool.consultar(
            "SELECT id, contenido, activo FROM rag_chunks WHERE proyecto_id = %s", (int(proyecto_id),)
        )
    }
    creados = actualizados = 0
    for entrada in entradas:
        actual = existentes.get(entrada["contenido"])
        if actual:
            if bool(actual["activo"]) != entrada["activo"]:
                pool.ejecutar(
                    "UPDATE rag_chunks SET activo = %s, actualizado_en = NOW() "
                    "WHERE proyecto_id = %s AND id = %s",
                    (entrada["activo"], int(proyecto_id), actual["id"]),
                )
                actualizados += 1
        else:
            fila = trazabilidad.crear_chunk(proyecto_id, entrada["contenido"])
            if not entrada["activo"]:
                trazabilidad.alternar_chunk_activo(proyecto_id, fila["id"])
            creados += 1
    return {"creados": creados, "actualizados": actualizados, "total": len(entradas)}


def exportar_conocimiento(proyecto_id: int) -> bytes:
    salida = io.StringIO(newline="")
    escritor = csv.writer(salida)
    escritor.writerow(["CONTENIDO", "ACTIVO"])
    for chunk in trazabilidad.listar_chunks(proyecto_id):
        escritor.writerow([chunk["contenido"], "SI" if chunk["activo"] else "NO"])
    return ("\ufeff" + salida.getvalue()).encode("utf-8")
