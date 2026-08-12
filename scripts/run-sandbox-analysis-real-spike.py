#!/usr/bin/env python3
"""Run one real OpenSandbox frame-JSON/table/plot integration spike."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "sandbox-analysis-real-spike.json"


def load_server():
    path = ROOT / "sandbox-analysis-mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("sandbox_analysis_real_spike", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    server = load_server()
    with tempfile.TemporaryDirectory() as tmp:
        setattr(server, "ARTIFACTS", server.ArtifactStore(Path(tmp) / "runs"))
        context = {"org_id": "1", "user_id": "real-spike"}
        run_id = server.ARTIFACTS.create_run(context)
        frame_ref = server.ARTIFACTS.write_json(
            context,
            run_id,
            "grafana-frame",
            [{
                "schema": {"fields": [{"name": "timestamp", "type": "time"}, {"name": "load_mw", "type": "number"}, {"name": "ambient_c", "type": "number"}, {"name": "heat_rate", "type": "number"}, {"name": "heat_rate_valid", "type": "boolean"}]},
                "data": {"values": [[1767225600000, 1767312000000, 1767398400000, 1767484800000, 1767571200000, 1767657600000, 1767744000000, 1767830400000], [410, 425, 440, 455, 470, 485, 500, 515], [18, 19, 21, 22, 24, 25, 27, 28], [9010, 8990, 8975, 8960, 8940, 8930, 8915, 8900], [True, False, True, True, True, True, True, True]]},
            }],
        )
        server.ARTIFACTS.write_json(
            context,
            run_id,
            "query-plan",
            {"analysis_input_contract": {"validity_rules": [{"field": "heat_rate_valid", "accepted_values": [True], "applies_to": ["heat_rate"]}]}},
        )
        result = server.execute_python_analysis(
            {
                "frame_ref": frame_ref,
                "python_code": "import imblearn, lightgbm, optuna, plotly, scipy, seaborn, shap, statsmodels, xgboost\nfrom sklearn.ensemble import RandomForestRegressor\nimport matplotlib.pyplot as plt\nX = df[['load_mw', 'ambient_c']]\nmodel = RandomForestRegressor(n_estimators=20, random_state=42).fit(X, df['heat_rate'])\nvalues = shap.TreeExplainer(model)(X)\ndisplay(df[['timestamp', 'heat_rate']])\nshap.plots.beeswarm(values, show=False)\nemit(plt.gcf(), name='shap-beeswarm')",
                "seed": 42,
                "_server_context": context,
            }
        )
        if not result.get("ok"):
            raise RuntimeError(result)
        summary = result["output_summary"]
        if summary.get("mime_types") != ["image/png", "text/html"] or result["provenance"]["validity"].get("excluded_rows") != 1:
            raise RuntimeError(result)
        listed = server.list_python_analyses({"_server_context": context})
        inspected = server.inspect_python_analysis({"provenance_ref": result["refs"]["provenance_ref"], "_server_context": context})
        network_execution = server.execute_opensandbox(
            json.dumps({"frame": {"schema": {"fields": [{"name": "x", "type": "number"}]}, "data": {"values": [[1]]}}, "validity_rules": []}),
            "import json, os, socket\ndef blocked(host):\n    try:\n        with socket.create_connection((host, 443), timeout=2):\n            return False\n    except OSError:\n        return True\nprint(json.dumps({'direct_ip_blocked': blocked('1.1.1.1'), 'dns_blocked': blocked('example.com'), 'input_bundle_unlinked': not os.path.exists('/tmp/input-frame.json')}))",
            42,
        )
        stdout_text = "".join(str(item.get("text") or "") for item in network_execution.get("stdout", []))
        try:
            network = json.loads(stdout_text.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError("sandbox network attestation returned invalid JSON") from exc
        if network != {"direct_ip_blocked": True, "dns_blocked": True, "input_bundle_unlinked": True}:
            raise RuntimeError(network_execution)
        evidence = {
            "ok": True,
            "frame_transport": "grafana-columnar-json",
            "csv_intermediate": False,
            "sandbox_image": os.environ.get("SANDBOX_IMAGE"),
            "output_summary": summary,
            "validity": result["provenance"]["validity"],
            "cross_conversation": {"listed": len(listed.get("analyses", [])), "inspect_code_sha256": inspected.get("code_sha256")},
            "sandbox_packages_imported": ["numpy", "scipy", "pandas", "matplotlib", "seaborn", "plotly", "scikit-learn", "statsmodels", "shap", "xgboost", "lightgbm", "imbalanced-learn", "optuna"],
            "runtime_config_attested": len(str(result["provenance"].get("server_config_sha256") or "")) == 64 and result["provenance"].get("runtime_class") == "runc",
            "enforced_limits": result["provenance"].get("limits"),
            "shap_plot_captured": "image/png" in summary.get("mime_types", []),
            "egress": network,
            "refs_are_opaque": all(str(value).startswith("artifact://") for value in result["refs"].values()),
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
