from fastapi.testclient import TestClient

from app import app
from data_source import PROCESSES


def test_default_categories_seeded():
    with TestClient(app) as c:
        data = c.get("/api/custom-kpis").json()
        names = [cat["name"] for cat in data["categories"]]
        assert names == ["생산성 Velocity 향상", "SBO 체질화", "셋업 리드타임", "엔지니어링"]
        assert all(cat["cards"] for cat in data["categories"])
        for cat in data["categories"]:
            for card in cat["cards"]:
                assert card["status"] in ("ok", "warn", "bad", "none")


def test_create_edit_value_delete_flow():
    with TestClient(app) as c:
        r = c.post("/api/custom-kpis", json={"category": "품질", "name": "CD 산포", "en": "CD Range",
                                              "unit": "nm", "target": 2.0, "lower": True, "agg": "avg", "digits": 2})
        assert r.status_code == 200
        kid = r.json()["id"]
        cats = c.get("/api/custom-kpis").json()["categories"]
        assert any(cat["name"] == "품질" and cat["cards"][0]["id"] == kid for cat in cats)

        periods = c.get("/api/custom-kpis").json()["periods"]
        assert c.put(f"/api/custom-kpis/{kid}/values", json={"process": "photo", "period": periods[-2], "value": 2.4}).status_code == 200
        assert c.put(f"/api/custom-kpis/{kid}/values", json={"process": "etch", "period": periods[-1], "value": 1.6}).status_code == 200
        photo = c.get(f"/api/kpis/{kid}", params={"process": "photo"}).json()
        assert photo["kind"] == "custom"
        assert photo["kpi"]["value"] == 2.4 and photo["kpi"]["status"] == "bad"
        allv = c.get(f"/api/kpis/{kid}", params={"process": "all"}).json()
        assert allv["kpi"]["value"] == 1.6  # 최신 달은 etch 만 입력됨

        # All 직접 입력이 자동 집계보다 우선
        c.put(f"/api/custom-kpis/{kid}/values", json={"process": "all", "period": periods[-1], "value": 1.9})
        assert c.get(f"/api/kpis/{kid}").json()["kpi"]["value"] == 1.9

        # 목표 없음으로 변경 → 상태 none
        assert c.put(f"/api/custom-kpis/{kid}", json={"target": None}).status_code == 200
        assert c.get(f"/api/kpis/{kid}").json()["kpi"]["status"] == "none"

        # 값 삭제
        c.put(f"/api/custom-kpis/{kid}/values", json={"process": "all", "period": periods[-1], "value": None})
        assert c.get(f"/api/kpis/{kid}").json()["kpi"]["value"] == 1.6

        assert c.delete(f"/api/custom-kpis/{kid}").status_code == 200
        assert c.get(f"/api/kpis/{kid}").status_code == 404
        cats = c.get("/api/custom-kpis").json()["categories"]
        cat = next(cat for cat in cats if cat["name"] == "품질")  # 빈 카테고리는 유지 → 명시적으로 삭제
        assert c.delete(f"/api/custom-categories/{cat['id']}").status_code == 200
        assert all(x["name"] != "품질" for x in c.get("/api/custom-kpis").json()["categories"])


def test_sum_aggregation_and_target():
    with TestClient(app) as c:
        d = c.get("/api/kpis/sbo_closed").json()
        per = [c.get("/api/kpis/sbo_closed", params={"process": p["id"]}).json()["kpi"]["value"] for p in PROCESSES]
        assert abs(d["kpi"]["value"] - sum(per)) < 1e-6
        assert d["kpi"]["target"] == 2 * len(PROCESSES)


def test_validation_errors():
    with TestClient(app) as c:
        assert c.post("/api/custom-kpis", json={"category": "x", "name": ""}).status_code == 400
        assert c.post("/api/custom-kpis", json={"name": "a"}).status_code == 400
        assert c.put("/api/custom-kpis/velocity/values", json={"process": "photo", "period": "2026-13", "value": 1}).status_code == 400
        assert c.put("/api/custom-kpis/velocity/values", json={"process": "nope", "period": "2026-01", "value": 1}).status_code == 400
        assert c.put("/api/custom-kpis/missing/values", json={"process": "photo", "period": "2026-01", "value": 1}).status_code == 404


def test_websocket_custom_subscription():
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.send_json({"type": "subscribe", "process": "photo", "kpi": "setup_lt"})
            msg = ws.receive_json()
            assert msg["detail"]["kind"] == "custom"
            assert msg["custom"]["categories"]
