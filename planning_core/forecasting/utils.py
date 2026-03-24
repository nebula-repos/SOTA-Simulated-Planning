"""Utilidades compartidas para todos los modelos de forecasting."""

from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# Constantes de frecuencia y estacionalidad
# ---------------------------------------------------------------------------

FREQ_MAP: dict[str, str] = {
    "D": "D",
    "W": "W-MON",
    "M": "MS",
}

# Longitud de la estacionalidad primaria por granularidad
SEASON_LENGTH: dict[str, int] = {
    "D": 7,    # semanal para datos diarios
    "W": 52,   # anual para datos semanales
    "M": 12,   # anual para datos mensuales
}


def get_season_length(granularity: str) -> int:
    """Retorna la longitud de estacionalidad para la granularidad dada."""
    return SEASON_LENGTH.get(granularity, 12)


# ---------------------------------------------------------------------------
# Conversion a formato Nixtla
# ---------------------------------------------------------------------------

def to_nixtla_df(
    demand_df: pd.DataFrame,
    unique_id: str = "SKU",
    target_col: str = "demand",
) -> pd.DataFrame:
    """Convierte un DataFrame [period, demand] al formato Nixtla [unique_id, ds, y].

    Parameters
    ----------
    demand_df : pd.DataFrame
        DataFrame con columnas ``period`` (DatetimeLike) y ``target_col`` (numeric).
    unique_id : str
        Identificador de la serie (nombre del SKU).
    target_col : str
        Columna de la variable objetivo en ``demand_df``.

    Returns
    -------
    pd.DataFrame
        Columnas ``[unique_id, ds, y]`` sin valores nulos en ``y``.
    """
    if demand_df.empty:
        return pd.DataFrame(columns=["unique_id", "ds", "y"])

    nixtla_df = pd.DataFrame(
        {
            "unique_id": unique_id,
            "ds": pd.to_datetime(demand_df["period"]),
            "y": demand_df[target_col].astype(float).values,
        }
    )
    return nixtla_df.dropna(subset=["y"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Normalizacion de forecast
# ---------------------------------------------------------------------------

def _normalize_forecast(
    forecast_df: pd.DataFrame,
    model_col: str,
    lo_col: str | None = None,
    hi_col: str | None = None,
    clip_lower: float = 0.0,
) -> pd.DataFrame:
    """Normaliza un DataFrame de forecast de StatsForecast al formato interno.

    Columns del DataFrame resultante:
        - ``ds``       : fecha del periodo pronosticado
        - ``yhat``     : pronostico puntual (clipeado a >= clip_lower)
        - ``yhat_lo80``: limite inferior IC 80 % (opcional)
        - ``yhat_hi80``: limite superior IC 80 % (opcional)

    Parameters
    ----------
    forecast_df : pd.DataFrame
        DataFrame de salida de ``sf.forecast()`` para un unico unique_id.
    model_col : str
        Nombre de la columna del modelo en el DataFrame (ej: "AutoETS").
    lo_col, hi_col : str, optional
        Nombres de columnas del intervalo de confianza inferior/superior.
    clip_lower : float
        Valor minimo para el pronostico puntual e intervalos. Default 0.0.
    """
    out = pd.DataFrame({"ds": forecast_df["ds"]})
    out["yhat"] = forecast_df[model_col].clip(lower=clip_lower).values

    if lo_col and lo_col in forecast_df.columns:
        out["yhat_lo80"] = forecast_df[lo_col].clip(lower=clip_lower).values
    else:
        out["yhat_lo80"] = out["yhat"]

    if hi_col and hi_col in forecast_df.columns:
        out["yhat_hi80"] = forecast_df[hi_col].clip(lower=clip_lower).values
    else:
        out["yhat_hi80"] = out["yhat"]

    return out.reset_index(drop=True)
