from src.application.flow_graph import FlowGraphRunner, FlowProcessingResult
from src.domain.entities import Channel, UserState


_runner = FlowGraphRunner()


def process_fsm(
    user_id: str,
    state: UserState,
    text: str,
    channel: Channel | str = Channel.TELEGRAM,
    user_name: str = "Desconocido",
) -> FlowProcessingResult:
    return _runner.run(channel, user_id, text, user_name=user_name)
