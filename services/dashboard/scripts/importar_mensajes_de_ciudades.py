"""Convierte el catálogo de ciudades en mensajes reutilizables del panel.

Cada ciudad del Excel es en realidad una cadena de hasta cinco mensajes que se
envían uno tras otro. Esto los deja disponibles en «Mensajes» con el nombre de
la ciudad como clave, para poder mandarlos a mano desde «Enviar».

    docker compose -f docker-compose.local.yml run --rm dashboard \\
        python scripts/importar_mensajes_de_ciudades.py

Los marcadores `Imagen=...` que traía el texto se convierten en el adjunto de esa
parte y se comprueba que el archivo se pueda abrir, igual que si se hubieran
escrito a mano en el panel. Con `--sin-verificar` se salta la comprobación (mucho
más rápido cuando son decenas de ciudades y solo quieres cargarlas).

Es re-ejecutable: una ciudad ya importada se salta, salvo que uses `--forzar`.
"""

import argparse
import re
import sys

from src.db import pool
from src.db.migrate import aplicar_migraciones
from src.services import media, mensajeria

# Mismo marcador que entiende el bot al enviar.
_MARCADOR = re.compile(r"(?im)(?:^|\s)(imagen|video)\s*=\s*([A-Za-z0-9_\-./:?=&%~+]+)")

_COLUMNAS = ("mensaje_1", "mensaje_2", "mensaje_3", "mensaje_4", "mensaje_5")


def _partir(texto: str) -> tuple[str, str, str]:
    """Separa el texto del adjunto que venía embebido como `Imagen=...`."""
    texto = texto or ""
    encontrado = _MARCADOR.search(texto)
    if not encontrado:
        return texto.strip(), "", ""

    tipo = "imagen" if encontrado.group(1).lower() == "imagen" else "video"
    referencia = encontrado.group(2).rstrip(".,;:")
    limpio = _MARCADOR.sub("", texto)
    limpio = re.sub(r"[ \t]+\n", "\n", limpio)
    limpio = re.sub(r"\n{3,}", "\n\n", limpio).strip()
    return limpio, tipo, referencia


def importar(verificar: bool, forzar: bool) -> tuple[int, int]:
    ciudades = pool.consultar(
        "SELECT ciudad, mensaje_1, mensaje_2, mensaje_3, mensaje_4, mensaje_5 "
        "FROM invitaciones_ciudades ORDER BY ciudad"
    )

    creados = 0
    saltados = 0
    for fila in ciudades:
        clave = (fila["ciudad"] or "").strip().upper()
        if not clave:
            continue

        existente = mensajeria.buscar_por_clave(clave)
        if existente and not forzar:
            saltados += 1
            continue
        if existente:
            mensajeria.eliminar_plantilla(existente["id"])

        partes = []
        for columna in _COLUMNAS:
            texto, tipo, referencia = _partir(fila.get(columna))
            if not texto and not referencia:
                continue
            partes.append((texto, tipo, referencia))

        if not partes:
            saltados += 1
            continue

        plantilla = mensajeria.crear_plantilla(clave, f"Curso teórico — {clave.title()}", "importación")
        for orden, (texto, tipo, referencia) in enumerate(partes, start=1):
            if verificar:
                # Ruta normal: comprueba el adjunto igual que el panel.
                mensajeria.guardar_parte(plantilla["id"], orden, texto, tipo, referencia)
            else:
                # Sin comprobar: se guarda con `media_ok` en NULL y luego se puede
                # revisar desde el panel con «Revisar adjuntos».
                pool.ejecutar(
                    """
                    INSERT INTO plantilla_partes (plantilla_id, orden, texto, media_tipo, media_ref)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (plantilla["id"], orden, texto, tipo, media.extraer_referencia(referencia) if tipo else ""),
                )
        creados += 1
        print(f"  ✓ {clave}: {len(partes)} parte(s)")

    return creados, saltados


def main() -> int:
    parser = argparse.ArgumentParser(description="Importa las ciudades como mensajes del panel.")
    parser.add_argument("--sin-verificar", action="store_true", help="No comprueba los adjuntos (más rápido).")
    parser.add_argument("--forzar", action="store_true", help="Reemplaza los mensajes ya importados.")
    args = parser.parse_args()

    aplicar_migraciones()
    creados, saltados = importar(verificar=not args.sin_verificar, forzar=args.forzar)

    print(f"\n{creados} mensaje(s) creados, {saltados} sin cambios.")
    if args.sin_verificar and creados:
        print("Los adjuntos no se comprobaron: usa «Revisar adjuntos» en el panel cuando quieras.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
