"""Tipos de datos compartidos del módulo de evaluación.

Sin dependencias de planning_core — importable de forma autónoma.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd


@dataclass
class EvalConfig:
    """Configuración de una corrida de evaluación del catálogo.

    Todos los campos son serializables a JSON para reproducibilidad.
    """

    granularity: str = "M"
    h: int = 3
    n_windows: int = 3
    use_lgbm: bool = False       # False por defecto: perf batch razonable
    outlier_method: str = "iqr"
    treat_strategy: str = "winsorize"
    run_name: str | None = None  # tag semántico (ej: "baseline", "lgbm_on")
    sample_n: int | None = None  # None = catálogo completo
    random_seed: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EvalConfig":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class CatalogEvalResult:
    """Resultado de una corrida de evaluación sobre el catálogo.

    Contiene el DataFrame completo por SKU y metadatos de la corrida.
    """

    config: EvalConfig
    run_id: str
    sku_results: pd.DataFrame     # 1 fila por SKU, ver catalog_runner._ROW_COLS
    elapsed_seconds: float
    n_ok: int
    n_fallback: int
    n_no_forecast: int
    n_error: int
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def n_evaluated(self) -> int:
        return len(self.sku_results)

    @property
    def mase_global_median(self) -> float:
        valid = self.sku_results[self.sku_results["status"].isin(["ok", "fallback"])]
        return float(valid["mase"].median()) if not valid.empty else float("nan")

    @property
    def mase_global_mean(self) -> float:
        valid = self.sku_results[self.sku_results["status"].isin(["ok", "fallback"])]
        return float(valid["mase"].mean()) if not valid.empty else float("nan")

    def summary_dict(self) -> dict:
        """Resumen serializable para run_metadata.json."""
        return {
            "n_skus_evaluated": self.n_evaluated,
            "n_ok": self.n_ok,
            "n_fallback": self.n_fallback,
            "n_no_forecast": self.n_no_forecast,
            "n_error": self.n_error,
            "mase_global_median": round(self.mase_global_median, 4),
            "mase_global_mean": round(self.mase_global_mean, 4),
            "elapsed_seconds": self.elapsed_seconds,
        }
