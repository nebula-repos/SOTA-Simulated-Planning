"""Modelo MSTL (Multiple Seasonal-Trend decomposition using LOESS) via StatsForecast.

MSTL descompone la serie en tendencia + componente estacional via STL (Seasonal
and Trend decomposition using LOESS), luego aplica AutoETS sobre el componente
de tendencia para generar el forecast. La estacionalidad se reconstruye sumando
el componente estacional ajustado.

Cuando usarlo
-------------
- Series smooth con estacionalidad anual fuerte y consistente
- Series donde AutoETS elige un modelo no-estacional (ETS A,N,N) pese a que
  hay patron estacional visible
- Alternativa a AutoARIMA para series con tendencia no lineal

Cuando NO usarlo
----------------
- Series intermittent o lumpy (la descomposicion STL requiere señal continua)
- Series con menos de 2 * season_length observaciones

Uso tipico
----------
>>> result = fit_predict_mstl(demand_df, granularity="M", h=6)
>>> result["model"]    # "MSTL"
>>> result["forecast"] # pd.DataFrame con [ds, yhat, yhat_lo80, yhat_hi80]
"""

from __future__ import annotations

import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import AutoETS, MSTL

from planning_core.forecasting.utils import (
    FREQ_MAP,
    _normalize_forecast,
    get_season_length,
    to_nixtla_df,
)

MODEL_NAME = "MSTL"

# Minimo de observaciones: 2 ciclos estacionales para STL
_MIN_OBS_FACTOR = 2


def get_mstl_model(season_length: int) -> MSTL:
    """Retorna la instancia MSTL configurada para el horse-race.

    Usa AutoETS como forecaster de la componente de tendencia, que selecciona
    automaticamente el mejor modelo ETS para la parte no estacional.
    """
    return MSTL(season_length=season_length, trend_forecaster=AutoETS(model="ZZN"))


def fit_predict_mstl(
    demand_df: pd.DataFrame,
    granularity: str = "M",
    h: int = 3,
    unique_id: str = "SKU",
    target_col: str = "demand",
    level: list[int] | None = None,
) -> dict:
    """Ajusta MSTL y genera h periodos de forecast con intervalos de confianza.

    Descompone la serie con STL iterativo y aplica AutoETS sobre la tendencia.
    Los intervalos de confianza se construyen a partir de la distribucion del
    error de la componente de tendencia (via AutoETS).

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
        Niveles de confianza. Default [80].

    Returns
    -------
    dict
        ``{"model": "MSTL", "forecast": pd.DataFrame, "season_length": int}``

        ``forecast`` tiene columnas ``[ds, yhat, yhat_lo80, yhat_hi80]``.
        Si los intervalos no estan disponibles, ``yhat_lo80 = yhat_hi80 = yhat``.

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
            f"MSTL requiere al menos {min_obs} observaciones "
            f"para granularidad {granularity!r} (season_length={season_length}). "
            f"Serie tiene {n_obs} obs."
        )

    freq = FREQ_MAP.get(granularity, "MS")
    mstl_model = MSTL(season_length=season_length, trend_forecaster=AutoETS(model="ZZN"))
    sf = StatsForecast(models=[mstl_model], freq=freq, n_jobs=1)
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
