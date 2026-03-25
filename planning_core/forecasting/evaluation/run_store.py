"""Persistencia y carga de runs de evaluación.

Módulo puro (pandas + pathlib + json) — sin dependencias de planning_core.

Estructura en disco
-------------------
<base_dir>/
└── <run_id>/
    ├── run_metadata.json      ← config + summary global (legible desde terminal)
    └── sku_results.parquet    ← DataFrame completo por SKU
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from planning_core.forecasting.evaluation._types import CatalogEvalResult, EvalConfig

_META_FILE = "run_metadata.json"
_SKU_FILE  = "sku_results.parquet"


# ---------------------------------------------------------------------------
# Guardar
# ---------------------------------------------------------------------------

def save_run(result: CatalogEvalResult, base_dir: str | Path = "output/eval_runs") -> Path:
    """Persiste un resultado de evaluación en disco.

    Parameters
    ----------
    result : CatalogEvalResult
    base_dir : str | Path
        Directorio raíz donde se crean los subdirectorios de runs.

    Returns
    -------
    Path
        Ruta al directorio del run creado.
    """
    run_dir = Path(base_dir) / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # sku_results → Parquet (preserva tipos, ~10x más compacto que CSV)
    result.sku_results.to_parquet(run_dir / _SKU_FILE, index=False)

    # Metadata → JSON legible
    metadata = {
        "run_id":     result.run_id,
        "run_name":   result.config.run_name,
        "created_at": result.created_at,
        "config":     result.config.to_dict(),
        "summary":    result.summary_dict(),
    }
    with open(run_dir / _META_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False, default=_json_default)

    return run_dir


# ---------------------------------------------------------------------------
# Cargar
# ---------------------------------------------------------------------------

def load_run(run_id: str, base_dir: str | Path = "output/eval_runs") -> CatalogEvalResult:
    """Carga un run previamente guardado desde disco.

    Parameters
    ----------
    run_id : str
        ID del run (nombre del subdirectorio).
    base_dir : str | Path

    Returns
    -------
    CatalogEvalResult
    """
    run_dir = Path(base_dir) / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"Run no encontrado: {run_dir}")

    with open(run_dir / _META_FILE, "r", encoding="utf-8") as f:
        meta = json.load(f)

    sku_results = pd.read_parquet(run_dir / _SKU_FILE)
    config = EvalConfig.from_dict(meta["config"])
    summary = meta.get("summary", {})

    return CatalogEvalResult(
        config=config,
        run_id=meta["run_id"],
        sku_results=sku_results,
        elapsed_seconds=summary.get("elapsed_seconds", 0.0),
        n_ok=summary.get("n_ok", 0),
        n_fallback=summary.get("n_fallback", 0),
        n_no_forecast=summary.get("n_no_forecast", 0),
        n_error=summary.get("n_error", 0),
        created_at=meta.get("created_at", ""),
    )


# ---------------------------------------------------------------------------
# Listar
# ---------------------------------------------------------------------------

def list_runs(base_dir: str | Path = "output/eval_runs") -> pd.DataFrame:
    """Lista todos los runs disponibles en ``base_dir``.

    Retorna un DataFrame con una fila por run, ordenado por fecha de creación
    descendente. Útil para elegir qué runs comparar.

    Returns
    -------
    pd.DataFrame con columnas:
        run_id, run_name, created_at, n_skus, n_ok, n_fallback,
        mase_global_median, elapsed_seconds,
        granularity, h, n_windows, use_lgbm
    """
    base = Path(base_dir)
    if not base.exists():
        return pd.DataFrame()

    rows = []
    for run_dir in sorted(base.iterdir()):
        meta_path = run_dir / _META_FILE
        if not meta_path.exists():
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            cfg = meta.get("config", {})
            summ = meta.get("summary", {})
            rows.append({
                "run_id":             meta.get("run_id"),
                "run_name":           meta.get("run_name"),
                "created_at":         meta.get("created_at"),
                "n_skus":             summ.get("n_skus_evaluated"),
                "n_ok":               summ.get("n_ok"),
                "n_fallback":         summ.get("n_fallback"),
                "n_no_forecast":      summ.get("n_no_forecast"),
                "n_error":            summ.get("n_error"),
                "mase_global_median": summ.get("mase_global_median"),
                "elapsed_seconds":    summ.get("elapsed_seconds"),
                "granularity":        cfg.get("granularity"),
                "h":                  cfg.get("h"),
                "n_windows":          cfg.get("n_windows"),
                "use_lgbm":           cfg.get("use_lgbm"),
            })
        except Exception:
            continue

    df = pd.DataFrame(rows)
    if not df.empty and "created_at" in df.columns:
        df = df.sort_values("created_at", ascending=False).reset_index(drop=True)
    return df


def delete_run(run_id: str, base_dir: str | Path = "output/eval_runs") -> None:
    """Elimina un run del disco."""
    import shutil
    run_dir = Path(base_dir) / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_default(obj):
    """Serializa tipos no-JSON (float nan → null, etc.)."""
    if isinstance(obj, float) and math.isnan(obj):
        return None
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
