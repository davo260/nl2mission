def make_backend(kind: str, world, **kw):
    if kind == "sim2d":
        from .sim2d import Sim2DBackend
        return Sim2DBackend(world, **kw)
    if kind == "ros2":
        from .ros2_nav2 import Ros2Nav2Backend
        return Ros2Nav2Backend(world, **kw)
    raise ValueError(f"backend desconocido: {kind}")
