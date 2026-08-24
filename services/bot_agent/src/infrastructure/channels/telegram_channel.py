import asyncio

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from src.infrastructure.channels.base_channel import BaseChannel
from src.infrastructure.channels.senders import TelegramSender
from src.domain.entities import Channel, InboundMessage, MessageType
from src.application.conversation_orchestrator import ConversationOrchestrator
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository

class TelegramChannel(BaseChannel):
    def __init__(self, token: str):
        self.token = token
        self.orchestrator = ConversationOrchestrator()
        # concurrent_updates: sin esto PTB procesa las actualizaciones en serie y
        # un solo cliente lento (NocoDB/Postgres/LLM) atasca la cola de todos.
        self.app = Application.builder().token(token).concurrent_updates(True).build()
        self._register_handlers()

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self._start_cmd))
        self.app.add_handler(CommandHandler("d", self._cmd_d))
        self.app.add_handler(CommandHandler("block", self._cmd_block))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))
        # Cada tipo por separado: el acuse automático nombra lo que llegó
        # ("recibí tu imagen" / "tu documento" / "tu video"), así que mandarlos
        # todos como IMAGE le respondería "imagen" a un PDF.
        self.app.add_handler(MessageHandler(filters.PHOTO, self._handle_image))
        self.app.add_handler(MessageHandler(filters.Document.ALL, self._handle_document))
        self.app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, self._handle_video))
        self.app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._handle_audio))
        # Los stickers NO se registran a propósito: sin handler, Telegram los
        # deja pasar sin respuesta ni rastro, que es la misma regla que aplica
        # el orquestador para WhatsApp.

    async def _start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = "Bot iniciado."
        user_id = str(update.message.chat_id)
        user_name = update.message.from_user.first_name if update.message.from_user else "Desconocido"
        await asyncio.to_thread(
            ConversationLogRepository.log_inbound,
            client_id=user_id,
            canal=Channel.TELEGRAM,
            sender_name=user_name,
            message_type=MessageType.TEXT,
            text="/start",
            event_type="command",
        )
        await update.message.reply_text(text)
        await asyncio.to_thread(
            ConversationLogRepository.log_outbound, client_id=user_id, canal=Channel.TELEGRAM, text=text
        )

    async def _cmd_d(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._dispatch(update, MessageType.TEXT, text="/d")

    async def _cmd_block(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._dispatch(update, MessageType.TEXT, text="/block")

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._dispatch(update, MessageType.TEXT, text=update.message.text or "")

    async def _handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._dispatch(update, MessageType.IMAGE)

    async def _handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._dispatch(update, MessageType.DOCUMENT)

    async def _handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._dispatch(update, MessageType.VIDEO)

    async def _handle_audio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._dispatch(update, MessageType.AUDIO)
        text = "Audio recibido, procesando..."
        await update.message.reply_text(text)
        await asyncio.to_thread(
            ConversationLogRepository.log_outbound,
            client_id=str(update.message.chat_id),
            canal=Channel.TELEGRAM,
            text=text,
        )

    async def _dispatch(self, update: Update, msg_type: MessageType, text: str = ""):
        user_id = str(update.message.chat_id)
        user_name = update.message.from_user.first_name if update.message.from_user else "Desconocido"
        inbound = InboundMessage(
            channel=Channel.TELEGRAM,
            user_id=user_id,
            user_name=user_name,
            message_type=msg_type,
            text=text,
        )
        # El orquestador hace I/O bloqueante (NocoDB, Postgres, Redis); se corre
        # en un hilo para no congelar el event loop del bot con otros clientes.
        actions = await asyncio.to_thread(self.orchestrator.handle, inbound)
        enviados = 0
        for action in actions:
            if action.action == "send_now" and action.text:
                if enviados:
                    from src.infrastructure.repositories import instrucciones_repository
                    await asyncio.sleep(instrucciones_repository.intervalo_entre_mensajes())
                await update.message.reply_text(action.text)
                if not action.skip_conversation_log:
                    await asyncio.to_thread(
                        ConversationLogRepository.log_outbound,
                        client_id=action.user_id,
                        canal=action.channel,
                        text=action.text,
                    )
                enviados += 1

    def send_message_sync(self, user_id: str, text: str):
        TelegramSender().send_message_sync(user_id, text)

    async def send_message(self, user_id: str, text: str):
        await self.app.bot.send_message(chat_id=user_id, text=text)

    def start(self):
        self.app.run_polling()
