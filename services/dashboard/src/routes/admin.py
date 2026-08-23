"""Zona del administrador: proyectos, facturación, incidencias y usuarios.

Todas las rutas de este módulo dependen de `requiere_admin`. Un `cliente` recibe
403 aunque escriba la URL a mano.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from src.core import security
from src.core.config import settings
from src.core.plantillas import render
from src.services import (
    clientes_whatsapp,
    facturacion,
    mensajeria,
    usuarios,
)

router = APIRouter(prefix="/admin")


# --- Configuración -----------------------------------------------------------

@router.get("/configuracion")
def configuracion(request: Request, usuario=Depends(security.requiere_admin)):
    """Estado del sistema en una sola página, con el enlace a dónde se cambia.

    No edita nada: cada ajuste se cambia donde vive (tarifas, periodos, usuarios)
    o en el `.env`. Aquí se ve, junto, qué está puesto y qué falta — que es lo que
    normalmente se busca cuando algo no funciona.

    De los secretos solo se dice si están definidos; su valor no sale nunca.
    """
    cuentas = usuarios.listar()
    return render(
        request,
        "configuracion.html",
        usuario,
        tarifa=facturacion.tarifa_vigente(),
        periodo=facturacion.periodo_abierto(),
        cuentas_total=len(cuentas),
        cuentas_admin=sum(1 for c in cuentas if c["rol"] == security.ROL_ADMIN),
        cuentas_inactivas=sum(1 for c in cuentas if not c["activo"]),
        password_fuera_del_entorno=usuarios.password_fuera_del_entorno(),
        ajustes=settings,
    )


# --- Clientes (los negocios a los que les damos el servicio) -------------------
#
# El perfil administrativo reúne datos agregados, facturación y configuración
# técnica. Para operar el interior del proyecto, soporte usa la suplantación
# auditada de su cuenta.

@router.get("/negocios")
def negocios(request: Request, usuario=Depends(security.requiere_admin)):
    return render(request, "negocios.html", usuario, negocios=clientes_whatsapp.listar())


@router.get("/negocios/{negocio_id}")
def negocio_detalle(request: Request, negocio_id: int, usuario=Depends(security.requiere_admin)):
    negocio = clientes_whatsapp.obtener(negocio_id)
    if not negocio:
        return RedirectResponse(f"/admin/negocios?error={quote('Ese cliente ya no existe')}", status_code=303)
    return render(
        request,
        "negocio.html",
        usuario,
        negocio=negocio,
        # Las fechas de esta página son de este proyecto: se muestran en SU hora.
        proyecto=negocio,
        resumen=clientes_whatsapp.resumen_actividad(negocio_id),
        cuentas=usuarios.listar_negocios_sin_vincular(negocio.get("usuario_id")),
        eventos=clientes_whatsapp.EVENTOS_REQUERIDOS,
        eventos_no=clientes_whatsapp.EVENTOS_DESACONSEJADOS,
        zonas=clientes_whatsapp.ZONAS_HORARIAS,
        ajustes=settings,
        estado_wasender=clientes_whatsapp.estado_wasender(negocio.get("wasender_api_key", "")),
        # Se muestra una sola vez, justo después de crear la cuenta del cliente.
        nueva_cuenta=request.query_params.get("nueva_cuenta", ""),
        nueva_password=request.query_params.get("nueva_password", ""),
        # Último tramo de la miga: «Clientes › Escuela de manejo», con «Clientes»
        # llevando de vuelta al listado.
        miga_final=negocio["nombre"],
    )


@router.get("/negocios/{negocio_id}/resumen")
def negocio_resumen(request: Request, negocio_id: int, usuario=Depends(security.requiere_admin)):
    """Las cuatro cifras del perfil, solas.

    Es la pantalla a la que se entra cuando el cliente llama a reclamar: sus
    números tienen que ser los de ahora, no los de cuando se abrió la pestaña.
    """
    negocio = clientes_whatsapp.obtener(negocio_id)
    if not negocio:
        raise HTTPException(status_code=404, detail="Ese cliente ya no existe")
    return render(
        request,
        "_negocio_resumen.html",
        usuario,
        negocio=negocio,
        proyecto=negocio,  # las horas, en la zona del proyecto
        resumen=clientes_whatsapp.resumen_actividad(negocio_id),
    )


@router.post("/negocios")
def negocio_crear(
    request: Request,
    nombre: str = Form(...),
    numero: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_admin),
):
    security.verificar_csrf(request, csrf)
    try:
        creado = clientes_whatsapp.crear(nombre, numero)
    except ValueError as e:
        return RedirectResponse(f"/admin/negocios?error={quote(str(e))}", status_code=303)
    return RedirectResponse(
        f"/admin/negocios/{creado['id']}?aviso={quote('Cliente creado con su webhook')}", status_code=303
    )


@router.post("/negocios/{negocio_id}/config")
def negocio_config(
    request: Request,
    negocio_id: int,
    csrf: str = Form(""),
    nombre: str = Form(""),
    numero: str = Form(""),
    zona_horaria: str = Form(""),
    notas: str = Form(""),
    usuario=Depends(security.requiere_admin),
):
    security.verificar_csrf(request, csrf)
    try:
        clientes_whatsapp.actualizar_config(
            negocio_id, nombre=nombre, numero=numero, zona_horaria=zona_horaria, notas=notas
        )
    except ValueError as e:
        return RedirectResponse(f"/admin/negocios/{negocio_id}?error={quote(str(e))}", status_code=303)
    return RedirectResponse(
        f"/admin/negocios/{negocio_id}?aviso={quote('Configuración guardada')}", status_code=303
    )


@router.post("/negocios/{negocio_id}/wasender")
def negocio_wasender(
    request: Request,
    negocio_id: int,
    csrf: str = Form(""),
    wasender_api_key: str = Form(""),
    wasender_webhook_secret: str = Form(""),
    usuario=Depends(security.requiere_admin),
):
    """Las dos credenciales que WasenderAPI entrega por sesión.

    Solo se escriben: no vuelven a mostrarse en el formulario. Dejar un campo
    vacío significa «no lo cambies», no «bórralo».
    """
    security.verificar_csrf(request, csrf)
    clientes_whatsapp.actualizar_credenciales(
        negocio_id, api_key=wasender_api_key, webhook_secret=wasender_webhook_secret
    )
    return RedirectResponse(
        f"/admin/negocios/{negocio_id}?aviso={quote('Credenciales guardadas')}", status_code=303
    )


@router.post("/negocios/{negocio_id}/cuenta")
def negocio_cuenta(
    request: Request,
    negocio_id: int,
    csrf: str = Form(""),
    usuario_id: str = Form(""),
    usuario=Depends(security.requiere_admin),
):
    """Vincula la cuenta de acceso con la que el negocio entra a su panel."""
    security.verificar_csrf(request, csrf)
    clientes_whatsapp.vincular_cuenta(negocio_id, int(usuario_id) if usuario_id.isdigit() else None)
    return RedirectResponse(
        f"/admin/negocios/{negocio_id}?aviso={quote('Cuenta de acceso actualizada')}", status_code=303
    )


@router.post("/negocios/{negocio_id}/cuenta/crear")
def negocio_cuenta_crear(
    request: Request,
    negocio_id: int,
    csrf: str = Form(""),
    nombre: str = Form(...),
    usuario=Depends(security.requiere_admin),
):
    """Crea la cuenta con la que ESTE cliente entra a su panel, y se la vincula.

    Antes había que ir a «Cuentas de acceso», crear un usuario suelto, volver al
    perfil y vincularlo desde un desplegable. Eran tres pantallas para una sola
    idea —«este cliente necesita entrar»—, y el desplegable no explicaba de qué
    cuenta hablaba. La contraseña se muestra una vez, como en el alta normal.
    """
    security.verificar_csrf(request, csrf)
    nombre = nombre.strip()
    if usuarios.buscar_por_usuario(nombre):
        return RedirectResponse(
            f"/admin/negocios/{negocio_id}?error={quote('Ya existe una cuenta con ese usuario')}",
            status_code=303,
        )

    password = usuarios.generar_password()
    try:
        cuenta = usuarios.crear(nombre, password, security.ROL_NEGOCIO)
    except ValueError as e:
        return RedirectResponse(f"/admin/negocios/{negocio_id}?error={quote(str(e))}", status_code=303)

    clientes_whatsapp.vincular_cuenta(negocio_id, cuenta["id"])
    return RedirectResponse(
        f"/admin/negocios/{negocio_id}?nueva_cuenta={quote(nombre)}&nueva_password={quote(password)}"
        f"&aviso={quote('Cuenta creada. Cópiale la contraseña ahora: no se vuelve a mostrar.')}",
        status_code=303,
    )


@router.post("/negocios/{negocio_id}/cuenta/renombrar")
def negocio_cuenta_renombrar(
    request: Request,
    negocio_id: int,
    csrf: str = Form(""),
    usuario_id: int = Form(...),
    nombre: str = Form(...),
    usuario=Depends(security.requiere_admin),
):
    """Cambia el nombre de la cuenta con la que este proyecto ingresa.

    Va aquí, en el perfil del proyecto, y no en «Cuentas de acceso»: el nombre de
    usuario es de este proyecto, y es donde se está mirando cuando uno se da
    cuenta de que dice «Cliente Germán» en vez del nombre de la persona.

    Se comprueba que la cuenta sea la de ESTE proyecto: sin eso, el id del
    formulario permitiría renombrar la cuenta de cualquier otro desde aquí.
    """
    security.verificar_csrf(request, csrf)
    negocio = clientes_whatsapp.obtener(negocio_id)
    if not negocio or negocio.get("usuario_id") != usuario_id:
        return RedirectResponse(
            f"/admin/negocios/{negocio_id}?error={quote('Esa cuenta no es de este proyecto')}",
            status_code=303,
        )

    try:
        cuenta = usuarios.renombrar(usuario_id, nombre)
    except ValueError as e:
        return RedirectResponse(f"/admin/negocios/{negocio_id}?error={quote(str(e))}", status_code=303)

    aviso = "La cuenta ahora ingresa como «%s»" % cuenta["usuario"]
    return RedirectResponse(
        f"/admin/negocios/{negocio_id}?aviso={quote(aviso)}", status_code=303
    )


@router.post("/negocios/{negocio_id}/regenerar")
def negocio_regenerar(
    request: Request, negocio_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_admin)
):
    """Regenera el webhook.

    Normalmente el token NO cambia: se le asigna al negocio al crearlo y vive lo
    que viva el negocio. Regenerarlo es la salida cuando se filtró, y tiene
    precio: la URL que esté puesta en WasenderAPI deja de funcionar hasta que se
    pegue la nueva. Por eso la confirmación es explícita.
    """
    security.verificar_csrf(request, csrf)
    clientes_whatsapp.rotar_token(negocio_id)
    return RedirectResponse(
        f"/admin/negocios/{negocio_id}?aviso={quote('Webhook regenerado: pega la URL nueva en WasenderAPI')}",
        status_code=303,
    )


@router.post("/negocios/{negocio_id}/activo")
def negocio_activo(
    request: Request, negocio_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_admin)
):
    security.verificar_csrf(request, csrf)
    fila = clientes_whatsapp.alternar_activo(negocio_id)
    estado = "activado" if (fila or {}).get("activo") else "desactivado"
    return RedirectResponse(
        f"/admin/negocios/{negocio_id}?aviso={quote(f'Cliente {estado}')}", status_code=303
    )


@router.post("/negocios/{negocio_id}/eliminar")
def negocio_eliminar(
    request: Request, negocio_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_admin)
):
    """Elimina el negocio y, con él, su webhook."""
    security.verificar_csrf(request, csrf)
    clientes_whatsapp.eliminar(negocio_id)
    return RedirectResponse(f"/admin/negocios?aviso={quote('Cliente eliminado')}", status_code=303)


# --- Incidencias de envíos ---------------------------------------------------

def _datos_incidencias(request: Request) -> dict:
    abiertas = request.query_params.get("abiertas") == "1"
    return {
        "incidencias": mensajeria.listar_incidencias(solo_abiertas=abiertas),
        "solo_abiertas": abiertas,
    }


@router.get("/incidencias")
def incidencias(request: Request, usuario=Depends(security.requiere_admin)):
    return render(request, "incidencias.html", usuario, **_datos_incidencias(request))


@router.get("/incidencias/lista")
def incidencias_lista(request: Request, usuario=Depends(security.requiere_admin)):
    """Las tarjetas solas, para que una incidencia recién escalada salga sola."""
    return render(request, "_incidencias_lista.html", usuario, **_datos_incidencias(request))


@router.post("/incidencias/{incidencia_id}/revisada")
def marcar_incidencia(
    request: Request,
    incidencia_id: int,
    nota: str = Form(""),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_admin),
):
    security.verificar_csrf(request, csrf)
    mensajeria.marcar_incidencia_revisada(incidencia_id, nota)
    return RedirectResponse(url="/admin/incidencias?aviso=Incidencia marcada como revisada.", status_code=303)


# --- Usuarios ----------------------------------------------------------------

@router.get("/usuarios")
def listar_usuarios(request: Request, usuario=Depends(security.requiere_admin)):
    return render(
        request,
        "usuarios.html",
        usuario,
        cuentas=usuarios.listar(),
        suplantaciones=usuarios.historial_suplantacion(20),
        password_fuera_del_entorno=usuarios.password_fuera_del_entorno(),
        # Se muestran una sola vez, justo después de generarlas.
        nueva_cuenta=request.query_params.get("nueva_cuenta", ""),
        nueva_password=request.query_params.get("nueva_password", ""),
    )


@router.post("/usuarios")
def crear_usuario(
    request: Request,
    nombre: str = Form(...),
    rol: str = Form(...),
    csrf: str = Form(""),
    usuario=Depends(security.requiere_admin),
):
    """Crea la cuenta con una contraseña provisional generada.

    La contraseña se muestra UNA vez, para que se la pases al cliente; después
    solo queda su hash. Si se pierde, la restableces desde esta misma pantalla.
    """
    security.verificar_csrf(request, csrf)
    if usuarios.buscar_por_usuario(nombre.strip()):
        return RedirectResponse(url="/admin/usuarios?error=Ese usuario ya existe.", status_code=303)

    password = usuarios.generar_password()
    try:
        usuarios.crear(nombre, password, rol)
    except ValueError as e:
        return RedirectResponse(url=f"/admin/usuarios?error={e}", status_code=303)

    return RedirectResponse(
        url=(
            f"/admin/usuarios?nueva_cuenta={nombre.strip()}&nueva_password={password}"
            "&aviso=Usuario creado. Cópiale la contraseña ahora: no se vuelve a mostrar."
        ),
        status_code=303,
    )


# --- Entrar a la cuenta de un cliente ---------------------------------------

@router.post("/usuarios/{usuario_id}/entrar")
def entrar_como(
    request: Request, usuario_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_admin)
):
    """Abre el panel del cliente sin usar su contraseña.

    Es la alternativa a guardar una segunda contraseña por cuenta: guardarla
    obligaría a almacenarla de forma recuperable, y quien leyera la base tendría
    las claves de todos. Aquí sigues siendo tú, y el acceso queda registrado.
    """
    security.verificar_csrf(request, csrf)
    objetivo = usuarios.obtener(usuario_id)
    if not usuarios.puede_suplantar(objetivo):
        return RedirectResponse(
            url="/admin/usuarios?error=Solo puedes entrar a cuentas de cliente activas.",
            status_code=303,
        )

    usuarios.iniciar_suplantacion(
        usuario["token"], usuario["id"], usuario_id, ip=request.client.host if request.client else ""
    )
    return RedirectResponse(url="/factura", status_code=303)


@router.post("/usuarios/{usuario_id}/activo")
def alternar_usuario(
    request: Request, usuario_id: int, csrf: str = Form(""), usuario=Depends(security.requiere_admin)
):
    security.verificar_csrf(request, csrf)
    # Desactivarse a sí mismo dejaría el dashboard sin administrador accesible.
    if usuario_id == usuario["id"]:
        return RedirectResponse(
            url="/admin/usuarios?error=No puedes desactivar tu propia cuenta.", status_code=303
        )
    usuarios.alternar_activo(usuario_id)
    return RedirectResponse(url="/admin/usuarios", status_code=303)


@router.post("/usuarios/{usuario_id}/password")
def resetear_password(
    request: Request,
    usuario_id: int,
    csrf: str = Form(""),
    usuario=Depends(security.requiere_admin),
):
    """Genera una contraseña provisional nueva y cierra las sesiones de esa cuenta."""
    security.verificar_csrf(request, csrf)
    objetivo = usuarios.obtener(usuario_id)
    if not objetivo:
        return RedirectResponse(url="/admin/usuarios?error=Esa cuenta no existe.", status_code=303)

    password = usuarios.generar_password()
    usuarios.cambiar_password(usuario_id, password)
    if objetivo["rol"] == security.ROL_ADMIN:
        # El .env deja de reflejar la contraseña real de esa cuenta.
        usuarios.marcar_password_fuera_del_entorno()
    return RedirectResponse(
        url=(
            f"/admin/usuarios?nueva_cuenta={objetivo['usuario']}&nueva_password={password}"
            "&aviso=Contraseña nueva generada y sesiones cerradas. Cópiala ahora."
        ),
        status_code=303,
    )
