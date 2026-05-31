from abc import ABC, abstractmethod
from src.domain.entities import Channel

class ChannelSender(ABC):
    channel: Channel

    @abstractmethod
    def send_message_sync(self, user_id: str, text: str):
        pass

class BaseChannel(ABC):
    @abstractmethod
    def send_message(self, user_id: str, text: str):
        pass

    @abstractmethod
    def start(self):
        pass
