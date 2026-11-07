"""Consola interactiva del agente.

Simulador 2D (cualquier PC):
    python scripts/nl2mission_cli.py --parser rules
    python scripts/nl2mission_cli.py --parser anthropic --gif-dir salida/
ROS2 + Nav2 + Gazebo (Ubuntu, con ROS2 sourceado y la simulación corriendo):
    python3 scripts/nl2mission_cli.py --backend ros2 --world config/world_gazebo.json --parser ollama
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nl2mission import MissionAgent, WorldModel  # noqa: E402
from nl2mission.backends import make_backend  # noqa: E402
from nl2mission.parsers import make_parser  # noqa: E402
from nl2mission.schema import plan_lines  # noqa: E402


def main():
    here = os.path.dirname(__file__)
    ap = argparse.ArgumentParser(description="Agente lenguaje natural → misión robótica")
    ap.add_argument("--parser", default="rules", choices=["rules", "anthropic", "ollama"])
    ap.add_argument("--model", default=None, help="modelo del LLM (p. ej. claude-sonnet-5-5 o qwen2.5:7b)")
    ap.add_argument("--backend", default="sim2d", choices=["sim2d", "ros2"])
    ap.add_argument("--world", default=os.path.join(here, "..", "config", "world_lab.json"))
    ap.add_argument("--base-frame", default="base_link", help="ros2: frame del robot (base_link / base_footprint)")
    ap.add_argument("--no-initial-pose", action="store_true",
                    help="ros2: no publicar pose inicial (la das tú con '2D Pose Estimate' en RViz)")
    ap.add_argument("--gif-dir", default=None, help="sim2d: guardar un GIF por misión")
    ap.add_argument("--show-json", action="store_true", help="mostrar el JSON de cada misión")
    args = ap.parse_args()

    world = WorldModel.load(args.world)
    bkw = ({"base_frame": args.base_frame, "set_initial_pose": not args.no_initial_pose}
           if args.backend == "ros2" else {})
    backend = make_backend(args.backend, world, **bkw)
    pkw = {"model": args.model} if args.model and args.parser != "rules" else {}
    agent = MissionAgent(world, make_parser(args.parser, world, **pkw), backend, log=lambda t: print(f"   {t}"))

    print(f"Agente listo · parser={args.parser} · backend={args.backend} · mundo: {world.name}")
    print("Lugares:", ", ".join(p.name for p in world.places.values()))
    print("Escribe una orden (o 'salir').\n")
    dialog = []
    while True:
        try:
            text = input("tú › ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in ("salir", "exit", "quit"):
            break
        reply = agent.handle(text)
        dialog.append(("Usuario", text))
        if reply.kind != "execute":
            print(f"robot › {reply.message}\n")
            dialog.append(("Agente", reply.message))
            continue
        print(f"robot › Entendido: {reply.message}")
        for lvl, line, _ in plan_lines(reply.result.steps, world):
            print("        " + "  " * lvl + line)
        est = reply.result.meta.get("estimate")
        if est:
            print(f"        (≈{est['meters']} m, batería ≈{est['battery_needed']}%)")
        if args.show_json:
            print(json.dumps(reply.result.steps, ensure_ascii=False, indent=2))
        dialog.append(("Agente", "Entendido. " + reply.message))
        agent.execute(reply)
        if args.gif_dir and args.backend == "sim2d":
            from nl2mission.render import render_mission
            os.makedirs(args.gif_dir, exist_ok=True)
            n = len(backend.missions)
            path = render_mission(backend, n - 1, dialog[-6:], os.path.join(args.gif_dir, f"mision{n:02d}.gif"))
            print(f"   GIF: {path}")
        dialog = []
        print()
    if hasattr(backend, "shutdown"):
        backend.shutdown()


if __name__ == "__main__":
    main()
