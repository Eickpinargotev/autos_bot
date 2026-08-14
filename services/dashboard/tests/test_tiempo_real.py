"""El panel se entera solo de lo que escribe el bot.

Lo que se prueba aquí es la cadena entera: la consulta de señales ve el cambio,
el hub decide a quién avisar, y cada fragmento se puede pedir por separado y
devuelve lo mismo que ya sale dentro de su página.

El hub se prueba por sus piezas (`senales`, `topics_para`, `_bucle`) y no
abriendo el flujo con `TestClient`: un SSE no termina nunca, y un test que se
pone a leerlo deja la suite colgada esperando un byte que no llega.
"""

import asyncio
import re

import pytest

from src.core import eventos
from src.db import pool
from src.services import trazabilidad


def _reporte(problema: str = "algo") -> None:
    pool.ejecutar(
        "INSERT INTO reportes (nombre, numero, problema) VALUES ('Ana', '50611112222', %s)",
        (problema,),
    )


def _mensaje(client_id: str, texto: str, direccion: str = "inbound") -> int:
    pool.ejecutar(
        """
        INSERT INTO conversation_messages (client_id, canal, direction, author, text)
        VALUES (%s, 'whatsapp', %s, 'cliente', %s)
        """,
        (client_id, direccion, texto),
    )
    return pool.consultar_uno("SELECT max(id) AS id FROM conversation_messages")["id"]


# --- Las señales --------------------------------------------------------------

def test_las_senales_traen_un_valor_por_tema():
    assert set(eventos.senales()) == set(eventos.TOPICS)


def test_un_reporte_nuevo_mueve_su_tema_y_ningun_otro():
    antes = eventos.senales()
    _reporte("el bot no supo qué decir")
    despues = eventos.senales()

    cambiados = {t for t in antes if antes[t] != despues[t]}
    assert cambiados == {"reportes"}


def test_marcar_un_reporte_revisado_tambien_se_nota():
    """El máximo de `id` no cambia al revisar: por eso la señal lleva también la
    cuenta de pendientes. Sin ella, la bandeja se quedaría sin refrescar justo
    cuando alguien la está atendiendo desde otra pestaña."""
    _reporte()
    fila = pool.consultar_uno("SELECT max(id) AS id FROM reportes")

    antes = eventos.senales()
    trazabilidad.marcar_reporte_revisado(fila["id"])
    despues = eventos.senales()

    assert antes["reportes"] != despues["reportes"]


def test_un_mensaje_nuevo_mueve_el_tema_de_conversaciones():
    antes = eventos.senales()
    _mensaje("50600000001", "hola")
    assert eventos.senales()["conversaciones"] != antes["conversaciones"]


def test_sin_movimiento_las_senales_no_cambian():
    """Es la condición que hace que el navegador no pida nada estando quieto."""
    assert eventos.senales() == eventos.senales()


# --- El reparto por rol -------------------------------------------------------

def test_el_proyecto_no_recibe_los_temas_del_administrador():
    """El aviso no lleva datos, pero enterarse de que hay movimiento en las
    conversaciones o en las incidencias ya dice algo de los demás negocios."""
    del_negocio = eventos.topics_para({"rol": "cliente"})

    assert "reportes" in del_negocio
    assert "preguntas" in del_negocio
    assert "conversaciones" not in del_negocio
    assert "incidencias" not in del_negocio
    assert "bloqueos" not in del_negocio


def test_el_administrador_recibe_lo_suyo():
    del_admin = eventos.topics_para({"rol": "admin"})
    assert {"conversaciones", "bloqueos", "incidencias"} <= del_admin


def test_sin_sesion_no_se_recibe_nada():
    assert eventos.topics_para(None) == frozenset()


def test_todos_los_temas_le_llegan_a_alguien():
    """Un tema que no esté en ninguna de las dos listas se calcularía en cada
    tick para no avisar a nadie."""
    cubiertos = eventos.topics_para({"rol": "admin"}) | eventos.topics_para({"rol": "cliente"})
    assert set(eventos.TOPICS) <= cubiertos


# --- El hub -------------------------------------------------------------------

def test_sin_suscriptores_el_hub_no_consulta(monkeypatch):
    """Es lo que hace que el panel cerrado no le cueste nada a Postgres."""
    veces = 0

    def contar():
        nonlocal veces
        veces += 1
        return {}

    monkeypatch.setattr(eventos, "senales", contar)
    monkeypatch.setattr(eventos, "INTERVALO", 0.01)

    async def prueba():
        nonlocal veces
        tarea = asyncio.create_task(eventos._bucle())
        try:
            await asyncio.sleep(0.1)
            assert veces == 0, "consultó sin que nadie estuviera mirando"

            async with eventos.suscribirse():
                await asyncio.sleep(0.1)
                assert veces > 0, "con alguien suscrito debería haber consultado"
        finally:
            tarea.cancel()
            await asyncio.gather(tarea, return_exceptions=True)

    asyncio.run(prueba())


def test_el_hub_avisa_solo_de_lo_que_cambio(monkeypatch):
    foto = {"reportes": (1, 1), "uso": (5,)}
    monkeypatch.setattr(eventos, "senales", lambda: dict(foto))
    monkeypatch.setattr(eventos, "INTERVALO", 0.01)

    async def prueba():
        tarea = asyncio.create_task(eventos._bucle())
        try:
            async with eventos.suscribirse() as cola:
                await asyncio.sleep(0.05)  # la primera foto es la referencia
                assert cola.empty(), "avisó en el primer tick, sin nada con qué comparar"

                foto["reportes"] = (2, 2)
                temas = await asyncio.wait_for(cola.get(), timeout=2)
                assert temas == {"reportes"}
        finally:
            tarea.cancel()
            await asyncio.gather(tarea, return_exceptions=True)

    asyncio.run(prueba())


def test_al_irse_el_ultimo_deja_de_consultar(monkeypatch):
    veces = 0

    def contar():
        nonlocal veces
        veces += 1
        return {}

    monkeypatch.setattr(eventos, "senales", contar)
    monkeypatch.setattr(eventos, "INTERVALO", 0.01)

    async def prueba():
        nonlocal veces
        tarea = asyncio.create_task(eventos._bucle())
        try:
            async with eventos.suscribirse():
                await asyncio.sleep(0.05)
            assert eventos.suscriptores() == 0

            quietas = veces
            await asyncio.sleep(0.15)
            # Puede quedar UNA consulta en vuelo: al soltar el último suscriptor
            # el bucle podía estar ya dentro de `senales()`, y ahí no se puede
            # deshacer. Lo que se comprueba es que no siga: con 0.15 s y ticks
            # de 0.01 s, sin la guarda habrían entrado unas quince más.
            assert veces - quietas <= 1, "siguió consultando con el panel cerrado"
        finally:
            tarea.cancel()
            await asyncio.gather(tarea, return_exceptions=True)

    asyncio.run(prueba())


# --- El endpoint --------------------------------------------------------------

def test_el_flujo_de_eventos_pide_sesion(cliente_http):
    respuesta = cliente_http.get("/eventos", follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/login")


def test_el_flujo_no_se_cachea_ni_se_bufferiza():
    """Sin `X-Accel-Buffering: no`, un nginx delante acumula el flujo y los
    avisos no llegan hasta que se llena su buffer.

    Se llama a la función y se miran las cabeceras de la respuesta, sin pedirla
    por HTTP: `TestClient` abriría el flujo de verdad y se quedaría esperando un
    byte que no llega nunca, colgando la suite entera. La respuesta se construye
    sin tocar el generador, así que nadie se suscribe al hub.
    """
    from src.routes import tiempo_real

    respuesta = asyncio.run(tiempo_real.eventos_del_panel(usuario={"rol": "admin"}))

    assert respuesta.media_type == "text/event-stream"
    assert respuesta.headers["cache-control"] == "no-cache"
    assert respuesta.headers["x-accel-buffering"] == "no"


# --- Los fragmentos -----------------------------------------------------------

FRAGMENTOS_DEL_NEGOCIO = (
    ("/reportes", "/reportes/lista"),
    ("/preguntas", "/preguntas/lista"),
    ("/clientes", "/clientes/lista"),
    ("/factura", "/factura/totales"),
    ("/envios", "/envios/sesiones"),
)

FRAGMENTOS_DEL_ADMIN = (
    ("/admin/bloqueos", "/admin/bloqueos/lista"),
    ("/admin/incidencias", "/admin/incidencias/lista"),
    ("/admin/logs", "/admin/logs/lista"),
    ("/admin/costos", "/admin/costos/totales"),
)


@pytest.mark.parametrize("pagina, fragmento", FRAGMENTOS_DEL_NEGOCIO)
def test_el_fragmento_del_negocio_es_el_mismo_que_pinta_su_pagina(sesion_cliente, pagina, fragmento):
    """Si la página y su fragmento no compartieran el `{% include %}`, un día la
    tabla que llega refrescando dejaría de parecerse a la que estaba."""
    _reporte()
    _mensaje("50600000002", "hola")

    trozo = sesion_cliente.get(fragmento)
    assert trozo.status_code == 200
    assert "<!doctype" not in trozo.text.lower(), "el fragmento trae el armazón entero"
    assert trozo.text.strip() in sesion_cliente.get(pagina).text


@pytest.mark.parametrize("pagina, fragmento", FRAGMENTOS_DEL_ADMIN)
def test_el_fragmento_del_admin_es_el_mismo_que_pinta_su_pagina(sesion_admin, pagina, fragmento):
    trozo = sesion_admin.get(fragmento)
    assert trozo.status_code == 200
    assert "<!doctype" not in trozo.text.lower()
    assert trozo.text.strip() in sesion_admin.get(pagina).text


def test_el_fragmento_de_reportes_respeta_el_filtro(sesion_cliente):
    """El refresco lleva el filtro en su URL: mirando solo los pendientes, lo
    que llegue no puede ser de pronto la lista completa."""
    _reporte("pendiente")
    _reporte("ya resuelto")
    revisado = pool.consultar_uno("SELECT max(id) AS id FROM reportes")
    trazabilidad.marcar_reporte_revisado(revisado["id"])

    todos = sesion_cliente.get("/reportes/lista").text
    solo_pendientes = sesion_cliente.get("/reportes/lista?pendientes=1").text

    assert "ya resuelto" in todos
    assert "ya resuelto" not in solo_pendientes
    assert "pendiente" in solo_pendientes


# --- Las pastillas del menú ---------------------------------------------------

def test_el_menu_lateral_cuenta_lo_que_falta_por_atender(sesion_cliente):
    _reporte()
    _reporte()
    menu = sesion_cliente.get("/pendientes?en=/reportes")

    assert menu.status_code == 200
    assert 'class="pendientes"' in menu.text
    assert ">2<" in menu.text


def test_el_menu_refrescado_sigue_marcando_donde_estas(sesion_cliente):
    """Sin el `?en=`, el primer refresco apagaba el resaltado y el menú dejaba
    de decir en qué página estabas."""
    assert 'href="/reportes" class="activo"' in sesion_cliente.get("/pendientes?en=/reportes").text
    assert 'href="/reportes" class="activo"' not in sesion_cliente.get("/pendientes?en=/factura").text


def test_el_menu_no_abre_flujo_por_su_cuenta(sesion_cliente):
    """El menú se actualiza si la página ya tenía flujo, pero no lo abre.

    Esto no es una preferencia: el menú sale en TODAS las pantallas, y en cuanto
    abre flujo, hasta un catálogo quieto se lleva una de las SEIS conexiones que
    Chrome permite por origen en HTTP/1.1. Al sexto cambio de pestaña el
    navegador se quedaba sin ranuras y el panel entero se quedaba pensando.
    """
    menu = sesion_cliente.get("/mensajes").text
    marca = re.search(r"<nav class=\"lateral-nav\"[^>]*>", menu).group(0)
    assert "data-secundario" in marca


# Pantallas de catálogo: se editan a mano y no las toca nadie más, así que no
# tienen por qué mantener una conexión abierta.
QUIETAS_DEL_NEGOCIO = ("/mensajes", "/palabras-clave", "/enviar", "/conocimiento")
QUIETAS_DEL_ADMIN = ("/admin/usuarios", "/admin/configuracion", "/admin/periodos", "/admin/tarifas")


def _abre_flujo(html: str) -> bool:
    """Lo mismo que decide `necesitanFlujo()` en app.js."""
    vivos = re.findall(r"<[^>]*data-refrescar=\"[^\"]*\"[^>]*>", html)
    return any("data-secundario" not in v for v in vivos) or "data-cola=" in html


@pytest.mark.parametrize("ruta", QUIETAS_DEL_NEGOCIO)
def test_una_pantalla_de_catalogo_del_negocio_no_abre_flujo(sesion_cliente, ruta):
    assert not _abre_flujo(sesion_cliente.get(ruta).text), f"{ruta} gasta una conexión sin necesitarla"


@pytest.mark.parametrize("ruta", QUIETAS_DEL_ADMIN)
def test_una_pantalla_de_catalogo_del_admin_no_abre_flujo(sesion_admin, ruta):
    assert not _abre_flujo(sesion_admin.get(ruta).text), f"{ruta} gasta una conexión sin necesitarla"


@pytest.mark.parametrize("ruta", ("/reportes", "/preguntas", "/clientes", "/factura", "/envios"))
def test_las_pantallas_vivas_si_abren_flujo(sesion_cliente, ruta):
    """La otra mitad del trato: lo que sí cambia solo tiene que enterarse."""
    assert _abre_flujo(sesion_cliente.get(ruta).text), f"{ruta} dejó de actualizarse sola"


def test_sin_nada_pendiente_no_se_pinta_pastilla(sesion_cliente):
    pool.ejecutar("DELETE FROM reportes")
    pool.ejecutar("DELETE FROM preguntas_sin_respuesta")
    assert 'class="pendientes"' not in sesion_cliente.get("/pendientes?en=/reportes").text


# --- El chat en vivo ----------------------------------------------------------

def test_desde_id_solo_trae_lo_posterior():
    primero = _mensaje("50699999999", "el primero")
    segundo = _mensaje("50699999999", "el segundo")

    nuevos = trazabilidad.mensajes_de("50699999999", "whatsapp", desde_id=primero)["mensajes"]

    assert [m["id"] for m in nuevos] == [segundo]
    assert nuevos[0]["text"] == "el segundo"


def test_desde_id_devuelve_en_orden_ascendente():
    _mensaje("50688888888", "uno")
    base = _mensaje("50688888888", "dos")
    _mensaje("50688888888", "tres")
    _mensaje("50688888888", "cuatro")

    nuevos = trazabilidad.mensajes_de("50688888888", "whatsapp", desde_id=base)["mensajes"]
    assert [m["text"] for m in nuevos] == ["tres", "cuatro"]


def test_leer_hacia_atras_sigue_funcionando_igual():
    """`desde_id` no puede haberle cambiado el sentido al cursor de siempre."""
    primero = _mensaje("50677777777", "viejo")
    segundo = _mensaje("50677777777", "nuevo")

    atras = trazabilidad.mensajes_de("50677777777", "whatsapp", antes_de=segundo)["mensajes"]
    assert [m["id"] for m in atras] == [primero]


def test_la_cola_del_chat_no_repite_el_separador_de_dia(sesion_admin):
    """Añadiendo mensajes por el final, el separador de fecha solo debe salir al
    cambiar de día. Sin decirle en qué día se quedó lo ya pintado, cada tanda
    volvería a estampar el de hoy."""
    primero = _mensaje("50666666666", "primero de hoy")

    # El día se lee del hilo YA PINTADO, igual que hace el navegador, y no de la
    # base: el separador se calcula en la zona horaria del proyecto, así que la
    # fecha que diga Postgres puede ser otra.
    hilo = sesion_admin.get("/admin/logs/whatsapp/50666666666?fragmento=1").text
    dia = re.search(r'data-dia="([^"]*)"', hilo).group(1)

    _mensaje("50666666666", "segundo de hoy")
    base = "/admin/logs/whatsapp/50666666666?fragmento=1&desde=%s" % primero

    con_dia = sesion_admin.get(f"{base}&dia={dia}").text
    sin_dia = sesion_admin.get(base).text

    assert con_dia.count("separador-dia") == 0
    assert con_dia.count('data-id="') == 1
    # Y se comprueba que es el `dia` quien lo evita, no que nunca salga.
    assert sin_dia.count("separador-dia") == 1


def test_sin_mensajes_nuevos_la_cola_viene_vacia(sesion_admin):
    """Un cuerpo vacío es lo correcto: no es que el chat esté vacío, es que no
    ha llegado nada. Decir «Sin mensajes» borraría la conversación en pantalla."""
    ultimo = _mensaje("50655555555", "el unico")
    cola = sesion_admin.get(f"/admin/logs/whatsapp/50655555555?fragmento=1&desde={ultimo}")

    assert cola.status_code == 200
    assert cola.text.strip() == ""


def test_el_hilo_apaga_la_cola_al_leer_hacia_atras(sesion_admin):
    """Leyendo una tanda vieja, «lo posterior a lo que veo» sería media
    conversación de golpe."""
    _mensaje("50644444444", "viejo")
    ultimo = _mensaje("50644444444", "nuevo")

    al_final = sesion_admin.get("/admin/logs/whatsapp/50644444444?fragmento=1").text
    hacia_atras = sesion_admin.get(f"/admin/logs/whatsapp/50644444444?fragmento=1&antes={ultimo}").text

    assert "data-cola=" in al_final
    assert "data-cola=" not in hacia_atras


def test_cada_burbuja_dice_su_id_y_su_dia(sesion_admin):
    """El navegador los relee del DOM para saber por dónde seguir pidiendo; es
    la única fuente que no se puede desincronizar de lo que hay en pantalla."""
    _mensaje("50633333333", "hola")
    hilo = sesion_admin.get("/admin/logs/whatsapp/50633333333?fragmento=1").text

    assert 'data-id="' in hilo
    assert 'data-dia="' in hilo
