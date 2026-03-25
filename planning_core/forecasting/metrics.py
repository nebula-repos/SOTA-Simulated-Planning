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
    naive_type: str = "seasonal",
) -> float:
    """Mean Absolute Scaled Error.

    El denominador es el MAE de un modelo naive de referencia calculado sobre
    el set de entrenamiento. El tipo de naive se controla con ``naive_type``:

    - ``"seasonal"`` (default): naive estacional lag-m. Correcto para SKUs con
      estacionalidad detectada (``is_seasonal=True``).
      Requiere ``len(train) > season_length``.
    - ``"lag1"``: naive lag-1 (random walk). Correcto para SKUs smooth/erratic
      **sin** estacionalidad — benchmark más exigente que lag-12 en series planas.
      Requiere ``len(train) > 1``.
    - ``"mean"``: desviación absoluta respecto a la media histórica. Correcto para
      SKUs intermittent/lumpy donde lag-1 y lag-12 devuelven frecuentemente 0.
      Requiere ``len(train) >= 1``.

    MASE < 1.0  → el modelo supera al baseline.
    MASE = 1.0  → igual al baseline.
    MASE > 1.0  → peor que el baseline.

    Parameters
    ----------
    actual : array-like
        Valores reales del periodo de evaluacion (h periodos).
    forecast : array-like
        Valores pronosticados para los mismos h periodos.
    season_length : int
        Longitud estacional — solo usada cuando ``naive_type="seasonal"``.
    train_actual : array-like, optional
        Serie de entrenamiento para calcular el denominador. Si None, se usa
        ``actual`` como proxy.
    naive_type : str
        Tipo de naive de referencia: ``"seasonal"``, ``"lag1"`` o ``"mean"``.
    """
    actual_arr = _to_array(actual)
    forecast_arr = _to_array(forecast)
    _check_lengths(actual_arr, forecast_arr)

    mae_model = float(np.mean(np.abs(actual_arr - forecast_arr)))

    base = _to_array(train_actual) if train_actual is not None else actual_arr

    if naive_type == "seasonal":
        if len(base) <= season_length:
            return float("nan")
        naive_errors = np.abs(base[season_length:] - base[:-season_length])
    elif naive_type == "lag1":
        if len(base) <= 1:
            return float("nan")
        naive_errors = np.abs(base[1:] - base[:-1])
    elif naive_type == "mean":
        if len(base) == 0:
            return float("nan")
        naive_errors = np.abs(base - base.mean())
    else:
        raise ValueError(
            f"naive_type desconocido: {naive_type!r}. Usar 'seasonal', 'lag1' o 'mean'."
        )

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
    naive_type: str = "seasonal",
) -> dict[str, float]:
    """Calcula todas las metricas de una vez.

    Parameters
    ----------
    naive_type : str
        Tipo de naive de referencia para MASE: ``"seasonal"``, ``"lag1"`` o ``"mean"``.
        Ver ``compute_mase`` para detalle.

    Returns
    -------
    dict
        Claves: ``mase``, ``wape``, ``bias``, ``mae``, ``rmse``.
        Los valores pueden ser ``float("nan")`` si la metrica no es calculable.
    """
    return {
        "mase": compute_mase(
            actual, forecast,
            season_length=season_length,
            train_actual=train_actual,
            naive_type=naive_type,
        ),
        "wape": compute_wape(actual, forecast),
        "bias": compute_bias(actual, forecast),
        "mae": compute_mae(actual, forecast),
        "rmse": compute_rmse(actual, forecast),
    }
