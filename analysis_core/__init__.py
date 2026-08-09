"""Pure deterministic analysis mechanics shared by domain MCPs."""

from .correlation import paired_statistics, pairwise_correlation
from .frame import AnalysisCoreError, dataframe_from_columnar_frame, numeric_series, scalar_float, scalar_int
from .profiling import profile_dataframe  # pyright: ignore[reportMissingImports]
from .provenance import deterministic_method_source
from .supervised import supervised_model  # pyright: ignore[reportMissingImports]
from .timeseries import forecast_series, parse_timestamp_series  # pyright: ignore[reportMissingImports]
from .unsupervised import cluster_rows, detect_anomalies  # pyright: ignore[reportMissingImports]
from .visualization import visualization_spec  # pyright: ignore[reportMissingImports]

__all__ = [
    "AnalysisCoreError",
    "dataframe_from_columnar_frame",
    "cluster_rows",
    "detect_anomalies",
    "deterministic_method_source",
    "forecast_series",
    "parse_timestamp_series",
    "numeric_series",
    "scalar_float",
    "scalar_int",
    "paired_statistics",
    "pairwise_correlation",
    "profile_dataframe",
    "supervised_model",
    "visualization_spec",
]
