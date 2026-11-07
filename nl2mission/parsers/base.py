from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..schema import ParseResult


@dataclass
class Context:
    """Estado conversacional que el parser necesita para resolver referencias."""
    robot_place: Optional[str] = None     # dónde está el robot ahora
    last_place: Optional[str] = None      # último lugar mencionado ("ahí", "allá")
    authorized: set = field(default_factory=set)   # zonas restringidas autorizadas


@dataclass
class Pending:
    """Una petición que quedó esperando aclaración del usuario."""
    text: str
    result: ParseResult


class Parser:
    name = "base"

    def parse(self, text: str, ctx: Context, history: Optional[list] = None) -> ParseResult:
        raise NotImplementedError

    def followup(self, pending: Pending, reply: str, ctx: Context) -> ParseResult:
        raise NotImplementedError


def clarify(question: str, **meta) -> ParseResult:
    return ParseResult(status="clarify", question=question, meta=dict(meta))


def reject(reason: str, **meta) -> ParseResult:
    return ParseResult(status="reject", question=reason, meta=dict(meta))
