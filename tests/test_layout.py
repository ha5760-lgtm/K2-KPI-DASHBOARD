from fastapi.testclient import TestClient

from app import app
from data_source import KPIS

CORE = [k["id"] for k in KPIS]


def card_ids(c, process="all"):
    return [x["id"] for x in c.get("/api/kpis", params={"process": process}).json()["cards"]]


def test_core_reorder_and_hide():
    with TestClient(app) as c:
        new = list(reversed(CORE))
        assert c.put("/api/layout/core", json={"order": new}).status_code == 200
        assert card_ids(c) == new
        r = c.put("/api/layout/core", json={"hidden": ["tpss", "move"]})
        assert r.status_code == 200
        cards = c.get("/api/kpis").json()
        hidden = {x["id"] for x in cards["cards"] if x["hidden"]}
        assert hidden == {"tpss", "move"}
        assert sum(cards["summary"].values()) == len(CORE) - 2
        # 잘못된 순서 / 전부 숨김 거부
        assert c.put("/api/layout/core", json={"order": CORE[:-1]}).status_code == 400
        assert c.put("/api/layout/core", json={"hidden": CORE}).status_code == 400
        assert c.post("/api/layout/reset").status_code == 200
        assert card_ids(c) == CORE


def test_core_name_and_target_override():
    with TestClient(app) as c:
        r = c.put("/api/layout/core/downloss", json={"name": "정지로스(비계획)", "target": 9.0})
        assert r.status_code == 200
        card = next(x for x in c.get("/api/kpis").json()["cards"] if x["id"] == "downloss")
        assert card["name"] == "정지로스(비계획)"
        assert card["target"] == 9.0 and card["status"] == "ok"
        d = c.get("/api/kpis/downloss").json()
        assert d["trend"]["target"] == 9.0
        # 병목 기준 변경 → 판정 · 문구 반영
        c.put("/api/layout/core/bottleneck", json={"target": 300})
        b = next(x for x in c.get("/api/kpis").json()["cards"] if x["id"] == "bottleneck")
        assert b["target_text"] == "≤ 300 wf"
        # MOVE 목표는 계획값이라 변경 불가
        assert c.put("/api/layout/core/move", json={"target": 1}).status_code == 400
        assert c.put("/api/layout/core/nope", json={"name": "x"}).status_code == 404
        # 기본값 복원
        c.put("/api/layout/core/downloss", json={"reset": True})
        card = next(x for x in c.get("/api/kpis").json()["cards"] if x["id"] == "downloss")
        assert card["name"] == "설비 정지로스" and card["target"] == 3.5


def test_custom_arrange_moves_between_categories():
    with TestClient(app) as c:
        cats = c.get("/api/custom-kpis").json()["categories"]
        arr = [{"id": cat["id"], "kpis": [k["id"] for k in cat["cards"]]} for cat in cats]
        # 첫 카테고리의 첫 KPI 를 마지막 카테고리 맨 앞으로, 카테고리 순서 뒤집기
        moved = arr[0]["kpis"].pop(0)
        arr[-1]["kpis"].insert(0, moved)
        arr.reverse()
        assert c.put("/api/custom-layout", json={"categories": arr}).status_code == 200
        cats2 = c.get("/api/custom-kpis").json()["categories"]
        assert [x["id"] for x in cats2] == [x["id"] for x in arr]
        assert cats2[0]["cards"][0]["id"] == moved
        # 누락된 KPI 가 있으면 거부
        arr[0]["kpis"].pop()
        assert c.put("/api/custom-layout", json={"categories": arr}).status_code == 400


def test_category_crud_and_hidden_kpi():
    with TestClient(app) as c:
        cid = c.post("/api/custom-categories", json={"name": "품질", "en": "Quality"}).json()["id"]
        assert c.post("/api/custom-categories", json={"name": "품질"}).status_code == 400
        assert c.put(f"/api/custom-categories/{cid}", json={"name": "품질 지표"}).status_code == 200
        names = [x["name"] for x in c.get("/api/custom-kpis").json()["categories"]]
        assert "품질 지표" in names
        # KPI 가 있는 카테고리는 삭제 불가
        assert c.delete("/api/custom-categories/velocity").status_code == 400
        assert c.delete(f"/api/custom-categories/{cid}").status_code == 200
        # 숨김 KPI 는 요약에서 제외
        before = c.get("/api/custom-kpis").json()["categories"][0]["summary"]
        kid = c.get("/api/custom-kpis").json()["categories"][0]["cards"][0]["id"]
        c.put(f"/api/custom-kpis/{kid}", json={"hidden": True})
        after = c.get("/api/custom-kpis").json()["categories"][0]
        assert sum(after["summary"].values()) == sum(before.values()) - 1
        assert after["cards"][0]["hidden"] is True
