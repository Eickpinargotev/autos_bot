"""Herramientas estrictas del agente; los efectos siguen en el pipeline existente.

Una llamada termina la decisión del turno. No se hace una segunda llamada al
modelo para parafrasear el resultado ni se envía su texto libre al cliente.
"""

from src.application.fragment_catalog import SPECIALIST_AREAS, allowed_fragments
from src.core.consultas_rag import MAX_CONSULTAS_RAG, MAX_LARGO_CONSULTA


TOOL_CONTRACT = """
═══ CONTRATO DE HERRAMIENTAS ═══
Ejecuta exactamente una herramienta por decisión. No devuelvas JSON de acciones
ni texto fuera de herramientas. Sus descripciones explican los campos.
El playbook define CUÁNDO actuar; las herramientas definen CÓMO ejecutarlo.
Los nombres de mensajes son literales: GENERAL.G3 es un nombre completo.
Todos los agentes pueden enviar cualquier mensaje activo del proyecto.
Envía únicamente el mensaje indicado por las instrucciones para el contexto;
no inventes nombres ni elijas otro mensaje por semejanza.
Los ejemplos antiguos con action/messages son referencias de intención:
reply + [[frag:NOMBRE]] = enviar_fragmento(nombre=NOMBRE);
reply con texto propio = responder_cliente;
[[rag]] + rag_query/rag_queries = consultar_conocimiento;
route/defer + target/report = derivar_agente(destino, reporte);
handoff = handoff_asesor; close = cerrar_conversacion;
city_invitation = invitacion_ciudad.
Las etiquetas del historial indican contenido YA enviado, no órdenes nuevas.
Las herramientas ejecutan la operación; no anuncies operaciones sin ejecutarlas.
"""


def _texto(descripcion="", **limites):
    return {"type": "string", "description": descripcion, **limites}


def _lista(item, maximo=4):
    return {"type": "array", "items": item, "maxItems": maximo}


def decision_tools(role: str) -> list[dict]:
    nombres = sorted(allowed_fragments(role))
    nombre = _texto("Nombre exacto indicado por las instrucciones, sin corchetes.", enum=nombres)
    pendiente = _texto("Pregunta o acción que queda en manos del cliente; vacío si ninguna.")
    textos = _lista(_texto("Texto conversacional breve, sin etiquetas ni datos de negocio de memoria."))
    definiciones = [
        ("responder_cliente", "Responde conversación breve al cliente. Para información del negocio usa consultar_conocimiento; para mensajes guardados usa enviar_fragmento.",
         {"mensajes": {**textos, "minItems": 1}, "pendiente": pendiente}),
        ("derivar_agente", "Transfiere a otro agente del bot cuando lo indique el playbook. No envía mensajes al cliente. Resume datos conocidos y motivo sin inventar. No se usa para un asesor humano.",
         {"destino": _texto(enum=[a for a in SPECIALIST_AREAS if a != role]), "reporte": _texto("Contexto y motivo de la derivación.", minLength=1)}),
        ("handoff_asesor", "Pasa el caso a un asesor humano especializado: crea el reporte y pausa la atención automática. Úsala para revisión humana según las instrucciones, nunca para cambiar de agente del bot.",
         {"reporte": _texto("Motivo y contexto que necesita el asesor.", minLength=1), "mensajes": textos}),
        ("consultar_conocimiento", "Resuelve dudas informativas con el RAG existente. El sistema busca y responde con evidencia. No redactes tú la respuesta. Conserva las preguntas reales y el contexto; hasta tres consultas para dudas múltiples.",
         {"pregunta": _texto("Duda informativa del cliente con contexto.", minLength=1),
          "consultas": _lista(_texto(maxLength=MAX_LARGO_CONSULTA), MAX_CONSULTAS_RAG),
          "mensajes_despues": textos, "fragmentos_despues": _lista(nombre), "pendiente": pendiente,
          "cerrar": {"type": "boolean", "description": "Cierra después de responder solo si el cliente se despide o rechaza continuar."}}),
        ("cerrar_conversacion", "Cierra cuando el cliente se despide o rechaza continuar sin dudas pendientes. Si hay duda informativa usa consultar_conocimiento con cerrar=true.",
         {"mensajes": textos}),
        ("invitacion_ciudad", "Ejecuta la invitación existente por la ciudad indicada, cuando el playbook lo ordene.",
         {"ciudad": _texto(minLength=1)}),
    ]
    # Un catálogo vacío no debe producir enum: [] (esquema no válido).
    if nombres:
        definiciones.insert(0, ("enviar_fragmento", "Envía literalmente el mensaje guardado con el nombre que ordena el prompt. No escribas su contenido ni cambies el nombre. El punto es parte del nombre. Todos los agentes tienen acceso.",
            {"nombre": nombre, "mensaje_previo": _texto("Vacío normalmente; solo una transición necesaria que no repita el fragmento."), "pendiente": pendiente}))
    else:
        definiciones = [(n, d, {k: v for k, v in campos.items() if k != "fragmentos_despues"}) for n, d, campos in definiciones]
    return [{"type": "function", "function": {
        "name": n, "description": d, "strict": True,
        "parameters": {"type": "object", "properties": campos,
                       "required": list(campos), "additionalProperties": False},
    }} for n, d, campos in definiciones]


def _validate(value, schema):
    """Valida también localmente el subconjunto de esquemas que emitimos."""
    tipo = schema["type"]
    if tipo == "object":
        if not isinstance(value, dict) or set(value) != set(schema["properties"]):
            raise ValueError("Campos de herramienta inválidos")
        for key, subschema in schema["properties"].items():
            _validate(value[key], subschema)
    elif tipo == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema["maxItems"]:
            raise ValueError("Lista de herramienta inválida")
        for item in value:
            _validate(item, schema["items"])
    elif tipo == "boolean":
        if type(value) is not bool:
            raise ValueError("Booleano inválido")
    elif tipo == "string":
        if not isinstance(value, str) or len(value.strip()) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", float("inf")):
            raise ValueError("Texto de herramienta inválido")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("Nombre o destino desconocido")


def tool_decision(name: str, args: dict, tools: list[dict], role: str) -> dict:
    definitions = {t["function"]["name"]: t["function"] for t in tools}
    if name not in definitions:
        raise ValueError("Herramienta desconocida")
    _validate(args, definitions[name]["parameters"])
    # Ninguna herramienta de texto puede filtrar etiquetas técnicas al cliente.
    for key in ("mensajes", "mensajes_despues", "mensaje_previo"):
        values = args.get(key, [])
        for text in ([values] if isinstance(values, str) else values):
            if "[[" in text:
                raise ValueError("Usa la herramienta de fragmentos o conocimiento")
    data = {"action": "reply", "messages": [], "pending": args.get("pendiente", "")}
    if name == "enviar_fragmento":
        data["messages"] = ([args["mensaje_previo"]] if args["mensaje_previo"] else []) + [f'[[frag:{args["nombre"]}]]']
    elif name == "derivar_agente":
        data.update(action="route" if role == "SUPERVISOR" else "defer", target=args["destino"], report=args["reporte"])
    elif name == "consultar_conocimiento":
        data.update(action="close" if args["cerrar"] else "reply", rag_query=args["pregunta"], rag_queries=args["consultas"],
                    messages=["[[rag]]", *args["mensajes_despues"], *[f"[[frag:{n}]]" for n in args.get("fragmentos_despues", [])]])
        if len(data["messages"]) > 4:
            raise ValueError("Demasiados mensajes después de conocimiento")
    elif name == "handoff_asesor":
        data.update(action="handoff", report=args["reporte"], messages=args["mensajes"])
    elif name == "cerrar_conversacion":
        data.update(action="close", messages=args["mensajes"])
    elif name == "invitacion_ciudad":
        data.update(action="city_invitation", city=args["ciudad"])
    else:
        data["messages"] = args["mensajes"]
    return data
