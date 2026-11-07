"""Genera GIF / MP4 de las misiones ejecutadas en el simulador 2D."""
from __future__ import annotations

import math
import subprocess
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import patches  # noqa: E402
from PIL import Image  # noqa: E402

from .schema import plan_lines  # noqa: E402

COLORS = {"rojo": "#d62728", "azul": "#1f77b4", "verde": "#2ca02c", "amarillo": "#e3b505",
          "negro": "#222222", "blanco": "#f5f5f5", "naranja": "#ff7f0e", None: "#8e44ad"}
MARKERS = {"caja": "s", "extintor": "^", "persona": "o", "mochila": "D", "taza": "P", "botiquin": "X"}
INK, MUTED, ACCENT, ALERT = "#1d2433", "#6b7280", "#0f766e", "#b91c1c"


def render_mission(sim, index: int, dialog: list, out_path: str, every: int = 1, fps: int = 10,
                   title: str = "Agente NL → Misión · simulador 2D") -> str:
    """Renderiza la misión `index` del simulador. `dialog` = [(quién, texto)] para el panel."""
    w = sim.world
    start = sim.missions[index][0]
    end = sim.missions[index + 1][0] if index + 1 < len(sim.missions) else len(sim.frames)
    result = sim.missions[index][1]
    frames = sim.frames[start:end][::every]
    if sim.frames[end - 1] is not frames[-1]:
        frames.append(sim.frames[end - 1])
    plan = plan_lines(result.steps, w)

    fig = plt.figure(figsize=(14, 6.6), dpi=80, facecolor="white")
    ax = fig.add_axes([0.015, 0.06, 0.6, 0.84])
    side = fig.add_axes([0.63, 0.02, 0.36, 0.94])
    side.axis("off")
    fig.text(0.015, 0.95, title, fontsize=15, fontweight="bold", color=INK)

    _draw_map(ax, sim)
    trail_line, = ax.plot([], [], color=ACCENT, lw=2, alpha=0.6, zorder=3)
    sense = patches.Circle((0, 0), sim.range, fill=True, fc=ACCENT, alpha=0.06, ec=ACCENT, lw=0.8, ls="--", zorder=2)
    ax.add_patch(sense)
    sense.set_clip_path(patches.Rectangle((0, 0), sim.W * sim.res, sim.H * sim.res, transform=ax.transData))
    body = patches.Circle((0, 0), 0.38, fc=ACCENT, ec="white", lw=1.5, zorder=6)
    ax.add_patch(body)
    nose, = ax.plot([], [], color="white", lw=2.5, zorder=7)
    seen_marks = ax.scatter([], [], s=420, facecolors="none", edgecolors="#f59e0b", linewidths=2.5, zorder=5)
    bubble = ax.text(0, 0, "", fontsize=10.5, color=INK, zorder=9, ha="left", va="bottom",
                     bbox=dict(boxstyle="round,pad=0.4", fc="#fff7d6", ec="#f59e0b"))
    obj_pos = {o.id: o.pos for o in w.objects}

    images = []
    for f in frames:
        x, y = f["pos"]
        trail = sim.trail[f.get("trail_start", 0):f["trail"]]
        trail_line.set_data([p[0] for p in trail], [p[1] for p in trail])
        sense.center = (x, y)
        body.center = (x, y)
        nose.set_data([x, x + 0.38 * math.cos(f["heading"])], [y, y + 0.38 * math.sin(f["heading"])])
        seen = [obj_pos[i] for i in f["seen"]]
        seen_marks.set_offsets(seen if seen else [[math.nan, math.nan]])
        if f["speech"]:
            bubble.set_text("\n".join(textwrap.wrap("🗨 " + f["speech"], 34)).replace("🗨 ", ""))
            bubble.set_position((min(x + 0.5, 15.5), min(y + 0.5, 13.2)))
            bubble.set_visible(True)
        else:
            bubble.set_visible(False)
        _draw_side(side, f, plan, dialog, result)
        fig.canvas.draw()
        img = Image.frombuffer("RGBA", fig.canvas.get_width_height(), fig.canvas.buffer_rgba()).convert("RGB")
        images.append(img)
    plt.close(fig)

    durations = [int(1000 / fps)] * len(images)
    durations[-1] = 2500
    if out_path.endswith(".gif"):
        pal = [im.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for im in images]
        pal[0].save(out_path, save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True)
    else:
        _to_mp4(images, out_path, fps)
    return out_path


def _to_mp4(images, out_path, fps):
    w, h = images[0].size
    w, h = w - w % 2, h - h % 2
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                             "-pix_fmt", "yuv420p", out_path], stdin=subprocess.PIPE)
    for im in images + [images[-1]] * (fps * 2):
        proc.stdin.write(im.crop((0, 0, w, h)).tobytes())
    proc.stdin.close()
    proc.wait()


def _draw_map(ax, sim):
    w = sim.world
    ax.set_xlim(-0.3, sim.W * sim.res + 0.3)
    ax.set_ylim(-0.3, sim.H * sim.res + 0.3)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    for p in w.places.values():
        if not p.rect or p.id == w.home:
            continue
        x0, y0, x1, y1 = p.rect
        fc = "#fde8e8" if p.restricted else ("#f3f4f6" if p.id == "pasillo" else "#eef6f5")
        ax.add_patch(patches.Rectangle((x0, y0), x1 - x0, y1 - y0, fc=fc, ec="none", zorder=0,
                                       hatch="//" if p.restricted else None))
        label = p.name + (" 🔒" if p.restricted else "")
        ax.text(x0 + 0.75, y1 - 0.75, label.replace(" 🔒", " (restringida)") if p.restricted else label,
                fontsize=10.5, color=ALERT if p.restricted else MUTED, va="top", fontweight="bold", zorder=1)
    ax.imshow(sim.occ, origin="lower", cmap=matplotlib.colors.ListedColormap([(0, 0, 0, 0), INK]),
              extent=(0, sim.W * sim.res, 0, sim.H * sim.res), interpolation="nearest", zorder=1)
    hx, hy = w.places[w.home].center
    ax.add_patch(patches.FancyBboxPatch((hx - 0.7, hy - 0.7), 1.4, 1.4, boxstyle="round,pad=0.05",
                                        fc="#dbeafe", ec="#2563eb", lw=1.5, zorder=1))
    ax.text(hx, hy - 1.0, "Base", ha="center", va="top", fontsize=9, color="#2563eb", fontweight="bold")
    for o in w.objects:
        c = COLORS.get(o.color, "#8e44ad")
        ax.scatter([o.pos[0]], [o.pos[1]], marker=MARKERS.get(o.type, "o"), s=150, c=c,
                   edgecolors=INK, linewidths=0.8, zorder=4)
        ax.text(o.pos[0], o.pos[1] - 0.45, o.describe(), ha="center", va="top", fontsize=7.5, color=MUTED, zorder=4)


def _draw_side(side, f, plan, dialog, result):
    side.clear()
    side.axis("off")
    side.set_xlim(0, 1)
    side.set_ylim(0, 1)
    y = 0.985
    side.text(0, y, "Conversación", fontsize=11.5, fontweight="bold", color=INK, va="top")
    y -= 0.045
    for who, txt in dialog:
        lines = textwrap.wrap(txt, 52)[:4]
        color = INK if who == "Usuario" else ACCENT
        side.text(0, y, f"{who}:", fontsize=9.5, fontweight="bold", color=color, va="top")
        for ln in lines:
            side.text(0.2, y, ln, fontsize=9.5, color=color, va="top")
            y -= 0.036
        y -= 0.008
    y -= 0.015
    side.text(0, y, f"Plan validado  ({result.source or 'parser'})", fontsize=11.5, fontweight="bold", color=INK, va="top")
    y -= 0.045
    active = f["active"]
    for level, txt, path in plan:
        is_active = active is not None and tuple(active) == tuple(path)
        done = active is None and f is not None and f["events"] and f["events"][-1].startswith("✔")
        for k, ln in enumerate(textwrap.wrap(txt, 50 - 4 * level)[:2]):
            side.text(0.02 + 0.06 * level + (0.04 if k else 0), y, ln, fontsize=9.5,
                      color=ACCENT if is_active else (MUTED if not done else INK),
                      fontweight="bold" if is_active else "normal", va="top")
            y -= 0.034
    y -= 0.02
    side.text(0, y, "Eventos", fontsize=11.5, fontweight="bold", color=INK, va="top")
    y -= 0.045
    for ev in f["events"][-6:]:
        ev = ev.replace("🗨", "»").replace("📏", "•").replace("🔎", "•").replace("✅", "✓").replace("❌", "✗").replace("🔌", "•")
        for ln in textwrap.wrap(ev, 56)[:2]:
            side.text(0.02, y, ln, fontsize=9, color=ALERT if ("Alerta" in ln or "✗" in ln) else INK, va="top")
            y -= 0.031
    # batería y tiempo
    b = f["battery"]
    side.add_patch(patches.Rectangle((0, 0.012), 0.5, 0.03, fc="#e5e7eb", ec="none"))
    side.add_patch(patches.Rectangle((0, 0.012), 0.5 * b / 100, 0.03,
                                     fc="#16a34a" if b > 40 else ("#f59e0b" if b > 20 else ALERT), ec="none"))
    side.text(0.53, 0.027, f"Batería {b:.0f}%   ·   t = {f['t']:.0f} s", fontsize=9.5, color=INK, va="center")
