import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nl2mission import MissionAgent, WorldModel  # noqa: E402
from nl2mission.backends.sim2d import Sim2DBackend  # noqa: E402
from nl2mission.parsers import RulesParser  # noqa: E402
from nl2mission.parsers.llm import AnthropicParser  # noqa: E402

WORLD = os.path.join(os.path.dirname(__file__), "..", "config", "world_lab.json")


@pytest.fixture
def setup():
    w = WorldModel.load(WORLD)
    sim = Sim2DBackend(w)
    return w, sim, MissionAgent(w, RulesParser(w), sim, log=None)


def run(agent, *turns):
    reply = None
    for t in turns:
        reply = agent.handle(t)
    return reply


# ---------------------------------------------------------------- comprensión
def test_navegar_y_medir(setup):
    _, _, a = setup
    r = run(a, "Ve al laboratorio y mide la temperatura")
    assert r.kind == "execute"
    assert r.result.steps == [{"action": "navigate_to", "target": "laboratorio"},
                              {"action": "inspect", "target": "laboratorio", "measure": "temperatura"}]


def test_medir_dos_variables_con_lugar_al_final(setup):
    _, _, a = setup
    r = run(a, "mide la temperatura y la humedad de la cocina")
    assert [s["target"] for s in r.result.steps] == ["cocina", "cocina"]
    assert [s["measure"] for s in r.result.steps] == ["temperatura", "humedad"]


def test_busqueda_condicional(setup):
    _, _, a = setup
    r = run(a, "Busca la caja roja en la bodega; si la encuentras avísame y vuelve a la base, si no revisa el laboratorio")
    s = r.result.steps[0]
    assert s["action"] == "find_object" and s["object_type"] == "caja" and s["object_color"] == "rojo"
    assert s["target"] == "bodega"
    assert [x["action"] for x in s["then"]] == ["say", "return_home"]
    assert s["else"][0]["action"] == "inspect" and s["else"][0]["target"] == "laboratorio"


def test_ambiguedad_pregunta_y_resuelve(setup):
    _, _, a = setup
    r = run(a, "Ve a la oficina")
    assert r.kind == "clarify" and "Oficina norte" in r.message and "Oficina sur" in r.message
    r = run(a, "la del sur")
    assert r.kind == "execute" and r.result.steps[0]["target"] == "oficina_sur"


def test_zona_restringida_requiere_confirmacion(setup):
    _, _, a = setup
    r = run(a, "Revisa la temperatura de la sala de servidores y si pasa de 30 grados avísame")
    assert r.kind == "clarify" and "restringida" in r.message
    assert run(a, "no").kind == "reject"
    run(a, "Revisa la temperatura de la sala de servidores y si pasa de 30 grados avísame")
    r = run(a, "sí")
    assert r.kind == "execute"
    s = r.result.steps[0]
    assert s["threshold_op"] == ">" and s["threshold_value"] == 30 and s["measure"] == "temperatura"


def test_accion_imposible(setup):
    _, _, a = setup
    r = run(a, "Tráeme un café de la cocina")
    assert r.kind == "reject" and "sin brazo" in r.message


def test_lugar_desconocido_y_typo(setup):
    _, _, a = setup
    assert run(a, "ve a la terraza").kind == "clarify"
    assert run(a, "mejor al laboratorio").result.steps[0]["target"] == "laboratorio"
    r = run(a, "ve al laboratrio")          # error de digitación
    assert r.kind == "execute" and r.result.steps[0]["target"] == "laboratorio"


def test_referencia_ahi(setup):
    _, _, a = setup
    run(a, "ve a la cocina")
    r = run(a, "busca un extintor ahí")
    assert r.result.steps[0]["target"] == "cocina"


def test_objeto_desconocido(setup):
    _, _, a = setup
    r = run(a, "busca a Juan")
    assert r.kind == "clarify"
    r = run(a, "una persona")
    assert r.kind == "execute" and r.result.steps[0]["object_type"] == "persona"


def test_patrulla_y_bateria(setup):
    _, _, a = setup
    r = run(a, "Patrulla el pasillo, el laboratorio y la bodega dos veces")
    assert r.result.steps[0] == {"action": "patrol", "places": ["pasillo", "laboratorio", "bodega"], "laps": 2}
    r = run(a, "Patrulla todo cinco veces")
    assert r.kind == "reject" and "batería" in r.message


def test_espera(setup):
    _, _, a = setup
    r = run(a, "espera 2 minutos y luego vuelve a la base")
    assert r.result.steps == [{"action": "wait", "seconds": 120.0}, {"action": "return_home"}]


def test_cancelar(setup):
    _, _, a = setup
    run(a, "ve a la oficina")
    assert run(a, "cancela").kind == "cancel"
    assert a.pending is None


# ---------------------------------------------------------------- ejecución
def test_ejecucion_encuentra_caja_y_avisa(setup):
    _, sim, a = setup
    r = run(a, "Busca la caja roja en la bodega; si la encuentras avísame y vuelve a la base")
    rep = a.execute(r)
    assert rep.success
    assert any("Encontré caja roja en Bodega" in e for e in rep.events)
    assert sim.current_place() == "base"


def test_ejecucion_alerta_temperatura(setup):
    _, _, a = setup
    run(a, "si la temperatura de la sala de servidores pasa de 30 grados avísame")
    rep = a.execute(run(a, "sí, autorizado"))
    assert any("Alerta: temperatura" in e for e in rep.events)


def test_objeto_inexistente_ejecuta_rama_else(setup):
    _, _, a = setup
    rep = a.execute(run(a, "busca el botiquín en la oficina norte"))
    assert rep.success and any("No encontré botiquín" in e for e in rep.events)


# ---------------------------------------------------------------- validador sobre salida de LLM
class FakeClient:
    """Imita anthropic.Anthropic().messages.create devolviendo un tool_use."""

    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        block = SimpleNamespace(type="tool_use", name="submit_mission", input=self.outputs.pop(0))
        return SimpleNamespace(content=[block])


def test_llm_parser_y_validador_corrigen_nombres():
    w = WorldModel.load(WORLD)
    sim = Sim2DBackend(w)
    client = FakeClient([{"status": "ok", "summary": "Ir al lab y buscar caja",
                          "steps": [{"action": "find_object", "object_type": "Caja", "object_color": "roja",
                                     "target": "Laboratorio"}]}])
    a = MissionAgent(w, AnthropicParser(w, client=client), sim, log=None)
    r = a.handle("busca la caja roja en el lab")
    assert r.kind == "execute"
    s = r.result.steps[0]
    assert (s["object_type"], s["object_color"], s["target"]) == ("caja", "rojo", "laboratorio")
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "submit_mission"}
    assert "laboratorio" in call["system"]


def test_llm_lugar_inventado_se_bloquea_y_se_pregunta():
    w = WorldModel.load(WORLD)
    sim = Sim2DBackend(w)
    client = FakeClient([
        {"status": "ok", "summary": "Ir a la azotea", "steps": [{"action": "navigate_to", "target": "azotea"}]},
        {"status": "ok", "summary": "Ir a la bodega", "steps": [{"action": "navigate_to", "target": "bodega"}]},
    ])
    a = MissionAgent(w, AnthropicParser(w, client=client), sim, log=None)
    r = a.handle("ve a la azotea")
    assert r.kind == "clarify" and "No conozco" in r.message
    r = a.handle("a la bodega entonces")
    assert r.kind == "execute"
    roles = [m["role"] for m in client.calls[1]["messages"]]
    assert roles == ["user", "assistant", "user"]


def test_llm_accion_no_permitida_se_rechaza():
    w = WorldModel.load(WORLD)
    client = FakeClient([{"status": "ok", "summary": "x", "steps": [{"action": "open_door", "target": "bodega"}]}])
    a = MissionAgent(w, AnthropicParser(w, client=client), Sim2DBackend(w), log=None)
    assert a.handle("abre la puerta").kind == "reject"
