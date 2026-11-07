"""Parsers basados en LLM: Claude (API de Anthropic) u Ollama (modelo local).

El LLM solo PROPONE la misión llamando a la herramienta `submit_mission`
(salida estructurada con JSON Schema). Nunca ejecuta: el validador
determinista revisa todo antes de que el robot se mueva.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional

from ..schema import MISSION_SCHEMA, ParseResult
from ..world import WorldModel
from .base import Context, Parser, Pending

EXAMPLES = """
EJEMPLOS (entrada -> argumentos de submit_mission):

"ve al laboratorio y mide la temperatura" ->
{"status":"ok","summary":"Ir al laboratorio y medir la temperatura.","steps":[{"action":"inspect","target":"laboratorio","measure":"temperatura"}]}

"busca la caja roja en la bodega; si la encuentras avísame, si no vuelve a la base" ->
{"status":"ok","summary":"Buscar la caja roja en la bodega y avisar; si no está, volver a la base.","steps":[{"action":"find_object","object_type":"caja","object_color":"rojo","target":"bodega","then":[{"action":"say","message":"Encontré la caja roja: {resultado}"}],"else":[{"action":"return_home"}]}]}

"si en la sala de servidores hace más de 30 grados avísame" ->
{"status":"ok","summary":"Medir la temperatura de la sala de servidores y avisar si supera 30 °C.","steps":[{"action":"inspect","target":"servidores","measure":"temperatura","threshold_op":">","threshold_value":30,"then":[{"action":"say","message":"Alerta: {resultado}"}]}]}

"ve a la oficina" (hay dos oficinas) ->
{"status":"clarify","summary":"Ir a una oficina.","question":"Tengo dos oficinas: la norte y la sur. ¿A cuál voy?","steps":[]}

"tráeme un café" ->
{"status":"reject","summary":"Traer un café.","question":"No puedo traer cosas porque no tengo brazo. Puedo ir a la cocina y avisarte qué hay ahí.","steps":[]}
"""


def build_system_prompt(world: WorldModel, ctx: Context) -> str:
    here = world.display(ctx.robot_place) if ctx.robot_place else "desconocido"
    last = world.display(ctx.last_place) if ctx.last_place else "ninguno"
    auth = ", ".join(world.display(p) for p in ctx.authorized) or "ninguno"
    return f"""Eres el módulo de comprensión de lenguaje natural de un robot móvil de servicio.
El robot es una base diferencial SIN brazo: no puede agarrar, traer, abrir puertas ni manipular objetos.
Tiene cámara con detector de objetos y sensores de temperatura y humedad.

Tu trabajo es convertir la petición del usuario en una misión estructurada llamando SIEMPRE a la herramienta submit_mission.
Tú no ejecutas nada: un validador determinista revisará la misión antes de que el robot se mueva.

{world.llm_summary()}

ESTADO ACTUAL
- El robot está en: {here}
- Último lugar mencionado (para "ahí", "allá", "ese lugar"): {last}
- Zonas restringidas ya autorizadas: {auth}

ACCIONES DISPONIBLES
- navigate_to(target)
- return_home()
- find_object(object_type, object_color?, target?) con then/else opcionales
- inspect(target, measure, threshold_op?, threshold_value?) con then/else si hay umbral (inspect ya incluye ir al lugar)
- say(message): usa {{resultado}} para incluir el último resultado (lo encontrado o lo medido)
- wait(seconds)
- patrol(places[], laps)

REGLAS
1. Usa solo ids de la lista de lugares. Si el lugar no existe, responde status=clarify y ofrece los lugares conocidos.
2. Si un nombre coincide con varios lugares (p. ej. "oficina") y la conversación no lo resuelve, responde status=clarify con las opciones.
3. Si piden manipular objetos, responde status=reject, explica por qué y ofrece una alternativa que sí puedas hacer.
4. Si piden buscar algo que no está en la lista de objetos reconocibles, responde status=clarify.
5. No inventes pasos que el usuario no pidió, salvo los implícitos.
6. Si el usuario dice "avísame" sin más, usa say con message "{{resultado}}".
7. Incluye los lugares restringidos normalmente: el sistema pedirá la autorización.
8. Respeta el orden temporal ("primero", "luego", "después").
9. Escribe summary y question en el idioma del usuario.
10. Si la petición es vaga ("haz algo útil"), responde status=clarify con una propuesta concreta.
11. Si el usuario responde a una pregunta tuya, combina su respuesta con la petición original y devuelve la misión completa.
{EXAMPLES}"""


TOOL = {
    "name": "submit_mission",
    "description": "Entrega la misión estructurada que el robot debe ejecutar (o una aclaración / rechazo).",
    "input_schema": MISSION_SCHEMA,
}


class _LLMParser(Parser):
    def __init__(self, world: WorldModel):
        self.world = world

    def _call(self, system: str, messages: list) -> dict:
        raise NotImplementedError

    def parse(self, text: str, ctx: Context, history: Optional[list] = None) -> ParseResult:
        messages = list(history or []) + [{"role": "user", "content": text}]
        try:
            data = self._call(build_system_prompt(self.world, ctx), messages)
        except Exception as e:  # noqa: BLE001 - mostrar el error al usuario sin romper la demo
            r = ParseResult("reject", question=f"No pude consultar el modelo de lenguaje ({type(e).__name__}: {e}).")
            r.source = self.name
            return r
        r = ParseResult.from_dict(data, source=self.name)
        r.meta["messages"] = messages
        return r

    def followup(self, pending: Pending, reply: str, ctx: Context) -> ParseResult:
        history = list(pending.result.meta.get("messages", [{"role": "user", "content": pending.text}]))
        history.append({"role": "assistant", "content": pending.result.question or "¿Puedes aclarar?"})
        return self.parse(reply, ctx, history=history)


class AnthropicParser(_LLMParser):
    """Claude vía la API de Anthropic (requiere ANTHROPIC_API_KEY)."""
    name = "anthropic"

    def __init__(self, world: WorldModel, model: Optional[str] = None, client=None, max_tokens: int = 2048):
        super().__init__(world)
        if client is None:
            import anthropic  # import perezoso: el modo offline no lo necesita
            client = anthropic.Anthropic()
        self.client = client
        self.model = model or os.environ.get("NL2M_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens

    def _call(self, system: str, messages: list) -> dict:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "submit_mission"},
            messages=messages,
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input)
        raise RuntimeError("el modelo no devolvió la herramienta submit_mission")


class OllamaParser(_LLMParser):
    """Modelo local con Ollama (sin costo, funciona offline). Ej.: qwen2.5:7b, llama3.1:8b."""
    name = "ollama"

    def __init__(self, world: WorldModel, model: Optional[str] = None, host: Optional[str] = None):
        super().__init__(world)
        self.model = model or os.environ.get("NL2M_OLLAMA_MODEL", "qwen2.5:7b")
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")

    def _call(self, system: str, messages: list) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "format": MISSION_SCHEMA,
            "stream": False,
            "options": {"temperature": 0},
        }
        req = urllib.request.Request(f"{self.host}/api/chat", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
        return json.loads(body["message"]["content"])
