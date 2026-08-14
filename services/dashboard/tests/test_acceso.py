"""Autenticación y separación de roles.

Es la barrera que impide que el cliente vea el costo real del proveedor, los
logs de conversación o el cierre de periodos.
"""

from tests.conftest import token_csrf

RUTAS_SOLO_ADMIN = (
    "/admin/costos",
    "/admin/negocios",
    "/admin/periodos",
    "/admin/tarifas",
    "/admin/logs",
    # El visor por negocio. Devuelve un fragmento para la ventana flotante, pero
    # sirve exactamente lo mismo que el listado completo: las conversaciones de
    # los clientes. Si se abriera al negocio, vería los chats de otros.
    "/admin/negocios/1/conversaciones",
    # Las cifras del perfil, que se refrescan solas. Llevan el costo real y el
    # margen: servírselas a un negocio sería enseñarle lo que ganamos con él.
    "/admin/negocios/1/resumen",
    "/admin/bloqueos",
    "/admin/incidencias",
    "/admin/usuarios",
    "/admin/configuracion",
    # Los fragmentos que el panel vuelve a pedir solo cuando algo cambia. Son
    # rutas como cualquier otra: devolver un trozo de HTML en vez de la página
    # entera no las hace menos sensibles, y sin esta línea una podría quedarse
    # sin `requiere_admin` sin que nada fallara.
    "/admin/bloqueos/lista",
    "/admin/incidencias/lista",
    "/admin/logs/lista",
)

# Páginas del panel del NEGOCIO. El administrador tampoco entra: no son su
# trabajo, y para verlas suplanta al negocio desde su perfil.
RUTAS_DEL_NEGOCIO = (
    "/factura",
    "/clientes",
    "/reportes",
    "/conocimiento",
    "/preguntas",
    "/mensajes",
    "/enviar",
    "/envios",
    # Los fragmentos que se refrescan solos, por el mismo motivo que arriba.
    "/reportes/lista",
    "/preguntas/lista",
    "/clientes/lista",
)

RUTAS_DEL_CLIENTE = RUTAS_DEL_NEGOCIO

# Páginas de cualquiera que tenga sesión, sea del rol que sea. «Mi cuenta» ya no
# está aquí: dejó de ser una página y es una ventana del armazón. Queda
# `/password`, que es la pantalla del cambio obligatorio del primer ingreso.
RUTAS_DE_CUALQUIER_SESION = ("/password",)


def test_el_cambio_de_contrasena_lo_ve_cualquier_sesion(sesion_admin, sesion_cliente):
    for ruta in RUTAS_DE_CUALQUIER_SESION:
        assert sesion_admin.get(ruta).status_code == 200, ruta
        assert sesion_cliente.get(ruta).status_code == 200, ruta


def test_sin_sesion_se_redirige_al_login(cliente_http):
    respuesta = cliente_http.get("/factura", follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/login")


def test_el_cliente_no_entra_a_ninguna_ruta_de_admin(sesion_cliente):
    for ruta in RUTAS_SOLO_ADMIN:
        assert sesion_cliente.get(ruta).status_code == 403, f"{ruta} quedó accesible al cliente"


def test_el_negocio_si_entra_a_lo_suyo(sesion_cliente):
    for ruta in RUTAS_DEL_NEGOCIO:
        assert sesion_cliente.get(ruta).status_code == 200, ruta


def test_el_admin_entra_a_lo_suyo(sesion_admin):
    from src.services import clientes_whatsapp

    # Hay rutas de la lista que son de UN proyecto (`/admin/negocios/1/...`), y
    # sin ninguno creado responderían 404 antes de llegar a comprobar nada.
    clientes_whatsapp.crear("Escuela de prueba")

    for ruta in RUTAS_SOLO_ADMIN:
        assert sesion_admin.get(ruta).status_code == 200, ruta


def test_el_admin_no_entra_al_panel_del_negocio(sesion_admin):
    """No es una restricción de seguridad, es de responsabilidad: el
    conocimiento, las preguntas y los mensajes los administra el negocio. El
    admin llega a ellos suplantando, lo que además deja registro."""
    for ruta in RUTAS_DEL_NEGOCIO:
        if ruta == "/factura":
            continue  # su propio consumo sí lo ve
        assert sesion_admin.get(ruta).status_code == 403, f"{ruta} quedó accesible al admin"


def test_la_factura_del_cliente_no_revela_el_costo_real(sesion_cliente):
    """El precio de venta sí; lo que se le paga al proveedor, nunca."""
    from src.db import pool

    pool.ejecutar(
        """
        INSERT INTO uso_eventos (periodo_id, client_id, canal, categoria, origen,
                                 mensajes, costo_real_microusd, costo_cliente_microusd)
        SELECT id, '506', 'whatsapp', 'llm', 'agente', 1, 1965, 3144
        FROM periodos_facturacion WHERE cerrado_en IS NULL
        """
    )
    cuerpo = sesion_cliente.get("/factura").text

    assert "0.0031" in cuerpo          # lo facturado
    assert "0.0020" not in cuerpo      # el costo real
    assert "argen" not in cuerpo       # ni el margen


def test_credenciales_malas_no_crean_sesion(cliente_http):
    from src.services import usuarios

    usuarios.crear("alguien", "clave-de-pruebas-1", "cliente", debe_cambiar=False)
    respuesta = cliente_http.post(
        "/login", data={"usuario": "alguien", "password": "incorrecta"}, follow_redirects=False
    )
    assert respuesta.status_code == 200
    assert "incorrectos" in respuesta.text
    assert cliente_http.get("/factura", follow_redirects=False).status_code == 303


def test_el_mensaje_de_error_no_distingue_usuario_inexistente(cliente_http):
    """Si el texto cambiara, se podría averiguar qué cuentas existen."""
    from src.services import usuarios

    usuarios.crear("existe", "clave-de-pruebas-1", "cliente", debe_cambiar=False)
    con_usuario = cliente_http.post("/login", data={"usuario": "existe", "password": "mala"}).text
    sin_usuario = cliente_http.post("/login", data={"usuario": "no-existe", "password": "mala"}).text

    assert "Usuario o contraseña incorrectos" in con_usuario
    assert "Usuario o contraseña incorrectos" in sin_usuario


def test_una_contrasena_provisional_bloquea_el_resto_del_panel(cliente_http):
    from src.services import usuarios

    usuarios.crear("nuevo", "clave-de-pruebas-1", "admin", debe_cambiar=True)
    cliente_http.post("/login", data={"usuario": "nuevo", "password": "clave-de-pruebas-1"})

    respuesta = cliente_http.get("/admin/costos", follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/password"
    # …pero la propia página de cambio sí debe abrirse, o quedaría atrapado.
    assert cliente_http.get("/password").status_code == 200


def test_sin_token_csrf_no_se_puede_escribir(sesion_admin):
    """Sin esto, un sitio externo podría cerrar un periodo desde el navegador."""
    respuesta = sesion_admin.post("/admin/periodos/cerrar", data={"nota": "sin csrf"})
    assert respuesta.status_code == 403


def test_con_token_csrf_si_se_puede(sesion_admin):
    respuesta = sesion_admin.post(
        "/admin/periodos/cerrar",
        data={"nota": "con csrf", "csrf": token_csrf(sesion_admin)},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303


def test_al_salir_la_sesion_deja_de_servir(sesion_admin):
    sesion_admin.post("/logout", follow_redirects=False)
    assert sesion_admin.get("/admin/costos", follow_redirects=False).status_code == 303


def test_el_login_no_redirige_a_sitios_externos(cliente_http):
    """`siguiente` con una URL externa convertiría el login en un trampolín."""
    from src.services import usuarios

    usuarios.crear("victima", "clave-de-pruebas-1", "cliente", debe_cambiar=False)
    respuesta = cliente_http.post(
        "/login",
        data={"usuario": "victima", "password": "clave-de-pruebas-1", "siguiente": "https://sitio-malo.example"},
        follow_redirects=False,
    )
    assert respuesta.headers["location"] == "/"
