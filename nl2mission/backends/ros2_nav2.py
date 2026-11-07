"""Backend ROS2 + Nav2 (Humble o Jazzy) para correr el mismo agente en Gazebo o en un robot real.

- Navegación: nav2_simple_commander.BasicNavigator (NavigateToPose).
- Pose del robot: TF map -> base_link (con /amcl_pose como respaldo).
- Percepción (MVP): "oráculo" con los objetos del JSON del mundo y un rango de sensado,
  o un detector real que publique en /nl2mission/detections (std_msgs/String con JSON):
      [{"type": "caja", "color": "rojo", "x": 1.2, "y": -0.4}, ...]
- Sensores: sensor_msgs/Temperature y sensor_msgs/RelativeHumidity si existen;
  si no llega nada, usa los valores del JSON (y lo avisa una vez).
- Batería: estimada por distancia recorrida (sustituible por sensor_msgs/BatteryState).
"""
from __future__ import annotations

import json
import math
import threading
import time
from typing import Callable, Optional

import rclpy
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import RelativeHumidity, Temperature
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener

from ..world import WorldModel


class _IO(Node):
    def __init__(self, base_frame: str, map_frame: str):
        super().__init__("nl2mission_io")
        self.base_frame, self.map_frame = base_frame, map_frame
        self.tf = Buffer()
        self.tf_listener = TransformListener(self.tf, self)
        self.amcl = None
        self.temp = None
        self.hum = None
        self.detections = None
        self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self._amcl_cb, 10)
        self.create_subscription(Temperature, "/nl2mission/temperature", lambda m: setattr(self, "temp", m.temperature), 10)
        self.create_subscription(RelativeHumidity, "/nl2mission/humidity",
                                 lambda m: setattr(self, "hum", m.relative_humidity * 100.0), 10)
        self.create_subscription(String, "/nl2mission/detections", self._det_cb, 10)
        self.speech_pub = self.create_publisher(String, "/nl2mission/speech", 10)
        self.event_pub = self.create_publisher(String, "/nl2mission/events", 10)

    def _amcl_cb(self, m):
        self.amcl = (m.pose.pose.position.x, m.pose.pose.position.y)

    def _det_cb(self, m):
        try:
            self.detections = (time.time(), json.loads(m.data))
        except json.JSONDecodeError:
            self.get_logger().warn("detections: JSON inválido")

    def pose(self):
        try:
            t = self.tf.lookup_transform(self.map_frame, self.base_frame, Time())
            return (t.transform.translation.x, t.transform.translation.y)
        except Exception:  # noqa: BLE001 - TF todavía no disponible
            return self.amcl


class Ros2Nav2Backend:
    def __init__(self, world: WorldModel, base_frame: str = "base_link", set_initial_pose: bool = True,
                 oracle_perception: bool = True, nav_timeout: float = 180.0):
        self.world = world
        self.oracle = oracle_perception
        self.nav_timeout = nav_timeout
        rb = world.robot
        self.range = rb.get("sensor_range", 3.5)
        self.consumption = rb.get("consumption_per_m", 0.25)
        self._battery = float(rb.get("battery", 100.0))
        self._warned = set()

        if not rclpy.ok():
            rclpy.init(args=["--ros-args", "-p", "use_sim_time:=true"])
        self.io = _IO(base_frame, world.frame)
        self._exec = SingleThreadedExecutor()
        self._exec.add_node(self.io)
        threading.Thread(target=self._exec.spin, daemon=True).start()

        self.nav = BasicNavigator()
        if set_initial_pose and rb.get("start"):
            self.nav.setInitialPose(self._pose_msg(rb["start"], 0.0))
            self.nav.waitUntilNav2Active()
        else:
            # La pose ya se dio en RViz: no esperar /amcl_pose (BasicNavigator re-publicaría una pose en el origen)
            pass  # Nav2 ya activo; goToPose espera al servidor de acciones
        self._last = None
        threading.Thread(target=self._odometer, daemon=True).start()

    # ------------------------------------------------------------------ helpers
    def _pose_msg(self, xy, yaw):
        p = PoseStamped()
        p.header.frame_id = self.world.frame
        p.header.stamp = self.nav.get_clock().now().to_msg()
        p.pose.position.x, p.pose.position.y = float(xy[0]), float(xy[1])
        p.pose.orientation.z = math.sin(yaw / 2)
        p.pose.orientation.w = math.cos(yaw / 2)
        return p

    def _odometer(self):
        while rclpy.ok():
            p = self.io.pose()
            if p and self._last:
                self._battery = max(0.0, self._battery - math.dist(p, self._last) * self.consumption)
            self._last = p or self._last
            time.sleep(0.5)

    def _warn_once(self, key, text):
        if key not in self._warned:
            self._warned.add(key)
            self.on_event(f"⚠ {text}")

    # ------------------------------------------------------------------ interfaz backend
    def pose(self):
        p = self.io.pose()
        return p if p else tuple(self.world.robot.get("start", (0.0, 0.0)))

    def battery(self) -> float:
        return self._battery

    def current_place(self):
        return self.world.place_at(*self.pose())

    def estimate_distance(self, a, b) -> float:
        return math.dist(a, b) * 1.3          # factor por rodeos; se puede cambiar por nav.getPath()

    def navigate_to(self, goal, label: str = "", stop_when: Optional[Callable] = None) -> str:
        here = self.pose()
        yaw = math.atan2(goal[1] - here[1], goal[0] - here[0])
        if not self.nav.goToPose(self._pose_msg(goal, yaw)):
            return "failed"
        t0 = time.time()
        while not self.nav.isTaskComplete():
            if stop_when and stop_when():
                self.nav.cancelTask()
                return "stopped"
            if time.time() - t0 > self.nav_timeout:
                self.nav.cancelTask()
                return "failed"
            time.sleep(0.05)
        if stop_when and stop_when():
            return "stopped"
        return "arrived" if self.nav.getResult() == TaskResult.SUCCEEDED else "failed"

    def detect(self) -> list:
        here = self.pose()
        if self.io.detections and time.time() - self.io.detections[0] < 2.0:
            out = []
            for d in self.io.detections[1]:
                pos = (d["x"], d["y"])
                out.append({"id": d.get("id", f"{d['type']}@{pos}"), "type": d["type"], "color": d.get("color"),
                            "pos": pos, "place": self.world.place_at(*pos)})
            return out
        if not self.oracle:
            return []
        self._warn_once("oracle", "Percepción en modo oráculo (objetos del JSON + rango de sensado)")
        return [{"id": o.id, "type": o.type, "color": o.color, "pos": o.pos, "place": o.place}
                for o in self.world.objects if math.dist(here, o.pos) <= self.range]

    def read_sensor(self, measure: str) -> float:
        live = self.io.temp if measure == "temperatura" else self.io.hum
        if live is not None:
            return round(float(live), 1)
        self._warn_once(measure, f"Sin tópico de {measure}: uso el valor de referencia del JSON")
        pid = self.current_place()
        return float(self.world.places[pid].sensors.get(measure, 0.0)) if pid else 0.0

    def say(self, msg: str):
        self.io.speech_pub.publish(String(data=msg))

    def wait(self, seconds: float):
        time.sleep(seconds)

    def on_event(self, text: str):
        self.io.event_pub.publish(String(data=text))

    def shutdown(self):
        self.nav.destroy_node()
        self.io.destroy_node()
        rclpy.shutdown()
