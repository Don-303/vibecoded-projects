"""
QuantLab web app — FastAPI + Jinja2 layer over the shared engine in core.py.

Run:  .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8899
(or use the `quantlab` CLI: `quantlab --browser` / `quantlab --desktop`)
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from core import (
    BASE_DIR,
    KERNEL_NAME,
    OLLAMA_MODEL,
    OLLAMA_TEMPERATURE,
    OLLAMA_URL,
    NotebookPathError,
    build_chat_messages,
    create_notebook,
    expand_file_references,
    kernel_service,
    list_notebooks,
    load_notebook_file,
    save_notebook_file,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("quantlab.web")

STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await asyncio.to_thread(kernel_service.start)
    except Exception:
        log.exception("Failed to start kernel — /api/execute will return 503 "
                      "until the kernel is restarted.")
    try:
        yield
    finally:
        await asyncio.to_thread(kernel_service.shutdown)


app = FastAPI(title="QuantLab", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------


class ExecuteRequest(BaseModel):
    code: str


class ChatMessage(BaseModel):
    role: str
    content: str


class SaveRequest(BaseModel):
    path: str
    cells: List[Dict[str, Any]]
    chat: List[ChatMessage] = Field(default_factory=list)


class CreateRequest(BaseModel):
    name: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(default_factory=list)
    # Optional live notebook cells so the model can read the whole notebook.
    cells: Optional[List[Dict[str, Any]]] = None


# --------------------------------------------------------------------------
# Routes: UI
# --------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "ollama_model": OLLAMA_MODEL,
        "kernel_name": KERNEL_NAME,
    })


# --------------------------------------------------------------------------
# Routes: notebook file I/O
# --------------------------------------------------------------------------


@app.get("/api/notebooks")
async def notebooks():
    from core import NOTEBOOK_DIR
    return {"notebooks": list_notebooks(), "dir": str(NOTEBOOK_DIR)}


@app.post("/api/notebooks/create")
async def notebooks_create(req: CreateRequest):
    try:
        return {"path": create_notebook(req.name)}
    except NotebookPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/notebook")
async def notebook_load(path: str):
    try:
        return load_notebook_file(path)
    except NotebookPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Notebook not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse notebook: {exc}") from exc


@app.post("/api/notebook/save")
async def notebook_save(req: SaveRequest):
    try:
        n = save_notebook_file(req.path, req.cells, [m.model_dump() for m in req.chat])
    except NotebookPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"saved": True, "path": req.path, "cells": n}


# --------------------------------------------------------------------------
# Routes: kernel / execution
# --------------------------------------------------------------------------


@app.post("/api/execute")
async def execute(req: ExecuteRequest):
    if not kernel_service.is_alive():
        raise HTTPException(status_code=503, detail="Kernel is not running — restart it first")
    try:
        result = await asyncio.to_thread(kernel_service.execute, req.code)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(result)


@app.get("/api/kernel/status")
async def kernel_status():
    return {"alive": kernel_service.is_alive(), "kernel_name": KERNEL_NAME}


@app.post("/api/kernel/restart")
async def kernel_restart():
    if kernel_service.kernel_id is None:
        await asyncio.to_thread(kernel_service.start)
    else:
        await asyncio.to_thread(kernel_service.restart)
    return {"restarted": True}


@app.post("/api/kernel/interrupt")
async def kernel_interrupt():
    await asyncio.to_thread(kernel_service.interrupt)
    return {"interrupted": True}


# --------------------------------------------------------------------------
# Routes: local AI reviewer (Ollama proxy, NDJSON streaming)
# --------------------------------------------------------------------------


@app.post("/api/chat")
async def chat(req: ChatRequest):
    messages = [m.model_dump() for m in req.messages]
    if messages and messages[-1]["role"] == "user":
        # @path/to/file.py in the newest message -> its contents, so the
        # mentor can read files that aren't already in the notebook.
        messages[-1]["content"] = expand_file_references(messages[-1]["content"])
    payload = {
        "model": OLLAMA_MODEL,
        "stream": True,
        "options": {"temperature": OLLAMA_TEMPERATURE},
        "messages": build_chat_messages(messages, req.cells),
    }

    async def generate():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0)) as client:
                async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode(errors="replace")
                        yield json.dumps({"error": f"Ollama HTTP {resp.status_code}: {body[:500]}"}) + "\n"
                        return
                    async for line in resp.aiter_lines():
                        if line.strip():
                            yield line + "\n"
        except httpx.ConnectError:
            yield json.dumps({"error": f"Cannot reach Ollama at {OLLAMA_URL}. "
                                       f"Start it with `ollama serve` and pull the model: "
                                       f"`ollama pull {OLLAMA_MODEL}`."}) + "\n"
        except Exception as exc:  # noqa: BLE001
            yield json.dumps({"error": f"Chat proxy error: {exc}"}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8899)
