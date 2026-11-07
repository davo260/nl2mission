"""Crea el JSON del mundo para Gazebo / robot real haciendo clic en RViz.

Uso (con la simulación y RViz abiertos):
    python3 scripts/capturar_lugares.py --out config/world_gazebo.json

En la consola escribe:
    lugar laboratorio            -> luego clic con "Publish Point" en RViz (centro del lugar)
    lugar base                   -> el primer lugar llamado 'base' será la casa del robot
    objeto caja rojo             -> clic donde está el objeto (para la percepción oráculo)
    restringido servidores       -> marca un lugar como restringido
    guardar                      -> escribe el JSON y sale
Cada lugar se guarda con un rectángulo de ±radio m alrededor del clic (ajústalo luego en el JSON).
"""
import argparse
import json
import queue
import threading

import rclpy
from geometry_msgs.msg import PointStamped
from rclpy.node import Node

DEFAULT_TYPES = {
    "caja": ["caja", "paquete", "box"], "extintor": ["extintor", "extinguidor"],
    "persona": ["persona", "alguien", "person"], "mochila": ["mochila", "maleta", "backpack"],
    "taza": ["taza", "pocillo", "mug"], "botiquin": ["botiquin"],
}
DEFAULT_COLORS = {"rojo": ["rojo", "roja", "red"], "azul": ["azul", "blue"], "verde": ["verde", "green"],
                  "amarillo": ["amarillo", "amarilla", "yellow"], "negro": ["negro", "negra", "black"],
                  "blanco": ["blanco", "blanca", "white"], "naranja": ["naranja", "orange"]}


class Clicks(Node):
    def __init__(self):
        super().__init__("nl2mission_capturar")
        self.q = queue.Queue()
        self.create_subscription(PointStamped, "/clicked_point", lambda m: self.q.put((m.point.x, m.point.y)), 10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="config/world_gazebo.json")
    ap.add_argument("--radio", type=float, default=1.0)
    args = ap.parse_args()
    rclpy.init()
    node = Clicks()
    from rclpy.executors import SingleThreadedExecutor
    ex = SingleThreadedExecutor()
    ex.add_node(node)
    spin = threading.Thread(target=ex.spin, daemon=True)
    spin.start()

    places, objects, start = [], [], None
    print(__doc__)
    while True:
        cmd = input("captura › ").strip().split()
        if not cmd:
            continue
        if cmd[0] == "guardar":
            break
        if cmd[0] == "restringido" and len(cmd) > 1:
            for p in places:
                if p["id"] == cmd[1]:
                    p["restricted"] = True
                    print(f"  {cmd[1]} marcado como restringido")
            continue
        if cmd[0] in ("lugar", "objeto") and len(cmd) > 1:
            print("  … haz clic en RViz (Publish Point)")
            x, y = node.q.get()
            x, y = round(x, 2), round(y, 2)
            if cmd[0] == "lugar":
                pid = "_".join(cmd[1:])
                r = args.radio
                places.append({"id": pid, "name": " ".join(cmd[1:]).capitalize(), "aliases": [" ".join(cmd[1:])],
                               "center": [x, y], "rect": [x - r, y - r, x + r, y + r], "restricted": False,
                               "sensors": {"temperatura": 22.0, "humedad": 50}})
                if pid == "base":
                    start = [x, y]
                print(f"  lugar {pid} en ({x}, {y})")
            else:
                objects.append({"id": f"{cmd[1]}_{len(objects) + 1}", "type": cmd[1],
                                "color": cmd[2] if len(cmd) > 2 else None, "pos": [x, y]})
                print(f"  objeto {cmd[1]} en ({x}, {y})")
            continue
        print("  comandos: lugar <nombre> | objeto <tipo> [color] | restringido <id> | guardar")

    home = "base" if any(p["id"] == "base" for p in places) else (places[0]["id"] if places else "base")
    data = {"name": "Mundo Gazebo", "frame": "map", "home": home,
            "robot": {"start": start or (places[0]["center"] if places else [0, 0]), "battery": 100.0,
                      "consumption_per_m": 0.25, "reserve": 15.0, "sensor_range": 2.0},
            "places": places, "object_types": DEFAULT_TYPES, "colors": DEFAULT_COLORS, "objects": objects}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Guardado en {args.out}. Agrega alias (\"lab\", \"almacén\"…) para que el agente entienda más formas de nombrarlos.")
    ex.shutdown()
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == "__main__":
    main()
