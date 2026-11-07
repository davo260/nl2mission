"""Validador determinista: el LLM propone, el validador decide.

Revisa cualquier misión (venga del LLM o de las reglas) antes de ejecutarla:
- grounding: cada lugar/objeto debe existir en el mundo (corrige nombres -> ids)
- acciones permitidas y parámetros dentro de rango
- zonas restringidas -> pide confirmación humana
- presupuesto de batería -> rechaza misiones que dejarían al robot sin reserva
"""
from __future__ import annotations

import copy

from .parsers.base import Context
from .planning import euclid, search_points
from .world import object_phrase
from .schema import ACTIONS, MEASURES, ParseResult, describe_step

ALLOWED_KEYS = {
    "navigate_to": {"target"},
    "return_home": set(),
    "find_object": {"target", "object_type", "object_color", "then", "else"},
    "inspect": {"target", "measure", "threshold_op", "threshold_value", "then", "else"},
    "say": {"message"},
    "wait": {"seconds"},
    "patrol": {"places", "laps"},
}


class Issue(Exception):
    def __init__(self, status: str, question: str, **meta):
        super().__init__(question)
        self.status, self.question, self.meta = status, question, meta


class Validator:
    def __init__(self, world, backend=None, max_steps: int = 25, max_wait: float = 600, max_laps: int = 5):
        self.world, self.backend = world, backend
        self.max_steps, self.max_wait, self.max_laps = max_steps, max_wait, max_laps

    def validate(self, r: ParseResult, ctx: Context) -> ParseResult:
        if r.status != "ok":
            return r
        warnings: list = []
        self._ctx = ctx
        try:
            if not r.steps:
                raise Issue("clarify", "No identifiqué ninguna acción. ¿Qué quieres que haga?")
            steps = self._steps(r.steps, depth=0, warnings=warnings)
            if _count(steps) > self.max_steps:
                raise Issue("reject", f"La misión tiene demasiados pasos (máx. {self.max_steps}). Divídela en partes.")
        except Issue as e:
            return ParseResult(e.status, summary=r.summary, question=e.question, steps=r.steps,
                               meta={**r.meta, **e.meta}, source=r.source)

        out = ParseResult("ok", summary=r.summary, steps=steps, meta=dict(r.meta), warnings=warnings, source=r.source)

        restricted = sorted({p for p in _places_used(steps)
                             if self.world.places[p].restricted and p not in ctx.authorized})
        if restricted:
            names = ", ".join(self.world.display(p) for p in restricted)
            out.status = "clarify"
            out.question = f"{names} es zona restringida. ¿Confirmas que tengo autorización para entrar? (sí / no)"
            out.meta.update(kind="confirm_restricted", places=restricted)
            return out

        if self.backend is not None:
            dist = getattr(self.backend, "estimate_distance", euclid)
            meters = self._cost(steps, tuple(self.backend.pose()), dist)[0]
            rb = self.world.robot
            need = meters * rb.get("consumption_per_m", 0.8)
            avail = self.backend.battery() - rb.get("reserve", 15.0)
            out.meta["estimate"] = {"meters": round(meters, 1), "battery_needed": round(need, 1),
                                    "battery_available": round(avail, 1)}
            if need > avail:
                out.status = "reject"
                out.question = (f"No me alcanza la batería: la misión requiere ~{need:.0f}% "
                                f"({meters:.0f} m) y tengo {self.backend.battery():.0f}% con una reserva mínima de "
                                f"{rb.get('reserve', 15):.0f}%. Divide la misión o déjame cargar primero.")
        return out

    # ------------------------------------------------------------------ pasos
    def _steps(self, steps, depth, warnings) -> list:
        return [self._step(s, depth, warnings) for s in steps]

    def _step(self, s, depth, warnings) -> dict:
        w = self.world
        if not isinstance(s, dict) or s.get("action") not in ACTIONS:
            raise Issue("reject", f"Acción no soportada: {s.get('action') if isinstance(s, dict) else s}.")
        a = s["action"]
        s = {k: copy.deepcopy(v) for k, v in s.items() if k in ALLOWED_KEYS[a] | {"action"} and v not in (None, "", [])}

        if a in ("navigate_to", "inspect") or (a == "find_object" and s.get("target")):
            if a == "inspect" and not s.get("target"):
                s["target"] = self._ctx.last_place or self._ctx.robot_place
            if not s.get("target"):
                raise Issue("clarify", "¿A qué lugar me refiero?", kind="missing_place")
            s["target"] = self._place(s["target"])

        if a == "find_object":
            t = w.canonical_type(s.get("object_type"))
            if not t:
                raise Issue("clarify", f"No sé reconocer '{s.get('object_type')}'. Puedo buscar: "
                            f"{', '.join(w.object_types)}.", kind="unknown_object", span=s.get("object_type") or "")
            s["object_type"] = t
            if s.get("object_color"):
                c = w.canonical_color(s["object_color"])
                if c:
                    s["object_color"] = c
                else:
                    warnings.append(f"Ignoro el color '{s.pop('object_color')}' (no lo distingo).")
            if depth == 0 and not s.get("else"):
                s["else"] = [{"action": "say", "message": f"No encontré {object_phrase(t, s.get('object_color'))}."}]

        if a == "inspect":
            s["measure"] = s.get("measure") if s.get("measure") in MEASURES else "general"
            if s.get("threshold_op") or s.get("threshold_value") is not None:
                if s.get("threshold_op") not in (">", "<") or not isinstance(s.get("threshold_value"), (int, float)):
                    raise Issue("clarify", "No entendí la condición. ¿Con qué valor comparo y si es mayor o menor?")
                if s["measure"] == "general":
                    s["measure"] = "temperatura"
                if depth == 0 and not s.get("then"):
                    s["then"] = [{"action": "say", "message": "Alerta: {resultado}"}]
            elif s.get("then") or s.get("else"):
                warnings.append("Ignoro las ramas de una medición sin condición.")
                s.pop("then", None)
                s.pop("else", None)

        if a == "wait":
            sec = s.get("seconds")
            if not isinstance(sec, (int, float)) or not 0 < sec <= self.max_wait:
                raise Issue("clarify", f"¿Cuánto tiempo espero? (entre 1 s y {self.max_wait / 60:.0f} min)")

        if a == "patrol":
            places = [self._place(p) for p in s.get("places", [])]
            if not places:
                raise Issue("clarify", "¿Qué lugares patrullo?", kind="missing_place")
            laps = int(s.get("laps") or 1)
            if not 1 <= laps <= self.max_laps:
                raise Issue("reject", f"Puedo hacer como máximo {self.max_laps} vueltas por misión.")
            s.update(places=places, laps=laps)

        if a == "say":
            s["message"] = str(s.get("message") or "{resultado}")

        for key in ("then", "else"):
            if key in s:
                if depth >= 1:
                    raise Issue("reject", "No manejo condiciones anidadas dentro de otra condición. Simplifica la misión.")
                s[key] = self._steps(s[key], depth + 1, warnings)
        return s

    def _place(self, token: str) -> str:
        ids = self.world.resolve_place(str(token))
        if not ids:
            known = ", ".join(p.name for p in self.world.places.values())
            raise Issue("clarify", f"No conozco '{token}'. Los lugares que conozco son: {known}.",
                        kind="unknown_place", span=str(token))
        if len(ids) > 1:
            opts = " o ".join(self.world.display(i) for i in ids)
            raise Issue("clarify", f"'{token}' puede ser {opts}. ¿A cuál te refieres?",
                        kind="ambiguous_place", span=str(token), options=ids)
        return ids[0]

    # ------------------------------------------------------------------ batería
    def _cost(self, steps, pos, dist):
        total = 0.0
        for s in steps:
            d, pos = self._cost_step(s, pos, dist)
            total += d
        return total, pos

    def _cost_step(self, s, pos, dist):
        w, a = self.world, s["action"]
        d = 0.0
        if a in ("navigate_to", "inspect", "return_home"):
            tgt = w.places[w.home if a == "return_home" else s["target"]].center
            d, pos = dist(pos, tgt), tgt
        elif a == "patrol":
            for _ in range(s["laps"]):
                for p in s["places"]:
                    tgt = w.places[p].center
                    d += dist(pos, tgt)
                    pos = tgt
        elif a == "find_object":
            auth = self._ctx.authorized
            for pt in search_points(w, s.get("target"), pos, auth):
                d += dist(pos, pt)
                pos = pt
        if s.get("then") or s.get("else"):
            branches = [self._cost(s.get(k, []), pos, dist) for k in ("then", "else")]
            bd, bpos = max(branches, key=lambda x: x[0])
            d += bd
            pos = bpos
        return d, pos


def _places_used(steps) -> set:
    out = set()
    for s in steps:
        if s.get("target"):
            out.add(s["target"])
        out |= set(s.get("places", []))
        for k in ("then", "else"):
            out |= _places_used(s.get(k, []))
    return out


def _count(steps) -> int:
    return sum(1 + _count(s.get("then", [])) + _count(s.get("else", [])) for s in steps)


def summarize(steps, world) -> str:
    return "; ".join(describe_step(s, world) for s in steps)
