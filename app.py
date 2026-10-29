"""
K2 KPI Board - Backend
8대 공정 그룹 공통 핵심 KPI 대시보드 (카드 → 상세 분석 drill-down)
"""

import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from data_source import KPIS, PROC_BY_ID, get_provider

TICK_SEC = float(os.environ.get("KPI_TICK_SEC", "3"))

provider = get_provider()
templates = Jinja2Templates(directory="templates")


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
        msg = {"type": "kpi_update", "processes": provider.processes(), "cards": provider.cards(sub["process"])}
        if sub["kpi"]:
            msg["detail"] = provider.detail(sub["process"], sub["kpi"])
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
    if process != "all" and process not in PROC_BY_ID:
        raise HTTPException(status_code=404, detail=f"Unknown process: {process}")


# ─── Pages ───────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# ─── API ─────────────────────────────────────────────────────────


@app.get("/api/processes")
async def get_processes():
    return provider.processes()


@app.get("/api/kpis")
async def get_kpis(process: str = Query("all")):
    _check_process(process)
    return provider.cards(process)


@app.get("/api/kpis/{kpi_id}")
async def get_kpi_detail(kpi_id: str, process: str = Query("all")):
    _check_process(process)
    if kpi_id not in {k["id"] for k in KPIS}:
        raise HTTPException(status_code=404, detail=f"Unknown KPI: {kpi_id}")
    return provider.detail(process, kpi_id)


@app.get("/api/sources")
async def get_sources():
    return provider.sources()


@app.get("/api/health")
async def health():
    return {"status": "ok", "provider": type(provider).__name__, "clients": len(manager.subs)}


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
                if (process == "all" or process in PROC_BY_ID) and (kpi is None or kpi in {k["id"] for k in KPIS}):
                    manager.subs[websocket] = {"process": process, "kpi": kpi}
                    await manager.push(websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), reload=False)
