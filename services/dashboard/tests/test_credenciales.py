"""Cuenta de administrador, recuperación por Telegram e impersonación.

Es lo que impide quedarse fuera del panel y, a la vez, lo que impide que
cualquiera entre. Se prueba sin mandar nada por Telegram: el envío se sustituye
por un espía que captura el código.
"""

import pytest

from src.core import security
from src.db import pool
from src.services import recuperacion, usuarios
from tests.conftest import token_csrf


@pytest.fixture
def admin_con_telegram(monkeypatch):
    monkeypatch.setattr(recuperacion.settings, "ADMIN_TELEGRAM_CHAT_ID", "1049838038")
    monkeypatch.setattr(recuperacion.settings, "TELEGRAM_BOT_TOKEN", "token-de-pruebas")

    enviados = []
    monkeypatch.setattr(
        recuperacion, "_enviar_por_telegram", lambda usuario, codigo: enviados.append(codigo) or True
    )
    usuarios.crear("admin", "clave-de-pruebas-1", "admin", debe_cambiar=False)
    return enviados


# --- Recuperación ------------------------------------------------------------

def test_el_codigo_permite_cambiar_la_contrasena(admin_con_telegram):
    recuperacion.solicitar("admin")
    codigo = admin_con_telegram[0]

    ok, _ = recuperacion.confirmar("admin", codigo, "una-clave-nueva-larga")

    assert ok is True
    assert usuarios.autenticar("admin", "una-clave-nueva-larga") is not None


def test_el_codigo_no_se_guarda_en_claro(admin_con_telegram):
    """Quien lea la base no debe poder usar el código."""
    recuperacion.solicitar("admin")
    codigo = admin_con_telegram[0]

    fila = pool.consultar_uno("SELECT codigo_hash FROM codigos_recuperacion ORDER BY id DESC LIMIT 1")
    assert codigo not in fila["codigo_hash"]


def test_un_codigo_solo_sirve_una_vez(admin_con_telegram):
    recuperacion.solicitar("admin")
    codigo = admin_con_telegram[0]

    assert recuperacion.confirmar("admin", codigo, "una-clave-nueva-larga")[0] is True
    assert recuperacion.confirmar("admin", codigo, "otra-clave-distinta")[0] is False


def test_pedir_uno_nuevo_invalida_el_anterior(admin_con_telegram):
    """Varios códigos válidos a la vez solo amplían la ventana de un atacante."""
    recuperacion.solicitar("admin")
    primero = admin_con_telegram[0]
    recuperacion.solicitar("admin")

    assert recuperacion.confirmar("admin", primero, "una-clave-nueva-larga")[0] is False


def test_los_intentos_fallidos_agotan_el_codigo(admin_con_telegram):
    recuperacion.solicitar("admin")
    codigo = admin_con_telegram[0]

    for _ in range(recuperacion.settings.RECUPERACION_MAX_INTENTOS):
        recuperacion.confirmar("admin", "000000", "una-clave-nueva-larga")

    assert recuperacion.confirmar("admin", codigo, "una-clave-nueva-larga")[0] is False


def test_no_se_recupera_la_cuenta_de_un_cliente(admin_con_telegram):
    """Un cliente que pierde su clave la pide a su administrador."""
    usuarios.crear("un_cliente", "clave-de-pruebas-2", "cliente", debe_cambiar=False)
    enviado, _ = recuperacion.solicitar("un_cliente")
    assert enviado is False
    assert admin_con_telegram == []


def test_la_respuesta_no_revela_si_la_cuenta_existe(admin_con_telegram):
    _, con_cuenta = recuperacion.solicitar("admin")
    _, sin_cuenta = recuperacion.solicitar("no-existe")
    assert con_cuenta == sin_cuenta


def test_una_contrasena_corta_no_se_acepta(admin_con_telegram):
    recuperacion.solicitar("admin")
    ok, mensaje = recuperacion.confirmar("admin", admin_con_telegram[0], "corta")
    assert ok is False
    assert "10 caracteres" in mensaje


# --- Cuenta de administrador desde el entorno --------------------------------

def test_el_entorno_crea_la_cuenta_si_no_existe(monkeypatch):
    monkeypatch.setattr(usuarios.settings, "ADMIN_USER", "dueño")
    monkeypatch.setattr(usuarios.settings, "ADMIN_PASSWORD", "clave-del-entorno-1")

    usuarios.sincronizar_admin()

    assert usuarios.autenticar("dueño", "clave-del-entorno-1") is not None


def test_cambiar_el_entorno_reaplica_la_contrasena(monkeypatch):
    monkeypatch.setattr(usuarios.settings, "ADMIN_USER", "dueño")
    monkeypatch.setattr(usuarios.settings, "ADMIN_PASSWORD", "clave-del-entorno-1")
    usuarios.sincronizar_admin()

    monkeypatch.setattr(usuarios.settings, "ADMIN_PASSWORD", "clave-del-entorno-2")
    usuarios.sincronizar_admin()

    assert usuarios.autenticar("dueño", "clave-del-entorno-2") is not None


def test_un_reinicio_no_revierte_una_contrasena_recuperada(monkeypatch, admin_con_telegram):
    """Lo contrario dejaría inútil la recuperación por Telegram."""
    monkeypatch.setattr(usuarios.settings, "ADMIN_USER", "admin")
    monkeypatch.setattr(usuarios.settings, "ADMIN_PASSWORD", "clave-del-entorno-1")
    usuarios.sincronizar_admin()

    recuperacion.solicitar("admin")
    recuperacion.confirmar("admin", admin_con_telegram[0], "recuperada-por-telegram")

    usuarios.sincronizar_admin()  # simula el reinicio

    assert usuarios.autenticar("admin", "recuperada-por-telegram") is not None
    assert usuarios.autenticar("admin", "clave-del-entorno-1") is None
    assert usuarios.password_fuera_del_entorno() is True


# --- Impersonación -----------------------------------------------------------

def test_el_admin_ve_el_panel_del_cliente_y_pierde_sus_permisos(sesion_admin):
    cliente = usuarios.crear("otro_cliente", "clave-de-pruebas-3", "cliente", debe_cambiar=False)

    assert sesion_admin.get("/admin/costos").status_code == 200
    sesion_admin.post(
        f"/admin/usuarios/{cliente['id']}/entrar", data={"csrf": token_csrf(sesion_admin)}
    )

    # Mientras suplanta ve exactamente lo que ve el cliente, incluidas las
    # restricciones: si no, no serviría para reproducir su problema.
    assert sesion_admin.get("/admin/costos").status_code == 403
    assert sesion_admin.get("/factura").status_code == 404
    assert sesion_admin.get("/conversaciones").status_code == 200

    sesion_admin.post("/salir-de-cuenta")
    assert sesion_admin.get("/admin/costos").status_code == 200


def test_no_se_puede_entrar_a_otra_cuenta_de_administrador(sesion_admin):
    otro = usuarios.crear("otro_admin", "clave-de-pruebas-4", "admin", debe_cambiar=False)
    sesion_admin.post(f"/admin/usuarios/{otro['id']}/entrar", data={"csrf": token_csrf(sesion_admin)})
    assert sesion_admin.get("/admin/costos").status_code == 200  # no suplantó


def test_el_acceso_queda_registrado(sesion_admin):
    cliente = usuarios.crear("cliente_auditado", "clave-de-pruebas-5", "cliente", debe_cambiar=False)
    sesion_admin.post(
        f"/admin/usuarios/{cliente['id']}/entrar", data={"csrf": token_csrf(sesion_admin)}
    )

    registro = usuarios.historial_suplantacion(1)[0]
    assert registro["objetivo"] == "cliente_auditado"
    assert registro["fin_en"] is None

    sesion_admin.post("/salir-de-cuenta")
    assert usuarios.historial_suplantacion(1)[0]["fin_en"] is not None


def test_suplantando_no_se_puede_cambiar_la_contrasena_del_cliente(sesion_admin):
    """Sería cambiarla en su nombre y sin que se entere."""
    cliente = usuarios.crear("cliente_clave", "clave-de-pruebas-6", "cliente", debe_cambiar=False)
    sesion_admin.post(
        f"/admin/usuarios/{cliente['id']}/entrar", data={"csrf": token_csrf(sesion_admin)}
    )

    sesion_admin.post(
        "/password",
        data={
            "actual": "clave-de-pruebas-6",
            "nueva": "clave-cambiada-por-admin",
            "repetir": "clave-cambiada-por-admin",
            "csrf": token_csrf(sesion_admin),
        },
    )
    assert usuarios.autenticar("cliente_clave", "clave-de-pruebas-6") is not None
