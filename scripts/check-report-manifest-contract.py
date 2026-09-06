#!/usr/bin/env python3
"""RED-only public-seam checks for canonical report manifests."""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
EXECUTION_REF = "artifact://report-contract-run/sandbox-execution"
PNG_1X1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


def load_contract():
    sys.path.insert(0, str(ROOT))
    path = ROOT / "ml_report_contract.py"
    spec = importlib.util.spec_from_file_location("report_manifest_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def normalize(contract: Any, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Call the required pure seam without reaching through private helpers."""
    normalizer: Callable[..., dict[str, Any]] | None = getattr(contract, "normalize_report_manifest", None)
    if not callable(normalizer):
        raise AssertionError("public normalize_report_manifest seam is missing")
    return normalizer(execution_ref=EXECUTION_REF, results=results)


def expect_reject(call: Callable[[], Any], fragment: str) -> None:
    try:
        call()
    except (ValueError, TypeError) as exc:
        if fragment.casefold() not in str(exc).casefold():
            raise AssertionError(f"expected rejection containing {fragment!r}: {str(exc)[:240]}") from exc
        return
    raise AssertionError(f"expected rejection containing {fragment!r}")


def source_result(source: dict[str, Any], name: str = "report-source.json") -> dict[str, Any]:
    return {"display_name": name, "mime": {"application/json": json.dumps(source, ensure_ascii=False, separators=(",", ":"))}}


def plotly_result(name: str, figure: dict[str, Any]) -> dict[str, Any]:
    return {"display_name": name, "mime": {"application/vnd.plotly.v1+json": json.dumps(figure, ensure_ascii=False, separators=(",", ":"))}}


def png_result(name: str, payload: str = PNG_1X1) -> dict[str, Any]:
    return {"display_name": name, "mime": {"image/png": payload}}


def artifact(artifact_id: str, *, plotly: str | None = None, png: str | None = None) -> dict[str, Any]:
    output: dict[str, Any] = {"artifact_id": artifact_id, "fact_refs": ["signal"]}
    if plotly is not None:
        output["plotly_output_name"] = plotly
    if png is not None:
        output["png_output_name"] = png
    return output


def report_source(artifacts: list[dict[str, Any]], *, facts: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    source: dict[str, Any] = {
        "format": "ask-o11y-report-source-v1",
        "purpose": "compare bounded aggregate evidence",
        "conclusion": "the authorized evidence is ready for review",
        "facts": facts or {"signal": {"kind": "number", "label": "Signal", "value": 0.42}},
        "artifacts": artifacts,
    }
    source.update(extra)
    return source


def results(source: dict[str, Any], *outputs: dict[str, Any]) -> list[dict[str, Any]]:
    return [source_result(source), *outputs]


def main() -> int:
    contract = load_contract()
    figure = {"data": [{"type": "scatter", "mode": "lines", "name": "cost", "x": [0.0, 0.5, 1.0], "y": [300.0, 260.0, 310.0]}], "layout": {"title": "threshold tradeoff"}}

    class IntendedRed(Exception):
        pass

    outcomes: list[tuple[str, str, str]] = []

    def run_case(name: str, case: Callable[[], Any]) -> None:
        try:
            case()
        except IntendedRed as exc:
            outcomes.append((name, "intended-red", str(exc)))
        except Exception as exc:
            outcomes.append((name, "failed", f"{type(exc).__name__}: {exc}"))
        else:
            outcomes.append((name, "current-pass", ""))

    def mark_not_executed(name: str, reason: str) -> None:
        outcomes.append((name, "not_executed", reason))

    def require_normalizer() -> None:
        if not callable(getattr(contract, "normalize_report_manifest", None)):
            raise IntendedRed("public normalize_report_manifest seam is missing")

    run_case("public-normalizer", require_normalizer)
    normalizer_available = callable(getattr(contract, "normalize_report_manifest", None))

    normalizer_cases = [
        "plotly-only",
        "png-only",
        "valid-pair-selects-plotly",
        "invalid-figure-with-png-rejects",
        "invalid-png-rejects",
        "missing-source-rejects",
        "missing-output-rejects",
        "empty-artifacts-rejects",
        "duplicate-artifact-id-rejects",
        "ambiguous-output-rejects",
        "orphan-output-rejects",
        "bounded-facts-reject",
        "bounded-artifacts-reject",
        "raw-rows-reject",
        "list-fact-reject",
        "legacy-source-rejects",
        "ordinary-json-allowed",
        "safe-fact-catalog",
    ]
    if not normalizer_available:
        for name in normalizer_cases:
            mark_not_executed(name, "requires public normalize_report_manifest")
    else:
        def plotly_only_case() -> None:
            plotly_only = normalize(contract, results(
                report_source([artifact("trend", plotly="trend.plotly")]),
                plotly_result("trend.plotly", figure),
            ))
            plotly_render = plotly_only["artifacts"][0]["render"]
            assert plotly_only["format"] == "ask-o11y-report-manifest-v1", plotly_only
            assert plotly_only["execution_ref"] == EXECUTION_REF, plotly_only
            assert plotly_render["mode"] == "plotly" and plotly_render["output_index"] == 1, plotly_render
            assert plotly_render["mime_type"] == "application/vnd.plotly.v1+json", plotly_render

        def png_only_case() -> None:
            image_only = normalize(contract, results(
                report_source([artifact("trend", png="trend.png")]),
                png_result("trend.png"),
            ))
            image_render = image_only["artifacts"][0]["render"]
            assert image_render["mode"] == "image" and image_render["mime_type"] == "image/png", image_render

        def valid_pair_case() -> None:
            both = normalize(contract, results(
                report_source([artifact("trend", plotly="trend.plotly", png="trend.png")]),
                plotly_result("trend.plotly", figure),
                png_result("trend.png"),
            ))
            both_artifact = both["artifacts"][0]
            assert both_artifact["render"]["mode"] == "plotly", both_artifact
            assert "png_output_index" not in both_artifact and "fallback" not in json.dumps(both_artifact), both_artifact

        def invalid_figure_case() -> None:
            invalid_figure = {"data": [{"type": "scatter", "x": [1], "y": [1], "customdata": ["row"]}], "layout": {}}
            expect_reject(
                lambda: normalize(contract, results(
                    report_source([artifact("trend", plotly="trend.plotly", png="trend.png")]),
                    plotly_result("trend.plotly", invalid_figure),
                    png_result("trend.png"),
                )),
                "customdata",
            )

        def invalid_png_case() -> None:
            expect_reject(
                lambda: normalize(contract, results(
                    report_source([artifact("trend", png="trend.png")]),
                    png_result("trend.png", base64.b64encode(b"not-a-png").decode("ascii")),
                )),
                "png",
            )

        def missing_source_case() -> None:
            expect_reject(lambda: normalize(contract, [plotly_result("trend.plotly", figure)]), "source")

        def missing_output_case() -> None:
            expect_reject(
                lambda: normalize(contract, results(report_source([artifact("missing", plotly="missing.plotly")]))),
                "output",
            )

        def empty_artifacts_case() -> None:
            expect_reject(lambda: normalize(contract, results(report_source([]))), "artifact")

        def duplicate_id_case() -> None:
            duplicate_ids = report_source([artifact("trend", png="trend.png"), artifact("trend", png="other.png")])
            expect_reject(lambda: normalize(contract, results(duplicate_ids, png_result("trend.png"), png_result("other.png"))), "duplicate")

        def ambiguous_output_case() -> None:
            ambiguous_output = report_source([artifact("trend", plotly="trend.plotly")])
            expect_reject(
                lambda: normalize(contract, results(ambiguous_output, plotly_result("trend.plotly", figure), plotly_result("trend.plotly", figure))),
                "ambiguous",
            )

        def orphan_output_case() -> None:
            orphan_output = report_source([artifact("trend", png="trend.png")])
            expect_reject(
                lambda: normalize(contract, results(orphan_output, png_result("trend.png"), plotly_result("orphan.plotly", figure))),
                "orphan",
            )

        def bounded_facts_case() -> None:
            oversized_facts = {f"signal_{index}": {"kind": "number", "label": "Signal", "value": index} for index in range(65)}
            oversized_facts["signal"] = {"kind": "number", "label": "Signal", "value": 0.42}
            expect_reject(
                lambda: normalize(contract, results(report_source([artifact("trend", png="trend.png")], facts=oversized_facts), png_result("trend.png"))),
                "bound",
            )

        def bounded_artifacts_case() -> None:
            oversized_artifacts = [artifact(f"chart-{index}", png=f"chart-{index}.png") for index in range(17)]
            expect_reject(
                lambda: normalize(contract, results(report_source(oversized_artifacts), *(png_result(item["png_output_name"]) for item in oversized_artifacts))),
                "bound",
            )

        def raw_rows_case() -> None:
            expect_reject(
                lambda: normalize(contract, results(report_source([artifact("trend", png="trend.png")], raw_rows=[[1, 2]]), png_result("trend.png"))),
                "source",
            )

        def list_fact_case() -> None:
            list_fact_source = report_source([artifact("trend", png="trend.png")])
            list_fact_source["facts"]["signal"]["value"] = [1, 2]
            expect_reject(lambda: normalize(contract, results(list_fact_source, png_result("trend.png"))), "fact")

        def legacy_source_case() -> None:
            legacy_source = {"format": "ask-o11y-legacy-report-v0", "purpose": "legacy", "conclusion": "legacy", "artifacts": [{"name": "legacy.png"}]}
            expect_reject(lambda: normalize(contract, results(legacy_source, png_result("legacy.png"))), "format")

        def ordinary_json_case() -> None:
            ordinary = {"format": "ask-o11y-data-profile-v1", "data": {"rows": 10}}
            normalized = normalize(contract, results(
                report_source([artifact("trend", png="trend.png")]),
                {"display_name": "data-profile.json", "mime": {"application/json": json.dumps(ordinary)}},
                png_result("trend.png"),
            ))
            assert normalized["format"] == "ask-o11y-report-manifest-v1", normalized

        def safe_fact_catalog_case() -> None:
            plotly_only = normalize(contract, results(
                report_source([artifact("trend", plotly="trend.plotly")]),
                plotly_result("trend.plotly", figure),
            ))
            catalog = contract.build_fact_catalog(plotly_only)
            encoded_catalog = json.dumps(catalog, ensure_ascii=False)
            assert "facts.signal" in catalog, catalog
            for coordinate in ("execution_ref", "output_index", "mime_type", "sha256", "application/vnd.plotly"):
                if coordinate in encoded_catalog:
                        raise AssertionError(f"{coordinate}: {catalog}")

        for name, case in (
            ("plotly-only", plotly_only_case),
            ("png-only", png_only_case),
            ("valid-pair-selects-plotly", valid_pair_case),
            ("invalid-figure-with-png-rejects", invalid_figure_case),
            ("invalid-png-rejects", invalid_png_case),
            ("missing-source-rejects", missing_source_case),
            ("missing-output-rejects", missing_output_case),
            ("empty-artifacts-rejects", empty_artifacts_case),
            ("duplicate-artifact-id-rejects", duplicate_id_case),
            ("ambiguous-output-rejects", ambiguous_output_case),
            ("orphan-output-rejects", orphan_output_case),
            ("bounded-facts-reject", bounded_facts_case),
            ("bounded-artifacts-reject", bounded_artifacts_case),
            ("raw-rows-reject", raw_rows_case),
            ("list-fact-reject", list_fact_case),
            ("legacy-source-rejects", legacy_source_case),
            ("ordinary-json-allowed", ordinary_json_case),
            ("safe-fact-catalog", safe_fact_catalog_case),
        ):
            run_case(name, case)

    for name, status, detail in outcomes:
        suffix = f": {detail}" if detail else ""
        print(f"scenario {name}: {status}{suffix}")
    failed = [item for item in outcomes if item[1] == "failed"]
    intended_red = [item for item in outcomes if item[1] == "intended-red"]
    if failed:
        print(f"failed: report manifest contract ({len(failed)} unexpected scenario failure(s))")
        return 2
    if intended_red:
        print(f"red: report manifest contract ({len(intended_red)} attributable production gap(s))")
        return 1
    print("ok: report manifest contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
