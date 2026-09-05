#!/usr/bin/env python3
"""Crash between completion and response publication; never execute again without evidence."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from artifact_store import ArtifactStore, ArtifactAuthError, WorkflowContractError

ACTOR = {"org_id": "1", "user_id": "owner", "session_id": "S" * 32}


def child(directory):
    import os
    store = ArtifactStore(directory)
    original = store._durable_json
    def crash_after_completion(path, value):
        if path.name == "response.json":
            os._exit(73)
        return original(path, value)
    def execute():
        ref = store.write_json(ACTOR, store.create_run(ACTOR), "sandbox-execution", {"results": []})
        return {"ok": True, "refs": {"execution_ref": ref}}
    with patch.object(store, "_durable_json", crash_after_completion):
        store.run_once(ACTOR, "compute", {"frame": "approved"}, execute)


def main():
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run([sys.executable, __file__, directory], check=False)
        assert result.returncode == 73
        store = ArtifactStore(directory)
        operation = next((Path(directory) / "operations").iterdir())
        assert not (operation / "response.json").exists()
        try:
            completion = json.loads((operation / "completion.json").read_text())
        except (OSError, ValueError) as exc:
            raise AssertionError("crashed worker did not leave valid completion evidence") from exc
        recovered = store.reconcile_operation(ACTOR, operation.name)
        assert recovered["status"] == "completed" and recovered["result"] == completion["result"]
        def forbidden():
            raise AssertionError("compute redispatched")
        assert store.run_once(ACTOR, "compute", {"frame": "approved"}, forbidden) == completion["result"]
        for actor in [{**ACTOR, "session_id": "T" * 32}, {**ACTOR, "user_id": "other"}, {**ACTOR, "org_id": "2"}]:
            try:
                store.reconcile_operation(actor, operation.name)
            except ArtifactAuthError:
                pass
            else:
                raise AssertionError("foreign operation disclosed")
        def unknown():
            return {"ok": False, "evidence": {"effect_outcome": "indeterminate"}}
        pending = store.run_once(ACTOR, "compute", {"frame": "unknown"}, unknown)
        state = store.reconcile_operation(ACTOR, pending["evidence"]["operation_id"])
        assert state["status"] == "indeterminate" and not state["redispatch_allowed"]
        try:
            store.run_once(ACTOR, "compute", {"frame": "unknown"}, forbidden)
        except WorkflowContractError:
            pass
        else:
            raise AssertionError("unknown operation was replayed")
        (operation / "response.json").unlink()
        completion["operation_id"] = "0" * 64
        (operation / "completion.json").write_text(json.dumps(completion))
        try:
            store.reconcile_operation(ACTOR, operation.name)
        except WorkflowContractError:
            pass
        else:
            raise AssertionError("mismatched completion accepted")
    print("ok: real process death, identical recovered execution_ref, actor binding, unknown/mismatch fail closed")


if __name__ == "__main__":
    child(sys.argv[1]) if len(sys.argv) > 1 else main()
