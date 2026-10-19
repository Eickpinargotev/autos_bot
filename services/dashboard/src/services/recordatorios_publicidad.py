"""Textos de publicidad por negocio; no modifica publicidades ni tareas en cola."""

import re

from src.db import pool
from src.services import instrucciones, tiempos_mensajes

TEXTOS_PUBLICIDAD_ORIGINALES = (
    '📌 Hola!!!  No recibí respuesta a nuestra conversación.  Podemos continuar???',
    (
        '👋🏻Vi que no se unió al grupo👋🏻\n'
        '\n'
        '*¿¿¿Tiene alguna duda duda antes de unirse al grupo???'
    ),
    (
        '📌Hola!!!\n'
        '\n'
        'Le comparto la información de nuestro curso.\n'
        '\n'
        'Fecha: {dia}\n'
        '\n'
        'Hora: {hora}\n'
        'Valor: {valor} colones\n'
        '\n'
        'Unirse al grupo: {enlace}\n'
        '\n'
        'Le esperamos'
    ),
)


class CampoInvalido(ValueError):
    def __init__(self, campo, mensaje):
        super().__init__(mensaje)
        self.campo = campo


def textos_de(config):
    return [config.get(f"publicidad_recordatorio_{i}_texto") or original
            for i, original in enumerate(TEXTOS_PUBLICIDAD_ORIGINALES, 1)]


def validar_textos(textos):
    for i, texto in enumerate(textos, 1):
        # None significa que un cliente anterior no envió el campo.
        if texto is None:
            continue
        if not texto.strip():
            raise CampoInvalido(f"publicidad_{i}_texto", f"Escribe el texto del recordatorio {i}.")
        if len(texto) > 2000:
            raise CampoInvalido(f"publicidad_{i}_texto", f"El recordatorio {i} admite hasta 2.000 caracteres.")
        resto = re.sub(r"\{(dia|hora|valor|enlace)\}", "", texto)
        if "{" in resto or "}" in resto:
            raise CampoInvalido(f"publicidad_{i}_texto", "Usa solamente {dia}, {hora}, {valor} y {enlace}, con sus llaves completas.")


def guardar_configuracion(proyecto_id, intervalo, recordatorios, habilitado,
                          cantidad, unidad, usuario, textos):
    """Un solo commit para tiempos, seguimiento y textos; todo se valida antes."""
    # Asociar cada error con su control sin cambiar las reglas existentes.
    try:
        numero = int(intervalo)
    except (TypeError, ValueError):
        numero = 0
    if not 1 <= numero <= tiempos_mensajes.MAX_INTERVALO_MENSAJES_SEGUNDOS:
        raise CampoInvalido("intervalo_mensajes_segundos", "La pausa debe ser un entero entre 1 y 60 segundos.")
    segundos = []
    for i, (valor, medida) in enumerate(recordatorios, 1):
        try:
            segundos.append(tiempos_mensajes._a_segundos(valor, medida, f"El recordatorio {i}"))
        except ValueError as exc:
            campo = f"publicidad_{i}_" + ("unidad" if medida not in tiempos_mensajes.UNIDADES else "cantidad")
            raise CampoInvalido(campo, str(exc)) from exc
    for i in range(1, len(segundos)):
        if segundos[i] <= segundos[i - 1]:
            raise CampoInvalido(f"publicidad_{i + 1}_cantidad", "Cada recordatorio debe salir después del anterior.")
    tiempos_mensajes.validar(intervalo, recordatorios)
    try:
        instrucciones.validar_configuracion_recordatorios(cantidad, unidad)
    except ValueError as exc:
        campo = "recordatorio_unidad" if unidad not in {"minutos", "horas"} else "recordatorio_cantidad"
        raise CampoInvalido(campo, str(exc)) from exc
    validar_textos(textos)
    with pool.conexion(autocommit=False) as conn:
        with conn:
            with conn.cursor() as cur:
                def consultar(sql, params):
                    cur.execute(sql, params)
                    return cur.fetchone()

                tiempos_mensajes.guardar(proyecto_id, intervalo, recordatorios, usuario,
                                         consultar=consultar)
                instrucciones.guardar_configuracion_recordatorios(
                    proyecto_id, habilitado, cantidad, unidad, usuario, consultar=consultar)
                cur.execute("""
                    UPDATE proyecto_tiempos_mensajes SET
                        publicidad_recordatorio_1_texto = COALESCE(%s, publicidad_recordatorio_1_texto),
                        publicidad_recordatorio_2_texto = COALESCE(%s, publicidad_recordatorio_2_texto),
                        publicidad_recordatorio_3_texto = COALESCE(%s, publicidad_recordatorio_3_texto)
                    WHERE proyecto_id = %s
                """, (*textos, proyecto_id))
