"""Agrega resultados por SKU a métricas globales y por segmento.

Módulo puro pandas — sin dependencias de planning_core.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Métricas globales
# ---------------------------------------------------------------------------

def compute_global_metrics(sku_results: pd.DataFrame) -> dict:
    """Resumen global de la corrida.

    Parameters
    ----------
    sku_results : pd.DataFrame
        Salida de ``run_catalog_evaluation``.

    Returns
    -------
    dict con claves:
        n_total, n_ok, n_fallback, n_no_forecast, n_error,
        fallback_rate, mase_median, mase_mean, mase_p75, mase_p90,
        wape_median, bias_median
    """
    total = len(sku_results)
    status = sku_results["status"].value_counts()

    valid = sku_results[sku_results["status"].isin(["ok", "fallback"])].copy()
    mase  = valid["mase"].dropna()

    return {
        "n_total":       total,
        "n_ok":          int(status.get("ok", 0)),
        "n_fallback":    int(status.get("fallback", 0)),
        "n_no_forecast": int(status.get("no_forecast", 0)),
        "n_error":       int(status.get("error", 0)),
        "fallback_rate": round(int(status.get("fallback", 0)) / max(total, 1), 4),
        "mase_median":   _safe_stat(mase, "median"),
        "mase_mean":     _safe_stat(mase, "mean"),
        "mase_p75":      _safe_stat(mase, 0.75),
        "mase_p90":      _safe_stat(mase, 0.90),
        "wape_median":   _safe_stat(valid["wape"].dropna(), "median"),
        "bias_median":   _safe_stat(valid["bias"].dropna(), "median"),
    }


# ---------------------------------------------------------------------------
# Métricas por segmento
# ---------------------------------------------------------------------------

def compute_segment_metrics(
    sku_results: pd.DataFrame,
    segment_cols: list[str] | None = None,
) -> pd.DataFrame:
    """Agrega métricas por cada dimensión de segmentación.

    Parameters
    ----------
    sku_results : pd.DataFrame
    segment_cols : list[str], optional
        Columnas de segmentación. Default: sb_class, abc_class, xyz_class,
        abc_xyz, is_seasonal, model_winner.

    Returns
    -------
    pd.DataFrame con columnas:
        segment_col, segment_value, n_skus, n_ok, n_fallback,
        mase_median, mase_mean, mase_p75, mase_p90,
        wape_median, bias_median, top_model, top_model_pct
    """
    if segment_cols is None:
        segment_cols = ["sb_class", "abc_class", "xyz_class", "abc_xyz",
                        "is_seasonal", "model_winner"]

    rows = []
    for col in segment_cols:
        if col not in sku_results.columns:
            continue
        for val, grp in sku_results.groupby(col, dropna=False):
            rows.append(_segment_row(col, val, grp))

    return pd.DataFrame(rows)


def _segment_row(col: str, val, grp: pd.DataFrame) -> dict:
    valid = grp[grp["status"].isin(["ok", "fallback"])]
    mase  = valid["mase"].dropna()

    top_model, top_pct = _top_model(valid)

    return {
        "segment_col":    col,
        "segment_value":  str(val),
        "n_skus":         len(grp),
        "n_ok":           int((grp["status"] == "ok").sum()),
        "n_fallback":     int((grp["status"] == "fallback").sum()),
        "n_no_forecast":  int((grp["status"] == "no_forecast").sum()),
        "n_error":        int((grp["status"] == "error").sum()),
        "mase_median":    _safe_stat(mase, "median"),
        "mase_mean":      _safe_stat(mase, "mean"),
        "mase_p75":       _safe_stat(mase, 0.75),
        "mase_p90":       _safe_stat(mase, 0.90),
        "wape_median":    _safe_stat(valid["wape"].dropna(), "median"),
        "bias_median":    _safe_stat(valid["bias"].dropna(), "median"),
        "top_model":      top_model,
        "top_model_pct":  top_pct,
    }


# ---------------------------------------------------------------------------
# Distribución de modelo ganador
# ---------------------------------------------------------------------------

def compute_model_selection_summary(
    sku_results: pd.DataFrame,
    by: str | None = None,
) -> pd.DataFrame:
    """Distribución de modelos ganadores, opcionalmente por segmento.

    Parameters
    ----------
    sku_results : pd.DataFrame
    by : str, optional
        Columna de agrupación (ej: "sb_class"). Si None, distribución global.

    Returns
    -------
    pd.DataFrame con columnas: model_winner, n_skus, pct (y ``by`` si se especificó)
    """
    valid = sku_results[sku_results["status"].isin(["ok", "fallback"])].copy()

    if by:
        counts = (
            valid.groupby([by, "model_winner"], dropna=False)
            .size()
            .reset_index(name="n_skus")
        )
        totals = valid.groupby(by).size().rename("total")
        counts = counts.join(totals, on=by)
        counts["pct"] = (counts["n_skus"] / counts["total"]).round(4)
        return counts.drop(columns="total").sort_values([by, "n_skus"], ascending=[True, False])
    else:
        total = len(valid)
        counts = valid["model_winner"].value_counts().reset_index()
        counts.columns = ["model_winner", "n_skus"]
        counts["pct"] = (counts["n_skus"] / max(total, 1)).round(4)
        return counts


# ---------------------------------------------------------------------------
# Distribución de una métrica (percentiles)
# ---------------------------------------------------------------------------

def compute_metric_distribution(
    sku_results: pd.DataFrame,
    metric: str = "mase",
    by: str | None = None,
    percentiles: list[float] | None = None,
) -> pd.DataFrame:
    """Distribución percentil de una métrica, opcionalmente por segmento.

    Parameters
    ----------
    sku_results : pd.DataFrame
    metric : str
        Columna de métrica (ej: "mase", "wape", "bias").
    by : str, optional
        Columna de agrupación. Si None, distribución global.
    percentiles : list[float], optional
        Default: [0.25, 0.50, 0.75, 0.90, 0.95].

    Returns
    -------
    pd.DataFrame con columnas: [by,] count, mean, p25, p50, p75, p90, p95
    """
    if percentiles is None:
        percentiles = [0.25, 0.50, 0.75, 0.90, 0.95]

    valid = sku_results[sku_results["status"].isin(["ok", "fallback"])].copy()
    valid = valid.dropna(subset=[metric])

    pct_names = {p: f"p{int(p * 100)}" for p in percentiles}

    def _describe(s: pd.Series) -> dict:
        return {
            "count": len(s),
            "mean":  round(float(s.mean()), 4) if len(s) else float("nan"),
            **{pct_names[p]: round(float(s.quantile(p)), 4) if len(s) else float("nan")
               for p in percentiles},
        }

    if by:
        rows = []
        for val, grp in valid.groupby(by, dropna=False):
            d = {by: val}
            d.update(_describe(grp[metric]))
            rows.append(d)
        return pd.DataFrame(rows)
    else:
        return pd.DataFrame([_describe(valid[metric])])


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _safe_stat(s: pd.Series, stat) -> float:
    if s.empty:
        return float("nan")
    if stat == "median":
        return round(float(s.median()), 4)
    if stat == "mean":
        return round(float(s.mean()), 4)
    return round(float(s.quantile(stat)), 4)  # stat es un float percentil


def _top_model(valid: pd.DataFrame) -> tuple[str | None, float]:
    if valid.empty or "model_winner" not in valid.columns:
        return None, 0.0
    counts = valid["model_winner"].value_counts()
    if counts.empty:
        return None, 0.0
    top = counts.index[0]
    pct = round(float(counts.iloc[0] / len(valid)), 4)
    return top, pct
