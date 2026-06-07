import httpx
import json
import time
from src.core.config import settings
from src.core.prompts import EXTRACT_AD_INFO_PROMPT
from src.domain.entities import Channel
from src.application.runtime_context import clear_user_runtime_context, register_ad_context
from src.infrastructure.repositories.postgres_user_repo import PostgresUserRepo
from src.infrastructure.tasks.celery_app import send_delayed_message_sequence, schedule_ad_programmed_messages
from src.infrastructure.logging.tool_call_logger import ToolCallLogger
from openai import OpenAI

client = OpenAI(api_key=settings.OPENAI_API_KEY or "test")

class PublicidadService:
    @staticmethod
    def handle_publicidad_entry(user_id: str, text: str, user_name: str = "Desconocido", channel: Channel | str = Channel.TELEGRAM):
        return PublicidadService.handle_invitation_by_city(user_id, text, user_name, channel)

    @staticmethod
    def handle_invitation_by_city(user_id: str, city_text: str, user_name: str = "Desconocido", channel: Channel | str = Channel.TELEGRAM) -> bool:
        channel_value = channel.value if isinstance(channel, Channel) else channel
        matched_record = PublicidadService._log_record(
            user_id,
            channel_value,
            "publicidad.find_invitation_record",
            {"city_text": city_text},
            lambda: PublicidadService._find_invitation_record(city_text),
            lambda record: {"found": bool(record), "city": (record or {}).get("CIUDAD") or (record or {}).get("CIUDADES") or ""},
            lambda record: f"Invitación por ciudad encontrada: {bool(record)}",
        )
        if not matched_record:
            print(f"No se encontró la ciudad '{city_text}' en la base de datos de NocoDB.")
            return False

        # Get messages 1 to 5
        messages_to_send = []
        for key in ["PRIMER MENSAJE", "SEGUNDO MENSAJE", "TERCER MENSAJE", "CUARTO MENSAJE", "QUINTO MENSAJE"]:
            msg = matched_record.get(key)
            if msg and str(msg).strip():
                messages_to_send.append(str(msg))
                
        if not messages_to_send:
            print("La ciudad encontrada no tiene mensajes configurados.")
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
        
        # Validar CUARTO MENSAJE
        cuarto_mensaje = matched_record.get("CUARTO MENSAJE")
        if not cuarto_mensaje or "https://chat.whatsapp.com" not in str(cuarto_mensaje):
            from src.infrastructure.repositories.report_repository import ReportRepository
            ReportRepository.create_report(
                nombre=user_name,
                numero=user_id,
                problema="no se programó mensajes de publicidad porque faltan campos",
                link_whatsapp=f"https://wa.me/{user_id}"
            )
                
            repo.block_user(user_id, reason="Falta link de whatsapp en el cuarto mensaje", channel=channel_value)
            clear_user_runtime_context(channel_value, user_id, cancel_scheduled=True, clear_reports=False)
            return True
        
        # 2. Extraer informacion con LLM para programar siguientes
        primer_mensaje = messages_to_send[0]
        try:
            started = time.monotonic()
            completion = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                response_format={ "type": "json_object" },
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that extracts data into JSON."},
                    {"role": "user", "content": EXTRACT_AD_INFO_PROMPT.format(mensaje=primer_mensaje)}
                ]
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
                
            # Todo bien, programar los mensajes
            # En la tabla Mensajes Programados (NocoDB), estos se deben guardar.
            import re
            link_match = re.search(r'(https://chat\.whatsapp\.com/\S+)', str(cuarto_mensaje))
            enlace_whatsapp = link_match.group(1) if link_match else "[Enlace no encontrado]"
            
            PublicidadService._log_record(
                user_id,
                channel_value,
                "celery.schedule_ad_programmed_messages",
                {"dia": dia, "valor": valor, "hora": hora, "has_link": bool(link_match)},
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
    def _find_invitation_record(text: str) -> dict | None:
        records = PublicidadService._fetch_invitation_records()
        text_lower = text.lower()
        for record in records:
            record_data = record.get("fields", record)

            ciudad_field = record_data.get("CIUDAD") or record_data.get("CIUDADES") or ""
            # NocoDB podría tener varias ciudades separadas por coma en la misma fila.
            ciudades_list = [c.strip().lower() for c in str(ciudad_field).split(",") if c.strip()]

            for ciudad in ciudades_list:
                if ciudad and ciudad in text_lower:
                    return record_data
        return None

    @staticmethod
    def _fetch_invitation_records() -> list[dict]:
        headers = {"xc-token": settings.NOCODB_TOKEN}
        url = settings.NOCODB_INVITACIONES_URL
        try:
            # Reemplazar pageSize con limit para la nueva versión de NocoDB.
            if "pageSize=" in url:
                import re
                url = re.sub(r'pageSize=\d+', 'limit=1000', url)
            if "limit=" not in url:
                url += "&limit=1000" if "?" in url else "?limit=1000"

            response = httpx.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            records = data.get("list", data.get("records", []))
            return records if isinstance(records, list) else []
        except httpx.HTTPStatusError as e:
            print(f"Error HTTP NocoDB: {e.response.status_code}. Revisa tu token en .env")
            return []
        except Exception as e:
            print(f"Error conectando a NocoDB: {e}")
            return []

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
