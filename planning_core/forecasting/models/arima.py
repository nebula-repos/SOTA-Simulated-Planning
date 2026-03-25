"""Modelo AutoARIMA via StatsForecast.

AutoARIMA identifica automaticamente los ordenes ARIMA/SARIMA optimos usando
criterios de informacion (AICc). Incluye diferenciacion ordinaria y estacional.

Equivalente a ``auto.arima()`` de Rob Hyndman en R.

Cuando usarlo
-------------
- Series smooth o erratic con tendencia lineal detectable
- Series con estructura de autocorrelacion (patrones AR/MA)
- Alternativa a AutoETS cuando la serie no es bien capturada por suavizado
  exponencial

Uso tipico
----------
>>> result = fit_predict_arima(demand_df, granularity="M", h=6)
>>> result["model"]    # "AutoARIMA"
>>> result["forecast"] # pd.DataFrame con [ds, yhat, yhat_lo80, yhat_hi80]
"""

from __future__ import annotations

import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import AutoARIMA

from planning_core.forecasting.utils import (
    FREQ_MAP,
    _normalize_forecast,
    get_season_length,
    to_nixtla_df,
)

MODEL_NAME = "AutoARIMA"

# Minimo de observaciones: 2 ciclos estacionales para identificar estructura SARIMA
_MIN_OBS_FACTOR = 2


def get_arima_model(season_length: int) -> AutoARIMA:
    """Retorna la instancia AutoARIMA configurada para el horse-race."""
    return AutoARIMA(season_length=season_length)


def fit_predict_arima(
    demand_df: pd.DataFrame,
    granularity: str = "M",
    h: int = 3,
    unique_id: str = "SKU",
    target_col: str = "demand",
    level: list[int] | None = None,
) -> dict:
    """Ajusta AutoARIMA y genera h periodos de forecast con intervalos de confianza.

    AutoARIMA busca los ordenes (p,d,q)(P,D,Q) que minimizan el AICc. Evalua
    diferenciacion ordinaria y estacional automaticamente. Produce intervalos
    de confianza analiticos a partir de la varianza del error.

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
        Niveles de confianza. Default [80, 95].

    Returns
    -------
    dict
        ``{"model": "AutoARIMA", "forecast": pd.DataFrame, "season_length": int}``

        ``forecast`` tiene columnas ``[ds, yhat, yhat_lo80, yhat_hi80]``.

    Raises
    ------
    ValueError
        Si la serie tiene menos de ``2 * season_length`` observaciones.
    """
    if level is None:
        level = [80, 95]

    season_length = get_season_length(granularity)
    nixtla_df = to_nixtla_df(demand_df, unique_id=unique_id, target_col=target_col)

    n_obs = len(nixtla_df)
    min_obs = _MIN_OBS_FACTOR * season_length
    if n_obs < min_obs:
        raise ValueError(
            f"AutoARIMA requiere al menos {min_obs} observaciones "
            f"para granularidad {granularity!r} (season_length={season_length}). "
            f"Serie tiene {n_obs} obs."
        )

    freq = FREQ_MAP.get(granularity, "MS")
    sf = StatsForecast(models=[AutoARIMA(season_length=season_length)], freq=freq, n_jobs=1)
    sf.fit(nixtla_df)
    raw = sf.forecast(df=nixtla_df, h=h, level=level)

    lo_col = f"{MODEL_NAME}-lo-80" if 80 in level else None
    hi_col = f"{MODEL_NAME}-hi-80" if 80 in level else None

    forecast_df = _normalize_forecast(raw, model_col=MODEL_NAME, lo_col=lo_col, hi_col=hi_col)

    return {
        "model": MODEL_NAME,
        "forecast": forecast_df,
        "season_length": season_length,
    }
