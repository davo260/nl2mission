"""Agente conversacional: lenguaje natural -> misión validada -> ejecución.

Flujo de cada turno:
    usuario ──► parser (LLM o reglas) ──► validador determinista ──┬─► ejecutar
                       ▲                                           ├─► pedir aclaración
                       └──────────── respuesta del usuario ◄───────┴─► rechazar con motivo
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .executor import ExecReport, Executor
from .parsers.base import Context, Parser, Pending
from .schema import ParseResult
from .validator import Validator
from .world import WorldModel, normalize

YES_RE = re.compile(r"^(si|claro|confirmo|confirmado|autorizado|estoy autorizado|tienes autorizacion|dale|adelante|ok|okay|yes|y)\b")
NO_RE = re.compile(r"^(no|negativo|mejor no|nope|n)\b")
CANCEL_RE = re.compile(r"^(cancela|cancelar|olvidalo|olvida eso|nada|dejalo|cancel|stop|para)\b")


@dataclass
class AgentReply:
    kind: str                         # execute | clarify | reject | cancel
    message: str
    result: Optional[ParseResult] = None


class MissionAgent:
    def __init__(self, world: WorldModel, parser: Parser, backend, log=print):
        self.world, self.parser, self.backend = world, parser, backend
        self.validator = Validator(world, backend)
        self.executor = Executor(world, backend, log=log)
        self.ctx = Context(robot_place=backend.current_place())
        self.pending: Optional[Pending] = None

    def handle(self, text: str) -> AgentReply:
        self.ctx.robot_place = self.backend.current_place()
        norm = normalize(text)

        if self.pending:
            p, self.pending = self.pending, None
            if CANCEL_RE.match(norm):
                return AgentReply("cancel", "Listo, cancelé esa misión.")
            if p.result.meta.get("kind") == "confirm_restricted":
                if YES_RE.match(norm):
                    self.ctx.authorized |= set(p.result.meta["places"])
                    r = ParseResult("ok", summary=p.result.summary, steps=p.result.steps,
                                    meta={k: v for k, v in p.result.meta.items() if k not in ("kind", "places")},
                                    source=p.result.source)
                elif NO_RE.match(norm):
                    return AgentReply("reject", "Entendido: no entro a zonas restringidas. La misión queda cancelada.")
                else:
                    self.pending = p
                    return AgentReply("clarify", "Necesito un sí o un no: " + p.result.question, p.result)
            else:
                r = self.parser.followup(p, text, self.ctx)
            source_text = r.meta.get("text", f"{p.text} / {text}")
        else:
            r = self.parser.parse(text, self.ctx)
            source_text = r.meta.get("text", text)

        r = self.validator.validate(r, self.ctx)
        if r.status == "clarify":
            self.pending = Pending(source_text, r)
            return AgentReply("clarify", r.question, r)
        if r.status == "reject":
            return AgentReply("reject", r.question or "No puedo hacer eso.", r)
        self._remember_places(r.steps)
        return AgentReply("execute", r.summary, r)

    def execute(self, reply: AgentReply) -> ExecReport:
        if hasattr(self.backend, "begin_mission"):
            self.backend.begin_mission(reply.result)
        report = self.executor.run(reply.result.steps, self.ctx.authorized)
        self.ctx.robot_place = self.backend.current_place()
        return report

    def _remember_places(self, steps):
        for s in steps:
            if s.get("target"):
                self.ctx.last_place = s["target"]
            if s.get("places"):
                self.ctx.last_place = s["places"][-1]
            if s["action"] == "return_home":
                self.ctx.last_place = self.world.home
