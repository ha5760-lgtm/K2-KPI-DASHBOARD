"""
K2 KPI Board - 데이터 소스 계층

프론트엔드/API는 KPIProvider 인터페이스(processes / cards / detail / tick)만 사용한다.
실제 MES · EES · PMS · TPSS 연동 시 KPIProvider 를 구현한 클래스를 추가하고
환경 변수 KPI_PROVIDER 로 선택한다. 현재는 SimulatedProvider(데모 데이터)만 제공.

SimulatedProvider 는 정지 이벤트(episode)를 원천으로 삼아
정지로스 → 가동률 → MOVE → 생산량 / 대기 WIP / TPSS 를 파생시키므로
화면의 카드 · 추이 · 랭킹 · 히트맵 · 이벤트가 서로 일관된 숫자를 가진다.
"""

import os
import random
import zlib
from collections import deque
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# ─── 공정 / KPI 정의 ─────────────────────────────────────────────

PROCESSES = [
    {"id": "photo", "name": "Photo", "en": "Photo", "types": [("SCN", "Scanner"), ("TRK", "Track")]},
    {"id": "etch", "name": "Etch", "en": "Etch", "types": [("ETC", "Dry Etch"), ("ASH", "Asher")]},
    {"id": "depo", "name": "Depo", "en": "Deposition", "types": [("CVD", "CVD"), ("ALD", "ALD")]},
    {"id": "diff", "name": "Diffusion", "en": "Diffusion", "types": [("FUR", "Furnace"), ("RTP", "RTP")]},
    {"id": "imp", "name": "Implant", "en": "Implant", "types": [("IMP", "Implanter"), ("ANL", "Anneal")]},
    {"id": "metal", "name": "Metal", "en": "Metal", "types": [("PVD", "PVD"), ("ECP", "ECP")]},
    {"id": "cmp", "name": "CMP", "en": "CMP", "types": [("CMP", "Polisher"), ("PCL", "Post-Clean")]},
    {"id": "clean", "name": "Clean", "en": "Clean", "types": [("WET", "Wet Bench"), ("SCR", "Scrubber")]},
]
PROC_BY_ID = {p["id"]: p for p in PROCESSES}

# 공정별 대표 BM 알람 (원인 코드, 내용)
FAULTS = {
    "photo": [("E-4127", "Wafer Stage Position Error"), ("E-3051", "Reticle Loader Fault"),
              ("T-2208", "Coater Cup Exhaust Low"), ("T-1172", "Developer Nozzle Clog")],
    "etch": [("R-1203", "RF Reflected Power High"), ("R-2210", "ESC Helium Leak"),
             ("R-3104", "Chamber Pressure Unstable"), ("R-4015", "Endpoint Detect Fail")],
    "depo": [("D-1101", "MFC Flow Deviation"), ("D-2301", "Heater Temp Out of Range"),
             ("D-3307", "Showerhead Particle Alarm"), ("D-4120", "Throttle Valve Fault")],
    "diff": [("F-1009", "Furnace Temp Uniformity Out"), ("F-2104", "Boat Elevator Fault"),
             ("F-3002", "Gas Leak Detected"), ("F-4211", "RTP Lamp Failure")],
    "imp": [("I-1150", "Beam Current Unstable"), ("I-2033", "Source Arc Fault"),
            ("I-3301", "Vacuum Pump Fault"), ("I-4102", "Faraday Cup Error")],
    "metal": [("M-1044", "Target Life End"), ("M-2120", "Cryo Pump Regen Fail"),
              ("M-3105", "Plating Bath Conc. OOS"), ("M-4007", "Magnet Rotation Fault")],
    "cmp": [("C-1201", "Pad Conditioner Fault"), ("C-2108", "Slurry Flow Low"),
            ("C-3011", "Head Pressure Error"), ("C-4103", "Endpoint Motor Current Fail")],
    "clean": [("W-1022", "Chemical Conc. OOS"), ("W-2105", "Megasonic Power Fault"),
              ("W-3110", "DIW Resistivity Low"), ("W-4201", "Robot Handoff Error")],
}

# 정지 유형: (이름, 비중, 최소 분, 최대 분)
DOWN_TYPES = [("BM", .38, 30, 170), ("대기", .24, 15, 60), ("PM 지연", .17, 20, 90),
              ("Setup", .12, 10, 40), ("기타", .09, 10, 50)]
DOWN_TYPE_LABEL = {"BM": "BM (고장)", "대기": "대기 (Wait Tech)", "PM 지연": "PM 지연",
                   "Setup": "Setup / Recipe", "기타": "자재 · 기타"}
WAIT_DESC = [("W-0103", "Tech 대기 (교대)"), ("W-0110", "Tech 대기 (타 설비 조치 중)")]
PM_DESC = [("PM-W02", "Weekly PM 지연 (부품 대기)"), ("PM-M01", "Monthly PM 연장")]
SETUP_DESC = [("S-0201", "Recipe 변경 Setup"), ("S-0305", "Qual Wafer 진행")]
ETC_DESC = [("X-0901", "소모품 교체 대기"), ("X-0712", "Utility 순간 정전")]

TECHNICIANS = ["김민수", "이서연", "박준혁", "최지은", "정우진", "강하늘", "윤서준", "한지민"]
PRODUCTS = [("DRAM", .45), ("NAND", .28), ("Foundry", .18), ("Eng / Monitor", .09)]
LAYERS = ["ACT", "GATE", "CNT", "M1", "V1", "M2"]
PM_TYPES = [("Daily", 88, 96), ("Weekly", 70, 86), ("Monthly", 52, 72), ("Quarterly", 30, 55)]
QUEUE_REASONS = [("Dispatch 대기", .40), ("Reticle · 소모품 대기", .22), ("계측 대기", .23), ("Recipe Hold", .15)]

HOURS = 24
BUCKETS = 48           # 오늘 24h + 전일 24h
WEEKS = 12
QUEUE_LIMIT = 1000     # 병목 판정 대기 WIP 기준(매)

KPIS = [
    {"id": "downloss", "category": "ops", "name": "설비 정지로스", "en": "Down Loss", "unit": "%", "digits": 1, "lower": True,
     "target": 3.5, "sources": ["EES 설비상태", "MES 이벤트", "PMS 작업이력"],
     "formula": "정지로스 = 비계획 정지시간 ÷ 캘린더 시간"},
    {"id": "util", "category": "ops", "name": "가동률", "en": "Utilization", "unit": "%", "digits": 1, "lower": False,
     "target": 85.0, "sources": ["EES 설비상태", "MES 이벤트"],
     "formula": "가동률 = Run 시간 ÷ 캘린더 시간"},
    {"id": "pm_auto", "category": "ops", "name": "PM 자동화율", "en": "PM Automation", "unit": "%", "digits": 1, "lower": False,
     "target": 75.0, "sources": ["PMS 작업이력"],
     "formula": "PM 자동화율 = 자동 수행 PM 항목 ÷ 전체 PM 항목 (주간)"},
    {"id": "bottleneck", "category": "ops", "name": "병목설비", "en": "Bottleneck", "unit": "wf", "digits": 0, "lower": True,
     "target": QUEUE_LIMIT, "sources": ["MES WIP", "EES 설비상태"],
     "formula": f"병목 = 현재 대기 WIP 최대 설비 (기준 {QUEUE_LIMIT:,}매)"},
    {"id": "move", "category": "prod", "name": "MOVE량", "en": "Moves", "unit": "mv", "digits": 0, "lower": False,
     "target": None, "sources": ["MES Track-out"],
     "formula": "MOVE = 최근 24시간 Track-out 웨이퍼 Step 수"},
    {"id": "output", "category": "prod", "name": "생산량", "en": "Wafer Out", "unit": "wf", "digits": 0, "lower": False,
     "target": None, "sources": ["MES Track-out"],
     "formula": "생산량 = 최근 24시간 공정 완료(Out) 웨이퍼 수"},
    {"id": "tpss", "category": "prod", "name": "TPSS", "en": "TPSS Index", "unit": "%", "digits": 1, "lower": False,
     "target": 92.0, "sources": ["TPSS"],
     "formula": "TPSS 지표 = TPSS 시스템 설비별 지수 평균"},
]
KPI_BY_ID = {k["id"]: k for k in KPIS}


def queue_limit():
    """병목 판정 기준 (화면에서 목표를 바꾸면 함께 바뀜)."""
    return int(round(KPI_BY_ID["bottleneck"]["target"]))


KPI_CATEGORIES = [{"id": "ops", "name": "설비 운영", "en": "Equipment"},
                  {"id": "prod", "name": "생산", "en": "Production"}]


def now_kst() -> datetime:
    return datetime.now(KST)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _pick(rng, weighted):
    r = rng.random() * sum(w for _, w in weighted)
    for item, w in weighted:
        r -= w
        if r <= 0:
            return item
    return weighted[-1][0]


# ─── 공정 단위 시뮬레이션 상태 ─────────────────────────────────────


class ProcessState:
    """한 공정(8대 공정 중 하나)의 설비 · 이벤트 원천 데이터."""

    def __init__(self, proc: dict, now: datetime):
        self.proc = proc
        self.rng = random.Random(zlib.crc32(proc["id"].encode()))
        r = self.rng
        self.eq = [f"{p}-{i:02d}" for p, _ in proc["types"] for i in range(1, 7)]
        self.eq_type = {f"{p}-{i:02d}": t for p, t in proc["types"] for i in range(1, 7)}
        self.bias = r.uniform(.55, 1.2)                       # 공정별 정지 성향
        self.factor = {e: r.lognormvariate(0, .45) for e in self.eq}
        self.rate = {e: r.uniform(150, 210) for e in self.eq}  # 풀가동 시 시간당 Move
        self.out_ratio = {e: r.uniform(.25, .29) for e in self.eq}
        self.q_base = {e: r.uniform(80, 420) for e in self.eq}
        self.pm_level = {e: r.uniform(48, 96) for e in self.eq}
        self.pm_type_rate = {t: r.uniform(lo, hi) for t, lo, hi in PM_TYPES}
        self.product_mix = [(n, w * r.uniform(.8, 1.2)) for n, w in PRODUCTS]
        self.queue_reason = {e: _pick(r, QUEUE_REASONS) for e in self.eq}

        self.anchor = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=BUCKETS - 1)
        self.idle = {e: [] for e in self.eq}
        self.pm_min = {e: [] for e in self.eq}
        self.noise = {e: [] for e in self.eq}
        self._idle_level = 1.0
        for _ in range(BUCKETS):
            self._append_bucket()

        full_rate = sum(self.rate.values()) * .86 * HOURS
        self.targets = {"move": round(full_rate * r.uniform(.94, 1.0), -2)}
        self.targets["output"] = round(self.targets["move"] * .27, -1)

        self.weekly = {e: [] for e in self.eq}
        for e in self.eq:
            slope = r.uniform(.2, .9)
            for w in range(WEEKS):
                self.weekly[e].append(_clamp(self.pm_level[e] - (WEEKS - 1 - w) * slope + r.gauss(0, 1.5), 5, 100))

        self.episodes = []
        self._seed_episodes(now)
        self.lot_seq = r.randint(10000, 60000)
        self.lot_events = deque(maxlen=40)
        self.out_events = deque(maxlen=40)
        self.pm_events = []
        self._seed_lot_events(now)
        self._seed_pm_events(now)
        self._cache_key = None
        self._grids = None

    # ── 버킷 관리 ──
    def _append_bucket(self):
        r = self.rng
        level = r.uniform(.8, 1.25) if len(self.idle[self.eq[0]]) % HOURS == 0 else self._idle_level
        self._idle_level = level
        for e in self.eq:
            self.idle[e].append(r.uniform(.03, .11) * level)
            self.pm_min[e].append(r.uniform(30, 55) if r.random() < .015 else 0.0)
            self.noise[e].append(r.uniform(.95, 1.05))

    def roll(self, now: datetime):
        """시(hour)가 바뀌면 버킷을 한 칸 민다."""
        target = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=BUCKETS - 1)
        while self.anchor < target:
            self.anchor += timedelta(hours=1)
            for e in self.eq:
                for arr in (self.idle[e], self.pm_min[e], self.noise[e]):
                    arr.pop(0)
            self._append_bucket()
        horizon = now - timedelta(hours=BUCKETS + 2)
        self.episodes = [ep for ep in self.episodes if ep["end"] is None or ep["end"] > horizon]

    # ── 정지 이벤트 ──
    def _make_episode(self, e, start, minutes, kind=None):
        r = self.rng
        kind = kind or _pick(r, [(t, w) for t, w, _, _ in DOWN_TYPES])
        if kind == "BM":
            code, desc = r.choice(FAULTS[self.proc["id"]])
        else:
            code, desc = r.choice({"대기": WAIT_DESC, "PM 지연": PM_DESC, "Setup": SETUP_DESC, "기타": ETC_DESC}[kind])
        return {"eq": e, "start": start, "end": None if minutes is None else start + timedelta(minutes=minutes),
                "kind": kind, "code": code, "desc": desc, "owner": r.choice(TECHNICIANS)}

    def _seed_episodes(self, now):
        r = self.rng
        # 전일 / 오늘 구간별로 정지 수준을 다르게 해 일간 변동을 만든다
        day_factor = [r.uniform(.7, 1.35), r.uniform(.7, 1.35)]
        for e in self.eq:
            for half in (0, 1):
                start0 = self.anchor + timedelta(hours=HOURS * half)
                n = max(0, round(r.gauss(.75, .6) * self.bias * self.factor[e] * day_factor[half]))
                for _ in range(n):
                    self._seed_one(e, start0, now)
        for e in r.sample(self.eq, r.randint(0, 1) + (1 if self.bias > 1.0 else 0)):
            self.episodes.append(self._make_episode(e, now - timedelta(minutes=r.uniform(3, 45)), None))

    def _seed_one(self, e, start0, now):
        r = self.rng
        if r.random() < .4:  # 교대(06/14/22시) 전후 집중
            day = start0 + timedelta(hours=r.randint(0, HOURS - 1))
            sh = r.choice([6, 14, 22])
            base = day.replace(hour=sh) + timedelta(minutes=r.uniform(-80, 80))
        else:
            base = start0 + timedelta(minutes=r.uniform(0, HOURS * 60))
        kind = _pick(r, [(t, w) for t, w, _, _ in DOWN_TYPES])
        lo, hi = next((a, b) for t, _, a, b in DOWN_TYPES if t == kind)
        dur = r.uniform(lo, hi)
        if base < now - timedelta(minutes=5) and base + timedelta(minutes=dur) < now:
            self.episodes.append(self._make_episode(e, base, dur, kind))

    def down_now(self):
        return {ep["eq"]: ep for ep in self.episodes if ep["end"] is None}

    # ── 로트/PM 이벤트 ──
    def _lot_event(self, t):
        r = self.rng
        self.lot_seq += 1
        e = r.choice(self.eq)
        prod = _pick(r, self.product_mix)
        qty = r.choice([25, 25, 25, 24, 13])
        ev = {"time": t, "lot": f"K2{prod[0]}{self.lot_seq:05d}.1", "eq": e,
              "step": f"{self.proc['en'][:2].upper()}-{r.choice(LAYERS)}", "qty": qty,
              "proc_min": round(qty * 60 / self.rate[e] * r.uniform(1.0, 1.25)), "product": prod}
        self.lot_events.appendleft(ev)
        if r.random() < .3:
            self.out_events.appendleft(ev)

    def _seed_lot_events(self, now):
        for i in range(150, 0, -1):
            self._lot_event(now - timedelta(minutes=i * 2.5))

    def _seed_pm_events(self, now):
        r = self.rng
        for _ in range(26):
            e = r.choice(self.eq)
            t, _, _ = r.choice(PM_TYPES)
            auto = r.random() * 100 < self.pm_level[e]
            self.pm_events.append({
                "time": now - timedelta(hours=r.uniform(1, 24 * 7)), "eq": e, "type": t, "auto": auto,
                "minutes": round(r.uniform(20, 70) * (.6 if auto else 1.0) * (1 + PM_TYPES.index(
                    next(p for p in PM_TYPES if p[0] == t)) * .8)),
                "result": "완료" if r.random() > .08 else "재작업", "owner": "Auto" if auto else r.choice(TECHNICIANS)})
        self.pm_events.sort(key=lambda x: x["time"], reverse=True)

    # ── 실시간 진행 ──
    def tick(self, now):
        r = self.rng
        self.roll(now)
        down = self.down_now()
        for ep in down.values():
            if r.random() < .006:
                ep["end"] = now
        if len(down) < 3 and r.random() < .004 * self.bias:
            e = r.choice([x for x in self.eq if x not in down])
            self.episodes.append(self._make_episode(e, now, None))
        if r.random() < .45:
            self._lot_event(now)
        self._cache_key = None

    # ── 파생 그리드 (설비 × 시간) ──
    def grids(self, now):
        key = (now.replace(second=0, microsecond=0), len(self.episodes),
               sum(1 for ep in self.episodes if ep["end"] is None))
        if self._grids is not None and self._cache_key == key:
            return self._grids
        starts = [self.anchor + timedelta(hours=i) for i in range(BUCKETS)]
        elapsed = [60.0] * (BUCKETS - 1) + [max(1.0, (now - starts[-1]).total_seconds() / 60)]
        g = {k: {} for k in ("down", "util", "move", "output", "queue", "tpss", "idle", "pm")}
        for e in self.eq:
            down = [0.0] * BUCKETS
            for ep in self.episodes:
                if ep["eq"] != e:
                    continue
                s, t = ep["start"], ep["end"] or now
                for i, b in enumerate(starts):
                    be = min(b + timedelta(hours=1), now)
                    ov = (min(t, be) - max(s, b)).total_seconds() / 60
                    if ov > 0:
                        down[i] += ov
            down = [min(d, elapsed[i]) for i, d in enumerate(down)]
            util, move, out, queue, tpss, idle, pm = [], [], [], [], [], [], []
            backlog = 0.0
            for i in range(BUCKETS):
                el = elapsed[i]
                pm_m = min(self.pm_min[e][i] * el / 60, el - down[i])
                idle_m = max(0.0, (el - down[i] - pm_m) * self.idle[e][i])
                run = max(0.0, el - down[i] - pm_m - idle_m)
                u = run / el * 100
                mv = self.rate[e] * run / 60 * self.noise[e][i]
                backlog = backlog * .72 + down[i] * 6.5
                util.append(u)
                move.append(mv)
                out.append(mv * self.out_ratio[e])
                queue.append(self.q_base[e] * self.noise[e][i] + backlog)
                tpss.append(_clamp(58 + .42 * u + (self.noise[e][i] - 1) * 40, 40, 100))
                idle.append(idle_m / el * 100)
                pm.append(pm_m / el * 100)
            g["down"][e], g["util"][e], g["move"][e], g["output"][e] = down, util, move, out
            g["queue"][e], g["tpss"][e] = queue, tpss
            g["idle"][e], g["pm"][e] = idle, pm
        g["elapsed"] = elapsed
        g["starts"] = starts
        self._grids, self._cache_key = g, key
        return g


# ─── 공정 묶음(단일 공정 또는 전체) 위의 KPI 계산 ─────────────────


class View:
    def __init__(self, states, now, label):
        self.states, self.now, self.label = states, now, label
        self.is_all = len(states) > 1
        self.eq = [e for s in states for e in s.eq]
        self.eq_type = {e: t for s in states for e, t in s.eq_type.items()}
        self.eq_proc = {e: s.proc for s in states for e in s.eq}
        self.g = {}
        for s in states:
            sg = s.grids(now)
            for k, v in sg.items():
                if isinstance(v, dict):
                    self.g.setdefault(k, {}).update(v)
                else:
                    self.g[k] = v
        self.el = self.g["elapsed"]
        self.down = {}
        for s in states:
            self.down.update(s.down_now())

    # 버킷 구간: 오늘 = 마지막 24, 전일 = 그 앞 24
    @staticmethod
    def rng_of(day):
        return range(HOURS, BUCKETS) if day == "today" else range(0, HOURS)

    def series(self, kid, day="today"):
        g, idx = self.g, list(self.rng_of(day))
        if kid == "downloss":
            return [sum(g["down"][e][i] for e in self.eq) / (len(self.eq) * self.el[i]) * 100 for i in idx]
        if kid in ("util", "tpss"):
            return [_mean(g[kid][e][i] for e in self.eq) for i in idx]
        if kid in ("move", "output"):  # 진행 중 구간은 시간당 환산
            return [sum(g[kid][e][i] for e in self.eq) * 60 / self.el[i] for i in idx]
        if kid == "bottleneck":
            return [max(g["queue"][e][i] for e in self.eq) for i in idx]
        if kid == "pm_auto":
            return [_mean(s.weekly[e][w] for s in self.states for e in s.eq) for w in range(WEEKS)]
        raise KeyError(kid)

    def value(self, kid, day="today"):
        g, idx = self.g, list(self.rng_of(day))
        if kid == "downloss":
            return sum(g["down"][e][i] for e in self.eq for i in idx) / (len(self.eq) * sum(self.el[i] for i in idx)) * 100
        if kid in ("util", "tpss"):
            w = sum(self.el[i] for i in idx)
            return _mean(sum(g[kid][e][i] * self.el[i] for i in idx) / w for e in self.eq)
        if kid in ("move", "output"):
            total = sum(g[kid][e][i] for e in self.eq for i in idx)
            if day == "yday":  # 오늘 마지막 구간이 진행 중이므로 전일도 같은 경과 시간만큼만 비교
                last = idx[-1]
                total -= sum(g[kid][e][last] for e in self.eq) * (1 - self.el[-1] / 60)
            return total
        if kid == "bottleneck":
            return max(g["queue"][e][idx[-1]] for e in self.eq)
        if kid == "pm_auto":
            sr = self.series("pm_auto")
            return sr[-1] if day == "today" else sr[-5]
        raise KeyError(kid)

    def target(self, kid):
        if kid in ("move", "output"):
            return sum(s.targets[kid] for s in self.states)
        return KPI_BY_ID[kid]["target"]

    def eq_total(self, kid, e, day="today"):
        g, idx = self.g, list(self.rng_of(day))
        if kid == "downloss":
            return sum(g["down"][e][i] for i in idx) / 60
        if kid in ("util", "tpss"):
            return _mean(g[kid][e][i] for i in idx)
        if kid in ("move", "output"):
            return sum(g[kid][e][i] for i in idx)
        if kid == "bottleneck":
            return g["queue"][e][idx[-1]]
        if kid == "pm_auto":
            s = next(s for s in self.states if e in s.weekly)
            return s.weekly[e][-1]
        raise KeyError(kid)


def status_of(kid, value, target):
    k = KPI_BY_ID[kid]
    if kid == "bottleneck":
        ratio = target / max(value, 1)
    elif k["lower"]:
        ratio = target / max(value, 1e-9)
    else:
        ratio = value / target
    st = "ok" if ratio >= 1 else "warn" if ratio >= .95 else "bad"
    return st, round(min(ratio, 1) * 100)


def fmt(v, digits=0):
    return f"{v:,.{digits}f}"


def _delta(kid, cur, prev, label):
    k = KPI_BY_ID[kid]
    diff = cur - prev
    good = (diff <= 0) if k["lower"] else (diff >= 0)
    arrow = "▲" if diff >= 0 else "▼"
    if k["unit"] == "%":
        txt = f"{arrow} {abs(diff):.{max(1, k['digits'])}f}%p {label}"
    else:
        txt = f"{arrow} {abs(diff) / max(prev, 1) * 100:.1f}% {label}"
    return txt, ("up" if good else "down")


def build_card(v: View, kid: str) -> dict:
    k = KPI_BY_ID[kid]
    target = v.target(kid)
    cur = v.value(kid)
    st, pct = status_of(kid, cur, target)
    series = v.series(kid)
    card = {"id": kid, "kind": "core", "category": k["category"], "name": k["name"], "en": k["en"], "unit": k["unit"],
            "status": st, "pct": pct,
            "value": round(cur, 3), "target": round(target, 3), "spark": [round(x, 3) for x in series[-12:]],
            "text": False}
    if kid == "bottleneck":
        idx = BUCKETS - 1
        ranked = sorted(v.eq, key=lambda e: v.g["queue"][e][idx], reverse=True)
        top = ranked[0]
        heavy = [e for e in ranked if v.g["queue"][e][idx] >= queue_limit() * .8]
        s = next(s for s in v.states if top in s.eq)
        wait = v.g["queue"][top][idx] / s.rate[top]
        card.update(display=top, text=True, unit="", target_text=f"≤ {queue_limit():,} wf",
                    delta=f"병목 후보 {len(heavy)}대 · 대기 {wait:.1f}h", delta_class="down" if st != "ok" else "up",
                    sub=v.eq_proc[top]["name"])
        return card
    prev = v.value(kid, "yday")
    label = "전월" if kid == "pm_auto" else "전일"
    card["delta"], card["delta_class"] = _delta(kid, cur, prev, label)
    card["display"] = fmt(cur, k["digits"] if k["unit"] == "%" else 0)
    tgt = fmt(target, k["digits"] if k["unit"] == "%" else 0) + ("%" if k["unit"] == "%" else "")
    card["target_text"] = ("≤ " if k["lower"] else "") + tgt
    return card


# ─── KPI 상세 ─────────────────────────────────────────────────────


def _hour_labels(v: View):
    return [s.strftime("%H:00") for s in v.g["starts"][HOURS:]]


def _week_labels(now):
    wk = now.isocalendar().week
    return [f"W{((wk - WEEKS + 1 + i - 1) % 52) + 1:02d}" for i in range(WEEKS)]


def _fmt_dur(minutes):
    minutes = int(minutes)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def _cell(t, k=None, s=None):
    return {"t": t, "k": k, "s": s}


def build_detail(v: View, kid: str) -> dict:
    k = KPI_BY_ID[kid]
    now = v.now
    card = build_card(v, kid)
    target = v.target(kid)
    d = {"kpi": card, "meta": {"name": k["name"], "en": k["en"], "unit": k["unit"], "digits": k["digits"],
                               "lower": k["lower"], "sources": k["sources"], "formula": k["formula"],
                               "target": target}}
    today, yday = v.series(kid), (None if kid == "pm_auto" else v.series(kid, "yday"))
    hourly_target = target / HOURS if kid in ("move", "output") else target
    d["trend"] = {
        "title": "주간 PM 자동화율 추이" if kid == "pm_auto" else f"시간대별 {k['name']} 추이",
        "sub": "최근 12주 · 주 단위" if kid == "pm_auto" else "최근 24시간 · 1시간 단위 (마지막 구간은 진행 중)",
        "labels": _week_labels(now) if kid == "pm_auto" else _hour_labels(v),
        "today": [round(x, 3) for x in today], "prev": None if yday is None else [round(x, 3) for x in yday],
        "prev_label": "전일", "target": round(hourly_target, 2), "lower": k["lower"],
        "unit": "wf" if kid == "bottleneck" else k["unit"],
        "series_label": {"bottleneck": "최대 대기 WIP (wf)", "move": "시간당 MOVE", "output": "시간당 생산량 (wf)"}
        .get(kid, f"{k['name']} ({k['unit']})"),
    }
    if kid in ("move", "output"):
        d["trend"]["sub"] = "최근 24시간 · 1시간 단위 · 목표선 = 일 목표 ÷ 24 (마지막 구간은 진행 중)"

    d["stats"] = _stats(v, kid)
    d["breakdown"] = _breakdown(v, kid)
    d["ranking"] = _ranking(v, kid)
    d["heatmap"] = _heatmap(v, kid, d["ranking"])
    d["events"] = _events(v, kid)
    d["events"]["sub"] = EVENT_SUB.get(kid, "최근 순 · 10분 이내 신규 이벤트 강조")
    d["insights"] = _insights(v, kid, d)
    return d


def _stats(v: View, kid):
    g, now = v.g, v.now
    down = v.down
    today = list(View.rng_of("today"))
    if kid == "downloss":
        win = now - timedelta(hours=HOURS)
        eps = [ep for s in v.states for ep in s.episodes]
        cnt = sum(1 for ep in eps if ep["start"] >= win)
        cnt_y = sum(1 for ep in eps if win - timedelta(hours=HOURS) <= ep["start"] < win)
        fin = [(ep["end"] - ep["start"]).total_seconds() / 3600 for ep in eps if ep["end"] and ep["start"] >= win]
        fin_y = [(ep["end"] - ep["start"]).total_seconds() / 3600 for ep in eps
                 if ep["end"] and win - timedelta(hours=HOURS) <= ep["start"] < win]
        tot = sum(v.eq_total(kid, e) for e in v.eq)
        tot_y = sum(v.eq_total(kid, e, "yday") for e in v.eq)
        mttr, mttr_y = _mean(fin), _mean(fin_y)
        return [
            {"label": "총 정지시간", "value": fmt(tot, 1), "unit": "h", "delta": f"{'▲' if tot >= tot_y else '▼'} {abs(tot - tot_y):.1f}h", "tone": "bad" if tot > tot_y else "good"},
            {"label": "정지 건수", "value": fmt(cnt), "unit": "건", "delta": f"{'▲' if cnt >= cnt_y else '▼'} {abs(cnt - cnt_y)}", "tone": "bad" if cnt > cnt_y else "good"},
            {"label": "MTTR (복구 완료 기준)", "value": fmt(mttr, 2), "unit": "h", "delta": f"{'▲' if mttr >= mttr_y else '▼'} {abs(mttr - mttr_y):.2f}h", "tone": "bad" if mttr > mttr_y else "good"},
            {"label": "현재 정지 설비", "value": fmt(len(down)), "unit": "대", "note": " · ".join(sorted(down)[:4]) or "없음", "tone": "alert" if down else "neutral"},
        ]
    if kid == "util":
        idle = _mean(_mean(g["idle"][e][i] for i in today) for e in v.eq)
        low = min(v.eq, key=lambda e: v.eq_total(kid, e))
        return [
            {"label": "평균 가동률 (Run)", "value": fmt(v.value(kid), 1), "unit": "%"},
            {"label": "Idle 비율", "value": fmt(idle, 1), "unit": "%", "note": "Dispatch · 물류 대기"},
            {"label": "최저 가동 설비", "value": low, "unit": "", "note": f"{v.eq_total(kid, low):.1f}%", "tone": "alert", "mono": True},
            {"label": "현재 가동 설비", "value": f"{len(v.eq) - len(down)}", "unit": f"/ {len(v.eq)}대", "note": f"정지 {len(down)}대", "tone": "neutral"},
        ]
    if kid == "pm_auto":
        evs = [ev for s in v.states for ev in s.pm_events]
        auto = sum(1 for ev in evs if ev["auto"])
        full = sum(1 for s in v.states for e in s.eq if s.weekly[e][-1] >= 90)
        return [
            {"label": "이번 주 자동화율", "value": fmt(v.value(kid), 1), "unit": "%", "delta": _delta(kid, v.value(kid), v.value(kid, "yday"), "4주 전")[0], "tone": "good"},
            {"label": "최근 7일 PM 수행", "value": fmt(len(evs)), "unit": "건", "note": f"자동 {auto} · 수동 {len(evs) - auto}"},
            {"label": "수동 PM 비중", "value": fmt((len(evs) - auto) / max(1, len(evs)) * 100, 1), "unit": "%", "tone": "neutral"},
            {"label": "자동화 90% 이상 설비", "value": fmt(full), "unit": f"/ {len(v.eq)}대"},
        ]
    if kid in ("move", "output"):
        cur, tgt = v.value(kid), v.target(kid)
        hourly = _mean(v.series(kid)[:-1])
        if kid == "move":
            best = max(v.eq, key=lambda e: v.eq_total(kid, e))
            extra = {"label": "최다 MOVE 설비", "value": best, "unit": "", "note": f"{fmt(v.eq_total(kid, best))} mv", "mono": True}
        else:
            mix = _breakdown(v, "output")["items"]
            top = max(mix, key=lambda x: x["value"])
            extra = {"label": "최다 생산 제품군", "value": top["name"], "unit": "", "note": f"{fmt(top['value'])} wf"}
        return [
            {"label": "최근 24h 누적", "value": fmt(cur), "unit": KPI_BY_ID[kid]["unit"]},
            {"label": "시간당 평균", "value": fmt(hourly), "unit": KPI_BY_ID[kid]["unit"]},
            {"label": "일 목표 대비", "value": fmt(cur / tgt * 100, 1), "unit": "%", "note": f"목표 {fmt(tgt)}", "tone": "good" if cur >= tgt else "bad"},
            extra,
        ]
    if kid == "bottleneck":
        idx = BUCKETS - 1
        ranked = sorted(v.eq, key=lambda e: g["queue"][e][idx], reverse=True)
        top = ranked[0]
        s = next(s for s in v.states if top in s.eq)
        heavy = sum(1 for e in ranked if g["queue"][e][idx] >= queue_limit() * .8)
        return [
            {"label": "병목 설비", "value": top, "unit": "", "note": v.eq_type[top], "mono": True, "tone": "alert"},
            {"label": "최대 대기 WIP", "value": fmt(g["queue"][top][idx]), "unit": "wf", "note": f"기준 {queue_limit():,}"},
            {"label": "예상 대기시간", "value": fmt(g["queue"][top][idx] / s.rate[top], 1), "unit": "h"},
            {"label": "적체 설비 (기준 80%↑)", "value": fmt(heavy), "unit": "대", "tone": "alert" if heavy else "neutral"},
        ]
    if kid == "tpss":
        vals = {e: v.eq_total(kid, e) for e in v.eq}
        low = min(vals, key=vals.get)
        return [
            {"label": "TPSS 평균", "value": fmt(v.value(kid), 1), "unit": "%"},
            {"label": "A등급 설비 (95↑)", "value": fmt(sum(1 for x in vals.values() if x >= 95)), "unit": "대"},
            {"label": "기준 미달 설비", "value": fmt(sum(1 for x in vals.values() if x < KPI_BY_ID[kid]["target"])), "unit": "대", "tone": "alert"},
            {"label": "최저 설비", "value": low, "unit": "", "note": f"{vals[low]:.1f}%", "mono": True},
        ]
    return []


def _breakdown(v: View, kid):
    g = v.g
    today = list(View.rng_of("today"))
    if kid == "downloss":
        win = v.now - timedelta(hours=HOURS)
        hrs = {t: 0.0 for t, *_ in DOWN_TYPES}
        for s in v.states:
            for ep in s.episodes:
                st, en = max(ep["start"], win), ep["end"] or v.now
                if en > st:
                    hrs[ep["kind"]] += (en - st).total_seconds() / 3600
        items = [{"name": DOWN_TYPE_LABEL[t], "value": round(h, 1)} for t, h in hrs.items()]
        return {"title": "원인별 구성", "sub": f"정지시간 {sum(hrs.values()):.1f}h 기준", "unit": "h", "share": True,
                "items": items, "src": "MES 이벤트"}
    if kid == "util":
        idle = _mean(_mean(g["idle"][e][i] for i in today) for e in v.eq)
        pm = _mean(_mean(g["pm"][e][i] for i in today) for e in v.eq)
        downp = v.value("downloss")
        run = v.value("util")
        items = [{"name": "Run (생산)", "value": round(run, 1)}, {"name": "Idle (대기)", "value": round(idle, 1)},
                 {"name": "Down (비계획 정지)", "value": round(downp, 1)}, {"name": "PM (계획 정비)", "value": round(pm, 1)}]
        return {"title": "시간 구성", "sub": "캘린더 시간 대비 비율", "unit": "%", "share": True, "items": items, "src": "EES"}
    if kid == "pm_auto":
        rates = {t: _mean(s.pm_type_rate[t] for s in v.states) for t, *_ in PM_TYPES}
        return {"title": "PM 유형별 자동화율", "sub": "자동 수행 항목 비율", "unit": "%", "share": False, "max": 100,
                "items": [{"name": f"{t} PM", "value": round(r, 1)} for t, r in rates.items()], "src": "PMS"}
    if kid in ("move", "output") and v.is_all and kid == "move":
        items = [{"name": s.proc["name"], "value": round(sum(g["move"][e][i] for e in s.eq for i in today))} for s in v.states]
        return {"title": "공정별 MOVE", "sub": "최근 24시간", "unit": "mv", "share": True, "items": items, "src": "MES"}
    if kid == "move":
        items = []
        for t in dict.fromkeys(v.eq_type.values()):
            items.append({"name": t, "value": round(sum(g["move"][e][i] for e in v.eq if v.eq_type[e] == t for i in today))})
        return {"title": "설비 유형별 MOVE", "sub": "최근 24시간", "unit": "mv", "share": True, "items": items, "src": "MES"}
    if kid == "output":
        tot = {}
        for s in v.states:
            out = sum(g["output"][e][i] for e in s.eq for i in today)
            w = sum(x for _, x in s.product_mix)
            for n, x in s.product_mix:
                tot[n] = tot.get(n, 0) + out * x / w
        return {"title": "제품군별 생산량", "sub": "최근 24시간", "unit": "wf", "share": True,
                "items": [{"name": n, "value": round(x)} for n, x in tot.items()], "src": "MES"}
    if kid == "bottleneck":
        idx = BUCKETS - 1
        tot = {"설비 정지": 0.0, **{n: 0.0 for n, _ in QUEUE_REASONS}}
        for s in v.states:
            for e in s.eq:
                q = g["queue"][e][idx]
                base = s.q_base[e] * s.noise[e][idx]
                tot["설비 정지"] += max(0.0, q - base)
                tot[s.queue_reason[e]] += min(q, base)
        return {"title": "대기 WIP 사유", "sub": f"현재 대기 WIP {sum(tot.values()):,.0f}매", "unit": "wf", "share": True,
                "items": [{"name": n, "value": round(x)} for n, x in tot.items()], "src": "MES WIP"}
    if kid == "tpss":
        vals = [v.eq_total(kid, e) for e in v.eq]
        bands = [("A (95 이상)", lambda x: x >= 95), ("B (90–95)", lambda x: 90 <= x < 95),
                 ("C (85–90)", lambda x: 85 <= x < 90), ("D (85 미만)", lambda x: x < 85)]
        return {"title": "설비 등급 분포", "sub": f"설비 {len(vals)}대 · 24h 평균", "unit": "대", "share": True,
                "items": [{"name": n, "value": sum(1 for x in vals if f(x))} for n, f in bands], "src": "TPSS"}
    return {"items": []}


def _ranking(v: View, kid):
    vals = {e: v.eq_total(kid, e) for e in v.eq}
    k = KPI_BY_ID[kid]
    worst_low = kid in ("util", "pm_auto", "tpss")
    order = sorted(vals, key=vals.get, reverse=not worst_low)[:10]
    titles = {"downloss": ("설비별 정지시간 Top 10", "h", "단위: 시간 · 붉은 표시 = 현재 정지 중"),
              "util": ("가동률 하위 10 설비", "%", "최근 24h 평균 · 붉은 표시 = 목표 −10%p 미만"),
              "pm_auto": ("자동화율 하위 10 설비", "%", "이번 주 기준 · 붉은 표시 = 50% 미만"),
              "move": ("설비별 MOVE Top 10", "mv", "최근 24시간 누적"),
              "bottleneck": ("대기 WIP Top 10", "wf", f"현재 기준 · 붉은 표시 = {queue_limit():,}매 이상"),
              "output": ("설비별 생산량 Top 10", "wf", "최근 24시간 누적"),
              "tpss": ("TPSS 하위 10 설비", "%", "최근 24h 평균 · 붉은 표시 = 85 미만")}
    title, unit, sub = titles[kid]

    def alert(e):
        x = vals[e]
        if kid == "downloss":
            return e in v.down
        if kid == "util":
            return x < k["target"] - 10
        if kid == "pm_auto":
            return x < 50
        if kid == "bottleneck":
            return x >= queue_limit()
        if kid == "tpss":
            return x < 85
        return False

    digits = 1 if unit in ("h", "%") else 0
    return {"title": title, "sub": sub, "unit": unit, "digits": digits,
            "max": 100 if unit == "%" else None, "src": k["sources"][0].split()[0],
            "items": [{"eq": e, "value": round(vals[e], digits), "alert": alert(e),
                       "proc": v.eq_proc[e]["name"] if v.is_all else None} for e in order]}


def _heatmap(v: View, kid, ranking):
    rows = [it["eq"] for it in ranking["items"]]
    g = v.g
    today = list(View.rng_of("today"))
    if kid == "pm_auto":
        cols = _week_labels(v.now)
        vals = [[round(next(s for s in v.states if e in s.weekly).weekly[e][w], 1) for w in range(WEEKS)] for e in rows]
        return {"title": "설비 × 주차 자동화율", "sub": "셀 농도 = 자동화 부족분 · 붉은 셀 = 50% 미만", "rows": rows,
                "cols": cols, "values": vals, "mode": "low_bad", "min": 30, "max": 100, "alert": 50, "unit": "%"}
    key, mode, lo, hi, alert, unit, sub = {
        "downloss": ("down", "high_bad", 0, 60, 45, "min", "셀 농도 = 해당 시간 정지 분(min) · 붉은 셀 = 45분 이상"),
        "util": ("util", "low_bad", 40, 100, 60, "%", "셀 농도 = 가동 손실 · 붉은 셀 = 가동률 60% 미만"),
        "move": ("move", "volume", 0, None, None, "mv", "셀 농도 = 시간당 MOVE · 붉은 셀 = 설비 평균 30% 미만 (Stall)"),
        "bottleneck": ("queue", "high_bad", 0, max(1400, queue_limit() * 1.4), queue_limit(), "wf", f"셀 농도 = 대기 WIP · 붉은 셀 = {queue_limit():,}매 이상"),
        "output": ("output", "volume", 0, None, None, "wf", "셀 농도 = 시간당 생산량 · 붉은 셀 = 설비 평균 30% 미만 (Stall)"),
        "tpss": ("tpss", "low_bad", 70, 100, 85, "%", "셀 농도 = 기준 대비 부족분 · 붉은 셀 = 85 미만"),
    }[kid]
    vals = [[round(g[key][e][i] / (v.el[i] / 60 if kid in ("move", "output") else 1), 1) for i in today] for e in rows]
    if hi is None:
        hi = max((x for r in vals for x in r), default=1)
    return {"title": "설비 × 시간 히트맵", "sub": sub, "rows": rows, "cols": [s[:2] for s in _hour_labels(v)],
            "values": vals, "mode": mode, "min": lo, "max": hi, "alert": alert, "unit": unit}


def _events(v: View, kid):
    now = v.now
    hm = lambda t: t.strftime("%H:%M:%S")
    fresh = lambda t: (now - t).total_seconds() < 600
    rows = []
    if kid in ("downloss", "util"):
        eps = sorted((ep for s in v.states for ep in s.episodes), key=lambda x: x["start"], reverse=True)
        if kid == "downloss":
            for ep in eps[:9]:
                ongoing = ep["end"] is None
                dur = ((ep["end"] or now) - ep["start"]).total_seconds() / 60
                rows.append({"new": fresh(ep["start"]), "cells": [
                    _cell(hm(ep["start"]), "mono"), _cell(ep["eq"], "eq"), _cell(ep["kind"]), _cell(ep["code"], "mono"),
                    _cell(ep["desc"]), _cell(("진행 " if ongoing else "") + _fmt_dur(dur), "mono"),
                    _cell("정지중" if ongoing else "복구", "status", "run" if ongoing else "done"), _cell(ep["owner"])]})
            return {"title": "실시간 정지 이벤트", "src": "MES · 실시간 스트림",
                    "columns": ["발생", "설비", "유형", "원인 코드", "내용", "지속", "상태", "담당"], "rows": rows}
        trans = []
        for ep in eps[:20]:
            trans.append((ep["start"], ep["eq"], "RUN → DOWN", ep["desc"], "run", ep["owner"]))
            if ep["end"]:
                trans.append((ep["end"], ep["eq"], "DOWN → RUN", f"{ep['kind']} 조치 완료", "done", ep["owner"]))
        trans.sort(key=lambda x: x[0], reverse=True)
        for t, e, chg, why, sc, who in trans[:9]:
            rows.append({"new": fresh(t), "cells": [_cell(hm(t), "mono"), _cell(e, "eq"), _cell(v.eq_type[e]),
                                                     _cell(chg, "status", sc), _cell(why), _cell(who)]})
        return {"title": "설비 상태 변경 이력", "src": "EES · 실시간 스트림",
                "columns": ["시각", "설비", "유형", "상태 변경", "사유", "담당"], "rows": rows}
    if kid == "pm_auto":
        evs = sorted((ev for s in v.states for ev in s.pm_events), key=lambda x: x["time"], reverse=True)[:9]
        for ev in evs:
            rows.append({"new": False, "cells": [
                _cell(ev["time"].strftime("%m-%d %H:%M"), "mono"), _cell(ev["eq"], "eq"), _cell(f"{ev['type']} PM"),
                _cell("자동" if ev["auto"] else "수동", "status", "pm" if ev["auto"] else "done"),
                _cell(_fmt_dur(ev["minutes"]), "mono"), _cell(ev["result"], "status", "done" if ev["result"] == "완료" else "run"),
                _cell(ev["owner"])]})
        return {"title": "PM 수행 이력", "src": "PMS", "columns": ["일시", "설비", "PM 유형", "방식", "소요", "결과", "담당"],
                "rows": rows}
    if kid in ("move", "output"):
        src = "lot_events" if kid == "move" else "out_events"
        evs = sorted((ev for s in v.states for ev in getattr(s, src)), key=lambda x: x["time"], reverse=True)[:9]
        for ev in evs:
            if kid == "move":
                cells = [_cell(hm(ev["time"]), "mono"), _cell(ev["lot"], "mono"), _cell(ev["eq"], "eq"),
                         _cell(ev["step"], "mono"), _cell(f"{ev['qty']} wf"), _cell(f"{ev['proc_min']}m", "mono")]
            else:
                cells = [_cell(hm(ev["time"]), "mono"), _cell(ev["lot"], "mono"), _cell(ev["product"]),
                         _cell(ev["eq"], "eq"), _cell(f"{ev['qty']} wf"), _cell("Out", "status", "pm")]
            rows.append({"new": fresh(ev["time"]), "cells": cells})
        cols = ["Track-out", "Lot ID", "설비", "Step", "매수", "처리시간"] if kid == "move" else \
            ["Out 시각", "Lot ID", "제품군", "최종 설비", "매수", "상태"]
        return {"title": "실시간 Track-out" if kid == "move" else "실시간 생산 완료 Lot", "src": "MES · 실시간 스트림",
                "columns": cols, "rows": rows}
    if kid == "bottleneck":
        idx = BUCKETS - 1
        ranked = sorted(v.eq, key=lambda e: v.g["queue"][e][idx], reverse=True)[:9]
        for e in ranked:
            s = next(s for s in v.states if e in s.eq)
            q = v.g["queue"][e][idx]
            prev = v.g["queue"][e][idx - 1]
            reason = "설비 정지" if e in v.down else s.queue_reason[e]
            sc, lbl = ("run", "적체") if q >= queue_limit() else ("pm", "주의") if q >= queue_limit() * .8 else ("done", "정상")
            rows.append({"new": False, "cells": [
                _cell(e, "eq"), _cell(v.eq_type[e]), _cell(f"{q:,.0f} wf", "mono"),
                _cell(f"{'▲' if q >= prev else '▼'} {abs(q - prev):,.0f}", "mono"), _cell(f"{q / s.rate[e]:.1f}h", "mono"),
                _cell(reason), _cell(lbl, "status", sc)]})
        return {"title": "대기 WIP 현황", "src": "MES WIP · 실시간",
                "columns": ["설비", "유형", "대기 WIP", "1h 변화", "예상 대기", "주요 사유", "상태"], "rows": rows}
    if kid == "tpss":
        idx = BUCKETS - 1
        ranked = sorted(v.eq, key=lambda e: v.g["tpss"][e][idx])[:9]
        tgt = KPI_BY_ID["tpss"]["target"]
        for e in ranked:
            x = v.g["tpss"][e][idx]
            sc, lbl = ("run", "미달") if x < 85 else ("pm", "주의") if x < tgt else ("done", "정상")
            rows.append({"new": False, "cells": [
                _cell(e, "eq"), _cell(v.eq_type[e]), _cell(f"{x:.1f}", "mono"), _cell(f"{tgt:.1f}", "mono"),
                _cell(f"{x - tgt:+.1f}", "mono"), _cell(f"{v.g['util'][e][idx]:.1f}%", "mono"), _cell(lbl, "status", sc)]})
        return {"title": "현재 시간대 TPSS 하위 설비", "src": "TPSS",
                "columns": ["설비", "유형", "TPSS", "기준", "편차", "가동률", "상태"], "rows": rows}
    return {"columns": [], "rows": []}


EVENT_SUB = {"bottleneck": "현재 대기 WIP 상위 설비 · 실시간 갱신", "tpss": "현재 시간대 TPSS 하위 설비",
             "pm_auto": "최근 7일 PM 수행 이력 · 최근 순"}


def _insights(v: View, kid, d):
    """규칙 기반 자동 분석 문구 (연결 데이터에서 계산)."""
    k = KPI_BY_ID[kid]
    out = []
    rk = d["ranking"]["items"]
    tr = d["trend"]
    unit = d["ranking"]["unit"]
    if rk:
        top = rk[0]
        total = sum(v.eq_total(kid, e) for e in v.eq) if kid in ("downloss", "move", "output") else None
        share = f" — 전체의 {top['value'] / total * 100:.0f}%" if total else ""
        verb = {"util": "가동률 최저", "pm_auto": "자동화율 최저", "tpss": "TPSS 최저", "move": "MOVE 최다",
                "output": "생산량 최다", "bottleneck": "대기 WIP 최대"}.get(kid, f"{k['name']} 최대")
        label = {"util": "24h 평균 가동률", "pm_auto": "이번 주 자동화율", "move": "24h MOVE",
                 "bottleneck": "현재 대기 WIP", "output": "24h 생산량", "tpss": "24h 평균 TPSS"}.get(kid, "")
        msg = f"{label} {top['value']:,.{d['ranking']['digits']}f}{unit}{share}."
        if top["alert"]:
            msg += " 관리 기준 이탈 — 조치 필요."
        if kid == "downloss":
            eq_eps = [ep for s in v.states for ep in s.episodes if ep["eq"] == top["eq"]
                      and ep["start"] >= v.now - timedelta(hours=HOURS)]
            if eq_eps:
                codes = {}
                for ep in eq_eps:
                    codes[ep["code"]] = codes.get(ep["code"], 0) + 1
                c = max(codes, key=codes.get)
                msg = f"최근 24h 정지 {len(eq_eps)}건, 정지시간 {top['value']}h{share}."
                if codes[c] >= 2:
                    msg += f" 동일 코드 {c} {codes[c]}회 반복 — 근본 원인 점검 권고."
        out.append({"level": "bad" if top["alert"] or kid in ("downloss", "bottleneck") else "info",
                    "title": f"{top['eq']} {verb}", "text": msg})
    if tr["labels"] and kid != "pm_auto":
        series = tr["today"][:-1]
        avg = _mean(series)
        if series:
            worse = max if k["lower"] or kid == "bottleneck" else min
            i = series.index(worse(series))
            lbl = tr["labels"][i]
            ratio = series[i] / avg if avg else 1
            digits = k["digits"] if k["unit"] == "%" else 0
            out.append({"level": "bad" if abs(ratio - 1) > .15 else "info",
                        "title": f"{lbl} 구간 {'최저' if worse is min else '최고'}치",
                        "text": f"{lbl} 시간대 {series[i]:,.{digits}f}{tr['unit']} — 24h 평균 대비 {ratio:.2f}배. "
                                + ("시간대별 편차 작음 (±10% 이내)." if abs(ratio - 1) < .1
                                   else "교대 전후 구간 — 인수인계 대기 여부 확인 필요." if lbl[:2] in ("05", "06", "07", "13", "14", "15", "21", "22", "23")
                                   else "해당 시간대 이벤트 확인 필요.")})
    elif kid == "pm_auto":
        sr = tr["today"]
        out.append({"level": "info", "title": f"12주간 {sr[-1] - sr[0]:+.1f}%p 변화",
                    "text": f"{tr['labels'][0]} {sr[0]:.1f}% → {tr['labels'][-1]} {sr[-1]:.1f}%. 목표 {k['target']:.0f}%까지 {max(0, k['target'] - sr[-1]):.1f}%p."})
    if v.is_all:
        cards = [(s.proc["name"], build_card(View([s], v.now, s.proc["name"]), kid)) for s in v.states]
        bad = [n for n, c in cards if c["status"] == "bad"]
        lower = KPI_BY_ID[kid]["lower"]
        if kid in ("move", "output"):
            best = max(cards, key=lambda x: x[1]["value"] / next(s for s in v.states if s.proc["name"] == x[0]).targets[kid])
        else:
            best = (min if lower else max)(cards, key=lambda x: x[1]["value"])
        out.append({"level": "bad" if bad else "info", "title": f"공정별 미달 {len(bad)}개",
                    "text": (f"미달 공정: {', '.join(bad)}. " if bad else "전 공정 목표 달성 또는 근접. ") + f"최고 달성 공정 {best[0]}."})
    else:
        items = d["breakdown"].get("items", [])
        if items:
            top = max(items, key=lambda x: x["value"])
            tot = sum(x["value"] for x in items)
            share = f" ({top['value'] / tot * 100:.0f}%)" if d["breakdown"].get("share") and tot else ""
            out.append({"level": "info", "title": f"{d['breakdown']['title']}: {top['name']} 최대",
                        "text": f"{top['name']} {top['value']:,}{d['breakdown']['unit']}{share}. 세부 이력은 아래 이벤트 표 참고."})
    return out


# ─── Provider ─────────────────────────────────────────────────────


class KPIProvider:
    """실데이터 연동 시 이 인터페이스를 구현한다."""

    def processes(self, kpis=None) -> list[dict]:
        """kpis: 상태 집계에 포함할 KPI id 목록 (None = 전체)."""
        raise NotImplementedError

    def cards(self, process: str) -> dict:
        raise NotImplementedError

    def detail(self, process: str, kpi: str) -> dict:
        raise NotImplementedError

    def tick(self) -> None:
        """주기적 갱신(폴링/스트림 반영). 기본 구현은 아무것도 하지 않는다."""

    def sources(self) -> list[dict]:
        return []


class SimulatedProvider(KPIProvider):
    def __init__(self):
        now = now_kst()
        self.states = {p["id"]: ProcessState(p, now) for p in PROCESSES}
        self.updated = now

    def _view(self, process):
        now = now_kst()
        if process == "all":
            return View(list(self.states.values()), now, "All")
        if process not in self.states:
            raise KeyError(process)
        return View([self.states[process]], now, PROC_BY_ID[process]["name"])

    def processes(self, kpis=None):
        now = now_kst()
        kpis = kpis or [k["id"] for k in KPIS]
        out = [{"id": "all", "name": "All", "en": "All"}]
        for p in PROCESSES:
            v = View([self.states[p["id"]]], now, p["name"])
            st = [build_card(v, kid)["status"] for kid in kpis]
            out.append({"id": p["id"], "name": p["name"], "en": p["en"], "eq_count": len(self.states[p["id"]].eq),
                        "down": len(v.down), "status": "bad" if "bad" in st else "warn" if "warn" in st else "ok",
                        "bad": st.count("bad"), "warn": st.count("warn")})
        return out

    def cards(self, process):
        v = self._view(process)
        cards = [build_card(v, k["id"]) for k in KPIS]
        return {"process": process, "label": v.label, "updated": v.now.isoformat(), "cards": cards,
                "summary": {s: sum(1 for c in cards if c["status"] == s) for s in ("ok", "warn", "bad")}}

    def detail(self, process, kpi):
        if kpi not in KPI_BY_ID:
            raise KeyError(kpi)
        v = self._view(process)
        d = build_detail(v, kpi)
        compare = []
        for s in self.states.values():
            c = build_card(View([s], v.now, s.proc["name"]), kpi)
            compare.append({"id": s.proc["id"], "name": s.proc["name"], "value": c["value"], "display": c["display"],
                            "target": c["target"], "pct": c["pct"], "status": c["status"],
                            "label": c["display"] if kpi == "bottleneck" else None})
            if kpi == "bottleneck":
                compare[-1]["display"] = fmt(c["value"])
        d["compare"] = {"items": compare, "target": KPI_BY_ID[kpi]["target"] if kpi not in ("move", "output") else None,
                        "unit": d["meta"]["unit"], "lower": d["meta"]["lower"] or kpi == "bottleneck",
                        "by_pct": kpi in ("move", "output")}
        d["process"], d["label"], d["updated"] = process, v.label, v.now.isoformat()
        return d

    def tick(self):
        now = now_kst()
        for s in self.states.values():
            s.tick(now)
        self.updated = now

    def sources(self):
        return [{"name": n, "status": "ok", "mode": "simulated"} for n in ("MES", "EES", "PMS", "TPSS", "FDC")]


def get_provider() -> KPIProvider:
    name = os.environ.get("KPI_PROVIDER", "simulated").lower()
    if name == "simulated":
        return SimulatedProvider()
    raise ValueError(f"Unknown KPI_PROVIDER: {name}")
