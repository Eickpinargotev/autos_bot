"""Las tareas conservan su copia y el bot mantiene los textos originales."""

from unittest.mock import MagicMock

from src.application.project_context import ambito_proyecto
from src.infrastructure.repositories import instrucciones_repository as repo
from src.infrastructure.tasks import celery_app as tasks


def programar(monkeypatch):
    llamadas = []

    def agendar(args, **kwargs):
        llamadas.append(args)
        return MagicMock(id=f"t{len(llamadas)}")

    monkeypatch.setattr(tasks.send_ad_reminder, "apply_async", agendar)
    tasks.schedule_ad_programmed_messages("whatsapp", "506", "lunes", "10000", "8:00", "https://chat.whatsapp.com/x")
    return llamadas


def test_originales_exactos_sin_configuracion(monkeypatch):
    llamadas = programar(monkeypatch)
    assert [args[2] for args in llamadas] == [
        "📌 Hola!!!  No recibí respuesta a nuestra conversación.  Podemos continuar???",
        "👋🏻Vi que no se unió al grupo👋🏻\n\n*¿¿¿Tiene alguna duda duda antes de unirse al grupo???",
        "📌Hola!!!\n\nLe comparto la información de nuestro curso.\n\nFecha: lunes\n\nHora: 8:00\nValor: 10000 colones\n\nUnirse al grupo: https://chat.whatsapp.com/x\n\nLe esperamos",
    ]


def test_textos_nuevos_solo_afectan_la_siguiente_programacion(monkeypatch):
    fila = {"publicidad_recordatorio_1_texto": "Antes {dia} {hora}",
            "publicidad_recordatorio_2_texto": "Texto libre 👋",
            "publicidad_recordatorio_3_texto": "{valor}\n{enlace}"}
    monkeypatch.setattr(repo, "consultar_uno", lambda *args: dict(fila))
    with ambito_proyecto(7):
        antiguas = programar(monkeypatch)
        fila["publicidad_recordatorio_1_texto"] = "Después {dia}"
        nuevas = programar(monkeypatch)
    assert antiguas[0][2] == "Antes lunes 8:00"
    assert nuevas[0][2] == "Después lunes"
    assert antiguas[1][2] == "Texto libre 👋"
    assert antiguas[2][2] == "10000\nhttps://chat.whatsapp.com/x"
    assert [args[3] for args in nuevas] == [1, 2, 3]


def test_lectura_aislada_por_proyecto_y_respaldo_parcial(monkeypatch):
    consultas = []

    def consultar(sql, params):
        consultas.append(params)
        return {"publicidad_recordatorio_1_texto": "Personalizado"} if params == (7,) else None

    monkeypatch.setattr(repo, "consultar_uno", consultar)
    with ambito_proyecto(7):
        assert repo.textos_recordatorios_publicidad() == ["Personalizado", *repo.TEXTOS_PUBLICIDAD_ORIGINALES[1:]]
    with ambito_proyecto(8):
        assert repo.textos_recordatorios_publicidad() == list(repo.TEXTOS_PUBLICIDAD_ORIGINALES)
    assert consultas == [(7,), (8,)]


def test_respaldo_si_la_migracion_no_esta_disponible(monkeypatch):
    def falla(*args):
        raise RuntimeError("columna no disponible")

    monkeypatch.setattr(repo, "consultar_uno", falla)
    with ambito_proyecto(7):
        assert repo.textos_recordatorios_publicidad() == list(repo.TEXTOS_PUBLICIDAD_ORIGINALES)
