from src.application.flow_graph import FlowGraphRunner, FlowProcessingResult
from src.application.message_catalog import get_messages_for_node, get_node_data, load_mensajes, mensajes_db
from src.domain.entities import Channel, UserState


_runner = FlowGraphRunner()


def categorize_message(text: str) -> UserState:
    flow = _runner._initial_flow(text)
    return _runner._legacy_state(flow)


def process_fsm(
    user_id: str,
    state: UserState,
    text: str,
    channel: Channel | str = Channel.TELEGRAM,
    user_name: str = "Desconocido",
) -> FlowProcessingResult:
    return _runner.run(channel, user_id, text, user_name=user_name)
