"""Comparación de múltiples runs de evaluación.

Módulo puro pandas — sin dependencias de planning_core.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from planning_core.forecasting.evaluation import run_store
from planning_core.forecasting.evaluation._types import CatalogEvalResult


# ---------------------------------------------------------------------------
# Comparación global entre runs
# ---------------------------------------------------------------------------

def compare_runs(
    run_ids: list[str],
    base_dir: str | Path = "output/eval_runs",
    metric: str = "mase",
) -> pd.DataFrame:
    """Compara métricas globales de múltiples runs side-by-side.

    Parameters
    ----------
    run_ids : list[str]
    base_dir : str | Path
    metric : str
        Métrica a comparar (ej: "mase", "wape", "bias").

    Returns
    -------
    pd.DataFrame
        Filas = runs, columnas = métricas clave + config.
    """
    rows = []
    for rid in run_ids:
        result = run_store.load_run(rid, base_dir)
        valid  = result.sku_results[result.sku_results["status"].isin(["ok", "fallback"])]
        col    = valid[metric].dropna() if metric in valid.columns else pd.Series(dtype=float)

        rows.append({
            "run_id":       rid,
            "run_name":     result.config.run_name or rid,
            "n_skus":       result.n_evaluated,
            "n_ok":         result.n_ok,
            "n_fallback":   result.n_fallback,
            "n_no_forecast":result.n_no_forecast,
            "n_error":      result.n_error,
            f"{metric}_median": round(float(col.median()), 4) if len(col) else float("nan"),
            f"{metric}_mean":   round(float(col.mean()),   4) if len(col) else float("nan"),
            f"{metric}_p75":    round(float(col.quantile(0.75)), 4) if len(col) else float("nan"),
            f"{metric}_p90":    round(float(col.quantile(0.90)), 4) if len(col) else float("nan"),
            "granularity":  result.config.granularity,
            "h":            result.config.h,
            "n_windows":    result.config.n_windows,
            "use_lgbm":     result.config.use_lgbm,
            "elapsed_s":    result.elapsed_seconds,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Comparación por segmento
# ---------------------------------------------------------------------------

def compare_runs_by_segment(
    run_ids: list[str],
    segment_col: str,
    base_dir: str | Path = "output/eval_runs",
    metric: str = "mase",
    agg: str = "median",
) -> pd.DataFrame:
    """Compara la métrica por segmento entre múltiples runs.

    Retorna una tabla wide: filas = valores del segmento, columnas = run_ids.
    Ideal para bar charts agrupados.

    Parameters
    ----------
    run_ids : list[str]
    segment_col : str
        Columna de segmentación (ej: "sb_class", "abc_class").
    base_dir : str | Path
    metric : str
    agg : str
        Función de agregación: "median" o "mean".

    Returns
    -------
    pd.DataFrame
        Index = segment_value, columnas = [run_id/run_name ...]
    """
    frames = {}
    for rid in run_ids:
        result = run_store.load_run(rid, base_dir)
        label  = result.config.run_name or rid
        valid  = result.sku_results[result.sku_results["status"].isin(["ok", "fallback"])]

        if segment_col not in valid.columns or metric not in valid.columns:
            continue

        agg_fn  = "median" if agg == "median" else "mean"
        grouped = valid.groupby(segment_col)[metric].agg(agg_fn).round(4)
        frames[label] = grouped

    if not frames:
        return pd.DataFrame()

    return pd.DataFrame(frames).rename_axis(segment_col)


# ---------------------------------------------------------------------------
# SKUs donde cambió el modelo ganador entre dos runs
# ---------------------------------------------------------------------------

def find_winner_changes(
    run_id_a: str,
    run_id_b: str,
    base_dir: str | Path = "output/eval_runs",
) -> pd.DataFrame:
    """Retorna los SKUs donde cambió el modelo ganador entre dos runs.

    Útil para entender el impacto de cambios de config en la selección de modelo.

    Returns
    -------
    pd.DataFrame con columnas:
        sku, sb_class, abc_class, model_a, mase_a, model_b, mase_b, mase_delta
    """
    result_a = run_store.load_run(run_id_a, base_dir)
    result_b = run_store.load_run(run_id_b, base_dir)

    _VALID = {"ok", "fallback"}
    cols = ["sku", "sb_class", "abc_class", "model_winner", "mase", "status"]
    a = result_a.sku_results[[c for c in cols if c in result_a.sku_results.columns]].copy()
    b = result_b.sku_results[[c for c in cols if c in result_b.sku_results.columns]].copy()

    # Solo comparar SKUs con forecast válido en ambos runs (excluye error/no_forecast)
    if "status" in a.columns:
        a = a[a["status"].isin(_VALID)]
    if "status" in b.columns:
        b = b[b["status"].isin(_VALID)]

    merged = a.merge(b, on="sku", suffixes=("_a", "_b"), how="inner")
    # Usar fillna para que None == None y NaN == NaN en la comparación
    ma = merged["model_winner_a"].fillna("__none__")
    mb = merged["model_winner_b"].fillna("__none__")
    changed = merged[ma != mb].copy()

    if changed.empty:
        return pd.DataFrame()

    result = changed.rename(columns={
        "model_winner_a": "model_a",
        "model_winner_b": "model_b",
        "mase_a":         "mase_a",
        "mase_b":         "mase_b",
        "sb_class_a":     "sb_class",
        "abc_class_a":    "abc_class",
    }).copy()

    result["mase_delta"] = (result["mase_b"] - result["mase_a"]).round(4)

    keep = ["sku", "sb_class", "abc_class", "model_a", "mase_a", "model_b", "mase_b", "mase_delta"]
    return result[[c for c in keep if c in result.columns]].sort_values("mase_delta")
