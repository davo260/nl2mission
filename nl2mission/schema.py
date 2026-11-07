"""Representación intermedia de una misión.

La misión es JSON plano (lista de pasos) para que la pueda producir
cualquier parser (LLM o reglas) y la pueda revisar un validador determinista.
Los condicionales tienen un solo nivel de anidamiento (then / else).
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Optional

ACTIONS = ["navigate_to", "find_object", "inspect", "say", "wait", "return_home", "patrol"]
MEASURES = ["temperatura", "humedad", "general"]


def _step_properties(nested: bool) -> dict:
    props = {
        "action": {"type": "string", "enum": ACTIONS},
        "target": {"type": "string", "description": "id del lugar (navigate_to, inspect) o del área donde buscar (find_object; omitir = buscar en todos los lugares permitidos)."},
        "object_type": {"type": "string", "description": "find_object: tipo de objeto reconocible."},
        "object_color": {"type": "string", "description": "find_object: color, si el usuario lo dijo."},
        "measure": {"type": "string", "enum": MEASURES, "description": "inspect: qué medir."},
        "threshold_op": {"type": "string", "enum": [">", "<"], "description": "inspect: condición sobre la medida."},
        "threshold_value": {"type": "number"},
        "message": {"type": "string", "description": "say: mensaje al usuario. {resultado} se reemplaza por el último resultado."},
        "seconds": {"type": "number", "description": "wait: segundos."},
        "places": {"type": "array", "items": {"type": "string"}, "description": "patrol: ids de lugares en orden."},
        "laps": {"type": "integer", "description": "patrol: número de vueltas."},
    }
    if nested:
        simple = {"type": "object", "properties": _step_properties(False), "required": ["action"]}
        props["then"] = {"type": "array", "items": simple,
                         "description": "find_object: si lo encuentra. inspect con umbral: si se cumple la condición."}
        props["else"] = {"type": "array", "items": simple,
                         "description": "find_object: si NO lo encuentra. inspect con umbral: si NO se cumple."}
    return props


MISSION_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "clarify", "reject"],
                   "description": "ok = misión ejecutable; clarify = falta información o hay ambigüedad; reject = imposible o inseguro."},
        "summary": {"type": "string", "description": "Una frase, en el idioma del usuario, con lo que entendiste."},
        "question": {"type": "string", "description": "Si clarify: la pregunta. Si reject: el motivo y una alternativa."},
        "steps": {"type": "array", "items": {"type": "object", "properties": _step_properties(True), "required": ["action"]}},
    },
    "required": ["status", "summary", "steps"],
}


@dataclass
class ParseResult:
    status: str                      # ok | clarify | reject
    summary: str = ""
    question: str = ""
    steps: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)       # info para resolver aclaraciones
    warnings: list = field(default_factory=list)
    source: str = ""                 # rules | anthropic | ollama

    @classmethod
    def from_dict(cls, d: dict, source: str = "") -> "ParseResult":
        return cls(status=d.get("status", "clarify"), summary=d.get("summary", ""),
                   question=d.get("question", "") or "", steps=copy.deepcopy(d.get("steps") or []),
                   source=source)

    def to_dict(self) -> dict:
        return {"status": self.status, "summary": self.summary, "question": self.question, "steps": self.steps}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def describe_step(step: dict, world=None) -> str:
    """Texto corto en español para mostrar el plan."""
    name = (lambda pid: world.display(pid)) if world else (lambda pid: pid)
    a = step.get("action")
    if a == "navigate_to":
        return f"Ir a {name(step.get('target'))}"
    if a == "return_home":
        return "Volver a la base"
    if a == "find_object":
        from .world import object_phrase
        obj = object_phrase(step.get("object_type"), step.get("object_color"))
        where = f" en {name(step['target'])}" if step.get("target") else " (en todo el mapa)"
        return f"Buscar {obj}{where}"
    if a == "inspect":
        if step.get("measure", "general") == "general":
            txt = f"Revisar {name(step.get('target'))} (temperatura, humedad y objetos)"
        else:
            txt = f"Medir {step.get('measure')} en {name(step.get('target'))}"
        if step.get("threshold_op"):
            txt += f" (¿{step['threshold_op']} {step.get('threshold_value')}?)"
        return txt
    if a == "say":
        msg = step.get("message") or "{resultado}"
        if msg.strip() == "{resultado}":
            return "Avisarte el resultado"
        return f"Avisar: \"{msg.replace('{resultado}', '<resultado>')}\""
    if a == "wait":
        return f"Esperar {step.get('seconds')} s"
    if a == "patrol":
        return f"Patrullar {' → '.join(name(p) for p in step.get('places', []))} ×{step.get('laps', 1)}"
    return str(step)


def _lc(t: str) -> str:
    return t[:1].lower() + t[1:]


def summarize(steps: list, world=None) -> str:
    """Resumen en una línea, incluyendo ramas condicionales."""
    parts = []
    for s in steps:
        txt = describe_step(s, world)
        cond = "si lo encuentra" if s.get("action") == "find_object" else "si se cumple"
        branches = []
        for key, label in (("then", cond), ("else", "si no")):
            if s.get(key):
                branches.append(f"{label}: " + ", ".join(_lc(describe_step(x, world)) for x in s[key]))
        parts.append(txt + (f" ({'; '.join(branches)})" if branches else ""))
    return "; ".join(parts)


def plan_lines(steps: list, world=None) -> list:
    """Plan aplanado con sangría para then/else -> [(nivel, texto, ruta)]."""
    out = []
    for i, s in enumerate(steps):
        out.append((0, f"{i + 1}. {describe_step(s, world)}", (i,)))
        cond = "si lo encuentra" if s.get("action") == "find_object" else "si se cumple"
        for key, label in (("then", cond), ("else", "si no")):
            if s.get(key):
                out.append((1, f"↳ {label}:", (i, key)))
                for j, sub in enumerate(s[key]):
                    out.append((2, f"- {describe_step(sub, world)}", (i, key, j)))
    return out
