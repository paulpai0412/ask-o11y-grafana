#!/usr/bin/env python3
"""Live, data-free completion regression. Uses configured OpenSandbox; no MCP receipts/dashboard writes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("completion_server", ROOT / "sandbox-analysis-mcp/server.py")
assert spec and spec.loader
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

from opensandbox.sync.sandbox import SandboxSync
from code_interpreter.sync.adapters.code_adapter import CodesAdapterSync

# Observe native SSE only: no filtering, synthetic terminal event, grace period or early close.
events = []
started = 0.0
class Response:
    def __init__(self, response): self.response = response
    def __getattr__(self, name): return getattr(self.response, name)
    def iter_lines(self):
        for line in self.response.iter_lines():
            try:
                event = json.loads(line.removeprefix("data:").strip())
                events.append({"elapsed": time.monotonic() - started, "type": event.get("type")})
            except (ValueError, AttributeError):
                pass
            yield line
class Stream:
    def __init__(self, stream): self.stream = stream
    def __enter__(self): return Response(self.stream.__enter__())
    def __exit__(self, exc_type, exc_value, traceback):
        return self.stream.__exit__(exc_type, exc_value, traceback)
class Client:
    def __init__(self, client): self.client = client
    def stream(self, *args, **kwargs): return Stream(self.client.stream(*args, **kwargs))

original_run = CodesAdapterSync.run
original_create = SandboxSync.create
expected_execd = os.environ["EXPECTED_EXECD_SHA256"]

def observe_run(self, *args, **kwargs):
    original = self._sse_client
    self._sse_client = Client(original)
    try:
        return original_run(self, *args, **kwargs)
    finally:
        self._sse_client = original

def verified_create(*args, **kwargs):
    sandbox = original_create(*args, **kwargs)
    try:
        digest = hashlib.sha256(sandbox.files.read_bytes("/opt/opensandbox/execd")).hexdigest()
        assert digest == expected_execd, f"execd digest {digest} != {expected_execd}"
    except BaseException:
        sandbox.kill()
        sandbox.close()
        raise
    return sandbox

bundle = json.dumps({"frame": {"schema": {"fields": [{"name": "x", "type": "number"}]}, "data": {"values": [[1]]}}, "validity_rules": []})
with patch.object(SandboxSync, "create", side_effect=verified_create), patch.object(CodesAdapterSync, "run", observe_run):
    for name, delay, failure in [("short", 0, False), ("long", 20, False), ("error", 0, True)]:
        events.clear()
        code = f"import time\ntime.sleep({delay})\nprint('completed body')\n"
        code += "raise ValueError('completion probe')" if failure else "emit({'finished': True}, name='probe.json')"
        started = time.monotonic()
        result = server.execute_opensandbox(bundle, code, 42)
        elapsed = time.monotonic() - started
        assert bool(result["error"]) == failure, result["error"]
        assert result["input_audit"]["input_rows"] == 1
        assert "completed body" in str(result["stdout"])
        completes = [e for e in events if e["type"] == "execution_complete"]
        if failure:
            assert not completes, completes
            assert result["error"]["name"] == "ValueError", result["error"]
        else:
            assert len(completes) == 1 and completes[0]["elapsed"] >= delay, events
            assert any('"finished":true' in str(item) or '"finished": true' in str(item) for item in result["results"]), result["results"]
        print(json.dumps({"case": name, "elapsed": elapsed, "events": events, "error": result["error"], "audit": result["input_audit"], "execd_sha256": expected_execd, "ok": True}), flush=True)
