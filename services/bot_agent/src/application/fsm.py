from src.application.agent_pipeline import AgentPipeline, FlowProcessingResult
from src.domain.entities import Channel, UserState


_pipeline = AgentPipeline()


def process_fsm(
    user_id: str,
    state: UserState,
    text: str,
    channel: Channel | str = Channel.TELEGRAM,
    user_name: str = "Desconocido",
) -> FlowProcessingResult:
    # Nombre histórico: el punto de entrada del turno conversacional. Desde el
    # modelo único ya no hay máquina de estados; delega en el AgentPipeline.
    return _pipeline.run(channel, user_id, text, user_name=user_name)
