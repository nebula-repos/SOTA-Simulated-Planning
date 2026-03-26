"""Modelo LightGBM con features temporales via MLForecast.

MLForecast construye un regresor tabular usando lags de la serie y features
de calendario como inputs. LightGBM actua como el estimador de cada paso.

A diferencia de AutoETS / AutoARIMA (modelos estadisticos), LightGBM:
- Captura relaciones no lineales entre lags y la demanda
- No requiere estacionariedad
- Puede incorporar multiples features de calendario simultaneamente
- Requiere mas datos para generalizar bien (minimo recomendado: 3*season_length)

Arquitectura de features
------------------------
- Lags: [1, 2, 3, season_length]         (mensual)
        [1, 2, 4, season_length]         (semanal)
        [1, 7, 14, season_length]        (diario)
- Fecha: mes del anio (M), semana del anio (W), dia de semana (D)

Intervalos de confianza
-----------------------
Se usan dos modelos de regresion cuantilica adicionales (alpha=0.1 y alpha=0.9)
para el IC 80 %. Esto triplica el tiempo de entrenamiento.

Uso tipico
----------
>>> result = fit_predict_lgbm(demand_df, granularity="M", h=6)
>>> result["model"]    # "LightGBM"
>>> result["forecast"] # pd.DataFrame con [ds, yhat, yhat_lo80, yhat_hi80]

>>> bt = run_backtest_lgbm(demand_df, granularity="M", h=6, n_windows=3)
>>> bt["LightGBM"]["mase"]  # float

Nota de integracion
-------------------
Este modulo usa MLForecast, cuya interfaz de cross_validation es distinta a
StatsForecast. Por eso ``run_backtest_lgbm`` es una funcion separada y no usa
``run_backtest()`` de backtest.py. El resultado tiene el mismo formato de
diccionario para integrarse al horse-race en selector.py.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from planning_core.forecasting.metrics import compute_all_metrics
from planning_core.forecasting.utils import FREQ_MAP, get_season_length, to_nixtla_df

MODEL_NAME = "LightGBM"

# Minimo de observaciones: 3 ciclos (mas conservador que ETS/ARIMA por necesidad de lags)
_MIN_OBS_FACTOR = 3


def _get_lags(granularity: str) -> list[int]:
    """Retorna los lags a usar como features segun la granularidad."""
    season_length = get_season_length(granularity)
    if granularity == "D":
        return [1, 7, 14, 30]
    if granularity == "W":
        return [1, 2, 4, season_length]
    # M y default
    return [1, 2, 3, season_length]


def _get_date_features(granularity: str):
    """Retorna las funciones de features de calendario segun la granularidad."""
    if granularity == "M":
        return ["month"]
    if granularity == "W":
        return ["week"]
    # D y default
    return ["dayofweek", "month"]


def _build_mlforecast_point(granularity: str):
    """Construye MLForecast con un solo modelo de punto (para backtest CV)."""
    try:
        from mlforecast import MLForecast
        from lightgbm import LGBMRegressor
    except ImportError as e:
        raise ImportError(
            "LightGBM y MLForecast son requeridos para este modelo. "
            "Instalar con: pip install 'sota-simulated-planning[forecast]'"
        ) from e

    return MLForecast(
        models={"LightGBM": LGBMRegressor(n_estimators=100, random_state=42, verbose=-1)},
        freq=FREQ_MAP.get(granularity, "MS"),
        lags=_get_lags(granularity),
        date_features=_get_date_features(granularity),
    )


def _build_mlforecast_full(granularity: str):
    """Construye MLForecast con punto + cuantiles 10/90 en una sola instancia (para inferencia)."""
    try:
        from mlforecast import MLForecast
        from lightgbm import LGBMRegressor
    except ImportError as e:
        raise ImportError(
            "LightGBM y MLForecast son requeridos para este modelo. "
            "Instalar con: pip install 'sota-simulated-planning[forecast]'"
        ) from e

    return MLForecast(
        models={
            "LightGBM": LGBMRegressor(n_estimators=100, random_state=42, verbose=-1),
            "lgbm_q10": LGBMRegressor(objective="quantile", alpha=0.10, n_estimators=100, random_state=42, verbose=-1),
            "lgbm_q90": LGBMRegressor(objective="quantile", alpha=0.90, n_estimators=100, random_state=42, verbose=-1),
        },
        freq=FREQ_MAP.get(granularity, "MS"),
        lags=_get_lags(granularity),
        date_features=_get_date_features(granularity),
    )


def fit_predict_lgbm(
    demand_df: pd.DataFrame,
    granularity: str = "M",
    h: int = 3,
    unique_id: str = "SKU",
    target_col: str = "demand",
    level: list[int] | None = None,
) -> dict:
    """Ajusta LightGBM con MLForecast y genera h periodos de forecast.

    Parameters
    ----------
    demand_df : pd.DataFrame
        Serie de demanda con columnas ``[period, demand]``.
    granularity : str
        ``"D"``, ``"W"`` o ``"M"``.
    h : int
        Horizonte de pronostico en periodos.
    unique_id : str
        Identificador del SKU.
    target_col : str
        Columna objetivo en ``demand_df``.
    level : list[int], optional
        Niveles de confianza. Default [80]. Solo se soporta 80.

    Returns
    -------
    dict
        ``{"model": "LightGBM", "forecast": pd.DataFrame, "season_length": int}``

    Raises
    ------
    ValueError
        Si la serie tiene menos de ``3 * season_length`` observaciones.
    ImportError
        Si mlforecast o lightgbm no estan instalados.
    """
    season_length = get_season_length(granularity)
    nixtla_df = to_nixtla_df(demand_df, unique_id=unique_id, target_col=target_col)

    n_obs = len(nixtla_df)
    min_obs = _MIN_OBS_FACTOR * season_length
    if n_obs < min_obs:
        raise ValueError(
            f"LightGBM requiere al menos {min_obs} observaciones "
            f"para granularidad {granularity!r}. Serie tiene {n_obs} obs."
        )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mlf = _build_mlforecast_full(granularity)
        mlf.fit(nixtla_df)
        preds = mlf.predict(h=h)

    forecast_df = pd.DataFrame({
        "ds": preds["ds"],
        "yhat": preds["LightGBM"].clip(lower=0).values,
        "yhat_lo80": preds["lgbm_q10"].clip(lower=0).values,
        "yhat_hi80": preds["lgbm_q90"].clip(lower=0).values,
    }).reset_index(drop=True)

    return {
        "model": MODEL_NAME,
        "forecast": forecast_df,
        "season_length": season_length,
    }


def run_backtest_lgbm(
    demand_df: pd.DataFrame,
    granularity: str = "M",
    h: int = 3,
    n_windows: int = 3,
    unique_id: str = "SKU",
    target_col: str = "demand",
    naive_type: str = "seasonal",
) -> dict:
    """Backtest expanding-window para LightGBM via MLForecast.

    Produce el mismo formato de resultado que ``run_backtest()`` en backtest.py
    para integrarse al horse-race en selector.py.

    Parameters
    ----------
    demand_df : pd.DataFrame
        Serie de demanda con columnas ``[period, demand]``.
    granularity : str
        ``"D"``, ``"W"`` o ``"M"``.
    h : int
        Horizonte de pronostico en periodos.
    n_windows : int
        Numero de ventanas de validacion.
    unique_id : str
        Identificador de la serie.
    target_col : str
        Columna objetivo.
    naive_type : str
        Tipo de benchmark naive para MASE: ``"seasonal"``, ``"lag1"`` o ``"mean"``.
        Debe coincidir con el que usa ``run_backtest()`` para el mismo SKU.

    Returns
    -------
    dict
        ``{"LightGBM": {mase, wape, bias, mae, rmse, n_windows, status, h}}``
        Mismo formato que ``run_backtest()`` para integrarse al horse-race.
    """
    season_length = get_season_length(granularity)
    nixtla_df = to_nixtla_df(demand_df, unique_id=unique_id, target_col=target_col)

    n_obs = len(nixtla_df)
    min_required = season_length + h * n_windows
    if n_obs < min_required or n_obs < _MIN_OBS_FACTOR * season_length:
        short = {
            "status": "series_too_short",
            "n_obs": n_obs,
            "min_required": max(min_required, _MIN_OBS_FACTOR * season_length),
            "mase": float("nan"),
            "wape": float("nan"),
            "bias": float("nan"),
            "mae": float("nan"),
            "rmse": float("nan"),
            "n_windows": 0,
            "h": h,
        }
        return {MODEL_NAME: short}

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mlf = _build_mlforecast_point(granularity)
            cv_df = mlf.cross_validation(
                data=nixtla_df,
                h=h,
                n_windows=n_windows,
                refit=True,
            )
    except Exception as exc:
        return {MODEL_NAME: {
            "status": "error",
            "error": str(exc),
            "mase": float("nan"),
            "wape": float("nan"),
            "bias": float("nan"),
            "mae": float("nan"),
            "rmse": float("nan"),
            "n_windows": 0,
            "h": h,
        }}

    model_col = "LightGBM"
    if model_col not in cv_df.columns:
        return {MODEL_NAME: {
            "status": "model_column_missing",
            "mase": float("nan"), "wape": float("nan"), "bias": float("nan"),
            "mae": float("nan"), "rmse": float("nan"),
            "n_windows": 0, "h": h,
        }}

    metrics_by_window = []
    for cutoff_date, window_df in cv_df.groupby("cutoff"):
        train_mask = nixtla_df["ds"] <= cutoff_date
        train_y = nixtla_df.loc[train_mask, "y"].values
        actual = window_df["y"].values
        forecast = window_df[model_col].clip(lower=0).values
        m = compute_all_metrics(actual, forecast, season_length=season_length, train_actual=train_y, naive_type=naive_type)
        metrics_by_window.append(m)

    # Promediar sobre ventanas
    keys = ["mase", "wape", "bias", "mae", "rmse"]
    aggregated: dict = {}
    for k in keys:
        values = [w[k] for w in metrics_by_window if not np.isnan(w.get(k, float("nan")))]
        aggregated[k] = float(np.mean(values)) if values else float("nan")

    aggregated["status"] = "ok"
    aggregated["n_windows"] = len(metrics_by_window)
    aggregated["h"] = h

    return {MODEL_NAME: aggregated}
