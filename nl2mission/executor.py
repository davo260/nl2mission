"""Ejecutor de misiones sobre cualquier backend (simulador 2D o ROS2/Nav2).

Interfaz que debe cumplir un backend:
    pose() -> (x, y)                      battery() -> float (%)
    current_place() -> id | None          navigate_to((x, y), label, stop_when=None) -> "arrived"|"stopped"|"failed"
    detect() -> [dict(id,type,color,pos,place)]
    read_sensor(measure) -> float         say(msg)       wait(seconds)
    on_event(text)  set_active(path)      (opcionales, para visualización)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .planning import euclid, search_points
from .world import object_phrase

UNITS = {"temperatura": "°C", "humedad": "%"}


class MissionAbort(Exception):
    pass


@dataclass
class ExecReport:
    success: bool = True
    events: list = field(default_factory=list)
    last_result: str = ""
    error: Optional[str] = None


class Executor:
    def __init__(self, world, backend, log=print):
        self.world, self.backend, self.log = world, backend, log

    def run(self, steps: list, authorized: set = frozenset()) -> ExecReport:
        self.report = ExecReport()
        self.authorized = set(authorized)
        try:
            self._run(steps, ())
            self._emit("✔ Misión completada")
        except MissionAbort as e:
            self.report.success, self.report.error = False, str(e)
            self._emit(f"✖ Misión abortada: {e}")
        self._active(None)
        return self.report

    # ------------------------------------------------------------------ internos
    def _emit(self, text: str):
        self.report.events.append(text)
        if self.log:
            self.log(text)
        if hasattr(self.backend, "on_event"):
            self.backend.on_event(text)

    def _active(self, path):
        if hasattr(self.backend, "set_active"):
            self.backend.set_active(path)

    def _run(self, steps, prefix):
        for i, s in enumerate(steps):
            path = prefix + (i,)
            self._active(path)
            getattr(self, "_do_" + s["action"])(s, path)

    def _goto(self, pid: str):
        if self.backend.current_place() == pid and euclid(self.backend.pose(), self.world.places[pid].center) < 1.5:
            return
        self._emit(f"→ Voy a {self.world.display(pid)}")
        if self.backend.navigate_to(self.world.places[pid].center, self.world.display(pid)) == "failed":
            raise MissionAbort(f"no pude llegar a {self.world.display(pid)}")

    def _branch(self, s, key, path):
        if s.get(key):
            self._run(s[key], path + (key,))

    # ------------------------------------------------------------------ acciones
    def _do_navigate_to(self, s, path):
        self._goto(s["target"])
        self.report.last_result = f"llegué a {self.world.display(s['target'])}"

    def _do_return_home(self, s, path):
        self._goto(self.world.home)
        if hasattr(self.backend, "dock"):
            self._emit("🔌 Acoplado a la base, cargando")
            self.backend.dock()
        self.report.last_result = "estoy en la base"

    def _do_patrol(self, s, path):
        for lap in range(s["laps"]):
            self._emit(f"Patrulla: vuelta {lap + 1}/{s['laps']}")
            for pid in s["places"]:
                self._goto(pid)
        self.report.last_result = f"patrulla terminada ({s['laps']} vueltas)"

    def _do_wait(self, s, path):
        self._emit(f"Esperando {s['seconds']:.0f} s")
        self.backend.wait(s["seconds"])

    def _do_say(self, s, path):
        msg = s.get("message") or "{resultado}"
        msg = msg.replace("{resultado}", self.report.last_result).replace("{last_result}", self.report.last_result)
        self._emit(f"🗨 {msg}")
        self.backend.say(msg)

    def _read(self, measure: str) -> tuple:
        v = self.backend.read_sensor(measure)
        return v, f"{measure} = {v:.1f} {UNITS.get(measure, '')}".strip()

    def _do_inspect(self, s, path):
        pid = s["target"]
        self._goto(pid)
        where = self.world.display(pid)
        if s["measure"] == "general":
            _, t = self._read("temperatura")
            _, h = self._read("humedad")
            seen = sorted({object_phrase(d["type"], d["color"]) for d in self.backend.detect()})
            vis = ", ".join(seen) if seen else "nada relevante"
            self.report.last_result = f"en {where}: {t}, {h}; veo: {vis}"
        else:
            value, txt = self._read(s["measure"])
            self.report.last_result = f"{txt} en {where}"
        self._emit(f"📏 {self.report.last_result}")
        if s.get("threshold_op"):
            op, th = s["threshold_op"], s["threshold_value"]
            ok = value > th if op == ">" else value < th
            self._emit(f"Condición {s['measure']} {op} {th}: {'sí' if ok else 'no'}")
            self._branch(s, "then" if ok else "else", path)

    def _do_find_object(self, s, path):
        otype, color = s["object_type"], s.get("object_color")
        desc = object_phrase(otype, color)
        area = s.get("target")
        self._emit(f"🔎 Busco {desc}" + (f" en {self.world.display(area)}" if area else " en todo el mapa"))

        def match():
            for d in self.backend.detect():
                if d["type"] == otype and (not color or d["color"] == color):
                    return d
            return None

        found = match()
        if not found:
            dist = getattr(self.backend, "estimate_distance", euclid)
            for pt in search_points(self.world, area, self.backend.pose(), self.authorized, dist):
                r = self.backend.navigate_to(pt, f"buscando {desc}", stop_when=match)
                found = match()
                if found or r == "failed":
                    break
        if found:
            place = self.world.display(found.get("place")) if found.get("place") else "?"
            x, y = found["pos"]
            self.report.last_result = f"encontré {desc} en {place} (x={x:.1f}, y={y:.1f})"
            self._emit(f"✅ {self.report.last_result[0].upper()}{self.report.last_result[1:]}")
            self.backend.navigate_to(found["pos"], f"acercándome a {desc}",
                                     stop_when=lambda: euclid(self.backend.pose(), found["pos"]) < 1.2)
            self._branch(s, "then", path)
        else:
            self.report.last_result = f"no encontré {desc}"
            self._emit(f"❌ No encontré {desc}")
            self._branch(s, "else", path)
