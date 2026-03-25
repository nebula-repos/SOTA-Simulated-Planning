"""Modelo ETS automatico via StatsForecast (AutoETS).

AutoETS selecciona automaticamente la combinacion optima de
Error / Trend / Seasonality minimizando AICc, equivalente a
``ets()`` de Rob Hyndman en R (paquete forecast).

Uso tipico
----------
>>> result = fit_predict_ets(demand_df, granularity="M", h=3, unique_id="SKU-001")
>>> result["model"]     # "AutoETS"
>>> result["forecast"]  # pd.DataFrame con [ds, yhat, yhat_lo80, yhat_hi80]
"""

from __future__ import annotations

import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import AutoETS

from planning_core.forecasting.utils import (
    FREQ_MAP,
    _normalize_forecast,
    get_season_length,
    to_nixtla_df,
)

MODEL_NAME = "AutoETS"

# Minimo de observaciones para entrenar AutoETS con estacionalidad
_MIN_OBS_ETS = 2  # al menos 2 * season_length observaciones


def fit_predict_ets(
    demand_df: pd.DataFrame,
    granularity: str = "M",
    h: int = 3,
    unique_id: str = "SKU",
    target_col: str = "demand",
    level: list[int] | None = None,
) -> dict:
    """Ajusta AutoETS y genera h periodos de forecast con intervalos de confianza.

    AutoETS prueba todos los modelos ETS posibles y selecciona el de menor AICc.
    Genera intervalos de confianza analiticos para los niveles indicados.

    Parameters
    ----------
    demand_df : pd.DataFrame
        Serie de demanda con columnas ``[period, demand]``.
    granularity : str
        Granularidad: ``"D"``, ``"W"`` o ``"M"``.
    h : int
        Horizonte de pronostico en periodos.
    unique_id : str
        Identificador del SKU.
    target_col : str
        Columna de la variable objetivo en ``demand_df``.
    level : list[int], optional
        Niveles de confianza a calcular. Default [80, 95].

    Returns
    -------
    dict
        ``{"model": "AutoETS", "forecast": pd.DataFrame, "season_length": int}``

        ``forecast`` tiene columnas ``[ds, yhat, yhat_lo80, yhat_hi80]``.

    Raises
    ------
    ValueError
        Si la serie tiene menos de ``2 * season_length`` observaciones no nulas.
    """
    if level is None:
        level = [80, 95]

    season_length = get_season_length(granularity)
    nixtla_df = to_nixtla_df(demand_df, unique_id=unique_id, target_col=target_col)

    n_obs = len(nixtla_df)
    if n_obs < _MIN_OBS_ETS * season_length:
        raise ValueError(
            f"AutoETS requiere al menos {_MIN_OBS_ETS * season_length} observaciones "
            f"para granularidad {granularity!r} (season_length={season_length}). "
            f"Serie tiene {n_obs} obs. Usar SeasonalNaive o HistoricAverage."
        )

    ets_model = AutoETS(season_length=season_length)
    sf = StatsForecast(models=[ets_model], freq=FREQ_MAP.get(granularity, "MS"), n_jobs=1)
    sf.fit(nixtla_df)
    raw = sf.forecast(df=nixtla_df, h=h, level=level)

    lo_col = f"{MODEL_NAME}-lo-80" if 80 in level else None
    hi_col = f"{MODEL_NAME}-hi-80" if 80 in level else None

    forecast_df = _normalize_forecast(
        raw,
        model_col=MODEL_NAME,
        lo_col=lo_col,
        hi_col=hi_col,
    )

    return {
        "model": MODEL_NAME,
        "forecast": forecast_df,
        "season_length": season_length,
    }


def get_ets_model_name(granularity: str) -> str:
    """Retorna el identificador del modelo ETS para la granularidad dada."""
    return MODEL_NAME
