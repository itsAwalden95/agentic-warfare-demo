"""
FastAPI + WebSocket server that drives one shared simulation and streams live
state to any number of dashboard viewers. REST endpoints let the presenter
play / pause / step / reset and retune the swarms on the fly.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .engine import Engine, SimConfig
from .watsonx_client import WatsonxClient

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class SimSession:
    """Owns the engine + run loop for the shared demo instance."""

    def __init__(self) -> None:
        self.wx = WatsonxClient()
        self.cfg = SimConfig()
        self.engine = Engine(self.cfg, self.wx)
        self.playing = False
        self.speed = 2.0            # ticks per second
        self.clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    def reset(self, cfg: SimConfig | None = None) -> None:
        if cfg:
            self.cfg = cfg
        self.engine = Engine(self.cfg, self.wx)
        self.playing = False

    def payload(self) -> dict[str, Any]:
        st = self.engine.state()
        st["events"] = self.engine.recent_events(30)
        st["playing"] = self.playing
        st["speed"] = self.speed
        return st

    async def broadcast(self) -> None:
        dead = []
        data = self.payload()
        for ws in list(self.clients):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def run_loop(self) -> None:
        """Background task: advance the engine while playing and broadcast."""
        while True:
            if self.playing and not self.engine.finished:
                async with self._lock:
                    self.engine.step()
                await self.broadcast()
                if self.engine.finished:
                    self.playing = False
                    await self.broadcast()
            await asyncio.sleep(max(0.05, 1.0 / max(0.1, self.speed)))


session = SimSession()
app = FastAPI(title="Agentic Cyber Range")


class ControlReq(BaseModel):
    action: str                       # play | pause | step | reset | speed
    speed: float | None = None
    seed: int | None = None
    size: str | None = None
    attackers: int | None = None
    defenders: int | None = None
    attacker_skill: float | None = None
    defender_skill: float | None = None
    max_ticks: int | None = None


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(session.run_loop())


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/api/state")
async def api_state() -> JSONResponse:
    return JSONResponse(session.payload())


@app.get("/api/health")
async def api_health() -> dict:
    return {"ok": True, "llm_active": session.wx.available}


@app.post("/api/control")
async def api_control(req: ControlReq) -> JSONResponse:
    async with session._lock:
        if req.action == "play":
            if session.engine.finished:
                session.reset(session.cfg)
            session.playing = True
        elif req.action == "pause":
            session.playing = False
        elif req.action == "step":
            session.playing = False
            if not session.engine.finished:
                session.engine.step()
        elif req.action == "speed" and req.speed:
            session.speed = max(0.25, min(20.0, req.speed))
        elif req.action == "reset":
            cfg = SimConfig(
                seed=req.seed if req.seed is not None else session.cfg.seed,
                size=req.size or session.cfg.size,
                attackers=req.attackers or session.cfg.attackers,
                defenders=req.defenders or session.cfg.defenders,
                attacker_skill=(req.attacker_skill
                                if req.attacker_skill is not None
                                else session.cfg.attacker_skill),
                defender_skill=(req.defender_skill
                                if req.defender_skill is not None
                                else session.cfg.defender_skill),
                max_ticks=req.max_ticks or session.cfg.max_ticks,
            )
            session.reset(cfg)
    await session.broadcast()
    return JSONResponse(session.payload())


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    session.clients.add(websocket)
    try:
        await websocket.send_json(session.payload())
        while True:
            # we don't require inbound messages; keep the socket alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        session.clients.discard(websocket)


def main() -> None:
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("cyberrange.server:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
