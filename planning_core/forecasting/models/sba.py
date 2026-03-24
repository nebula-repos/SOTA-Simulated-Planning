"""Modelos para demanda intermitente: CrostonSBA y ADIDA.

CrostonSBA (Syntetos-Boylan Approximation)
    Corrige el sesgo de Croston clasico. Es el modelo recomendado para
    demanda intermitente (ADI > 1.32, CV2 cualquiera) y lumpy (ambos altos).
    No genera intervalos de confianza analiticos.

ADIDA (Aggregate-Disaggregate Intermittent Demand Approach)
    Alternativa robusta para demanda muy esporadica. Agrega la serie
    a granularidad mayor, aplica un modelo simple, y desagrega al periodo
    original. Util cuando CrostonSBA falla por demasiados ceros consecutivos.

Uso tipico
----------
>>> result = fit_predict_sba(demand_df, granularity="M", h=3, unique_id="SKU-001")
>>> result["model"]     # "CrostonSBA"
>>> result["forecast"]  # pd.DataFrame con [ds, yhat, yhat_lo80, yhat_hi80]
"""

from __future__ import annotations

import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import ADIDA, CrostonSBA

from planning_core.forecasting.utils import (
    _normalize_forecast,
    get_season_length,
    to_nixtla_df,
)

MODEL_SBA = "CrostonSBA"
MODEL_ADIDA = "ADIDA"

# Minimo de observaciones no nulas para CrostonSBA
_MIN_NONZERO_OBS = 3


def fit_predict_sba(
    demand_df: pd.DataFrame,
    granularity: str = "M",
    h: int = 3,
    unique_id: str = "SKU",
    target_col: str = "demand",
) -> dict:
    """Ajusta CrostonSBA y genera h periodos de forecast.

    Si la serie tiene menos de ``_MIN_NONZERO_OBS`` periodos con demanda > 0,
    se usa ADIDA como fallback (mas robusto para series muy esporadicas).

    CrostonSBA no genera intervalos de confianza analiticos: los limites
    inferior y superior se fijan iguales al punto central.

    Parameters
    ----------
    demand_df : pd.DataFrame
        Serie de demanda con columnas ``[period, demand]``.
        Se espera que tenga muchos ceros (demanda intermitente/lumpy).
    granularity : str
        Granularidad: ``"D"``, ``"W"`` o ``"M"``.
    h : int
        Horizonte de pronostico en periodos.
    unique_id : str
        Identificador del SKU.
    target_col : str
        Columna de la variable objetivo.

    Returns
    -------
    dict
        ``{"model": str, "forecast": pd.DataFrame, "season_length": int}``

        ``forecast`` tiene columnas ``[ds, yhat, yhat_lo80, yhat_hi80]``.
        Para SBA/ADIDA ``yhat_lo80 == yhat_hi80 == yhat`` (sin IC analitico).
    """
    season_length = get_season_length(granularity)
    nixtla_df = to_nixtla_df(demand_df, unique_id=unique_id, target_col=target_col)

    n_nonzero = int((nixtla_df["y"] > 0).sum())
    use_adida = n_nonzero < _MIN_NONZERO_OBS

    if use_adida:
        model = ADIDA()
        model_name = MODEL_ADIDA
    else:
        model = CrostonSBA()
        model_name = MODEL_SBA

    sf = StatsForecast(models=[model], freq=_get_freq(granularity), n_jobs=1)
    sf.fit(nixtla_df)
    raw = sf.forecast(df=nixtla_df, h=h)

    # CrostonSBA/ADIDA no tienen intervalos — lo/hi = yhat
    forecast_df = _normalize_forecast(
        raw,
        model_col=model_name,
        lo_col=None,
        hi_col=None,
    )

    return {
        "model": model_name,
        "forecast": forecast_df,
        "season_length": season_length,
    }


def fit_predict_adida(
    demand_df: pd.DataFrame,
    granularity: str = "M",
    h: int = 3,
    unique_id: str = "SKU",
    target_col: str = "demand",
) -> dict:
    """Ajusta ADIDA directamente (sin fallback a SBA).

    Util cuando se sabe de antemano que la serie es muy esporadica
    (ej: lifecycle = declining o inactive con historia muy corta).

    Returns
    -------
    dict
        Mismo formato que ``fit_predict_sba``.
    """
    season_length = get_season_length(granularity)
    nixtla_df = to_nixtla_df(demand_df, unique_id=unique_id, target_col=target_col)

    model = ADIDA()
    sf = StatsForecast(models=[model], freq=_get_freq(granularity), n_jobs=1)
    sf.fit(nixtla_df)
    raw = sf.forecast(df=nixtla_df, h=h)

    forecast_df = _normalize_forecast(
        raw,
        model_col=MODEL_ADIDA,
        lo_col=None,
        hi_col=None,
    )

    return {
        "model": MODEL_ADIDA,
        "forecast": forecast_df,
        "season_length": season_length,
    }


def _get_freq(granularity: str) -> str:
    _map = {"D": "D", "W": "W-MON", "M": "MS"}
    return _map.get(granularity, "MS")
