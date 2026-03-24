"""Modelo baseline: Naive Estacional + HistoricAverage fallback.

SeasonalNaive es el benchmark obligatorio contra el cual se escala MASE.
Si la serie es demasiado corta para aplicar naive estacional
(< 2 * season_length), se cae a HistoricAverage.

Uso tipico
----------
>>> result = fit_predict_naive(demand_df, granularity="M", h=3, unique_id="SKU-001")
>>> result["model"]   # "SeasonalNaive" o "HistoricAverage"
>>> result["forecast"]  # pd.DataFrame con columnas [ds, yhat, yhat_lo80, yhat_hi80]
"""

from __future__ import annotations

import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import HistoricAverage, SeasonalNaive

from planning_core.forecasting.utils import (
    _normalize_forecast,
    get_season_length,
    to_nixtla_df,
)

# Minimo de observaciones para usar SeasonalNaive
_MIN_OBS_SEASONAL = 2  # necesita al menos 2 ciclos completos


def fit_predict_naive(
    demand_df: pd.DataFrame,
    granularity: str = "M",
    h: int = 3,
    unique_id: str = "SKU",
    target_col: str = "demand",
    level: list[int] | None = None,
) -> dict:
    """Ajusta SeasonalNaive (o HistoricAverage como fallback) y genera h periodos de forecast.

    Parameters
    ----------
    demand_df : pd.DataFrame
        Serie de demanda con columnas ``[period, demand]``.
    granularity : str
        Granularidad temporal: ``"D"``, ``"W"`` o ``"M"``.
    h : int
        Horizonte de pronostico en periodos.
    unique_id : str
        Identificador del SKU para el formato Nixtla.
    target_col : str
        Columna de la variable objetivo en ``demand_df``.
    level : list[int], optional
        Niveles de confianza a calcular (ej: [80, 95]). Default [80].

    Returns
    -------
    dict
        ``{"model": str, "forecast": pd.DataFrame, "season_length": int}``

        ``forecast`` tiene columnas ``[ds, yhat, yhat_lo80, yhat_hi80]``.
    """
    if level is None:
        level = [80]

    season_length = get_season_length(granularity)
    nixtla_df = to_nixtla_df(demand_df, unique_id=unique_id, target_col=target_col)

    n_obs = len(nixtla_df)
    use_seasonal = n_obs >= _MIN_OBS_SEASONAL * season_length

    if use_seasonal:
        model = SeasonalNaive(season_length=season_length)
        model_name = "SeasonalNaive"
    else:
        model = HistoricAverage()
        model_name = "HistoricAverage"

    sf = StatsForecast(models=[model], freq=_get_freq(granularity), n_jobs=1)
    sf.fit(nixtla_df)
    raw = sf.forecast(df=nixtla_df, h=h, level=level)

    # Columnas de intervalo
    lo_col = f"{model_name}-lo-80" if 80 in level else None
    hi_col = f"{model_name}-hi-80" if 80 in level else None

    forecast_df = _normalize_forecast(
        raw,
        model_col=model_name,
        lo_col=lo_col,
        hi_col=hi_col,
    )

    return {
        "model": model_name,
        "forecast": forecast_df,
        "season_length": season_length,
    }


def _get_freq(granularity: str) -> str:
    _map = {"D": "D", "W": "W-MON", "M": "MS"}
    return _map.get(granularity, "MS")
