"""Metricas de evaluacion de forecast.

Metricas implementadas
----------------------
- MASE  : Mean Absolute Scaled Error (metrica primaria — scale-free)
- WAPE  : Weighted Absolute Percentage Error
- Bias  : Sesgo relativo promedio (sobre / sub-estimacion sistematica)
- MAE   : Mean Absolute Error
- RMSE  : Root Mean Squared Error

Todas las funciones reciben arrays/Series de ``actual`` y ``forecast``
y retornan un float. ``compute_all_metrics`` retorna un dict con todas.

Referencias
-----------
- Hyndman & Koehler (2006). Another look at measures of forecast accuracy.
- Kolassa & Schütz (2007). Advantages of the MAD/Mean ratio over the MAPE.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _to_array(x: pd.Series | np.ndarray | list) -> np.ndarray:
    return np.asarray(x, dtype=float)


def _check_lengths(actual: np.ndarray, forecast: np.ndarray) -> None:
    if len(actual) != len(forecast):
        raise ValueError(
            f"actual y forecast deben tener el mismo largo: "
            f"{len(actual)} != {len(forecast)}"
        )


# ---------------------------------------------------------------------------
# Metricas individuales
# ---------------------------------------------------------------------------

def compute_mase(
    actual: pd.Series | np.ndarray,
    forecast: pd.Series | np.ndarray,
    season_length: int = 12,
    train_actual: pd.Series | np.ndarray | None = None,
) -> float:
    """Mean Absolute Scaled Error.

    El denominador es el MAE de SeasonalNaive sobre el set de entrenamiento.
    Si ``train_actual`` no se provee, se usa ``actual`` como proxy
    (util en backtest donde el historico esta disponible como ``actual``).

    MASE < 1.0  → el modelo supera al baseline SeasonalNaive.
    MASE = 1.0  → igual al baseline.
    MASE > 1.0  → peor que el baseline.

    Parameters
    ----------
    actual : array-like
        Valores reales del periodo de evaluacion (h periodos).
    forecast : array-like
        Valores pronosticados para los mismos h periodos.
    season_length : int
        m en la formula — normalmente 12 (mensual), 52 (semanal), 7 (diario).
    train_actual : array-like, optional
        Serie completa de entrenamiento. Se usa para calcular el denominador
        (MAE de naive estacional sobre entrenamiento). Si None, se usa ``actual``.
    """
    actual_arr = _to_array(actual)
    forecast_arr = _to_array(forecast)
    _check_lengths(actual_arr, forecast_arr)

    # Errores absolutos del modelo
    mae_model = np.mean(np.abs(actual_arr - forecast_arr))

    # Denominador: MAE naive estacional sobre entrenamiento
    base = _to_array(train_actual) if train_actual is not None else actual_arr
    if len(base) <= season_length:
        # No hay suficientes datos para naive estacional — usar MAE sobre la misma serie
        naive_errors = np.abs(np.diff(base))
        if len(naive_errors) == 0:
            return float("nan")
        mae_naive = float(np.mean(naive_errors))
    else:
        naive_errors = np.abs(base[season_length:] - base[:-season_length])
        mae_naive = float(np.mean(naive_errors))

    if mae_naive == 0:
        return float("nan")

    return float(mae_model / mae_naive)


def compute_wape(
    actual: pd.Series | np.ndarray,
    forecast: pd.Series | np.ndarray,
) -> float:
    """Weighted Absolute Percentage Error (aka MAD/Mean).

    WAPE = sum(|actual - forecast|) / sum(actual)

    Robusto a series con ceros (a diferencia del MAPE clasico).
    Interpretacion: fraccion del volumen total mal pronosticado.
    """
    actual_arr = _to_array(actual)
    forecast_arr = _to_array(forecast)
    _check_lengths(actual_arr, forecast_arr)

    total_actual = np.sum(actual_arr)
    if total_actual == 0:
        return float("nan")

    return float(np.sum(np.abs(actual_arr - forecast_arr)) / total_actual)


def compute_bias(
    actual: pd.Series | np.ndarray,
    forecast: pd.Series | np.ndarray,
) -> float:
    """Sesgo relativo promedio.

    Bias = mean(forecast - actual) / mean(actual)

    Positivo → sobre-estimacion sistematica.
    Negativo → sub-estimacion sistematica (mas peligroso para stockouts).
    """
    actual_arr = _to_array(actual)
    forecast_arr = _to_array(forecast)
    _check_lengths(actual_arr, forecast_arr)

    mean_actual = np.mean(actual_arr)
    if mean_actual == 0:
        return float("nan")

    return float(np.mean(forecast_arr - actual_arr) / mean_actual)


def compute_mae(
    actual: pd.Series | np.ndarray,
    forecast: pd.Series | np.ndarray,
) -> float:
    """Mean Absolute Error en unidades originales."""
    actual_arr = _to_array(actual)
    forecast_arr = _to_array(forecast)
    _check_lengths(actual_arr, forecast_arr)
    return float(np.mean(np.abs(actual_arr - forecast_arr)))


def compute_rmse(
    actual: pd.Series | np.ndarray,
    forecast: pd.Series | np.ndarray,
) -> float:
    """Root Mean Squared Error en unidades originales."""
    actual_arr = _to_array(actual)
    forecast_arr = _to_array(forecast)
    _check_lengths(actual_arr, forecast_arr)
    return float(np.sqrt(np.mean((actual_arr - forecast_arr) ** 2)))


# ---------------------------------------------------------------------------
# Calculo conjunto
# ---------------------------------------------------------------------------

def compute_all_metrics(
    actual: pd.Series | np.ndarray,
    forecast: pd.Series | np.ndarray,
    season_length: int = 12,
    train_actual: pd.Series | np.ndarray | None = None,
) -> dict[str, float]:
    """Calcula todas las metricas de una vez.

    Returns
    -------
    dict
        Claves: ``mase``, ``wape``, ``bias``, ``mae``, ``rmse``.
        Los valores pueden ser ``float("nan")`` si la metrica no es
        calculable (ej: denominador cero).
    """
    return {
        "mase": compute_mase(actual, forecast, season_length=season_length, train_actual=train_actual),
        "wape": compute_wape(actual, forecast),
        "bias": compute_bias(actual, forecast),
        "mae": compute_mae(actual, forecast),
        "rmse": compute_rmse(actual, forecast),
    }
