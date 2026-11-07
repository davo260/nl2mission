"""Modelo semántico del mundo: lugares, alias, tipos de objeto y colores.

Lo comparten el parser, el validador y los backends (simulador 2D y ROS2),
así que el "conocimiento" del entorno vive en un solo archivo JSON.
"""
from __future__ import annotations

import difflib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

STOPWORDS = {"de", "del", "la", "el", "los", "las", "a", "al", "en", "the", "of", "y"}


def normalize(text: str) -> str:
    """Minúsculas, sin tildes, sin signos raros, espacios colapsados."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[¡¿\"“”'`´]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


FEMININE_TYPES = {"caja", "persona", "mochila", "taza"}
DISPLAY = {"botiquin": "botiquín"}


def object_phrase(otype, color=None) -> str:
    """'caja' + 'rojo' -> 'caja roja' (concordancia simple de género)."""
    otype = DISPLAY.get(otype, otype)
    if not color:
        return otype or "objeto"
    if otype in FEMININE_TYPES and color.endswith("o"):
        color = color[:-1] + "a"
    return f"{otype} {color}"


@dataclass
class Place:
    id: str
    name: str
    aliases: list
    center: tuple
    rect: Optional[tuple] = None
    restricted: bool = False
    sensors: dict = field(default_factory=dict)


@dataclass
class WorldObject:
    id: str
    type: str
    color: Optional[str]
    pos: tuple
    place: Optional[str] = None

    def describe(self) -> str:
        return object_phrase(self.type, self.color)


@dataclass
class Mention:
    start: int
    end: int
    alias: str
    ids: list


class WorldModel:
    def __init__(self, data: dict):
        self.data = data
        self.name = data.get("name", "mundo")
        self.frame = data.get("frame", "map")
        self.home = data["home"]
        self.robot = data.get("robot", {})
        self.places: dict[str, Place] = {}
        for p in data["places"]:
            self.places[p["id"]] = Place(
                id=p["id"], name=p["name"], aliases=p.get("aliases", []),
                center=tuple(p["center"]), rect=tuple(p["rect"]) if p.get("rect") else None,
                restricted=bool(p.get("restricted", False)), sensors=p.get("sensors", {}),
            )
        self.object_types: dict[str, list] = {k: [normalize(s) for s in v] for k, v in data.get("object_types", {}).items()}
        self.colors: dict[str, list] = {k: [normalize(s) for s in v] for k, v in data.get("colors", {}).items()}
        self.objects: list[WorldObject] = []
        for o in data.get("objects", []):
            obj = WorldObject(id=o["id"], type=o["type"], color=o.get("color"), pos=tuple(o["pos"]))
            obj.place = self.place_at(*obj.pos)
            self.objects.append(obj)
        self.sim = data.get("sim", {})

        # índice alias -> ids (un alias puede ser ambiguo, p. ej. "oficina")
        self.alias_index: dict[str, list] = {}
        for p in self.places.values():
            for a in set([p.id.replace("_", " "), p.name] + list(p.aliases)):
                key = normalize(a)
                self.alias_index.setdefault(key, [])
                if p.id not in self.alias_index[key]:
                    self.alias_index[key].append(p.id)
        self._aliases_by_len = sorted(self.alias_index, key=len, reverse=True)

    @classmethod
    def load(cls, path: str) -> "WorldModel":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    # ------------------------------------------------------------------ lugares
    def find_mentions(self, text: str) -> list[Mention]:
        """Menciones de lugares en un texto normalizado (alias más largos primero)."""
        taken: list[tuple] = []
        out: list[Mention] = []
        for alias in self._aliases_by_len:
            for m in re.finditer(rf"\b{re.escape(alias)}\b", text):
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue
                taken.append((m.start(), m.end()))
                out.append(Mention(m.start(), m.end(), alias, list(self.alias_index[alias])))
        return sorted(out, key=lambda x: x.start)

    def resolve_place(self, token: str) -> list:
        """id / nombre / alias -> lista de ids candidatos (vacía si no existe)."""
        if token in self.places:
            return [token]
        key = normalize(token)
        if key in self.alias_index:
            return list(self.alias_index[key])
        return self.fuzzy_place(key)

    def fuzzy_place(self, phrase: str, cutoff: float = 0.8) -> list:
        phrase = normalize(phrase)
        if not phrase:
            return []
        best = difflib.get_close_matches(phrase, list(self.alias_index), n=1, cutoff=cutoff)
        return list(self.alias_index[best[0]]) if best else []

    def _tokens(self, pid: str) -> set:
        p = self.places[pid]
        toks = set()
        for a in [p.name] + list(p.aliases):
            toks |= set(normalize(a).split())
        return toks - STOPWORDS

    def disambiguate(self, ids: list, text: str) -> Optional[str]:
        """Si el texto contiene una palabra que solo tiene un candidato, lo elige."""
        words = set(normalize(text).split())
        common = set.intersection(*(self._tokens(i) for i in ids)) if ids else set()
        hits = [i for i in ids if (self._tokens(i) - common) & words]
        return hits[0] if len(hits) == 1 else None

    def place_at(self, x: float, y: float) -> Optional[str]:
        best, best_area = None, math.inf
        for p in self.places.values():
            if not p.rect:
                continue
            x0, y0, x1, y1 = p.rect
            if x0 <= x <= x1 and y0 <= y <= y1:
                area = (x1 - x0) * (y1 - y0)
                if area < best_area:
                    best, best_area = p.id, area
        return best

    def viewpoints(self, pid: str, inset: float = 1.4) -> list:
        """Puntos de observación para buscar dentro de un lugar."""
        p = self.places[pid]
        pts = [p.center]
        if p.rect:
            x0, y0, x1, y1 = p.rect
            if (x1 - x0) > 2 * inset + 1 and (y1 - y0) > 2 * inset + 1:
                pts += [(x0 + inset, y0 + inset), (x1 - inset, y0 + inset),
                        (x1 - inset, y1 - inset), (x0 + inset, y1 - inset)]
        return pts

    def display(self, pid: str) -> str:
        return self.places[pid].name if pid in self.places else pid

    # ------------------------------------------------------------------ objetos
    def match_object_type(self, text: str) -> Optional[tuple]:
        """-> (tipo, (start, end)) del primer tipo de objeto mencionado."""
        best = None
        for t, syns in self.object_types.items():
            for s in syns:
                m = re.search(rf"\b{re.escape(s)}\b", text)
                if m and (best is None or m.start() < best[1][0]):
                    best = (t, (m.start(), m.end()))
        return best

    def canonical_type(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        key = normalize(token)
        for t, syns in self.object_types.items():
            if key == t or key in syns:
                return t
        return None

    def match_color(self, text: str) -> Optional[str]:
        for c, variants in self.colors.items():
            for v in variants:
                if re.search(rf"\b{re.escape(v)}\b", text):
                    return c
        return None

    def canonical_color(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        key = normalize(token)
        for c, variants in self.colors.items():
            if key == c or key in variants:
                return c
        return None

    # ------------------------------------------------------------------ LLM
    def llm_summary(self) -> str:
        lines = ["LUGARES CONOCIDOS (usa SIEMPRE el id):"]
        for p in self.places.values():
            tag = " [RESTRINGIDO: requiere autorización]" if p.restricted else ""
            lines.append(f"- id={p.id} | nombre={p.name} | alias={', '.join(p.aliases)}{tag}")
        lines.append(f"Base / casa del robot: id={self.home}")
        lines.append("OBJETOS QUE EL ROBOT SABE RECONOCER (object_type): " + ", ".join(self.object_types))
        lines.append("COLORES (object_color): " + ", ".join(self.colors))
        lines.append("MEDIDAS (measure): temperatura (°C), humedad (%), general (temperatura + humedad + objetos a la vista)")
        return "\n".join(lines)
