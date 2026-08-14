"""Flujo de publicidad: alguien llega por un anuncio y pide el curso de su ciudad.

El catálogo del que sale la secuencia es **«Mensajes» del panel**, el mismo que
se envía a mano y el que dispara una palabra clave. Antes había además una tabla
`invitaciones_ciudades` con cinco columnas de texto: era una copia vieja de lo
mismo, con las mismas ciudades y sin adjuntos de verdad (el `Imagen=` iba escrito
dentro del texto). Un mensaje se identifica por su CLAVE, y esa clave es lo que
se reconoce aquí.
"""

import json
import re
import time
import unicodedata
from src.core.config import settings
from src.core.prompts import EXTRACT_AD_INFO_PROMPT
from src.domain.entities import Channel
from src.application.runtime_context import clear_user_runtime_context, register_ad_context
from src.infrastructure.repositories import plantillas_repository
from src.infrastructure.repositories.postgres_user_repo import PostgresUserRepo
from src.infrastructure.tasks.celery_app import send_delayed_message_sequence, schedule_ad_programmed_messages
from src.infrastructure.logging.tool_call_logger import ToolCallLogger
from openai import OpenAI

client = OpenAI(
    api_key=settings.OPENAI_API_KEY or "test",
    timeout=settings.OPENAI_TIMEOUT_SECONDS,
    max_retries=settings.OPENAI_MAX_RETRIES,
)

_ENLACE_DE_GRUPO = re.compile(r"(https://chat\.whatsapp\.com/\S+)")


def _sin_tildes(texto: str) -> str:
    """Minúsculas y sin acentos: «Cañas» y «CANAS» tienen que ser la misma."""
    normalizado = unicodedata.normalize("NFD", str(texto or "").lower())
    return "".join(c for c in normalizado if unicodedata.category(c) != "Mn").strip()


class PublicidadService:
    @staticmethod
    def handle_publicidad_entry(user_id: str, text: str, user_name: str = "Desconocido", channel: Channel | str = Channel.TELEGRAM):
        return PublicidadService.handle_invitation_by_city(user_id, text, user_name, channel)

    @staticmethod
    def handle_invitation_by_city(user_id: str, city_text: str, user_name: str = "Desconocido", channel: Channel | str = Channel.TELEGRAM) -> bool:
        channel_value = channel.value if isinstance(channel, Channel) else channel
        clave = PublicidadService._log_record(
            user_id,
            channel_value,
            "publicidad.find_invitation_record",
            {"city_text": city_text},
            lambda: PublicidadService._buscar_clave(city_text),
            lambda encontrada: {"found": bool(encontrada), "city": encontrada or ""},
            lambda encontrada: f"Invitación por ciudad encontrada: {bool(encontrada)}",
        )
        if not clave:
            print(f"No hay ningún mensaje cuya clave coincida con '{city_text}'.")
            return False

        # La cadena viene del panel con el adjunto ya incrustado como marcador
        # (`Imagen=<ref>`), que es el formato que entiende el envío.
        messages_to_send = plantillas_repository.textos_de(clave)
        if not messages_to_send:
            print(f"El mensaje '{clave}' no tiene ningún texto que enviar.")
            return False

        repo = PostgresUserRepo()
        repo.block_user(user_id, reason="Flujo de publicidad", channel=channel_value)
        register_ad_context(channel_value, user_id)

        # 1. Enviar los mensajes de invitacion con delay de 4-6 seg
        PublicidadService._log_record(
            user_id,
            channel_value,
            "celery.send_delayed_message_sequence",
            {"message_count": len(messages_to_send)},
            lambda: send_delayed_message_sequence.apply_async((channel_value, user_id, messages_to_send)),
            lambda task: {"scheduled": True, "task_id": getattr(task, "id", "")},
            lambda task: "Secuencia de publicidad programada",
        )
        
        # El enlace del grupo se busca en TODA la cadena, no en un mensaje
        # concreto: las cadenas del panel tienen el largo que el negocio quiera y
        # atarlo al cuarto rompía el flujo con solo agregar un mensaje en medio.
        enlace_whatsapp = PublicidadService._enlace_de_grupo(messages_to_send)
        if not enlace_whatsapp:
            from src.infrastructure.repositories.report_repository import ReportRepository
            ReportRepository.create_report(
                nombre=user_name,
                numero=user_id,
                problema="no se programó mensajes de publicidad porque faltan campos",
                link_whatsapp=f"https://wa.me/{user_id}"
            )
                
            repo.block_user(user_id, reason=f"Falta el enlace del grupo en el mensaje «{clave}»", channel=channel_value)
            clear_user_runtime_context(channel_value, user_id, cancel_scheduled=True, clear_reports=False)
            return True
        
        # 2. Extraer informacion con LLM para programar siguientes
        primer_mensaje = messages_to_send[0]
        try:
            started = time.monotonic()
            # Extraer día/valor/hora de un texto ya conocido es la tarea más
            # mecánica del sistema: va al modelo auxiliar.
            modelo = settings.OPENAI_MODEL_AUXILIAR
            completion = client.chat.completions.create(
                model=modelo,
                response_format={ "type": "json_object" },
                messages=[
                    {"role": "system", "content": EXTRACT_AD_INFO_PROMPT},
                    {"role": "user", "content": json.dumps({"mensaje": primer_mensaje}, ensure_ascii=False)}
                ]
            )
            from src.application import seguimiento_service

            seguimiento_service.registrar_uso_llm(
                user_id, channel_value, getattr(completion, "usage", None),
                origen="publicidad", modelo=modelo,
            )
            extracted = json.loads(completion.choices[0].message.content)
            dia = extracted.get("dia")
            valor = extracted.get("valor")
            hora = extracted.get("hora")
            ToolCallLogger.success(
                client_id=user_id,
                canal=channel_value,
                tool_name="publicidad.extract_ad_info",
                input_data={"message": primer_mensaje},
                output_data={"dia": dia, "valor": valor, "hora": hora},
                text="Datos de publicidad extraídos",
                duration_ms=ToolCallLogger._duration_ms(started),
            )
            
            if not dia or not valor or not hora or dia == "null" or valor == "null" or hora == "null":
                # Faltan datos -> Bloquear y reportar
                repo.block_user(user_id, reason="Falta informacion para programar publicidad. Datos extraidos: " + str(extracted), channel=channel_value)
                clear_user_runtime_context(channel_value, user_id, cancel_scheduled=True, clear_reports=False)
                return True
                
            # Todo bien, programar los recordatorios con el enlace del grupo.
            PublicidadService._log_record(
                user_id,
                channel_value,
                "celery.schedule_ad_programmed_messages",
                {"dia": dia, "valor": valor, "hora": hora, "has_link": True},
                lambda: schedule_ad_programmed_messages.apply_async((channel_value, user_id, dia, valor, hora, enlace_whatsapp)),
                lambda task: {"scheduled": True, "task_id": getattr(task, "id", "")},
                lambda task: "Recordatorios de publicidad programados",
            )
            return True
            
        except Exception as e:
            ToolCallLogger.error(
                client_id=user_id,
                canal=channel_value,
                tool_name="publicidad.extract_ad_info",
                input_data={"message": primer_mensaje},
                error=e,
            )
            print(f"Error en extracción LLM: {e}")
            return True

    @staticmethod
    def _buscar_clave(texto: str) -> str:
        """De qué mensaje del panel habla el cliente. "" si de ninguno.

        Reconocer una clave anunciada no es interpretar lenguaje natural: es lo
        mismo que hace una palabra clave, y por eso aquí sí se compara texto
        (ver §5 de CLAUDE.md). Lo que llega no es el mensaje entero sino la
        ciudad ya extraída —del `add["…"]` del anuncio o de la decisión del
        agente—, así que se busca la clave dentro de ese texto y, si no aparece,
        se admite un parecido razonable para los errores de tipeo.
        """
        import difflib

        buscado = _sin_tildes(texto)
        if not buscado:
            return ""

        # Las claves de menos de cuatro letras no se buscan por subcadena: una
        # clave corta aparecería dentro de casi cualquier texto y se llevaría
        # por delante a la que de verdad se pidió.
        candidatas = [(clave, _sin_tildes(clave)) for clave in plantillas_repository.claves()]
        candidatas = [(clave, normalizada) for clave, normalizada in candidatas if len(normalizada) >= 4]

        for clave, normalizada in candidatas:
            if normalizada in buscado:
                return clave

        for clave, normalizada in candidatas:
            if difflib.get_close_matches(buscado, [normalizada], n=1, cutoff=0.7):
                return clave

        return ""

    @staticmethod
    def _enlace_de_grupo(mensajes: list[str]) -> str:
        """El enlace del grupo de WhatsApp que lleve la cadena. "" si no lleva ninguno."""
        for mensaje in mensajes:
            encontrado = _ENLACE_DE_GRUPO.search(str(mensaje))
            if encontrado:
                return encontrado.group(1)
        return ""

    @staticmethod
    def _log_record(
        user_id: str,
        channel: Channel | str,
        tool_name: str,
        input_data: dict,
        call,
        output_mapper,
        text_mapper,
    ):
        return ToolCallLogger.record(
            client_id=user_id,
            canal=channel,
            tool_name=tool_name,
            input_data=input_data,
            output_mapper=output_mapper,
            text_mapper=text_mapper,
            call=call,
        )
