"""Vuelca páginas del panel a HTML para mirarlas con los ojos.

No es una aserción: es una ayuda para revisar el diseño sin levantar el panel
ni crear datos a mano. Solo corre si se pide expresamente:

    docker compose -f docker-compose.local.yml run --rm \
        -e VISTA_PREVIA=1 dashboard pytest tests/test_vista_previa.py -s
"""

import os
import re
from datetime import datetime, timedelta, timezone

import pytest

from src.db import pool
from src.services import clientes_whatsapp

saltar = pytest.mark.skipif(os.environ.get("VISTA_PREVIA") != "1", reason="solo bajo petición")


# El `?v=` del CSS y del JS sube cada vez que se tocan, así que aquí se
# reconoce por patrón: escrito a mano, el volcado dejaba de incluir los estilos
# en cuanto alguien subía la versión y la vista previa salía en blanco y negro
# sin que nada avisara.
_HOJA = re.compile(r'<link rel="stylesheet" href="/static/app\.css\?v=\d+">')
_GUION = re.compile(r'<script src="/static/app\.js\?v=\d+"></script>')

# Se vuelca dentro del repo (bind mount) y no a /tmp del contenedor, que muere
# con él. `_local/` está ignorado por git.
DIRECTORIO_VISTAS = "_local/vistas"


def _volcar(sesion, ruta: str, salida: str) -> None:
    html = sesion.get(ruta).text
    css = open("src/static/app.css", encoding="utf-8").read()
    js = open("src/static/app.js", encoding="utf-8").read()
    html = _HOJA.sub(lambda _: f"<style>{css}</style>", html)
    html = _GUION.sub(lambda _: f"<script>{js}</script>", html)

    os.makedirs(DIRECTORIO_VISTAS, exist_ok=True)
    destino = f"{DIRECTORIO_VISTAS}/{salida}"
    with open(destino, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nVista previa: services/dashboard/{destino}")


@saltar
def test_perfil_del_cliente(sesion_admin, monkeypatch):
    monkeypatch.setattr(
        clientes_whatsapp.settings, "PUBLIC_WEBHOOK_BASE_URL", "https://webhook.ejemplo.com"
    )
    negocio = clientes_whatsapp.crear("Escuela de manejo", "50688887777")
    inicio = datetime(2026, 7, 8, 20, 0, tzinfo=timezone.utc)
    for i in range(14):
        pool.ejecutar(
            """
            INSERT INTO conversation_messages
                (client_id, canal, direction, author, sender_name, text, created_at)
            VALUES (%s, 'whatsapp', 'inbound', 'cliente', 'Ana', 'hola', %s)
            """,
            (f"5068888{i:04d}", inicio + timedelta(minutes=i)),
        )
    pool.ejecutar(
        """
        INSERT INTO uso_eventos (periodo_id, client_id, canal, categoria, origen,
                                 mensajes, costo_real_microusd, costo_cliente_microusd)
        SELECT id, '506', 'whatsapp', 'llm', 'agente', 40, 196500, 314400
        FROM periodos_facturacion WHERE cerrado_en IS NULL
        """
    )
    _volcar(sesion_admin, f"/admin/negocios/{negocio['id']}", "vista_negocio.html")


@saltar
def test_lista_de_clientes(sesion_admin, monkeypatch):
    monkeypatch.setattr(
        clientes_whatsapp.settings, "PUBLIC_WEBHOOK_BASE_URL", "https://webhook.ejemplo.com"
    )
    clientes_whatsapp.crear("Escuela de manejo", "50688887777")
    clientes_whatsapp.crear("Taller mecánico", "50699998888")
    _volcar(sesion_admin, "/admin/negocios", "vista_negocios.html")


@saltar
def test_sesiones_de_envio(sesion_cliente, monkeypatch):
    """Una tanda a medias, otra terminada con fallos y una programada."""
    from src.services import envios as svc_envios
    from src.services import media, mensajeria

    monkeypatch.setattr(media, "verificar", lambda ref, tipo: (True, ""))
    plantilla = mensajeria.crear_plantilla("ALAJUELA", "german")
    mensajeria.guardar_parte(plantilla["id"], 1, "El curso empieza el lunes a las 6 pm.", "", "")

    def _tanda(cuantos):
        return svc_envios.crear_lote(
            categoria="mensaje", referencia_id=plantilla["id"], canal="whatsapp",
            destinos=[f"5068888{i:04d}" for i in range(cuantos)], usuario="german",
        )

    en_curso = _tanda(40)
    ids = [d["id"] for d in svc_envios.destinos_de(en_curso["id"])]
    pool.ejecutar("UPDATE envios SET estado='enviado' WHERE id = ANY(%s)", (ids[:11],))
    pool.ejecutar("UPDATE envios SET estado='error', error_cliente='El número no está en WhatsApp' WHERE id = ANY(%s)", (ids[11:13],))

    terminada = _tanda(8)
    ids = [d["id"] for d in svc_envios.destinos_de(terminada["id"])]
    pool.ejecutar("UPDATE envios SET estado='enviado' WHERE id = ANY(%s)", (ids[:7],))
    pool.ejecutar("UPDATE envios SET estado='error', error_cliente='No se pudo abrir la imagen' WHERE id = %s", (ids[7],))
    pool.ejecutar("UPDATE envios_lote SET creado_en = NOW() - INTERVAL '3 hours' WHERE id = %s", (terminada["id"],))

    _volcar(sesion_cliente, "/envios", "vista_envios.html")
    _volcar(sesion_cliente, "/enviar", "vista_enviar.html")


@saltar
def test_palabras_clave(sesion_cliente):
    from src.services import palabras_clave

    tareas = next(p for p in palabras_clave.listar() if p["palabra"] == "tareas")
    _volcar(sesion_cliente, f"/palabras-clave?abierta={tareas['id']}", "vista_palabras.html")


@saltar
def test_lista_de_mensajes(sesion_cliente, monkeypatch):
    """La lista, con una ventana abierta y un mensaje con adjunto comprobado."""
    from src.services import media, mensajeria

    monkeypatch.setattr(media, "verificar", lambda ref, tipo: (True, ""))
    alajuela = mensajeria.crear_plantilla("ALAJUELA", "german")
    mensajeria.guardar_parte(alajuela["id"], 1, "Buenas! El curso empieza el lunes a las 6 pm.", "", "")
    mensajeria.guardar_parte(alajuela["id"], 2, "Aquí tiene el mapa del local.", "imagen", "1AbC_defGHIjklMNO")

    heredia = mensajeria.crear_plantilla("HEREDIA", "german")
    mensajeria.guardar_parte(heredia["id"], 1, "", "", "")

    _volcar(sesion_cliente, f"/mensajes?abierto={alajuela['id']}&parte=2", "vista_mensajes.html")


@saltar
def test_base_de_conocimiento(sesion_cliente):
    from src.services import trazabilidad

    trazabilidad.crear_chunk(
        "El curso teórico cuesta 45.000 colones e incluye el material de estudio. "
        "Se paga en efectivo el mismo día, no se cobra nada por adelantado."
    )
    trazabilidad.crear_chunk("Las clases prácticas son de dos horas y se agendan por WhatsApp.")
    pool.ejecutar("INSERT INTO rag_chunks (contenido) VALUES (%s)", ("x" * 1400,))

    _volcar(sesion_cliente, "/conocimiento", "vista_conocimiento.html")
