"""Corre la demo guionizada y exporta la traza (mundo + cuadros + diálogos) a JSON para el visor 3D."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nl2mission import MissionAgent, WorldModel  # noqa: E402
from nl2mission.backends.sim2d import Sim2DBackend  # noqa: E402
from nl2mission.parsers import make_parser  # noqa: E402
from nl2mission.schema import plan_lines  # noqa: E402
from demo_presentacion import GUION  # noqa: E402


def export(parser="rules", out="demo_out/traza.json"):
    here = os.path.dirname(__file__)
    w = WorldModel.load(os.path.join(here, "..", "config", "world_lab.json"))
    sim = Sim2DBackend(w)
    agent = MissionAgent(w, make_parser(parser, w), sim, log=None)
    scenes = []
    for n, (title, turns) in enumerate(GUION, 1):
        dialog, mission = [], None
        for u in turns:
            r = agent.handle(u)
            dialog.append(["Usuario", u])
            if r.kind == "execute":
                dialog.append(["Agente", "Entendido. " + r.message])
                agent.execute(r)
                mission = len(sim.missions) - 1
            else:
                dialog.append(["Agente", r.message])
        sc = {"n": n, "title": title, "dialog": dialog, "mission": mission}
        if mission is not None:
            res = sim.missions[mission][1]
            start = sim.missions[mission][0]
            end = sim.missions[mission + 1][0] if mission + 1 < len(sim.missions) else len(sim.frames)
            sc["plan"] = [[lvl, txt, list(map(str, path))] for lvl, txt, path in plan_lines(res.steps, w)]
            sc["steps"] = res.steps
            sc["frames"] = [{"t": round(f["t"], 1), "x": round(f["pos"][0], 2), "y": round(f["pos"][1], 2),
                             "h": round(f["heading"], 3), "b": round(f["battery"], 1),
                             "a": list(map(str, f["active"])) if f["active"] else None,
                             "e": f["events"], "s": f["speech"], "seen": f["seen"]} for f in sim.frames[start:end]]
        scenes.append(sc)
    # paredes como segmentos con huecos de puertas
    world = {
        "width": w.sim["width"], "height": w.sim["height"], "res": sim.res,
        "occ": [[int(v) for v in row] for row in sim.occ.tolist()],
        "places": [{"id": p.id, "name": p.name, "rect": p.rect, "center": p.center, "restricted": p.restricted}
                   for p in w.places.values()],
        "home": w.home,
        "objects": [{"id": o.id, "type": o.type, "color": o.color, "pos": o.pos, "label": o.describe()}
                    for o in w.objects],
        "range": sim.range,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"world": world, "scenes": scenes, "parser": parser}, f, ensure_ascii=False)
    return out


if __name__ == "__main__":
    print(export(*(sys.argv[1:2] or ["rules"])))
