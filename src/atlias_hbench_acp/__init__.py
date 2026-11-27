"""Atlias for hbench: a thin ACP agent that hands each task to Atlias.

hbench installs this agent inside each task's container and sends it one prompt. Atlias itself is closed source and
is not in this repository: the agent packs the task workspace, sends it with the prompt to an Atlias bridge running on
the benchmark operator's machine (ATLIAS_BRIDGE_URL, default http://host.docker.internal:18788/run, authenticated with
ATLIAS_BRIDGE_TOKEN), and writes the files Atlias changed back into the workspace. hbench then runs the task's tests in
the container as usual.

This agent makes no model calls of its own. The usage it reports in the ACP PromptResponse is the sum of Atlias's own
per-call usage (uncached input, cache reads, cache writes, output) as returned by the model provider, so the tokens on
the board are exactly Atlias's.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import tarfile
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, RequestError, run_agent
from acp.schema import (AgentCapabilities, Implementation, InitializeResponse, NewSessionResponse, PromptResponse,
                        SessionConfigOptionSelect, SessionConfigSelectOption, SetSessionConfigOptionResponse, Usage)

# The bridge runs Atlias on one model (Claude Haiku 5.5 at low effort). hbench selects the model through this option;
# any other value is refused instead of being silently swapped.
MODEL = "anthropic/claude-haiku-5-5"


def model_option() -> SessionConfigOptionSelect:
    return SessionConfigOptionSelect(id="model", name="Model", category="model", type="select", current_value=MODEL,
                                     options=[SessionConfigSelectOption(value=MODEL, name="Claude Haiku 5.5")])

__version__ = "0.1.0"
BRIDGE = os.environ.get("ATLIAS_BRIDGE_URL", "http://host.docker.internal:18788/run")
SKIP = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache"}


def pack(root: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if any(part in SKIP for part in rel.parts) or not p.is_file():
                continue
            tar.add(p, arcname=rel.as_posix(), recursive=False)
    return buf.getvalue()


def unpack(data: bytes, root: Path, deleted: list[str]) -> None:
    base = root.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            dest = (base / m.name).resolve()
            if not m.isfile() or not str(dest).startswith(str(base)):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            if src is not None:
                dest.write_bytes(src.read())
    for rel in deleted:
        dest = (base / rel).resolve()
        if str(dest).startswith(str(base)) and dest.is_file():
            dest.unlink()


def post(body: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(BRIDGE, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class AtliasAgent:
    def __init__(self) -> None:
        self.sessions: dict[str, str] = {}

    async def initialize(self, protocol_version: int, client_capabilities: Any = None, client_info: Any = None, **kw: Any) -> InitializeResponse:
        return InitializeResponse(protocol_version=PROTOCOL_VERSION, agent_capabilities=AgentCapabilities(), auth_methods=[],
                                  agent_info=Implementation(name="atlias", title="Atlias", version=__version__))

    async def new_session(self, cwd: str, **kw: Any) -> NewSessionResponse:
        sid = uuid.uuid4().hex
        self.sessions[sid] = cwd
        return NewSessionResponse(session_id=sid, config_options=[model_option()])

    async def set_config_option(self, config_id: str, session_id: str, value: Any, **kw: Any) -> SetSessionConfigOptionResponse:
        if config_id != "model" or value not in (MODEL, MODEL.split("/", 1)[1]):
            raise RequestError(-32602, f"Atlias on hbench runs {MODEL} only; {config_id}={value} is not available")
        return SetSessionConfigOptionResponse(config_options=[model_option()])

    async def authenticate(self, **kw: Any) -> None:
        return None

    async def cancel(self, session_id: str, **kw: Any) -> None:
        return None

    async def prompt(self, session_id: str, prompt: list[Any], **kw: Any) -> PromptResponse:
        text = "\n".join(getattr(b, "text", "") for b in prompt if getattr(b, "type", "") == "text")
        root = Path(self.sessions.get(session_id) or os.getcwd())
        body = {"token": os.environ.get("ATLIAS_BRIDGE_TOKEN", ""), "prompt": text, "tar": base64.b64encode(pack(root)).decode()}
        res = await asyncio.to_thread(post, body, float(os.environ.get("ATLIAS_BRIDGE_TIMEOUT", "1800")))
        if res.get("tar"):
            unpack(base64.b64decode(res["tar"]), root, res.get("deleted") or [])
        u = res.get("usage") or {}
        inp, cr, cw, out = (int(u.get(k) or 0) for k in ("input", "cacheRead", "cacheWrite", "output"))
        return PromptResponse(stop_reason="end_turn",
                              usage=Usage(input_tokens=inp, cached_read_tokens=cr, cached_write_tokens=cw, output_tokens=out,
                                          total_tokens=inp + cr + cw + out))


def main() -> None:
    result = run_agent(AtliasAgent())
    if asyncio.iscoroutine(result):
        asyncio.run(result)
