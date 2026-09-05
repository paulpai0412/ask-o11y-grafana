#!/usr/bin/env python3
"""Small synthetic checks for NLAP authority/receipt invariants; not a live E2E."""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from artifact_store import ArtifactStore, ArtifactAuthError
from workflow_node import WorkflowContractError
from ontology_contract import optimization_direction
from ml_dashboard_contract import validate_narrative_content
from scripts.nlap_receipts import verify_recovery, tool_calls  # pyright: ignore[reportMissingImports]


def rejects(callback, error: type[Exception] = ValueError):
    try:
        callback()
    except error:
        return
    raise AssertionError("unsafe input was accepted")


def main() -> None:
    owner = {"org_id": "1", "user_id": "same-user", "session_id": "A" * 32}
    other = {**owner, "session_id": "B" * 32}
    with tempfile.TemporaryDirectory(prefix="nlap-check-") as directory:
        store = ArtifactStore(directory)
        run_id = store.create_run(owner)
        ref = store.write_json(owner, run_id, "query-plan", {"plan": "approved-A"})
        rejects(lambda: store.read_json(other, ref), ArtifactAuthError)
        assert store.read_json(owner, ref) == {"plan": "approved-A"}
        rejects(lambda: store.write_json(owner, run_id, "query-plan", {"plan": "changed-B"}), WorkflowContractError)
        execution_ref = store.write_json(owner, run_id, "sandbox-execution", {"results": []})
        store.grant_reuse(owner, execution_ref, other["session_id"])
        assert store.read_json(other, execution_ref) == {"results": []}
        rejects(lambda: store.read_json(other, ref), ArtifactAuthError)
        rejects(lambda: store.grant_reuse(other, execution_ref, "C" * 32), ArtifactAuthError)
        rejects(lambda: store.read_json({**other, "user_id": "different"}, execution_ref), ArtifactAuthError)
        calls = []
        def effect():
            calls.append("effect")
            return {"ok": True, "receipt": "successful"}
        assert store.run_once(owner, "compute", {"input": "A"}, effect)["ok"]
        assert ArtifactStore(directory).run_once(owner, "compute", {"input": "A"}, effect)["ok"]
        assert calls == ["effect"]
        def interrupted():
            calls.append("interrupted")
            raise RuntimeError("connection lost after dispatch")
        rejects(lambda: store.run_once(owner, "compute", {"input": "B"}, interrupted), RuntimeError)
        rejects(lambda: store.run_once(owner, "compute", {"input": "B"}, effect), WorkflowContractError)
        assert calls == ["effect", "interrupted"]
    for content in ['<img src="x">', '<video poster="x"></video>', '<div style="background:image-set(x 1x)">x</div>', r'<div style="background:u\72l(x)">x</div>', '![image](x)']:
        rejects(lambda: validate_narrative_content(content))
    validate_narrative_content('<div style="padding:8px 12px"><h2>報告</h2><strong>證據</strong></div>')
    assert optimization_direction({"objective": "mae"}) is None
    assert optimization_direction({"objective": "mae", "optimization": {"direction": "maximize"}}) == "maximize"
    rejects(lambda: optimization_direction({"optimization": {"direction": "maximize"}, "target_direction": "minimize"}))
    rejects(lambda: verify_recovery({"dataset_id": "upload_" + "a" * 32, "session_id": "s" * 32, "native_llm_recovery": True, "recovery": {"unrecovered_errors": []}}))
    rejects(lambda: tool_calls({"events": [{"type": "tool_call_result", "data": {"id": "invented", "name": "compose", "isError": False, "content": "{}"}}]}))
    print("NLAP synthetic core checks passed; no live/browser acceptance claimed")


if __name__ == "__main__":
    main()
