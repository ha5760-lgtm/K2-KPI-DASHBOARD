"""
K2 KPI Board - 화면 배치 / 실시간 KPI 내용 설정

- 실시간 KPI 카드 순서 · 숨김
- 실시간 KPI 이름 · 영문명 · 목표 변경 (기본값 복원 가능)
저장: JSON 파일 (KPI_LAYOUT_FILE, 기본 data/layout.json). 그룹 공용 설정이며 모든 화면에 반영된다.
"""

import json
import os
import threading

from data_source import KPI_BY_ID, KPIS

DATA_FILE = os.environ.get("KPI_LAYOUT_FILE", os.path.join("data", "layout.json"))

# 목표를 화면에서 바꿀 수 있는 실시간 KPI (MOVE · 생산량은 공정별 일 계획값을 사용하므로 제외)
EDITABLE_TARGET = {"downloss", "util", "pm_auto", "bottleneck", "tpss"}
DEFAULTS = {k["id"]: {"name": k["name"], "en": k["en"], "target": k["target"]} for k in KPIS}

_lock = threading.Lock()


class ValidationError(ValueError):
    pass


def _default():
    return {"core_order": [k["id"] for k in KPIS], "core_hidden": [], "core_overrides": {}}


def _load():
    if not os.path.exists(DATA_FILE):
        return _default()
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    # KPI 가 추가/삭제된 경우에도 순서 목록이 항상 전체 KPI 를 담도록 보정
    ids = [k["id"] for k in KPIS]
    order = [i for i in data.get("core_order", []) if i in ids]
    data["core_order"] = order + [i for i in ids if i not in order]
    data["core_hidden"] = [i for i in data.get("core_hidden", []) if i in ids]
    data.setdefault("core_overrides", {})
    return data


def _save(data):
    os.makedirs(os.path.dirname(DATA_FILE) or ".", exist_ok=True)
    tmp = f"{DATA_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def apply():
    """저장된 이름 · 목표를 KPI 정의에 반영 (모든 계산이 KPI_BY_ID 를 참조)."""
    with _lock:
        data = _load()
    for kid, base in DEFAULTS.items():
        k = KPI_BY_ID[kid]
        ov = data["core_overrides"].get(kid, {})
        k["name"] = ov.get("name") or base["name"]
        k["en"] = ov.get("en") or base["en"]
        k["target"] = ov["target"] if kid in EDITABLE_TARGET and ov.get("target") is not None else base["target"]
    lim = int(round(KPI_BY_ID["bottleneck"]["target"]))
    KPI_BY_ID["bottleneck"]["formula"] = f"병목 = 현재 대기 WIP 최대 설비 (기준 {lim:,}매)"


def core():
    with _lock:
        data = _load()
    return {"order": data["core_order"], "hidden": data["core_hidden"],
            "overrides": data["core_overrides"], "editable_target": sorted(EDITABLE_TARGET),
            "defaults": DEFAULTS}


def visible_core():
    c = core()
    return [i for i in c["order"] if i not in c["hidden"]]


def set_core(order=None, hidden=None):
    with _lock:
        data = _load()
        ids = set(DEFAULTS)
        if order is not None:
            if set(order) != ids or len(order) != len(ids):
                raise ValidationError("순서 목록은 모든 실시간 KPI 를 한 번씩 포함해야 합니다")
            data["core_order"] = list(order)
        if hidden is not None:
            if not set(hidden) <= ids:
                raise ValidationError("알 수 없는 KPI")
            if set(hidden) >= ids:
                raise ValidationError("실시간 KPI 를 모두 숨길 수는 없습니다")
            data["core_hidden"] = list(dict.fromkeys(hidden))
        _save(data)


def set_core_kpi(kid, body):
    if kid not in DEFAULTS:
        raise KeyError(kid)
    with _lock:
        data = _load()
        if body.get("reset"):
            data["core_overrides"].pop(kid, None)
        else:
            ov = data["core_overrides"].setdefault(kid, {})
            for f in ("name", "en"):
                if f in body:
                    v = (body.get(f) or "").strip()[:40]
                    if v and v != DEFAULTS[kid][f]:
                        ov[f] = v
                    else:
                        ov.pop(f, None)
            if "target" in body:
                t = body.get("target")
                if t is None or t == "":
                    ov.pop("target", None)
                else:
                    if kid not in EDITABLE_TARGET:
                        raise ValidationError("이 KPI 의 목표는 공정별 계획값을 사용하므로 바꿀 수 없습니다")
                    try:
                        t = float(t)
                    except (TypeError, ValueError):
                        raise ValidationError("목표는 숫자") from None
                    if t <= 0:
                        raise ValidationError("목표는 0보다 커야 합니다")
                    if kid == "bottleneck":
                        t = int(round(t))
                    ov["target"] = t
            if not ov:
                data["core_overrides"].pop(kid, None)
        _save(data)
    apply()


def reset_core():
    with _lock:
        _save(_default())
    apply()

