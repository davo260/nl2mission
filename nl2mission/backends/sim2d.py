"""Simulador 2D ligero (sin ROS): mapa de ocupación, A*, percepción con línea de vista.

Sirve para probar el agente en cualquier PC y para generar el GIF de la demo.
Implementa la misma interfaz que el backend ROS2, así el agente no cambia.
"""
from __future__ import annotations

import heapq
import math
import random
from typing import Callable, Optional

import numpy as np

from ..world import WorldModel

SQRT2 = math.sqrt(2)


class Sim2DBackend:
    def __init__(self, world: WorldModel, seed: int = 7):
        self.world = world
        sim = world.sim
        self.res = sim.get("resolution", 0.5)
        self.W = int(round(sim["width"] / self.res))
        self.H = int(round(sim["height"] / self.res))
        self.occ = np.zeros((self.H, self.W), dtype=bool)
        self.occ[0, :] = self.occ[-1, :] = True
        self.occ[:, 0] = self.occ[:, -1] = True
        for x0, y0, x1, y1 in sim.get("walls", []):
            if y0 == y1:
                r = self._ri(y0)
                self.occ[r, self._ci(min(x0, x1)):self._ci(max(x0, x1)) + 1] = True
            else:
                c = self._ci(x0)
                self.occ[self._ri(min(y0, y1)):self._ri(max(y0, y1)) + 1, c] = True
        for dx, dy, width in sim.get("doors", []):
            r, c = self._ri(dy), self._ci(dx)
            half = int(round(width / 2 / self.res))
            if any(abs(w[1] - w[3]) < 1e-9 and abs(w[1] - dy) < 1e-9 for w in sim.get("walls", [])):
                self.occ[r, max(1, c - half):min(self.W - 1, c + half)] = False
            else:
                self.occ[max(1, r - half):min(self.H - 1, r + half), c] = False
        self.inflated = self.occ.copy()
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                self.inflated |= _shift(self.occ, dr, dc)

        rb = world.robot
        self.rng = random.Random(seed)
        self.pos = tuple(rb.get("start", world.places[world.home].center))
        self.heading = 0.0
        self._battery = float(rb.get("battery", 100.0))
        self.consumption = rb.get("consumption_per_m", 0.8)
        self.range = rb.get("sensor_range", 3.5)
        self.speed = rb.get("speed", 0.5)
        self.t = 0.0
        self.trail = [self.pos]
        self.frames: list = []
        self.events: list = []
        self.active = None
        self.speech = None
        self.speech_ttl = 0
        self.mission = None
        self.trail_start = 0
        self.missions: list = []          # (inicio_frame, ParseResult)
        self._dist_cache: dict = {}

    # ------------------------------------------------------------------ grilla
    def _ri(self, y):
        return min(self.H - 1, max(0, int(y / self.res)))

    def _ci(self, x):
        return min(self.W - 1, max(0, int(x / self.res)))

    def _xy(self, rc):
        return ((rc[1] + 0.5) * self.res, (rc[0] + 0.5) * self.res)

    def _nearest_free(self, rc):
        if not self.inflated[rc]:
            return rc
        seen, frontier = {rc}, [rc]
        while frontier:
            nxt = []
            for r, c in frontier:
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    q = (r + dr, c + dc)
                    if 0 <= q[0] < self.H and 0 <= q[1] < self.W and q not in seen:
                        if not self.inflated[q]:
                            return q
                        seen.add(q)
                        nxt.append(q)
            frontier = nxt
        return rc

    def astar(self, a, b) -> Optional[list]:
        s = self._nearest_free((self._ri(a[1]), self._ci(a[0])))
        g = self._nearest_free((self._ri(b[1]), self._ci(b[0])))
        h = lambda p: math.hypot(p[0] - g[0], p[1] - g[1])
        openq = [(h(s), 0.0, s)]
        came, cost = {s: None}, {s: 0.0}
        while openq:
            _, gc, cur = heapq.heappop(openq)
            if cur == g:
                path = []
                while cur is not None:
                    path.append(cur)
                    cur = came[cur]
                return [self._xy(p) for p in reversed(path)]
            if gc > cost[cur]:
                continue
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == dc == 0:
                        continue
                    q = (cur[0] + dr, cur[1] + dc)
                    if not (0 <= q[0] < self.H and 0 <= q[1] < self.W) or self.inflated[q]:
                        continue
                    if dr and dc and (self.inflated[cur[0] + dr, cur[1]] or self.inflated[cur[0], cur[1] + dc]):
                        continue
                    nc = gc + (SQRT2 if dr and dc else 1.0)
                    if nc < cost.get(q, math.inf):
                        cost[q], came[q] = nc, cur
                        heapq.heappush(openq, (nc + h(q), nc, q))
        return None

    def estimate_distance(self, a, b) -> float:
        key = (self._ri(a[1]), self._ci(a[0]), self._ri(b[1]), self._ci(b[0]))
        if key not in self._dist_cache:
            p = self.astar(a, b)
            self._dist_cache[key] = (sum(math.dist(p[i], p[i + 1]) for i in range(len(p) - 1))
                                     if p else math.inf)
        return self._dist_cache[key]

    # ------------------------------------------------------------------ interfaz backend
    def pose(self):
        return self.pos

    def battery(self) -> float:
        return self._battery

    def current_place(self):
        return self.world.place_at(*self.pos)

    def navigate_to(self, goal, label: str = "", stop_when: Optional[Callable] = None) -> str:
        path = self.astar(self.pos, goal)
        if path is None:
            self.on_event(f"⚠ No hay ruta hacia {label or goal}")
            return "failed"
        for p in path[1:]:
            d = math.dist(self.pos, p)
            if d > 1e-6:
                self.heading = math.atan2(p[1] - self.pos[1], p[0] - self.pos[0])
            self.pos = p
            self._battery = max(0.0, self._battery - d * self.consumption)
            self.t += d / self.speed
            self.trail.append(p)
            self._record()
            if stop_when and stop_when():
                return "stopped"
        return "arrived"

    def dock(self):
        """En la base el robot recarga (simulado: sube 10 % por cuadro hasta 100 %)."""
        while self._battery < 100.0:
            self._battery = min(100.0, self._battery + 10.0)
            self.t += 5.0
            self._record()

    def detect(self) -> list:
        out = []
        for o in self.world.objects:
            if math.dist(self.pos, o.pos) <= self.range and self._los(self.pos, o.pos):
                out.append({"id": o.id, "type": o.type, "color": o.color, "pos": o.pos, "place": o.place})
        return out

    def read_sensor(self, measure: str) -> float:
        pid = self.current_place()
        base = self.world.places[pid].sensors.get(measure, 0.0) if pid else 0.0
        return round(base + self.rng.gauss(0, 0.15), 1)

    def say(self, msg: str):
        self.speech, self.speech_ttl = msg, 14
        for _ in range(10):
            self.t += 0.3
            self._record()

    def wait(self, seconds: float):
        for _ in range(min(10, max(1, int(seconds)))):
            self.t += seconds / min(10, max(1, int(seconds)))
            self._record()

    # ------------------------------------------------------------------ visualización
    def begin_mission(self, result):
        self.mission = result
        self.missions.append((len(self.frames), result))
        self.trail_start = len(self.trail) - 1
        self.events = []
        self._record()

    def on_event(self, text: str):
        self.events.append(text)
        self._record()

    def set_active(self, path):
        self.active = path

    def _record(self):
        if self.speech_ttl > 0:
            self.speech_ttl -= 1
        else:
            self.speech = None
        self.frames.append({
            "t": self.t, "pos": self.pos, "heading": self.heading, "battery": self._battery,
            "trail": len(self.trail), "trail_start": self.trail_start, "active": self.active, "events": list(self.events[-7:]),
            "speech": self.speech, "seen": [d["id"] for d in self.detect()],
            "mission": len(self.missions) - 1,
        })

    def _los(self, a, b) -> bool:
        n = max(2, int(math.dist(a, b) / (self.res / 3)))
        for i in range(1, n):
            x = a[0] + (b[0] - a[0]) * i / n
            y = a[1] + (b[1] - a[1]) * i / n
            if self.occ[self._ri(y), self._ci(x)]:
                return False
        return True


def _shift(m: np.ndarray, dr: int, dc: int) -> np.ndarray:
    out = np.zeros_like(m)
    H, W = m.shape
    out[max(0, dr):H + min(0, dr), max(0, dc):W + min(0, dc)] = m[max(0, -dr):H - max(0, dr), max(0, -dc):W - max(0, dc)]
    return out
