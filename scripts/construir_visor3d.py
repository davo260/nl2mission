"""Genera viewer/visor3d.html (autocontenido) a partir de la traza de la demo."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from exportar_traza import export  # noqa: E402

root = os.path.join(os.path.dirname(__file__), "..")
os.makedirs(os.path.join(root, "demo_out"), exist_ok=True)
parser = sys.argv[1] if len(sys.argv) > 1 else "rules"
path = export(parser, os.path.join(root, "demo_out", "traza.json"))
tpl = open(os.path.join(root, "viewer", "visor3d_template.html"), encoding="utf-8").read()
data = open(path, encoding="utf-8").read().replace("</", "<\\/")
out = os.path.join(root, "viewer", "visor3d.html")
vendor = os.path.join(root, "viewer", "vendor")
three = open(os.path.join(vendor, "three.min.js"), encoding="utf-8").read()
orbit = open(os.path.join(vendor, "OrbitControls.js"), encoding="utf-8").read()
html = tpl.replace("__DATA__", data)
html = html.replace("<script>__THREE__</script>", "<script>" + three.replace("</script", "<\\/script") + "</script>")
html = html.replace("<script>__ORBIT__</script>", "<script>" + orbit + "</script>")
open(out, "w", encoding="utf-8").write(html)
print(out)
