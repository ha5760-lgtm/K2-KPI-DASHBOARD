"""
K2 KPI Board - Backend
8대 공정 그룹 공통 핵심 KPI 대시보드 (카드 → 상세 분석 drill-down)
- 실시간 KPI: data_source (MES/EES/PMS/TPSS 연동 지점)
- 공통 KPI: custom_kpis (화면에서 추가/수정/월별 값 입력)
"""

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import custom_kpis
from data_source import KPI_BY_ID, KPI_CATEGORIES, PROC_BY_ID, get_provider

TICK_SEC = float(os.environ.get("KPI_TICK_SEC", "3"))

provider = get_provider()
templates = Jinja2Templates(directory="templates")


def _valid_process(process: str) -> bool:
    return process == "all" or process in PROC_BY_ID


def _kpi_kind(kpi: Optional[str]) -> Optional[str]:
    if kpi is None:
        return None
    if kpi in KPI_BY_ID:
        return "core"
    if custom_kpis.exists(kpi):
        return "custom"
    return None


def _detail(process: str, kpi: str):
    if _kpi_kind(kpi) == "core":
        return {"kind": "core", **provider.detail(process, kpi)}
    return custom_kpis.detail(process, kpi)


# ─── WebSocket ───────────────────────────────────────────────────


class ConnectionManager:
    """연결별로 구독(공정 · KPI)을 기억하고 해당 데이터만 push 한다."""

    def __init__(self):
        self.subs: dict[WebSocket, dict] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.subs[websocket] = {"process": "all", "kpi": None}

    def disconnect(self, websocket: WebSocket):
        self.subs.pop(websocket, None)

    async def push(self, websocket: WebSocket):
        sub = self.subs.get(websocket)
        if sub is None:
            return
        msg = {"type": "kpi_update", "processes": provider.processes(), "cards": provider.cards(sub["process"]),
               "custom": custom_kpis.overview(sub["process"])}
        if sub["kpi"] and _kpi_kind(sub["kpi"]):
            msg["detail"] = _detail(sub["process"], sub["kpi"])
        await websocket.send_json(msg)

    async def broadcast(self):
        for ws in list(self.subs):
            try:
                await self.push(ws)
            except Exception:
                self.disconnect(ws)


manager = ConnectionManager()


async def ticker():
    while True:
        await asyncio.sleep(TICK_SEC)
        try:
            provider.tick()
            await manager.broadcast()
        except Exception as e:  # 데이터 소스 오류로 루프가 죽지 않도록
            print(f"[ticker] {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(ticker())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="K2 KPI Board", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


def _check_process(process: str):
    if not _valid_process(process):
        raise HTTPException(status_code=404, detail=f"Unknown process: {process}")


# ─── Pages ───────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# ─── 실시간 KPI ──────────────────────────────────────────────────


@app.get("/api/processes")
async def get_processes():
    return provider.processes()


@app.get("/api/kpis")
async def get_kpis(process: str = Query("all")):
    _check_process(process)
    return {**provider.cards(process), "categories": KPI_CATEGORIES}


@app.get("/api/kpis/{kpi_id}")
async def get_kpi_detail(kpi_id: str, process: str = Query("all")):
    _check_process(process)
    if _kpi_kind(kpi_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown KPI: {kpi_id}")
    return _detail(process, kpi_id)


@app.get("/api/sources")
async def get_sources():
    return provider.sources()


@app.get("/api/health")
async def health():
    return {"status": "ok", "provider": type(provider).__name__, "clients": len(manager.subs)}


# ─── 공통 KPI (사용자 정의) ──────────────────────────────────────


class CustomKpiBody(BaseModel):
    category: Optional[str] = None
    name: Optional[str] = None
    en: Optional[str] = None
    unit: Optional[str] = None
    digits: Optional[int] = None
    lower: Optional[bool] = None
    target: Optional[float] = None
    agg: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None


class ValueBody(BaseModel):
    process: str
    period: str
    value: Optional[float] = None


@app.get("/api/custom-kpis")
async def get_custom_kpis(process: str = Query("all")):
    _check_process(process)
    return custom_kpis.overview(process)


@app.post("/api/custom-kpis")
async def create_custom_kpi(body: CustomKpiBody):
    try:
        kid = custom_kpis.create(body.model_dump(exclude_none=True))
    except custom_kpis.ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    await manager.broadcast()
    return {"id": kid}


@app.put("/api/custom-kpis/{kpi_id}")
async def update_custom_kpi(kpi_id: str, body: CustomKpiBody):
    # target 은 None(목표 없음)으로 되돌릴 수 있어야 하므로 명시된 필드만 반영
    fields = body.model_dump(exclude_unset=True)
    try:
        custom_kpis.update(kpi_id, fields)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown KPI: {kpi_id}") from None
    except custom_kpis.ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    await manager.broadcast()
    return {"id": kpi_id}


@app.delete("/api/custom-kpis/{kpi_id}")
async def delete_custom_kpi(kpi_id: str):
    try:
        custom_kpis.delete(kpi_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown KPI: {kpi_id}") from None
    await manager.broadcast()
    return {"deleted": kpi_id}


@app.put("/api/custom-kpis/{kpi_id}/values")
async def set_custom_value(kpi_id: str, body: ValueBody):
    try:
        custom_kpis.set_value(kpi_id, body.process, body.period, body.value)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown KPI: {kpi_id}") from None
    except custom_kpis.ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    await manager.broadcast()
    return {"id": kpi_id, "process": body.process, "period": body.period, "value": body.value}


# ─── WebSocket ───────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Client → {"type":"subscribe","process":"photo","kpi":"downloss"} / {"type":"ping"}"""
    await manager.connect(websocket)
    try:
        while True:
            msg = await websocket.receive_json()
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg.get("type") == "subscribe":
                process = msg.get("process") or "all"
                kpi = msg.get("kpi")
                if _valid_process(process) and (kpi is None or _kpi_kind(kpi)):
                    manager.subs[websocket] = {"process": process, "kpi": kpi}
                    await manager.push(websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), reload=False)
