#!/usr/bin/env python3
"""Bridge-level check: askO11yPlotlyBindings resolve, sanitize and reject."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PNG_1X1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def expect_reject(fn, fragment: str) -> None:
    try:
        result = fn()
    except Exception as exc:  # WorkflowContractError or ValueError
        if fragment not in str(exc):
            raise AssertionError(f"expected rejection containing {fragment!r}: {str(exc)[:200]}") from exc
        return
    if isinstance(result, dict) and not result.get("ok") and fragment in str(result.get("error")):
        return
    raise AssertionError(f"expected rejection containing {fragment!r}")


def main() -> int:
    bridge = load_module("plotly_bridge", ROOT / "artifact-bridge-mcp/server.py")
    store = bridge.ArtifactStore(Path(tempfile.mkdtemp()) / "runs")
    setattr(bridge, "ARTIFACTS", store)
    context = {"org_id": "1", "user_id": "plotly-e2e"}

    figure = {
        "data": [{"type": "scatter", "mode": "lines", "name": "cost", "x": [0.0, 0.5, 1.0], "y": [300.0, 260.0, 310.0]}],
        "layout": {"title": "門檻取捨"},
    }
    run_id = store.create_run(context)
    store.write_json(context, run_id, "sandbox-execution", {"results": [
        {"text": None, "timestamp": 0, "mime": {"image/png": base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")}, "display_name": "fallback.png"},
        {"text": None, "timestamp": 0, "mime": {"application/json": json.dumps(figure)}, "display_name": "Plotly: cost"},
    ]})
    execution_ref = f"artifact://{run_id}/sandbox-execution"
    plotly_only_run = store.create_run(context)
    store.write_json(context, plotly_only_run, "sandbox-execution", {"results": [
        {"text": None, "timestamp": 0, "mime": {"application/json": json.dumps(figure)}, "display_name": "Plotly: figure-only"},
    ]})
    plotly_only_ref = f"artifact://{plotly_only_run}/sandbox-execution"

    def resolve(panel: dict):
        return bridge.resolve_dashboard_refs({"dashboard": {
            "uid": "plotly-check", "title": "Plotly check", "tags": ["ask-o11y-preview"],
            "panels": [panel],
        }, "_server_context": context})

    def resolve_opaque(panel: dict):
        run = store.create_run(context)
        dashboard_ref = store.write_json(context, run, "dashboard", {"uid": "opaque-report", "title": "Opaque report", "panels": [panel]})
        return bridge.resolve_dashboard_refs({"dashboard": {"$dashboard_ref": dashboard_ref}, "_server_context": context})

    import copy

    class IntendedRed(Exception):
        pass

    outcomes: list[tuple[str, str, str]] = []

    def run_case(name: str, case) -> None:
        try:
            case()
        except IntendedRed as exc:
            outcomes.append((name, "intended-red", str(exc)))
        except Exception as exc:
            outcomes.append((name, "failed", f"{type(exc).__name__}: {exc}"))
        else:
            outcomes.append((name, "current-pass", ""))

    def expected_success_or_red(result: dict, markers: tuple[str, ...]) -> None:
        if isinstance(result, dict) and result.get("ok"):
            return
        error = str(result.get("error") if isinstance(result, dict) else result)
        lowered = error.casefold()
        if any(marker.casefold() in lowered for marker in markers):
            raise IntendedRed(error)
        raise AssertionError(f"unexpected rejection: {error[:240]}")

    def plotly_with_fallback_case() -> None:
        panel = {
            "type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID,
            "title": "門檻取捨",
            "options": {
                "figure": "$plotly_figure_cost",
                "fallbackUrl": "$asset_url_cost_fallback",
                "alt": "門檻成本圖 fallback",
                "caption": "已選門檻為 train OOF 事實；holdout 僅評估。",
            },
            "askO11yPlotlyBindings": [{"placeholder": "$plotly_figure_cost", "$execution_ref": execution_ref, "output_index": 1, "plugin_id": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID}],
            "askO11yAssetBindings": [{"placeholder": "$asset_url_cost_fallback", "$execution_ref": execution_ref, "output_index": 0}],
        }
        result = resolve(panel)
        assert result["ok"], result
        resolved_panel = result["dashboard"]["panels"][0]
        options = resolved_panel["options"]
        assert isinstance(options["figure"], dict) and "data" in options["figure"] and "config" in options["figure"], options["figure"]
        assert str(options["fallbackUrl"]).startswith("http"), options["fallbackUrl"]
        assert "askO11yPlotlyBindings" not in resolved_panel and "askO11yAssetBindings" not in resolved_panel

    def manual_report_dashboard_case() -> None:
        result = bridge.resolve_dashboard_refs({"dashboard": {"uid": "manual-report", "tags": ["ask-o11y-report"], "panels": []}, "_server_context": context})
        assert not result["ok"] and "opaque composed dashboard ref" in result["error"], result
        binding = bridge.resolve_dashboard_refs({"dashboard": {"uid": "manual-binding", "panels": [{"type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID, "options": {"renderMode": "image", "fallbackUrl": "$asset_url_cost"}, "askO11yAssetBindings": [{"placeholder": "$asset_url_cost", "$report_manifest_ref": report_manifest_ref, "artifact_id": "cost"}]}]}, "_server_context": context})
        assert not binding["ok"] and "opaque composed dashboard ref" in binding["error"], binding

    def wrong_plugin_case() -> None:
        panel = {
            "type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID,
            "options": {"figure": "$plotly_figure_cost", "fallbackUrl": "$asset_url_cost_fallback"},
            "askO11yPlotlyBindings": [{"placeholder": "$plotly_figure_cost", "$execution_ref": execution_ref, "output_index": 1, "plugin_id": "nline-plotlyjs-panel"}],
            "askO11yAssetBindings": [{"placeholder": "$asset_url_cost_fallback", "$execution_ref": execution_ref, "output_index": 0}],
        }
        expect_reject(lambda: resolve(panel), "plugin id")

    def script_rejection_case() -> None:
        panel = {
            "type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID,
            "options": {"figure": "$plotly_figure_cost", "fallbackUrl": "$asset_url_cost_fallback", "script": "return data"},
            "askO11yPlotlyBindings": [{"placeholder": "$plotly_figure_cost", "$execution_ref": execution_ref, "output_index": 1, "plugin_id": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID}],
            "askO11yAssetBindings": [{"placeholder": "$asset_url_cost_fallback", "$execution_ref": execution_ref, "output_index": 0}],
        }
        expect_reject(lambda: resolve(panel), "executable handlers")

    def literal_figure_case() -> None:
        panel = {
            "type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID,
            "options": {"figure": figure, "fallbackUrl": "$asset_url_cost_fallback"},
            "askO11yPlotlyBindings": [{"placeholder": "$plotly_figure_cost", "$execution_ref": execution_ref, "output_index": 1, "plugin_id": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID}],
            "askO11yAssetBindings": [{"placeholder": "$asset_url_cost_fallback", "$execution_ref": execution_ref, "output_index": 0}],
        }
        expect_reject(lambda: resolve(panel), "placeholder")

    bad_figure = {"data": [{"type": "scatter", "x": [1], "y": [1], "customdata": ["row-1"]}], "layout": {}}
    run_bad = store.create_run(context)
    store.write_json(context, run_bad, "sandbox-execution", {"results": [
        {"text": None, "timestamp": 0, "mime": {"image/png": base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")}, "display_name": "fallback.png"},
        {"text": None, "timestamp": 0, "mime": {"application/json": json.dumps(bad_figure)}, "display_name": "Plotly: bad"},
    ]})
    bad_ref = f"artifact://{run_bad}/sandbox-execution"

    def invalid_execution_figure_case() -> None:
        panel = {
            "type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID,
            "options": {"figure": "$plotly_figure_cost", "fallbackUrl": "$asset_url_cost_fallback"},
            "askO11yPlotlyBindings": [{"placeholder": "$plotly_figure_cost", "$execution_ref": bad_ref, "output_index": 1, "plugin_id": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID}],
            "askO11yAssetBindings": [{"placeholder": "$asset_url_cost_fallback", "$execution_ref": execution_ref, "output_index": 0}],
        }
        expect_reject(lambda: resolve(panel), "customdata")

    def figure_only_case() -> None:
        panel = {
            "type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID,
            "title": "門檻取捨",
            "options": {"renderMode": "plotly", "figure": "$plotly_figure_cost", "alt": "門檻成本圖", "caption": "已選門檻為證據事實。"},
            "askO11yPlotlyBindings": [{"placeholder": "$plotly_figure_cost", "$execution_ref": plotly_only_ref, "output_index": 0, "plugin_id": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID}],
        }
        result = resolve(panel)
        expected_success_or_red(result, ("fallback",))
        resolved = result["dashboard"]["panels"][0]
        assert resolved["options"]["renderMode"] == "plotly", resolved
        assert isinstance(resolved["options"]["figure"], dict), resolved
        assert "fallbackUrl" not in resolved["options"], resolved

    def image_execution_case() -> None:
        image = {
            "type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID,
            "options": {"renderMode": "image", "fallbackUrl": "$asset_url_image"},
            "askO11yAssetBindings": [
                {"placeholder": "$asset_url_image", "$execution_ref": execution_ref, "output_index": 0}
            ],
        }
        resolved_image = resolve(image)
        assert resolved_image["ok"], resolved_image
        assert resolved_image["dashboard"]["panels"][0]["options"]["fallbackUrl"].startswith("http")

    def legacy_mode_url_case() -> None:
        legacy_image = {
            "type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID,
            "options": {"mode": "image", "url": "$asset_url_legacy"},
            "askO11yAssetBindings": [
                {"placeholder": "$asset_url_legacy", "$execution_ref": execution_ref, "output_index": 0}
            ],
        }
        expect_reject(lambda: resolve(legacy_image), "renderMode")

    clean_figure = bridge.ml_plotly_contract.sanitize_figure(figure, legacy=True)
    figure_digest = hashlib.sha256(json.dumps(clean_figure, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    png_digest = hashlib.sha256(base64.b64decode(PNG_1X1)).hexdigest()

    def write_report(results, manifest):
        report_run = store.create_run(context)
        execution = store.write_json(context, report_run, "sandbox-execution", {"results": results, "error": None})
        manifest_copy = copy.deepcopy(manifest)
        manifest_copy["execution_ref"] = execution
        report_ref = store.write_json(context, report_run, "report-manifest", manifest_copy)
        store.write_json(context, report_run, "sandbox-provenance", {
            "executor_kind": "profile_dataset", "trusted_ml_contract": False, "report_manifest_ref": report_ref,
        })
        return execution, report_ref

    source = {
        "format": "ask-o11y-report-source-v1",
        "purpose": "compare bounded evidence",
        "conclusion": "the evidence is ready for review",
        "facts": {"signal": {"kind": "number", "label": "Signal", "value": 0.42}},
        "artifacts": [{"artifact_id": "cost", "fact_refs": ["signal"], "plotly_output_name": "cost.plotly", "png_output_name": "cost.png"}],
    }
    report_results = [
        {"display_name": "report-source.json", "mime": {"application/json": json.dumps(source, ensure_ascii=False, separators=(",", ":"))}},
        {"display_name": "cost.plotly", "mime": {"application/vnd.plotly.v1+json": json.dumps(figure, ensure_ascii=False, separators=(",", ":"))}},
        {"display_name": "cost.png", "mime": {"image/png": PNG_1X1}},
    ]
    canonical_manifest = {
        "format": "ask-o11y-report-manifest-v1",
        "execution_ref": "",
        "source": {"format": "ask-o11y-report-source-v1", "output_index": 0, "display_name": "report-source.json"},
        "purpose": source["purpose"],
        "conclusion": source["conclusion"],
        "facts": source["facts"],
        "artifacts": [{"artifact_id": "cost", "fact_refs": ["signal"], "render": {"mode": "plotly", "output_index": 1, "mime_type": "application/vnd.plotly.v1+json", "sha256": figure_digest, "png_output_index": 2, "png_sha256": png_digest}}],
    }
    _canonical_execution_ref, report_manifest_ref = write_report(report_results, canonical_manifest)

    def report_manifest_ref_case() -> None:
        preferred = bridge.prepare_ml_report({"report_manifest_ref": report_manifest_ref, "_server_context": context})
        expected_success_or_red(preferred, ("unsupported tool arguments",))
        assert "output_index" not in json.dumps(preferred.get("report_context") or {}, ensure_ascii=False), preferred

    def missing_report_ref_case() -> None:
        missing = bridge.prepare_ml_report({"report_manifest_ref": "artifact://missing/report-manifest", "_server_context": context})
        if isinstance(missing, dict) and missing.get("ok"):
            raise AssertionError("missing report-manifest ref was accepted")
        error = str(missing.get("error") if isinstance(missing, dict) else missing).casefold()
        if "unsupported tool arguments" in error:
            raise IntendedRed(str(missing.get("error")))
        assert any(marker in error for marker in ["missing", "unavailable", "not found"]), missing

    def legacy_report_ref_case() -> None:
        legacy = bridge.prepare_ml_report({"report_manifest_ref": execution_ref, "_server_context": context})
        if isinstance(legacy, dict) and legacy.get("ok"):
            raise AssertionError("execution ref was accepted as a canonical report manifest ref")
        error = str(legacy.get("error") if isinstance(legacy, dict) else legacy).casefold()
        if "unsupported tool arguments" in error:
            raise IntendedRed(str(legacy.get("error")))
        assert any(marker in error for marker in ["canonical", "manifest format", "report manifest"]), legacy

    figure_only_panel = {
        "type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID,
        "title": "門檻取捨",
        "options": {"renderMode": "plotly", "figure": "$plotly_figure_cost", "alt": "門檻成本圖", "caption": "已選門檻為證據事實。"},
        "askO11yPlotlyBindings": [{"placeholder": "$plotly_figure_cost", "$report_manifest_ref": report_manifest_ref, "artifact_id": "cost", "plugin_id": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID}],
    }

    def figure_only_report_binding_case() -> None:
        resolved = resolve_opaque(copy.deepcopy(figure_only_panel))
        expected_success_or_red(resolved, ("plotly binding requires only", "fallback"))
        options = resolved["dashboard"]["panels"][0]["options"]
        assert options["renderMode"] == "plotly" and isinstance(options["figure"], dict), resolved
        assert "fallbackUrl" not in options, resolved

    def paired_report_binding_case() -> None:
        paired = copy.deepcopy(figure_only_panel)
        paired["options"]["fallbackUrl"] = "$asset_url_cost"
        paired["askO11yAssetBindings"] = [{"placeholder": "$asset_url_cost", "$report_manifest_ref": report_manifest_ref, "artifact_id": "cost"}]
        resolved = resolve_opaque(paired)
        assert resolved["ok"], resolved
        options = resolved["dashboard"]["panels"][0]["options"]
        assert str(options["fallbackUrl"]).startswith("http"), resolved

    invalid_figure = {"data": [{"type": "scatter", "x": [1], "y": [1], "customdata": ["row"]}], "layout": {}}
    bad_source = dict(source)
    bad_source["artifacts"] = [{"artifact_id": "cost", "fact_refs": ["signal"], "plotly_output_name": "cost.plotly", "png_output_name": "cost.png"}]
    bad_results = [
        {"display_name": "report-source.json", "mime": {"application/json": json.dumps(bad_source, ensure_ascii=False, separators=(",", ":"))}},
        {"display_name": "cost.plotly", "mime": {"application/vnd.plotly.v1+json": json.dumps(invalid_figure, ensure_ascii=False, separators=(",", ":"))}},
        {"display_name": "cost.png", "mime": {"image/png": PNG_1X1}},
    ]
    bad_manifest = dict(canonical_manifest)
    bad_manifest["source"] = dict(canonical_manifest["source"])
    bad_manifest["artifacts"] = [{"artifact_id": "cost", "fact_refs": ["signal"], "render": {"mode": "plotly", "output_index": 1, "mime_type": "application/vnd.plotly.v1+json", "sha256": "0" * 64}}]
    _bad_execution_ref, bad_report_ref = write_report(bad_results, bad_manifest)

    def invalid_report_figure_case() -> None:
        bad_panel = copy.deepcopy(figure_only_panel)
        bad_panel["askO11yPlotlyBindings"][0]["$report_manifest_ref"] = bad_report_ref
        resolved = resolve_opaque(bad_panel)
        if isinstance(resolved, dict) and resolved.get("ok"):
            raise AssertionError("invalid Plotly report figure was accepted")
        error = str(resolved.get("error") if isinstance(resolved, dict) else resolved).casefold()
        if "plotly binding requires only" in error:
            raise IntendedRed(str(resolved.get("error")))
        assert "customdata" in error, resolved

    image_source = dict(source)
    image_source["artifacts"] = [{"artifact_id": "cost", "fact_refs": ["signal"], "png_output_name": "cost.png"}]
    image_results = [{"display_name": "report-source.json", "mime": {"application/json": json.dumps(image_source, ensure_ascii=False, separators=(",", ":"))}}, {"display_name": "cost.png", "mime": {"image/png": PNG_1X1}}]
    image_manifest = dict(canonical_manifest)
    image_manifest["source"] = dict(canonical_manifest["source"])
    image_manifest["artifacts"] = [{"artifact_id": "cost", "fact_refs": ["signal"], "render": {"mode": "image", "output_index": 1, "mime_type": "image/png", "sha256": png_digest}}]
    _image_execution_ref, image_report_ref = write_report(image_results, image_manifest)

    def image_report_binding_case() -> None:
        image_panel = {
            "type": bridge.ml_plotly_contract.PLOTLY_PLUGIN_ID,
            "title": "門檻取捨",
            "options": {"renderMode": "image", "fallbackUrl": "$asset_url_cost", "alt": "門檻成本圖", "caption": "靜態證據圖。"},
            "askO11yAssetBindings": [{"placeholder": "$asset_url_cost", "$report_manifest_ref": image_report_ref, "artifact_id": "cost"}],
        }
        resolved = resolve_opaque(image_panel)
        expected_success_or_red(resolved, ("asset binding requires only", "report manifest"))
        assert resolved["dashboard"]["panels"][0]["options"]["fallbackUrl"].startswith("http"), resolved

    def legacy_generic_manifest_bypass_case() -> None:
        generic_run = store.create_run(context)
        generic_results = [
            {"display_name": "analysis.png", "mime": {"image/png": PNG_1X1}},
            {"display_name": "analysis.json", "mime": {"application/json": json.dumps({
                "format": "ask-o11y-report-source-v1",
                "facts": {"secret": {"kind": "text", "value": "should-not-reach-report"}},
                "artifacts": [{"name": "analysis.png"}],
            })}},
        ]
        generic_ref = store.write_json(context, generic_run, "sandbox-execution", {"results": generic_results, "error": None})
        store.write_json(context, generic_run, "sandbox-provenance", {"executor_kind": "execute_python_analysis", "report_manifest_ref": None})
        prepared = bridge.prepare_ml_report({"execution_ref": generic_ref, "manifest_output_index": 1, "_server_context": context})
        assert isinstance(prepared, dict) and not prepared.get("ok"), prepared
        assert "report_manifest_ref" in str(prepared.get("error") or "").casefold(), prepared
        assert "report_context_ref" not in (prepared.get("refs") or {}), prepared

    def ordinary_summary_wrong_index_case() -> None:
        ordinary_summary = {"result_count": 6, "summary": "ordinary execution output", "mime_types": ["image/png"]}
        observed_results = [
            {"display_name": "summary.json", "mime": {"application/json": json.dumps(ordinary_summary, separators=(",", ":"))}},
            *({"display_name": f"evidence-{index}.png", "mime": {"image/png": PNG_1X1}} for index in range(6)),
        ]
        assert "artifacts" not in ordinary_summary and len(observed_results[1:]) == 6, observed_results
        ordinary_run = store.create_run(context)
        ordinary_ref = store.write_json(context, ordinary_run, "sandbox-execution", {"results": observed_results, "error": None})
        prepared = bridge.prepare_ml_report({"execution_ref": ordinary_ref, "manifest_output_index": 0, "_server_context": context})
        assert isinstance(prepared, dict) and not prepared.get("ok"), prepared
        prepared_error = str(prepared.get("error") or "").casefold()
        assert any(marker in prepared_error for marker in ["manifest", "artifact", "renderable"]), prepared
        assert "report_context_ref" not in (prepared.get("refs") or {}) and "report_context" not in prepared, prepared

        dashboard = bridge.compose_ml_dashboard({
            "report_context_ref": ordinary_ref,
            "inspection_refs": [],
            "synthesis": {},
            "uid": "ordinary-summary",
            "title": "Ordinary summary",
            "output_mode": "ref",
            "_server_context": context,
        })
        assert isinstance(dashboard, dict) and not dashboard.get("ok"), dashboard
        dashboard_error = str(dashboard.get("error") or "").casefold()
        assert any(marker in dashboard_error for marker in ["report context", "invalid", "required"]), dashboard
        assert "dashboard_ref" not in (dashboard.get("refs") or {}) and "dashboard_ref" not in dashboard, dashboard

    for name, case in (
        ("plotly-with-fallback", plotly_with_fallback_case),
        ("manual-report-dashboard-rejects", manual_report_dashboard_case),
        ("wrong-plugin-rejects", wrong_plugin_case),
        ("script-rejects", script_rejection_case),
        ("literal-figure-rejects", literal_figure_case),
        ("invalid-execution-figure-rejects", invalid_execution_figure_case),
        ("figure-only-optional-fallback", figure_only_case),
        ("image-execution-binding", image_execution_case),
        ("legacy-mode-url-rejects", legacy_mode_url_case),
        ("report-manifest-ref", report_manifest_ref_case),
        ("missing-report-manifest-ref", missing_report_ref_case),
        ("legacy-execution-ref-rejects", legacy_report_ref_case),
        ("ordinary-summary-wrong-manifest-index", ordinary_summary_wrong_index_case),
        ("figure-only-report-binding", figure_only_report_binding_case),
        ("paired-report-binding", paired_report_binding_case),
        ("invalid-report-figure-rejects", invalid_report_figure_case),
        ("image-report-binding", image_report_binding_case),
    ):
        run_case(name, case)

    for name, status, detail in outcomes:
        suffix = f": {detail}" if detail else ""
        print(f"scenario {name}: {status}{suffix}")
    failed = [item for item in outcomes if item[1] == "failed"]
    intended_red = [item for item in outcomes if item[1] == "intended-red"]
    if failed:
        print(f"failed: artifact bridge plotly ({len(failed)} unexpected scenario failure(s))")
        return 2
    if intended_red:
        print(f"red: artifact bridge plotly ({len(intended_red)} attributable production gap(s))")
        return 1
    print("ok: artifact bridge plotly bindings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
