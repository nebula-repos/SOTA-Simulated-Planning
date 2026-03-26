"""Backtest de ventana expandible para modelos de forecast.

Implementa una evaluacion out-of-sample sistematica usando el metodo de
ventana expandible (expanding window): se entrena con todo el historico hasta
el punto de corte, se predice h periodos hacia adelante, y se avanza el corte.

Minimo de observaciones: ``season_length + h * n_windows`` (un ciclo completo de
entrenamiento mas todas las ventanas de evaluacion). Series mas cortas son marcadas
como ``"series_too_short"`` y no entran al horse-race.

Diagrama de ventanas (h=3, n_windows=3, step_size=3)::

    [---Train 1---] [Eval 1 ]
    [----Train 2----] [Eval 2 ]
    [-----Train 3-----] [Eval 3 ]

Referencias
-----------
- Hyndman & Athanasopoulos (2021). Forecasting: Principles and Practice, Cap. 5.
- StatsForecast docs: ``StatsForecast.cross_validation()``
"""

from __future__ import annotations

import warnings
from typing import Callable

import numpy as np
import pandas as pd
from statsforecast import StatsForecast

from planning_core.forecasting.metrics import compute_all_metrics
from planning_core.forecasting.utils import FREQ_MAP, get_season_length, to_nixtla_df

_DEFAULT_MIN_WINDOWS = 3


def run_backtest(
    demand_df: pd.DataFrame,
    model_instances: list,
    model_names: list[str],
    granularity: str = "M",
    h: int = 3,
    n_windows: int = _DEFAULT_MIN_WINDOWS,
    step_size: int | None = None,
    unique_id: str = "SKU",
    target_col: str = "demand",
    level: list[int] | None = None,
    naive_type: str = "seasonal",
    return_cv: bool = False,
) -> dict[str, dict]:
    """Corre backtest expanding-window para una lista de modelos sobre una serie.

    Para cada modelo retorna las metricas promediadas sobre ``n_windows``
    ventanas de evaluacion.

    Parameters
    ----------
    demand_df : pd.DataFrame
        Serie de demanda con columnas ``[period, demand]`` (o ``target_col``).
    model_instances : list
        Instancias de modelos StatsForecast (ej: ``[AutoETS(...), SeasonalNaive(...)]``).
    model_names : list[str]
        Nombres de los modelos en el mismo orden (columna en el output de StatsForecast).
    granularity : str
        ``"D"``, ``"W"`` o ``"M"``.
    h : int
        Horizonte de pronostico por ventana.
    n_windows : int
        Numero de ventanas de evaluacion. Minimo 3 para MASE estable.
    step_size : int, optional
        Avance del punto de corte entre ventanas. Por defecto = h (ventanas no solapadas).
    unique_id : str
        Identificador del SKU.
    target_col : str
        Columna de la variable objetivo en ``demand_df``.
    level : list[int], optional
        Niveles de confianza a calcular. Por defecto ninguno (solo punto central).
    naive_type : str
        Tipo de naive de referencia para el denominador del MASE.
        ``"seasonal"`` (lag-m), ``"lag1"`` (random walk) o ``"mean"``
        (desviacion respecto a la media). Ver ``compute_mase`` para detalle.

    Returns
    -------
    dict[str, dict]
        Clave: nombre del modelo.
        Valor: dict con claves ``mase``, ``wmape``, ``rmsse``, ``bias``, ``mae``, ``rmse``,
        ``n_windows``, ``h``, ``status``.

        Si la serie es demasiado corta: ``{"status": "series_too_short", ...}``.

    Examples
    --------
    >>> from statsforecast.models import AutoETS, SeasonalNaive
    >>> result = run_backtest(demand_df, [AutoETS(12), SeasonalNaive(12)],
    ...                       ["AutoETS", "SeasonalNaive"], granularity="M", h=3)
    >>> result["AutoETS"]["mase"]
    0.72
    """
    if step_size is None:
        step_size = h

    freq = FREQ_MAP.get(granularity, "MS")
    season_length = get_season_length(granularity)
    nixtla_df = to_nixtla_df(demand_df, unique_id=unique_id, target_col=target_col)

    # Validar longitud minima: 1 ciclo completo de entrenamiento + todas las ventanas de evaluacion
    min_required = season_length + h * n_windows
    if len(nixtla_df) < min_required:
        short_result = {
            "status": "series_too_short",
            "n_obs": len(nixtla_df),
            "min_required": min_required,
            "mase": float("nan"),
            "wmape": float("nan"),
            "rmsse": float("nan"),
            "bias": float("nan"),
            "mae": float("nan"),
            "rmse": float("nan"),
            "n_windows": 0,
            "h": h,
        }
        return {name: short_result.copy() for name in model_names}

    sf = StatsForecast(models=model_instances, freq=freq, n_jobs=1)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cv_df = sf.cross_validation(
            df=nixtla_df,
            h=h,
            n_windows=n_windows,
            step_size=step_size,
            level=level or [],
        )

    if return_cv:
        results: dict[str, dict] = {"__cv_df__": cv_df}
    else:
        results: dict[str, dict] = {}
    for model_name in model_names:
        if model_name not in cv_df.columns:
            results[model_name] = {
                "status": "model_column_missing",
                "mase": float("nan"), "wmape": float("nan"), "rmsse": float("nan"),
                "bias": float("nan"), "mae": float("nan"), "rmse": float("nan"),
                "n_windows": 0, "h": h,
            }
            continue

        metrics_by_window: list[dict] = []
        for cutoff_date, window_df in cv_df.groupby("cutoff"):
            train_mask = nixtla_df["ds"] <= cutoff_date
            train_y = nixtla_df.loc[train_mask, "y"].values
            actual = window_df["y"].values
            forecast = window_df[model_name].clip(lower=0).values
            m = compute_all_metrics(actual, forecast, season_length=season_length, train_actual=train_y, naive_type=naive_type)
            metrics_by_window.append(m)

        # Promediar metricas sobre ventanas (ignorando NaN)
        aggregated = _aggregate_window_metrics(metrics_by_window)
        aggregated["status"] = "ok"
        aggregated["n_windows"] = len(metrics_by_window)
        aggregated["h"] = h
        results[model_name] = aggregated

    return results


def _aggregate_window_metrics(windows: list[dict]) -> dict:
    """Promedia metricas sobre ventanas del backtest ignorando NaN."""
    if not windows:
        return {"mase": float("nan"), "wmape": float("nan"), "rmsse": float("nan"),
                "bias": float("nan"), "mae": float("nan"), "rmse": float("nan")}

    keys = ["mase", "wmape", "rmsse", "bias", "mae", "rmse"]
    result = {}
    for k in keys:
        values = [w[k] for w in windows if not np.isnan(w.get(k, float("nan")))]
        result[k] = float(np.mean(values)) if values else float("nan")
    return result


def backtest_summary(backtest_results: dict[str, dict]) -> pd.DataFrame:
    """Convierte los resultados del backtest a un DataFrame tabulado.

    Util para comparar modelos side-by-side.

    Parameters
    ----------
    backtest_results : dict[str, dict]
        Salida de ``run_backtest()``.

    Returns
    -------
    pd.DataFrame
        Filas = modelos, columnas = metricas. Ordenado por MASE ascendente.
    """
    rows = []
    for model_name, metrics in backtest_results.items():
        row = {"model": model_name}
        row.update(metrics)
        rows.append(row)

    df = pd.DataFrame(rows)
    if "mase" in df.columns:
        df = df.sort_values("mase", na_position="last")
    return df.reset_index(drop=True)
