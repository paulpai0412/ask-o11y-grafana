"""Deterministic library provenance without runtime agent machinery."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .frame import AnalysisCoreError


def deterministic_method_source(*, implementation: str, method: str, algorithm: str, packages: list[str], seed: int | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    if not implementation or not method or not algorithm or not packages:
        raise AnalysisCoreError("implementation, method, algorithm, and packages are required")
    libraries = []
    for package in packages:
        try:
            package_version = version(package)
        except PackageNotFoundError as exc:
            raise AnalysisCoreError(f"required analysis package is unavailable: {package}") from exc
        libraries.append({"name": package, "version": package_version})
    source = {
        "mode": "deterministic_library",
        "implementation": implementation,
        "method": method,
        "algorithm": algorithm,
        "algorithm_version": ",".join(f"{item['name']}-{item['version']}" for item in libraries),
        "libraries": libraries,
        "seed": seed,
        "runtime_agent": False,
        "runtime_llm": False,
        "runtime_skill": False,
    }
    if extra:
        source.update(extra)
    return source
