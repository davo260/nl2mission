"""Utilidades de planificación compartidas por el validador y el ejecutor."""
from __future__ import annotations

import math


def euclid(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def searchable_places(world, area, authorized) -> list:
    if area:
        return [area]
    return [p.id for p in world.places.values()
            if (not p.restricted or p.id in authorized) and p.id != world.home]


def search_points(world, area, start, authorized, dist=euclid) -> list:
    """Puntos de observación para buscar un objeto, ordenados por vecino más cercano."""
    pending = []
    for pid in searchable_places(world, area, authorized):
        pending += world.viewpoints(pid)
    route, pos = [], start
    while pending:
        nxt = min(pending, key=lambda p: dist(pos, p))
        pending.remove(nxt)
        route.append(nxt)
        pos = nxt
    return route
