"""Demo guionizada para la presentación: corre una conversación completa en el
simulador 2D, imprime la transcripción y genera un GIF por misión ejecutada.

    python scripts/demo_presentacion.py                      # parser offline (reglas)
    python scripts/demo_presentacion.py --parser anthropic   # Claude (ANTHROPIC_API_KEY)
    python scripts/demo_presentacion.py --parser ollama      # modelo local
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nl2mission import MissionAgent, WorldModel  # noqa: E402
from nl2mission.backends.sim2d import Sim2DBackend  # noqa: E402
from nl2mission.parsers import make_parser  # noqa: E402
from nl2mission.schema import plan_lines  # noqa: E402

GUION = [
    ("navegación + medición", ["Ve al laboratorio y mide la temperatura y la humedad"]),
    ("búsqueda condicional", ["Busca la caja roja en la bodega; si la encuentras avísame y vuelve a la base, "
                              "si no revisa el laboratorio"]),
    ("ambigüedad → pregunta", ["Ve a la oficina", "la del norte"]),
    ("zona restringida → confirmación humana", ["Revisa la temperatura de la sala de servidores y si pasa de "
                                                "30 grados avísame", "sí, estoy autorizado"]),
    ("acción imposible → rechazo con alternativa", ["Tráeme un café de la cocina"]),
    ("lugar desconocido → pregunta", ["Ve a la terraza", "mejor a la cocina"]),
    ("referencia contextual ('ahí')", ["busca un extintor ahí"]),
    ("objeto desconocido → pregunta", ["busca a Juan", "una persona"]),
    ("patrulla", ["Patrulla el pasillo, el laboratorio y la bodega dos veces y luego vuelve a la base"]),
    ("límite de batería → rechazo", ["Patrulla todo cinco veces"]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parser", default="rules", choices=["rules", "anthropic", "ollama"])
    ap.add_argument("--world", default=os.path.join(os.path.dirname(__file__), "..", "config", "world_lab.json"))
    ap.add_argument("--out", default="demo_out")
    ap.add_argument("--no-gif", action="store_true")
    ap.add_argument("--mp4", action="store_true", help="además del GIF, exportar MP4")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    world = WorldModel.load(args.world)
    sim = Sim2DBackend(world)
    agent = MissionAgent(world, make_parser(args.parser, world), sim, log=lambda t: print(f"      {t}"))

    transcript = ["# Transcripción de la demo\n", f"Parser: `{args.parser}`\n"]
    renders = []
    for n, (scene, turns) in enumerate(GUION, 1):
        print(f"\n=== Escena {n}: {scene} ===")
        transcript.append(f"\n## Escena {n}: {scene}\n")
        dialog = []
        for user in turns:
            print(f"  Usuario: {user}")
            reply = agent.handle(user)
            dialog += [("Usuario", user)]
            transcript.append(f"- **Usuario:** {user}")
            if reply.kind == "execute":
                print(f"  Agente : (ejecuta) {reply.message}")
                for lvl, txt, _ in plan_lines(reply.result.steps, world):
                    print("           " + "  " * lvl + txt)
                transcript.append(f"- **Agente:** {reply.message}")
                transcript.append("\n```json\n" + json.dumps(reply.result.steps, ensure_ascii=False, indent=2) + "\n```")
                dialog += [("Agente", "Entendido. " + reply.message)]
                report = agent.execute(reply)
                transcript.append("\nEjecución: " + " · ".join(report.events) + "\n")
                renders.append((len(sim.missions) - 1, list(dialog), f"escena{n:02d}"))
            else:
                print(f"  Agente : ({reply.kind}) {reply.message}")
                transcript.append(f"- **Agente ({reply.kind}):** {reply.message}")
                dialog += [("Agente", reply.message)]

    with open(os.path.join(args.out, "transcripcion.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(transcript) + "\n")

    if not args.no_gif:
        from nl2mission.render import render_mission
        for idx, dialog, name in renders:
            path = render_mission(sim, idx, dialog, os.path.join(args.out, f"{name}.gif"))
            print("GIF:", path)
            if args.mp4:
                render_mission(sim, idx, dialog, os.path.join(args.out, f"{name}.mp4"))


if __name__ == "__main__":
    main()
