"""Cada reporte deja una nota durable en la conversación correspondiente."""

from unittest.mock import patch

from src.infrastructure.repositories.report_repository import ReportRepository


def test_crear_reporte_inserta_reporte_y_evento_en_una_operacion():
    with patch(
        "src.infrastructure.repositories.report_repository.ejecutar", return_value=1
    ) as ejecutar:
        ok, _ = ReportRepository.create_report(
            nombre="Ana",
            numero="50688888888",
            problema="Necesita ayuda humana",
            link_whatsapp="https://wa.me/50688888888",
            canal="whatsapp",
        )

    assert ok is True
    sql, params = ejecutar.call_args.args
    assert "WITH nuevo AS" in sql
    assert "report_created" in sql
    assert "conversation_messages" in sql
    assert params[6:9] == ("50688888888", "whatsapp", "Necesita ayuda humana")


def test_un_reporte_revisado_caduca_despues_de_un_dia():
    with patch(
        "src.infrastructure.repositories.report_repository.ejecutar", return_value=1
    ) as ejecutar:
        borrados = ReportRepository.purge_reviewed()

    assert borrados == 1
    sql, params = ejecutar.call_args.args
    assert "revisado_en < NOW()" in sql
    assert params == (1,)
