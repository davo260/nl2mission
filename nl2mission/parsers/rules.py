"""Parser offline basado en reglas (español + algo de inglés).

No pretende competir con el LLM: sirve para demos sin internet ni API key,
como línea base en la presentación y para probar el resto del pipeline.
"""
from __future__ import annotations

import re
from typing import Optional

from ..schema import ParseResult, summarize
from ..world import WorldModel, normalize
from .base import Context, Parser, Pending, clarify, reject

SEQ = r"(?:y luego|y despues|y finalmente|luego|despues|finalmente|al final|and then|then|after that)"
SPLIT_RE = re.compile(rf"(\s*,\s*|\s+{SEQ}\s+|\s+(?:y|e|and)\s+)")
LEAD_SEQ_RE = re.compile(rf"^(?:y\s+)?{SEQ}\b\s*")
FILLER_RE = re.compile(r"^(?:por favor|porfa|porfavor|please|robot|oye|hey|primero|first|ahora|now|necesito que|quiero que|puedes|podrias)\b[\s,]*")

V_UNSUPPORTED = r"\b(traeme|trae|traer|traiga|agarra|agarrar|coge|coger|recoge|recoger|levanta|levantar|abre|abrir|cierra|cerrar|empuja|empujar|limpia|limpiar|mueve la|mueve el|bring|pick up|grab|open|close|clean|fetch)\b"
V_PATROL = r"\b(patrulla|patrullar|recorre|recorrer|haz una ronda|ronda|patrol)\b"
V_FIND = r"\b(busca|buscar|buscame|encuentra|encontrar|encuentrame|localiza|localizar|ubica|ubicar|find|look for|search for|search)\b"
V_INSPECT = r"\b(mide|medir|revisa|revisar|verifica|verificar|inspecciona|inspeccionar|chequea|chequear|monitorea|comprueba|check|measure|inspect)\b"
V_SAY = r"\b(avisame|avisa|avisar|dime|reporta|reportame|reportar|notificame|notifica|informame|informa|alerta|alertame|notify|tell me|report|alert me|let me know)\b"
V_WAIT = r"\b(espera|esperar|esperate|aguarda|quedate|wait|stay)\b"
V_HOME = r"\b(vuelve|volver|regresa|regresar|retorna|recargate|recarga|a cargar|carga la bateria|return|go back|come back|recharge)\b"
V_NAV = r"\b(ve|vaya|vayas|ir|anda|andate|dirigete|desplazate|muevete|acercate|pasa|visita|visitar|entra|go|move|navigate|head|drive)\b"
HOME_WORDS = r"\b(base|casa|estacion de carga|cargador|dock|home)\b"
PRONOUN = r"\b(ahi|alli|alla|there|ese lugar|ese sitio)\b"
ALL_WORDS = r"\b(todo el edificio|todas partes|todo|todas|todos|everywhere|anywhere|all)\b"
PREP_RE = re.compile(r"\b(?:a la|al|a los|a las|a|hacia la|hacia el|hacia|en la|en el|en|del|de la|por la|por el|to the|to|in the|in)\s+(?:la |el |los |las |the )?([a-z0-9 ]+)$")

NUM = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
       "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "one": 1, "two": 2, "three": 3, "five": 5}

FOUND_RE = re.compile(r"^(?:si|if|cuando|when)\s+(?:la |lo |las |los |it |you )?(?:encuentras|encuentra|ves|hallas|detectas|localizas|esta|hay|find|see|found)\b\s*(?:(?:la|lo|it)\b\s*)?")
ELSE_RE = re.compile(r"^(?:si no|sino|de lo contrario|en caso contrario|otherwise|if not|else)\b\s*(?:(?:la|lo|las|los|it)\s+)?(?:(?:encuentras|encuentra|ves|hallas|esta|hay|pasa|supera|find)\b\s*)?")
_ABOVE = r"pasa de|pasa los|supera los|supera|es mayor a|es mayor que|esta por encima de|por encima de|mayor a|mayor que|mas de|excede|sube de|above|exceeds|greater than|is over|over|>"
_BELOW = r"baja de|es menor a|es menor que|esta por debajo de|por debajo de|menor a|menor que|menos de|below|less than|under|<"
THRESH_RE = re.compile(rf"^(?:si|if|cuando|when)\b(?P<pre>.*?)(?P<op>{_ABOVE}|{_BELOW})\s*(?P<val>-?\d+(?:[.,]\d+)?)\s*(?:grados centigrados|grados celsius|grados|°c|ºc|°|c\b|%|por ciento|percent|degrees)?\s*")


def _measure(text: str) -> Optional[str]:
    if re.search(r"\b(temperatura|temperature|calor)\b", text):
        return "temperatura"
    if re.search(r"\b(humedad|humidity)\b", text):
        return "humedad"
    return None


class RulesParser(Parser):
    name = "rules"

    def __init__(self, world: WorldModel):
        self.world = world

    # ------------------------------------------------------------------ API
    def parse(self, text: str, ctx: Context, history=None) -> ParseResult:
        norm = normalize(text)
        self._ctx = ctx
        self._last_place = ctx.last_place or ctx.robot_place
        steps: list = []
        sentences = [s.strip() for s in re.split(r"(?<!\d)[.;!?\n]+(?!\d)", norm) if s.strip()]
        for sent in sentences:
            res = self._sentence(sent, steps)
            if res is not None:
                res.meta.setdefault("text", norm)
                res.source = self.name
                return res
        if not steps:
            r = clarify("No entendí qué quieres que haga. Puedo ir a un lugar, buscar objetos, medir temperatura/humedad, patrullar o esperar.", text=norm)
            r.source = self.name
            return r
        _strip_private(steps)
        summary = summarize(steps, self.world)
        return ParseResult("ok", summary=summary, steps=steps, meta={"text": norm}, source=self.name)

    def followup(self, pending: Pending, reply: str, ctx: Context) -> ParseResult:
        meta = pending.result.meta
        kind = meta.get("kind")
        base = meta.get("text") or normalize(pending.text)
        r = normalize(reply)
        new = None
        if kind == "ambiguous_place":
            pid = self.world.disambiguate(meta["options"], r)
            if not pid:
                for m in self.world.find_mentions(r):
                    hit = [i for i in m.ids if i in meta["options"]]
                    if len(hit) == 1:
                        pid = hit[0]
                        break
            if pid:
                new = _replace_word(base, meta["span"], normalize(self.world.places[pid].name))
        elif kind == "unknown_place":
            ms = self.world.find_mentions(r)
            ids = ms[0].ids if ms else self.world.fuzzy_place(r)
            if ids:
                name = normalize(self.world.places[ids[0]].name) if len(ids) == 1 else ms[0].alias
                new = _replace_word(base, meta["span"], name)
        elif kind == "unknown_object":
            new = _replace_word(base, meta["span"], r)
        if new is None:
            new = f"{base} {r}"
        return self.parse(new, ctx)

    # ------------------------------------------------------------------ frases
    def _sentence(self, sent: str, steps: list) -> Optional[ParseResult]:
        m = re.search(V_PATROL, sent)
        if m:
            seq = re.search(rf"\s+{SEQ}\s+", sent[m.end():])
            part = sent if not seq else sent[: m.end() + seq.start()]
            rest = "" if not seq else sent[m.end() + seq.end():]
            r = self._patrol(part, steps)
            if r is not None:
                return r
            return self._segments(rest, steps) if rest.strip() else None
        return self._segments(sent, steps)

    def _segments(self, sent: str, steps: list) -> Optional[ParseResult]:
        parts = SPLIT_RE.split(sent)
        segs, seq_flag = [], False
        for i, p in enumerate(parts):
            if i % 2 == 1:
                seq_flag = seq_flag or bool(re.search(SEQ, p))
                continue
            p = p.strip()
            while True:
                q = FILLER_RE.sub("", p)
                lead = LEAD_SEQ_RE.match(q)
                if lead:
                    seq_flag = True
                    q = q[lead.end():]
                if q == p:
                    break
                p = q.strip()
            if p:
                segs.append((p, seq_flag))
                seq_flag = False

        mode = None          # None | ("then"|"else", step)
        prev = None
        for seg, seqf in segs:
            if seqf:
                mode = None
            em = ELSE_RE.match(seg)
            if em:
                target = mode[1] if mode else _last_conditional(steps)
                if target is None:
                    return clarify("Dijiste 'si no', pero no sé a qué condición se refiere.")
                mode = ("else", target)
                seg = seg[em.end():].strip()
                if not seg:
                    continue
            else:
                cond = self._condition(seg, steps)
                if isinstance(cond, ParseResult):
                    return cond
                if cond is not None:
                    target, seg = cond
                    mode = ("then", target)
                    if not seg:
                        continue
            dest = steps if mode is None else mode[1].setdefault(mode[0], [])
            r = self._action(seg, dest, prev)
            if isinstance(r, ParseResult):
                return r
            if mode and r == "say" and dest[-1]["message"] == "{resultado}" and mode[1]["action"] == "inspect":
                dest[-1]["message"] = "Alerta: {resultado}"
            prev = r
        return None

    def _condition(self, seg: str, steps: list):
        fm = FOUND_RE.match(seg)
        tm = None if fm else THRESH_RE.match(seg)
        if not fm and not tm:
            return None
        last = steps[-1] if steps else None
        if fm:
            if not last or last["action"] != "find_object":
                return clarify("Dijiste 'si lo encuentras', pero no me pediste buscar nada antes. ¿Qué busco?")
            return last, seg[fm.end():].strip()
        if not last or last["action"] != "inspect":
            pre = tm.group("pre")
            pid, clar = self._place(pre, allow_none=True) if self.world.find_mentions(pre) else (None, None)
            if clar:
                return clar
            if pid is None and last and last["action"] == "navigate_to":
                pid = last["target"]
            if pid is None and _measure(pre):
                pid = self._last_place
            if pid is None:
                return clarify("Me diste una condición con un valor, pero no sé qué medir ni dónde. ¿Qué mido?")
            last = {"action": "inspect", "target": pid, "measure": _measure(pre) or "temperatura"}
            steps.append(last)
        op = "<" if re.fullmatch(_BELOW, tm.group("op")) else ">"
        last["threshold_op"] = op
        last["threshold_value"] = float(tm.group("val").replace(",", "."))
        m = _measure(tm.group("pre"))
        if m:
            last["measure"] = m
        elif last.get("measure", "general") == "general":
            last["measure"] = "temperatura"
        return last, seg[tm.end():].strip()

    # ------------------------------------------------------------------ acciones
    def _action(self, seg: str, dest: list, prev: Optional[str]):
        w = self.world
        bad = re.search(V_UNSUPPORTED, seg)
        if bad:
            return reject(f"No puedo '{bad.group(1)}': soy un robot móvil sin brazo, no manipulo objetos. "
                          "Sí puedo ir al lugar, buscar el objeto y avisarte dónde está.")

        if re.search(V_WAIT, seg):
            secs = _duration(seg)
            if secs is None:
                return clarify("¿Cuánto tiempo espero?", kind="missing_duration")
            dest.append({"action": "wait", "seconds": secs})
            return "wait"

        if re.search(V_FIND, seg) or (re.search(V_INSPECT, seg) and not _measure(seg) and w.match_object_type(seg)):
            return self._find(seg, dest)

        measure = _measure(seg)
        if re.search(V_INSPECT, seg) or (measure and re.search(V_SAY, seg)):
            r = self._inspect(seg, dest, measure or "general")
            if isinstance(r, ParseResult):
                return r
            if re.search(V_SAY, seg):
                dest.append({"action": "say", "message": "{resultado}"})
            return "inspect"

        if re.search(V_SAY, seg):
            qm = re.search(r"\bque\s+(.+)$", seg)
            dest.append({"action": "say", "message": qm.group(1) if qm else "{resultado}"})
            return "say"

        nav = re.search(V_NAV, seg)
        if re.search(V_HOME, seg) or (nav and re.search(HOME_WORDS, seg)):
            pid, clar = self._place(seg, allow_none=True)
            if clar:
                return clar
            if pid is None or pid == w.home:
                dest.append({"action": "return_home"})
                self._last_place = w.home
                return "return_home"
            dest.append({"action": "navigate_to", "target": pid})
            return "navigate_to"

        if nav:
            pid, clar = self._place(seg)
            if clar:
                return clar
            dest.append({"action": "navigate_to", "target": pid})
            return "navigate_to"

        # segmento sin verbo: hereda la acción anterior ("mide la temperatura y la humedad")
        if measure:
            pid, clar = self._place(seg, allow_none=True)
            if clar:
                return clar
            if pid:
                for s in reversed(dest):
                    if s["action"] == "inspect" and s.get("_implicit"):
                        s["target"] = pid
                        s.pop("_implicit")
                        break
            dest.append({"action": "inspect", "target": pid or self._last_place, "measure": measure})
            return "inspect"
        pid, clar = self._place(seg, allow_none=True)
        if clar:
            return clar
        if pid:
            if prev == "inspect":
                last = next((s for s in reversed(dest) if s["action"] == "inspect"), None)
                dest.append({"action": "inspect", "target": pid, "measure": last["measure"] if last else "general"})
                return "inspect"
            dest.append({"action": "navigate_to", "target": pid})
            return "navigate_to"
        return clarify(f"No entendí esta parte: '{seg}'. ¿Me lo dices de otra forma?", kind="not_understood")

    def _find(self, seg: str, dest: list):
        w = self.world
        ot = w.match_object_type(seg)
        if not ot:
            vm = re.search(V_FIND, seg) or re.search(V_INSPECT, seg)
            phrase = seg[vm.end():] if vm else seg
            phrase = re.split(r"\s+(?:en|in)\s+", phrase)[0]
            phrase = re.sub(r"^(?:a |al |el |la |los |las |un |una |unos |unas |si hay )+", "", phrase.strip())
            return clarify(f"No sé reconocer '{phrase}'. Puedo buscar: {', '.join(w.object_types)}. ¿Cuál de esos?",
                           kind="unknown_object", span=phrase)
        otype, (_, end) = ot
        color = w.match_color(seg[end:]) or w.match_color(seg)
        step = {"action": "find_object", "object_type": otype}
        if color:
            step["object_color"] = color
        after = seg[end:]
        if re.search(PRONOUN, after):
            if not self._last_place:
                return clarify("¿En qué lugar busco?", kind="missing_place")
            step["target"] = self._last_place
        elif re.search(ALL_WORDS, after):
            pass
        else:
            am = re.search(r"\b(?:en|dentro de|por|in|inside)\s+.+$", after)
            if am:
                pid, clar = self._place(am.group(0))
                if clar:
                    return clar
                step["target"] = pid
            elif w.find_mentions(seg):          # "ve al almacén a buscar una mochila"
                pid, clar = self._place(seg)
                if clar:
                    return clar
                step["target"] = pid
        dest.append(step)
        return "find_object"

    def _inspect(self, seg: str, dest: list, measure: str):
        pid, clar = self._place(seg, allow_none=True)
        if clar:
            return clar
        step = {"action": "inspect", "measure": measure}
        if pid:
            step["target"] = pid
        else:
            if not self._last_place:
                return clarify("¿Dónde quieres que mida?", kind="missing_place")
            step["target"] = self._last_place
            step["_implicit"] = True
        dest.append(step)
        return "inspect"

    def _patrol(self, text: str, steps: list):
        w = self.world
        laps = _laps(text) or 1
        if re.search(ALL_WORDS, text):
            places = [p.id for p in w.places.values() if not p.restricted and p.id != w.home]
        else:
            places = []
            for m in w.find_mentions(text):
                if len(m.ids) == 1:
                    places.append(m.ids[0])
                else:
                    pid = w.disambiguate(m.ids, text)
                    if not pid:
                        opts = " o ".join(w.display(i) for i in m.ids)
                        return clarify(f"'{m.alias}' puede ser {opts}. ¿Cuál incluyo en la patrulla?",
                                       kind="ambiguous_place", span=m.alias, options=m.ids)
                    places.append(pid)
        if not places:
            return clarify("¿Qué lugares quieres que patrulle?", kind="missing_place")
        steps.append({"action": "patrol", "places": places, "laps": laps})
        self._last_place = places[-1]
        return None

    def _place(self, seg: str, allow_none: bool = False):
        w = self.world
        if re.search(PRONOUN, seg):
            if self._last_place:
                return self._last_place, None
            return None, clarify("¿A qué lugar te refieres con 'ahí'?", kind="missing_place")
        ms = w.find_mentions(seg)
        if ms:
            m = ms[0]
            pid = m.ids[0] if len(m.ids) == 1 else w.disambiguate(m.ids, seg)
            if not pid:
                opts = " o ".join(w.display(i) for i in m.ids)
                return None, clarify(f"Hay varios lugares que coinciden con '{m.alias}': {opts}. ¿A cuál te refieres?",
                                     kind="ambiguous_place", span=m.alias, options=m.ids)
            self._last_place = pid
            return pid, None
        pm = PREP_RE.search(seg)
        if pm:
            phrase = pm.group(1).strip()
            if not (_measure(phrase) or w.match_object_type(phrase)):
                ids = w.fuzzy_place(phrase)
                if len(ids) == 1:
                    self._last_place = ids[0]
                    return ids[0], None
                known = ", ".join(p.name for p in w.places.values())
                return None, clarify(f"No conozco '{phrase}'. Los lugares que conozco son: {known}. ¿A cuál voy?",
                                     kind="unknown_place", span=phrase)
        if allow_none:
            return None, None
        return None, clarify("¿A qué lugar quieres que vaya?", kind="missing_place")


# ---------------------------------------------------------------------- utilidades
def _duration(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:[.,]\d+)?|" + "|".join(NUM) + r")\s*(segundos?|seg|s|minutos?|min|seconds?|minutes?)\b", text)
    if not m:
        return None
    raw = m.group(1)
    val = float(raw.replace(",", ".")) if raw[0].isdigit() else float(NUM[raw])
    return val * 60 if m.group(2).startswith("min") else val


def _laps(text: str) -> Optional[int]:
    if re.search(r"\btwice\b", text):
        return 2
    m = re.search(r"\b(\d+|" + "|".join(NUM) + r")\s+(veces|vez|vueltas?|rondas?|times)\b", text)
    if not m:
        return None
    raw = m.group(1)
    return int(raw) if raw.isdigit() else NUM[raw]


def _last_conditional(steps: list):
    for s in reversed(steps):
        if s["action"] == "find_object" or (s["action"] == "inspect" and s.get("threshold_op")):
            return s
    return None


def _replace_word(text: str, old: str, new: str) -> str:
    return re.sub(rf"\b{re.escape(old)}\b", new, text, count=1)


def _strip_private(steps: list):
    for s in steps:
        for k in [k for k in s if k.startswith("_")]:
            s.pop(k)
        for key in ("then", "else"):
            if s.get(key):
                _strip_private(s[key])
