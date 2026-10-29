from fastapi.testclient import TestClient

from app import app
from data_source import KPIS, PROCESSES

PROC_IDS = ["all"] + [p["id"] for p in PROCESSES]


def test_index_page():
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "KPI Board" in r.text


def test_processes():
    with TestClient(app) as c:
        data = c.get("/api/processes").json()
        assert [p["id"] for p in data] == PROC_IDS
        assert all(p["status"] in ("ok", "warn", "bad") for p in data[1:])


def test_cards_every_process():
    with TestClient(app) as c:
        for pid in PROC_IDS:
            data = c.get("/api/kpis", params={"process": pid}).json()
            assert [k["id"] for k in data["cards"]] == [k["id"] for k in KPIS]
            assert sum(data["summary"].values()) == len(KPIS)
            for card in data["cards"]:
                assert card["status"] in ("ok", "warn", "bad")
                assert 0 <= card["pct"] <= 100
                assert len(card["spark"]) == 12


def test_detail_every_kpi_and_process():
    with TestClient(app) as c:
        for pid in PROC_IDS:
            for k in KPIS:
                d = c.get(f"/api/kpis/{k['id']}", params={"process": pid}).json()
                assert d["kpi"]["id"] == k["id"]
                assert len(d["stats"]) == 4
                assert len(d["trend"]["today"]) == len(d["trend"]["labels"])
                assert d["ranking"]["items"] and len(d["ranking"]["items"]) <= 10
                hm = d["heatmap"]
                assert len(hm["values"]) == len(hm["rows"])
                assert all(len(row) == len(hm["cols"]) for row in hm["values"])
                assert len(d["compare"]["items"]) == len(PROCESSES)
                assert d["insights"]
                for row in d["events"]["rows"]:
                    assert len(row["cells"]) == len(d["events"]["columns"])


def test_downloss_consistency():
    """카드 정지로스(%) · 설비별 정지시간 · 시간별 추이가 같은 원천(정지 이벤트)에서 계산되는지 확인."""
    from data_source import HOURS, SimulatedProvider, View, now_kst

    p = SimulatedProvider()
    for pid in ("photo", "cmp"):
        v = View([p.states[pid]], now_kst(), pid)
        down_h = sum(v.eq_total("downloss", e) for e in v.eq)
        cal_h = len(v.eq) * sum(v.el[HOURS:]) / 60
        assert abs(v.value("downloss") - down_h / cal_h * 100) < 1e-6
        # 가동률 + 정지 + Idle + PM = 100%
        bd = {x["name"]: x["value"] for x in p.detail(pid, "util")["breakdown"]["items"]}
        assert abs(sum(bd.values()) - 100) < 0.5


def test_unknown_process_and_kpi():
    with TestClient(app) as c:
        assert c.get("/api/kpis", params={"process": "nope"}).status_code == 404
        assert c.get("/api/kpis/nope").status_code == 404


def test_websocket_subscribe():
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}
            ws.send_json({"type": "subscribe", "process": "cmp", "kpi": "util"})
            msg = ws.receive_json()
            assert msg["type"] == "kpi_update"
            assert msg["cards"]["process"] == "cmp"
            assert msg["detail"]["kpi"]["id"] == "util"


def test_simulator_tick_keeps_working():
    from data_source import SimulatedProvider

    p = SimulatedProvider()
    for _ in range(200):
        p.tick()
    assert p.cards("all")["cards"]
