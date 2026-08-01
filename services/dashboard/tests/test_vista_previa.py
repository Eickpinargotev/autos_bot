"""Vuelca páginas del panel a HTML para mirarlas con los ojos.

No es una aserción: es una ayuda para revisar el diseño sin levantar el panel
ni crear datos a mano. Solo corre si se pide expresamente:

    docker compose -f docker-compose.local.yml run --rm \
        -e VISTA_PREVIA=1 dashboard pytest tests/test_vista_previa.py -s
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from src.db import pool
from src.services import clientes_whatsapp

saltar = pytest.mark.skipif(os.environ.get("VISTA_PREVIA") != "1", reason="solo bajo petición")


def _volcar(sesion, ruta: str, salida: str) -> None:
    html = sesion.get(ruta).text
    css = open("src/static/app.css", encoding="utf-8").read()
    js = open("src/static/app.js", encoding="utf-8").read()
    html = html.replace('<link rel="stylesheet" href="/static/app.css?v=5">', f"<style>{css}</style>")
    html = html.replace('<script src="/static/app.js?v=5"></script>', f"<script>{js}</script>")
    with open(f"/tmp/{salida}", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nVista previa: /tmp/{salida}")


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
def test_lista_de_mensajes(sesion_cliente):
    from src.services import mensajeria, media

    tareas = mensajeria.buscar_por_clave("TAREAS")
    _volcar(sesion_cliente, f"/mensajes?abierto={tareas['id']}", "vista_mensajes.html")
