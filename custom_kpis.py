"""
K2 KPI Board - 공통 KPI (사용자 정의)

생산성 Velocity · SBO 체질화 · 셋업 리드타임 · 엔지니어링 등
실시간 연동이 아닌 월 단위 KPI 를 화면에서 자유롭게 추가/수정/값 입력할 수 있게 한다.
저장: JSON 파일 (KPI_DATA_FILE, 기본 data/custom_kpis.json). 파일이 없으면 예시 데이터로 생성.
"""

import json
import os
import random
import re
import threading
import uuid
import zlib

from data_source import PROCESSES, fmt, now_kst

DATA_FILE = os.environ.get("KPI_DATA_FILE", os.path.join("data", "custom_kpis.json"))
PERIODS = 12
PROC_IDS = [p["id"] for p in PROCESSES]
PROC_NAME = {p["id"]: p["name"] for p in PROCESSES}

DEFAULT_CATEGORIES = [
    {"id": "velocity", "name": "생산성 Velocity 향상", "en": "Productivity Velocity"},
    {"id": "sbo", "name": "SBO 체질화", "en": "SBO"},
    {"id": "setup", "name": "셋업 리드타임", "en": "Setup Lead Time"},
    {"id": "eng", "name": "엔지니어링", "en": "Engineering"},
]

# (category, id, name, en, unit, digits, lower, target, agg, base, slope/월, noise, description)
DEFAULT_KPIS = [
    ("velocity", "velocity", "Velocity", "Layer per Day", "L/D", 2, False, 2.50, "avg", 2.22, .022, .04,
     "일 평균 Layer 진행 속도 (Layer ÷ Cycle Time)"),
    ("velocity", "wph_gain", "WPH 향상률", "WPH Gain", "%", 1, False, 5.0, "avg", 1.2, .33, .35,
     "전년 동월 대비 설비 WPH 향상률"),
    ("velocity", "queue_time", "Lot 대기시간", "Queue Time", "h", 1, True, 2.5, "avg", 3.7, -.08, .18,
     "공정 진입 전 Lot 평균 대기시간"),
    ("sbo", "sbo_cover", "SBO 적용률", "SBO Coverage", "%", 1, False, 80.0, "avg", 54, 2.3, 1.8,
     "SBO 적용 대상 설비 중 적용 완료 비율"),
    ("sbo", "sbo_comply", "SBO 준수율", "SBO Compliance", "%", 1, False, 95.0, "avg", 88.5, .5, .9,
     "SBO 점검 항목 준수 비율"),
    ("sbo", "sbo_closed", "SBO 과제 완료", "SBO Tasks Closed", "건", 0, False, 2, "sum", 1.2, .06, .7,
     "월간 SBO 체질화 과제 완료 건수 (공정별 목표 2건)"),
    ("setup", "setup_lt", "셋업 리드타임", "Setup Lead Time", "일", 1, True, 45.0, "avg", 59, -1.1, 2.5,
     "설비 반입 ~ 양산 Release 소요 일수"),
    ("setup", "setup_ontime", "셋업 일정 준수율", "On-time Setup", "%", 1, False, 90.0, "avg", 77, 1.1, 3.5,
     "계획 대비 일정 내 셋업 완료 비율"),
    ("setup", "setup_tools", "셋업 진행 설비", "Tools in Setup", "대", 0, False, None, "sum", 1.4, 0, .8,
     "현재 셋업 진행 중인 설비 수 (모니터링 항목, 목표 없음)"),
    ("eng", "eng_closure", "개선 과제 완료율", "Improvement Closure", "%", 1, False, 85.0, "avg", 69, 1.3, 3,
     "계획 대비 엔지니어링 개선 과제 완료 비율"),
    ("eng", "recipe_opt", "Recipe 최적화", "Recipe Optimization", "건", 0, False, 3, "sum", 2.3, .05, 1,
     "월간 Recipe 최적화 적용 건수 (공정별 목표 3건)"),
    ("eng", "change_lt", "변경점 처리 리드타임", "Change Lead Time", "일", 1, True, 7.0, "avg", 9.6, -.16, .7,
     "변경점(ECO) 접수 ~ 적용 완료 소요 일수"),
]

_lock = threading.Lock()


class ValidationError(ValueError):
    pass


def period_labels(n=PERIODS):
    now = now_kst()
    y, m = now.year, now.month
    out = []
    for i in range(n - 1, -1, -1):
        yy, mm = y, m - i
        while mm <= 0:
            mm += 12
            yy -= 1
        out.append(f"{yy}-{mm:02d}")
    return out


# ─── 저장소 ───────────────────────────────────────────────────────


def _seed():
    labels = period_labels()
    kpis = []
    for cat, kid, name, en, unit, digits, lower, target, agg, base, slope, noise, desc in DEFAULT_KPIS:
        values = {}
        for pid in PROC_IDS:
            r = random.Random(zlib.crc32(f"{kid}:{pid}".encode()))
            offset = r.uniform(-.12, .12) * base if unit != "%" else r.uniform(-6, 6)
            row = {}
            for i, p in enumerate(labels):
                v = base + offset + slope * i + r.gauss(0, noise)
                v = max(0.0, min(100.0, v) if unit == "%" else v)
                row[p] = round(v) if digits == 0 else round(v, digits)
            values[pid] = row
        kpis.append({"id": kid, "category": cat, "name": name, "en": en, "unit": unit, "digits": digits,
                     "lower": lower, "target": target, "agg": agg, "description": desc, "owner": "",
                     "demo": True, "values": values})
    return {"categories": [dict(c) for c in DEFAULT_CATEGORIES], "kpis": kpis}


def _load():
    if not os.path.exists(DATA_FILE):
        data = _seed()
        _save(data)
        return data
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    os.makedirs(os.path.dirname(DATA_FILE) or ".", exist_ok=True)
    tmp = f"{DATA_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def _find(data, kid):
    for k in data["kpis"]:
        if k["id"] == kid:
            return k
    raise KeyError(kid)


def exists(kid) -> bool:
    with _lock:
        return any(k["id"] == kid for k in _load()["kpis"])


# ─── 계산 ─────────────────────────────────────────────────────────


def _series(k, process, labels):
    vals = []
    for p in labels:
        if process != "all":
            vals.append(k["values"].get(process, {}).get(p))
            continue
        direct = k["values"].get("all", {}).get(p)
        if direct is not None:
            vals.append(direct)
            continue
        xs = [k["values"].get(pid, {}).get(p) for pid in PROC_IDS]
        xs = [x for x in xs if x is not None]
        vals.append(None if not xs else sum(xs) if k["agg"] == "sum" else sum(xs) / len(xs))
    return vals


def _target(k, process):
    if k["target"] is None:
        return None
    if process == "all" and k["agg"] == "sum":
        n = sum(1 for pid in PROC_IDS if k["values"].get(pid)) or len(PROC_IDS)
        return k["target"] * n
    return k["target"]


def _status(k, value, target):
    if value is None or target is None:
        return "none", 0
    if k["lower"]:
        ratio = target / value if value else 1
    else:
        ratio = value / target if target else 1
    st = "ok" if ratio >= 1 else "warn" if ratio >= .95 else "bad"
    return st, round(min(ratio, 1) * 100)


def _fmt_unit(k, v):
    if v is None:
        return "-"
    return fmt(v, k["digits"]) + ("%" if k["unit"] == "%" else f" {k['unit']}" if k["unit"] else "")


def card(k, process, labels=None):
    labels = labels or period_labels()
    vals = _series(k, process, labels)
    idx = [i for i, v in enumerate(vals) if v is not None]
    target = _target(k, process)
    c = {"id": k["id"], "kind": "custom", "category": k["category"], "name": k["name"], "en": k.get("en", ""),
         "unit": k["unit"], "demo": k.get("demo", False), "target": target, "text": False}
    tgt_txt = "없음" if target is None else ("≤ " if k["lower"] else "") + _fmt_unit(k, target)
    c["target_text"] = tgt_txt
    if not idx:
        c.update(value=None, display="-", status="none", pct=0, spark=[], delta="데이터 없음 · 값을 입력하세요",
                 delta_class="", period=None)
        return c
    cur = vals[idx[-1]]
    st, pct = _status(k, cur, target)
    c.update(value=round(cur, 4), display=fmt(cur, k["digits"]), status=st, pct=pct,
             spark=[round(vals[i], 4) for i in idx][-12:], period=labels[idx[-1]])
    if len(idx) >= 2:
        prev = vals[idx[-2]]
        diff = cur - prev
        good = diff == 0 or (diff < 0) == bool(k["lower"])
        unit = "%p" if k["unit"] == "%" else k["unit"]
        c["delta"] = f"{'▲' if diff >= 0 else '▼'} {abs(diff):,.{k['digits']}f}{unit} 전월"
        c["delta_class"] = "up" if good or target is None else "down"
    else:
        c["delta"], c["delta_class"] = f"{labels[idx[-1]]} 기준", ""
    return c


def overview(process):
    with _lock:
        data = _load()
    labels = period_labels()
    cats = []
    for cat in data["categories"]:
        cards = [card(k, process, labels) for k in data["kpis"] if k["category"] == cat["id"]]
        cats.append({**cat, "cards": cards,
                     "summary": {s: sum(1 for c in cards if c["status"] == s) for s in ("ok", "warn", "bad", "none")}})
    return {"process": process, "categories": cats, "periods": labels}


def detail(process, kid):
    with _lock:
        data = _load()
    k = _find(data, kid)
    labels = period_labels()
    c = card(k, process, labels)
    vals = _series(k, process, labels)
    cat = next((x for x in data["categories"] if x["id"] == k["category"]), {"name": k["category"]})
    target = _target(k, process)

    # 추이: 첫 입력값 이후 구간, 빈 달은 직전 값 유지(표시는 gaps 로 구분)
    first = next((i for i, v in enumerate(vals) if v is not None), None)
    trend = None
    if first is not None:
        filled, gaps, last = [], [], None
        for i in range(first, len(vals)):
            if vals[i] is None:
                gaps.append(i - first)
            else:
                last = vals[i]
            filled.append(round(last, 4))
        trend = {"title": "월별 추이", "sub": f"최근 {len(filled)}개월 · 빈 달은 직전 값 유지",
                 "labels": labels[first:], "today": filled, "prev": None, "gaps": gaps,
                 "target": target if target is not None else None, "lower": bool(k["lower"]),
                 "unit": k["unit"], "series_label": f"{k['name']} ({k['unit']})"}

    known = [v for v in vals if v is not None]
    stats = [
        {"label": f"현재값 ({c['period'] or '-'})", "value": c["display"], "unit": k["unit"] if c["value"] is not None else ""},
        {"label": "목표", "value": c["target_text"], "unit": ""},
        {"label": "달성률", "value": "-" if c["status"] == "none" else f"{c['pct']}", "unit": "" if c["status"] == "none" else "%",
         "tone": "alert" if c["status"] == "bad" else "neutral"},
    ]
    if len(known) >= 2:
        d = known[-1] - known[0]
        good = (d < 0) == bool(k["lower"])
        stats.append({"label": f"{len(known)}개월 변화", "value": f"{d:+,.{k['digits']}f}",
                      "unit": "%p" if k["unit"] == "%" else k["unit"],
                      "tone": "neutral" if target is None else ("good" if good else "alert")})
    else:
        stats.append({"label": "기간 변화", "value": "-", "unit": ""})

    compare = []
    for pid in PROC_IDS:
        pc = card(k, pid, labels)
        compare.append({"id": pid, "name": PROC_NAME[pid], "value": pc["value"], "display": pc["display"],
                        "target": pc["target"], "pct": pc["pct"], "status": pc["status"]})

    shown = labels[-6:]
    rows = [{"id": "all", "name": "All", "values": [k["values"].get("all", {}).get(p) for p in shown],
             "computed": [None if v is None else round(v, k["digits"] + 1) for v in _series(k, "all", shown)]}]
    for pid in PROC_IDS:
        rows.append({"id": pid, "name": PROC_NAME[pid], "values": [k["values"].get(pid, {}).get(p) for p in shown]})

    return {"kind": "custom", "process": process, "label": "All" if process == "all" else PROC_NAME.get(process, process),
            "updated": now_kst().isoformat(), "kpi": c,
            "meta": {"id": k["id"], "name": k["name"], "en": k.get("en", ""), "unit": k["unit"], "digits": k["digits"],
                     "lower": bool(k["lower"]), "target": k["target"], "agg": k["agg"], "description": k.get("description", ""),
                     "owner": k.get("owner", ""), "category": k["category"], "category_name": cat["name"],
                     "demo": k.get("demo", False), "sources": ["수기 입력"],
                     "formula": k.get("description", "")},
            "stats": stats, "trend": trend, "insights": _insights(k, process, vals, labels, compare, target),
            "compare": {"items": compare, "target": k["target"], "unit": k["unit"],
                        "lower": bool(k["lower"]), "by_pct": False},
            "matrix": {"periods": shown, "rows": rows, "agg": k["agg"]}}


def _insights(k, process, vals, labels, compare, target):
    out = []
    known = [(labels[i], v) for i, v in enumerate(vals) if v is not None]
    if len(known) >= 4:
        recent = known[-1][1] - known[-4][1]
        good = (recent < 0) == bool(k["lower"])
        out.append({"level": "info" if good or target is None else "bad",
                    "title": f"최근 3개월 {'개선' if good else '악화'} 추세" if target is not None else "최근 3개월 변화",
                    "text": f"{known[-4][0]} {_fmt_unit(k, known[-4][1])} → {known[-1][0]} {_fmt_unit(k, known[-1][1])} "
                            f"({recent:+,.{k['digits']}f})."})
    if target is not None and known:
        gap = known[-1][1] - target
        met = (gap <= 0) if k["lower"] else (gap >= 0)
        out.append({"level": "info" if met else "bad", "title": "목표 달성" if met else "목표 미달",
                    "text": f"현재 {_fmt_unit(k, known[-1][1])}, 목표 대비 {gap:+,.{k['digits']}f}."})
    have = [c for c in compare if c["value"] is not None]
    if process == "all" and len(have) >= 2:
        key = lambda c: c["value"]
        best = (min if k["lower"] else max)(have, key=key)
        worst = (max if k["lower"] else min)(have, key=key)
        out.append({"level": "info", "title": f"최고 {best['name']} · 최저 {worst['name']}",
                    "text": f"{best['name']} {_fmt_unit(k, best['value'])} / {worst['name']} {_fmt_unit(k, worst['value'])}. "
                            f"미입력 공정 {len(compare) - len(have)}개."})
    if k.get("demo"):
        out.append({"level": "info", "title": "예시 데이터",
                    "text": "초기 예시 값입니다. 아래 '월별 입력' 표에서 실제 값으로 바꿔 입력하세요."})
    return out


# ─── 변경 ─────────────────────────────────────────────────────────


def _clean_def(body, data, partial=False):
    out = {}
    name = (body.get("name") or "").strip()
    if not partial or "name" in body:
        if not name or len(name) > 40:
            raise ValidationError("KPI 이름은 1~40자")
        out["name"] = name
    if "en" in body:
        out["en"] = (body.get("en") or "").strip()[:40]
    if not partial or "unit" in body:
        out["unit"] = (body.get("unit") or "").strip()[:10]
    if not partial or "digits" in body:
        try:
            d = int(body.get("digits", 1))
        except (TypeError, ValueError):
            raise ValidationError("소수 자리는 0~3") from None
        if not 0 <= d <= 3:
            raise ValidationError("소수 자리는 0~3")
        out["digits"] = d
    if not partial or "lower" in body:
        out["lower"] = bool(body.get("lower", False))
    if not partial or "target" in body:
        t = body.get("target")
        if t in (None, ""):
            out["target"] = None
        else:
            try:
                out["target"] = float(t)
            except (TypeError, ValueError):
                raise ValidationError("목표는 숫자") from None
    if not partial or "agg" in body:
        agg = body.get("agg", "avg")
        if agg not in ("avg", "sum"):
            raise ValidationError("집계 방식은 avg 또는 sum")
        out["agg"] = agg
    for f, n in (("description", 200), ("owner", 40)):
        if f in body:
            out[f] = (body.get(f) or "").strip()[:n]
    if not partial or "category" in body:
        cat = (body.get("category") or "").strip()
        if not cat:
            raise ValidationError("카테고리를 입력하세요")
        match = next((c for c in data["categories"] if cat in (c["id"], c["name"])), None)
        if match is None:
            match = {"id": "cat-" + uuid.uuid4().hex[:6], "name": cat[:30], "en": ""}
            data["categories"].append(match)
        out["category"] = match["id"]
    return out


def create(body):
    with _lock:
        data = _load()
        k = _clean_def(body, data)
        slug = re.sub(r"[^a-z0-9]+", "_", (body.get("en") or "").lower()).strip("_")[:24]
        kid = f"{slug or 'kpi'}_{uuid.uuid4().hex[:5]}"
        k.update(id=kid, values={}, demo=False)
        k.setdefault("description", "")
        k.setdefault("owner", "")
        k.setdefault("en", "")
        data["kpis"].append(k)
        _save(data)
        return kid


def update(kid, body):
    with _lock:
        data = _load()
        k = _find(data, kid)
        k.update(_clean_def(body, data, partial=True))
        _prune_categories(data)
        _save(data)


def delete(kid):
    with _lock:
        data = _load()
        _find(data, kid)
        data["kpis"] = [k for k in data["kpis"] if k["id"] != kid]
        _prune_categories(data)
        _save(data)


def _prune_categories(data):
    """기본 카테고리는 유지, 사용자가 만든 빈 카테고리는 제거."""
    used = {k["category"] for k in data["kpis"]}
    keep = {c["id"] for c in DEFAULT_CATEGORIES}
    data["categories"] = [c for c in data["categories"] if c["id"] in used or c["id"] in keep]


def set_value(kid, process, period, value):
    if process != "all" and process not in PROC_IDS:
        raise ValidationError("알 수 없는 공정")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period or ""):
        raise ValidationError("기간 형식은 YYYY-MM")
    with _lock:
        data = _load()
        k = _find(data, kid)
        row = k["values"].setdefault(process, {})
        if value is None:
            row.pop(period, None)
        else:
            row[period] = round(float(value), k["digits"])
        k["demo"] = False
        _save(data)
