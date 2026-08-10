"""Trusted in-sandbox bootstrap for DataFrame injection and rich output capture."""
from __future__ import annotations

import ast
import importlib
import json
import random
import shutil
from pathlib import Path
from typing import Any

OUTPUT_DIR = Path("/tmp/sandbox-output")
MAX_ITEM_BYTES = 4 * 1024 * 1024


def load_dataframe(bundle_path: str, pd: Any) -> tuple[Any, dict[str, Any]]:
    bundle = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
    frame = bundle.get("frame") if isinstance(bundle, dict) else None
    rules = bundle.get("validity_rules", []) if isinstance(bundle, dict) else None
    fields = frame.get("schema", {}).get("fields") if isinstance(frame, dict) else None
    values = frame.get("data", {}).get("values") if isinstance(frame, dict) else None
    if not isinstance(fields, list) or not isinstance(values, list) or len(fields) != len(values):
        raise ValueError("columnar frame fields/values must be equal-length arrays")
    names = [field.get("name") if isinstance(field, dict) else None for field in fields]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise ValueError("columnar frame field names must be unique non-empty strings")
    if any(not isinstance(column, list) for column in values):
        raise ValueError("columnar frame values must be arrays")
    lengths = {len(column) for column in values}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise ValueError("columnar frame columns must have one shared non-zero row count")
    data = pd.DataFrame(dict(zip(names, values, strict=True)))
    for field in fields:
        name = field.get("name")
        if field.get("type") == "time":
            numeric = pd.to_numeric(data[name], errors="coerce")
            data[name] = pd.to_datetime(numeric, unit="ms", utc=True, errors="coerce") if numeric.notna().any() else pd.to_datetime(data[name], utc=True, errors="coerce")
    if not isinstance(rules, list):
        raise ValueError("validity_rules must be an array")
    mask = pd.Series(True, index=data.index)
    applied = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("validity rule must be an object")
        field = rule.get("field")
        accepted = rule.get("accepted_values")
        if not isinstance(field, str) or field not in data.columns or not isinstance(accepted, list) or not accepted:
            raise ValueError("validity rule is incomplete")
        normalized = {str(value).strip().lower() for value in accepted}
        mask &= data[field].map(lambda value: str(value).strip().lower() in normalized)
        applied.append({"field": field, "accepted_values": accepted, "applies_to": rule.get("applies_to", [])})
    filtered = data.loc[mask].copy()
    if rules and filtered.empty:
        raise ValueError("validity rules exclude every row")
    audit = {"input_rows": len(data), "valid_rows": len(filtered), "excluded_rows": len(data) - len(filtered), "rules": applied}
    return filtered, audit


def run(code: str, bundle_path: str, seed: int) -> None:
    matplotlib = importlib.import_module("matplotlib")
    matplotlib.use("Agg")
    plt = importlib.import_module("matplotlib.pyplot")
    np = importlib.import_module("numpy")
    pd = importlib.import_module("pandas")
    Figure = importlib.import_module("matplotlib.figure").Figure

    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    OUTPUT_DIR.mkdir(mode=0o700)
    random.seed(seed)
    np.random.seed(seed)
    manifest: list[dict[str, str]] = []
    captured_figures: set[int] = set()
    data, audit = load_dataframe(bundle_path, pd)
    (OUTPUT_DIR / "audit.json").write_text(json.dumps(audit), encoding="utf-8")

    def write(name: str, mime_type: str, value: str | bytes) -> None:
        payload = value.encode() if isinstance(value, str) else value
        if len(payload) > MAX_ITEM_BYTES:
            raise ValueError(f"captured output {name} exceeds {MAX_ITEM_BYTES} bytes")
        path = OUTPUT_DIR / name
        path.write_bytes(payload)
        manifest.append({"path": str(path), "mime_type": mime_type})

    def emit(value: Any, name: str | None = None) -> None:
        if value is None:
            return
        del name  # Optional LLM-friendly label; filenames remain server-controlled.
        index = len(manifest) + 1
        if isinstance(value, Figure):
            path = OUTPUT_DIR / f"figure-{index}.png"
            value.savefig(path, format="png", bbox_inches="tight")
            if path.stat().st_size > MAX_ITEM_BYTES:
                path.unlink()
                raise ValueError(f"captured output {path.name} exceeds {MAX_ITEM_BYTES} bytes")
            manifest.append({"path": str(path), "mime_type": "image/png"})
            captured_figures.add(value.number)
            return
        if hasattr(value, "to_plotly_json") and hasattr(value, "to_json"):
            write(f"plotly-{index}.json", "application/vnd.plotly.v1+json", value.to_json())
            return
        if isinstance(value, pd.DataFrame):
            write(f"table-{index}.html", "text/html", value.to_html(index=False))
            return
        html = getattr(value, "_repr_html_", None)
        if callable(html) and (rendered := html()) is not None:
            write(f"output-{index}.html", "text/html", str(rendered))
            return
        write(f"output-{index}.txt", "text/plain", repr(value))

    namespace = {"df": data, "pd": pd, "np": np, "display": emit, "emit": emit}
    setattr(plt, "show", lambda *args, **kwargs: None)
    try:
        tree = ast.parse(code, filename="<generated-analysis>", mode="exec")
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            body = ast.Module(body=tree.body[:-1], type_ignores=[])
            exec(compile(body, "<generated-analysis>", "exec"), namespace)
            emit(eval(compile(ast.Expression(tree.body[-1].value), "<generated-analysis>", "eval"), namespace))
        else:
            exec(compile(tree, "<generated-analysis>", "exec"), namespace)
    finally:
        for number in plt.get_fignums():
            if number not in captured_figures:
                emit(plt.figure(number))
        (OUTPUT_DIR / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
