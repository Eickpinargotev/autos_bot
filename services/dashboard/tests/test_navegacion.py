"""Que el panel se pueda recorrer sin callejones ni cosas repetidas.

Los problemas que arregla esto eran de navegación, no de datos: la misma lista
en el lateral y en el menú de la cuenta, una miga de pan que no llevaba a ningún
sitio, y «Mi cuenta» como página entera cuando es una ventana.

Aquí también se fija el VOCABULARIO, que era la mitad de la confusión: la
plataforma es «Base de Control», cada cliente nuestro es un «proyecto» y quien
lo administra es su único usuario.
"""

from src.core import navegacion
from src.services import clientes_whatsapp, usuarios
from tests.conftest import token_csrf


# --- Menú ---------------------------------------------------------------------

def test_el_lateral_no_repite_lo_del_menu_de_la_cuenta():
    """Cuentas, ajustes y «mi cuenta» viven en el menú de la cuenta, no en los dos."""
    urls = {
        pagina["url"]
        for seccion in navegacion.SECCIONES_ADMIN
        for pagina in seccion["paginas"]
    }

    assert "/admin/usuarios" not in urls
    assert "/admin/configuracion" not in urls
    assert "/password" not in urls and "/cuenta" not in urls
    # …y lo del día a día sí sigue ahí.
    assert {"/admin/negocios", "/admin/bloqueos", "/admin/costos"} <= urls


def test_una_pagina_oculta_no_se_dibuja_en_el_lateral(sesion_admin):
    cuerpo = sesion_admin.get("/admin/costos").text
    assert 'href="/admin/logs"' not in cuerpo
    assert 'href="/admin/bloqueos"' in cuerpo


# --- Miga de pan --------------------------------------------------------------

def test_cada_tramo_de_la_miga_lleva_a_algun_sitio():
    tramos = navegacion.migas(es_admin=True, ruta="/admin/negocios")

    assert [t["etiqueta"] for t in tramos] == ["Operación", "Proyectos"]
    assert [t["url"] for t in tramos] == ["/admin/negocios", "/admin/negocios"]


def test_las_paginas_del_menu_de_la_cuenta_tambien_tienen_miga():
    """Sin esto, entrar a «Cuentas de acceso» dejaba la cabecera muda."""
    tramos = navegacion.migas(es_admin=True, ruta="/admin/usuarios")
    assert [t["etiqueta"] for t in tramos] == ["Sistema", "Cuentas de acceso"]


def test_el_perfil_del_proyecto_se_lee_desde_su_listado(sesion_admin):
    """«Proyectos › Escuela de Manejo», con «Proyectos» devolviendo al listado."""
    negocio = clientes_whatsapp.crear("Escuela de Manejo")

    cuerpo = sesion_admin.get(f"/admin/negocios/{negocio['id']}").text

    assert '<a href="/admin/negocios">Proyectos</a>' in cuerpo
    assert "Escuela de Manejo" in cuerpo


# --- La marca es la plataforma, no un proyecto --------------------------------

def test_la_marca_del_lateral_es_la_plataforma(sesion_admin):
    """Decía «Escuela de manejo» —el nombre de UN proyecto— en el panel de todos."""
    cuerpo = sesion_admin.get("/admin/costos").text

    assert "Base de Control" in cuerpo
    assert "Escuela de manejo" not in cuerpo


def test_el_lateral_de_un_proyecto_dice_en_cual_estas(sesion_cliente):
    cuenta = usuarios.buscar_por_usuario("cliente_test")
    negocio = clientes_whatsapp.crear("Escuela de Manejo")
    clientes_whatsapp.vincular_cuenta(negocio["id"], cuenta["id"])

    cuerpo = sesion_cliente.get("/factura").text

    assert "Base de Control" in cuerpo
    assert "Escuela de Manejo" in cuerpo
    # Y el rol crudo de la base no se pinta: «cliente» no significaba nada.
    assert '<span class="cuenta-rol">cliente' not in cuerpo


# --- Mi cuenta es una ventana, no una página ----------------------------------

def test_mi_cuenta_dejo_de_ser_una_pagina(sesion_admin):
    assert sesion_admin.get("/cuenta").status_code == 404


def test_la_ventana_de_mi_cuenta_viaja_en_todas_las_paginas(sesion_admin):
    """Se declara en el armazón: se abre desde donde estés, sin recargar nada."""
    cuerpo = sesion_admin.get("/admin/costos").text

    assert 'id="dlg-mi-cuenta"' in cuerpo
    assert 'data-abre="dlg-mi-cuenta"' in cuerpo
    assert "Cambiar contraseña" in cuerpo
    # Lo que se quitó: un panel de sesiones abiertas que no le servía a nadie.
    assert "Sesiones abiertas" not in cuerpo


def test_el_cambio_de_contrasena_sigue_funcionando_desde_mi_cuenta(sesion_admin):
    respuesta = sesion_admin.post(
        "/password",
        data={
            "actual": "clave-de-pruebas-1",
            "nueva": "otra-clave-larga-1",
            "repetir": "otra-clave-larga-1",
            "csrf": token_csrf(sesion_admin),
        },
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    assert usuarios.autenticar("admin_test", "otra-clave-larga-1")


# --- El usuario del proyecto se administra desde su perfil --------------------

def test_la_cuenta_del_cliente_se_crea_desde_su_perfil(sesion_admin):
    """Antes eran tres pantallas para una sola idea: «este proyecto necesita entrar»."""
    negocio = clientes_whatsapp.crear("Escuela de manejo")

    respuesta = sesion_admin.post(
        f"/admin/negocios/{negocio['id']}/cuenta/crear",
        data={"nombre": "escuela-de-manejo", "csrf": token_csrf(sesion_admin)},
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    # La contraseña provisional viaja en la redirección para mostrarse UNA vez.
    assert "nueva_password=" in respuesta.headers["location"]
    creada = usuarios.buscar_por_usuario("escuela-de-manejo")
    assert creada["rol"] == "cliente"
    assert creada["debe_cambiar_password"] is True
    # Y queda vinculada sola: sin eso no habría a quién suplantar.
    assert clientes_whatsapp.obtener(negocio["id"])["usuario_id"] == creada["id"]


def test_no_se_crea_una_cuenta_con_un_usuario_que_ya_existe(sesion_admin):
    negocio = clientes_whatsapp.crear("Escuela de manejo")
    usuarios.crear("repetido", "clave-de-pruebas-9", "cliente", debe_cambiar=False)

    respuesta = sesion_admin.post(
        f"/admin/negocios/{negocio['id']}/cuenta/crear",
        data={"nombre": "repetido", "csrf": token_csrf(sesion_admin)},
        follow_redirects=False,
    )

    assert "error=" in respuesta.headers["location"]
    assert clientes_whatsapp.obtener(negocio["id"])["usuario_id"] is None


def test_el_usuario_del_proyecto_se_puede_renombrar(sesion_admin):
    """El nombre de la cuenta era definitivo: un proyecto se quedaba con
    «Cliente Germán» de por vida, que ni es una persona ni dice quién entra."""
    negocio = clientes_whatsapp.crear("Escuela de Manejo")
    cuenta = usuarios.crear("Cliente Germán", "clave-de-pruebas-7", "cliente")
    clientes_whatsapp.vincular_cuenta(negocio["id"], cuenta["id"])

    respuesta = sesion_admin.post(
        f"/admin/negocios/{negocio['id']}/cuenta/renombrar",
        data={
            "usuario_id": cuenta["id"],
            "nombre": "Enrique",
            "csrf": token_csrf(sesion_admin),
        },
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    assert usuarios.obtener(cuenta["id"])["usuario"] == "Enrique"
    # Y sigue siendo la misma cuenta: la contraseña no se toca al renombrar.
    assert usuarios.autenticar("Enrique", "clave-de-pruebas-7")


def test_no_se_renombra_a_un_usuario_que_ya_existe(sesion_admin):
    """El nombre es la credencial de ingreso: dos iguales dejan el login ciego."""
    negocio = clientes_whatsapp.crear("Escuela de Manejo")
    cuenta = usuarios.crear("Cliente Germán", "clave-de-pruebas-7", "cliente")
    clientes_whatsapp.vincular_cuenta(negocio["id"], cuenta["id"])
    usuarios.crear("Enrique", "clave-de-pruebas-8", "cliente", debe_cambiar=False)

    respuesta = sesion_admin.post(
        f"/admin/negocios/{negocio['id']}/cuenta/renombrar",
        data={
            "usuario_id": cuenta["id"],
            "nombre": "Enrique",
            "csrf": token_csrf(sesion_admin),
        },
        follow_redirects=False,
    )

    assert "error=" in respuesta.headers["location"]
    assert usuarios.obtener(cuenta["id"])["usuario"] == "Cliente Germán"


def test_no_se_renombra_la_cuenta_de_otro_proyecto(sesion_admin):
    """Sin comprobarlo, el id del formulario renombraría cualquier cuenta."""
    mio = clientes_whatsapp.crear("Escuela de Manejo")
    ajeno = clientes_whatsapp.crear("Otro Proyecto")
    cuenta_ajena = usuarios.crear("dueño-ajeno", "clave-de-pruebas-7", "cliente")
    clientes_whatsapp.vincular_cuenta(ajeno["id"], cuenta_ajena["id"])

    respuesta = sesion_admin.post(
        f"/admin/negocios/{mio['id']}/cuenta/renombrar",
        data={
            "usuario_id": cuenta_ajena["id"],
            "nombre": "secuestrada",
            "csrf": token_csrf(sesion_admin),
        },
        follow_redirects=False,
    )

    assert "error=" in respuesta.headers["location"]
    assert usuarios.obtener(cuenta_ajena["id"])["usuario"] == "dueño-ajeno"
