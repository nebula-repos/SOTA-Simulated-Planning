"""Cálculo de stock de seguridad (Safety Stock) y punto de reorden (ROP).

Implementa las fórmulas del documento de referencia "Gestión de Inventario
Orientada a Decisiones" (Marzo 2026):

- Sección 4.3 — Fórmula extendida (demanda y lead time variables):
  ``SS = z × √((LT + R) × σ_d² + d̄² × σ_LT²)``
  Usada para clase A. Aprovecha ``sigma_lt_days`` calculado desde el
  historial real de purchase_orders / purchase_receipts.

- Sección 4.3 — Fórmula estándar (demanda variable, lead time fijo):
  ``SS = z × σ_d × √(LT + R)``
  Usada para clase B. Ignora la variabilidad de lead time en el término
  de seguridad, aunque ``sigma_lt_days`` siga estando disponible en params.

- Sección 2.3 — Regla simple (clase C):
  ``SS = pct × d̄ × LT``
  Sin factor z — apropiado para SKUs de bajo valor donde la complejidad
  estadística no se justifica.

Unidad base
-----------
Todos los cálculos se realizan en **unidades diarias** para consistencia con
``lead_time_days`` y ``sigma_lt_days``, que ya están expresados en días.

  d_daily   = mean(demand_series) / days_per_period
  σ_d_daily = std(demand_series)  / √days_per_period   (suma de var. i.i.d.)

La ventana de exposición incluye el período de revisión (sección 4.4):

  exposure_days = lead_time_days + review_period_days

Funciones públicas
------------------
compute_demand_stats(demand_series, granularity)
    → (mean_daily, sigma_daily, n_periods)
compute_safety_stock(params, mean_daily, sigma_daily, simple_safety_pct)
    → float (SS en unidades)
compute_rop(mean_daily, lead_time_days, safety_stock)
    → float (ROP en unidades)
compute_sku_safety_stock(params, demand_series, granularity, simple_safety_pct)
    → SafetyStockResult
"""

from __future__ import annotations

import math
import warnings
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from planning_core.inventory.params import InventoryParams


# ---------------------------------------------------------------------------
# Conversión de granularidad a días
# ---------------------------------------------------------------------------

_DAYS_PER_PERIOD: dict[str, float] = {
    "D": 1.0,
    "W": 7.0,
    "M": 365.25 / 12,  # ~30.4375
}


# ---------------------------------------------------------------------------
# Dataclass de resultado
# ---------------------------------------------------------------------------

@dataclass
class SafetyStockResult:
    """Resultado completo del cálculo de safety stock para un SKU.

    Attributes
    ----------
    sku : str
        Identificador del SKU.
    granularity : str
        Granularidad de la serie de demanda usada (``"D"``, ``"W"``, ``"M"``).
    mean_demand_daily : float
        Demanda media diaria en unidades.
    sigma_demand_daily : float
        Desviación estándar de la demanda diaria en unidades.
    safety_stock : float
        Stock de seguridad en unidades.
    reorder_point : float
        Punto de reorden en unidades. ROP = d̄_daily × lead_time_days + SS.
    coverage_ss_days : float
        Días de demanda cubiertos por el SS = SS / d̄_daily.
        0 si la demanda media es 0.
    ss_method : str
        Método de cálculo usado: ``"extended"``, ``"standard"``
        o ``"simple_pct_lt"``.
    n_periods : int
        Número de períodos de la serie de demanda usados para estimar σ_d.
    """

    sku: str
    granularity: str
    mean_demand_daily: float
    sigma_demand_daily: float
    safety_stock: float
    reorder_point: float
    coverage_ss_days: float
    ss_method: str
    n_periods: int

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# compute_demand_stats
# ---------------------------------------------------------------------------

def compute_demand_stats(
    demand_series: pd.DataFrame,
    granularity: str = "M",
) -> tuple[float, float, int]:
    """Calcula estadísticas de demanda en unidades diarias.

    Parameters
    ----------
    demand_series : pd.DataFrame
        DataFrame con columna ``demand`` (unidades por período).
        Debe ser la salida de ``prepare_demand_series`` o ``sku_demand_series``.
    granularity : str
        Granularidad temporal: ``"D"``, ``"W"`` o ``"M"``.

    Returns
    -------
    tuple[float, float, int]
        ``(mean_demand_daily, sigma_demand_daily, n_periods)``.
        Si la serie tiene menos de 3 períodos o está vacía, emite
        ``warnings.warn`` y retorna ``(0.0, 0.0, 0)``.

    Notes
    -----
    La conversión a diario asume periodos independientes e idénticamente
    distribuidos (i.i.d.), lo que implica:

    - ``mean_daily   = mean_period / days_per_period``
    - ``var_daily    = var_period  / days_per_period``   →  ``σ_daily = σ_period / √days``
    """
    days = _DAYS_PER_PERIOD.get(granularity, _DAYS_PER_PERIOD["M"])

    if demand_series.empty or "demand" not in demand_series.columns:
        warnings.warn(
            "compute_demand_stats: serie de demanda vacía — retornando ceros.",
            stacklevel=2,
        )
        return 0.0, 0.0, 0

    demand = demand_series["demand"].dropna().values.astype(float)
    n = len(demand)

    if n < 3:
        warnings.warn(
            f"compute_demand_stats: solo {n} período(s) — σ_d no confiable. "
            "Se retornan ceros para evitar SS espurios.",
            stacklevel=2,
        )
        return 0.0, 0.0, n

    mean_period = float(np.mean(demand))
    # ddof=1 — estimador insesgado para muestras finitas
    std_period = float(np.std(demand, ddof=1)) if n > 1 else 0.0

    mean_daily = mean_period / days
    sigma_daily = std_period / math.sqrt(days)

    return mean_daily, sigma_daily, n


# ---------------------------------------------------------------------------
# compute_safety_stock
# ---------------------------------------------------------------------------

def compute_safety_stock(
    params: InventoryParams,
    mean_demand_daily: float,
    sigma_demand_daily: float,
    simple_safety_pct: float = 0.5,
) -> float:
    """Calcula el stock de seguridad según el método del segmento ABC.

    Parameters
    ----------
    params : InventoryParams
        Parámetros del SKU (lead_time_days, sigma_lt_days, review_period_days,
        z_factor, ss_method).
    mean_demand_daily : float
        Demanda media diaria en unidades.
    sigma_demand_daily : float
        Desviación estándar de la demanda diaria en unidades.
    simple_safety_pct : float
        Fracción de la demanda durante el lead time usada como SS para
        ``ss_method="simple_pct_lt"`` (clase C). Default 0.5.

    Returns
    -------
    float
        Safety stock en unidades. Siempre >= 0.

    Notes
    -----
    **"extended"** (clase A):

        exposure = lead_time_days + review_period_days
        SS = z × √(exposure × σ_d² + d̄² × σ_LT²)

    Cuando ``sigma_lt_days = 0``, se reduce a la fórmula clásica
    ``z × σ_d × √exposure``.

    **"standard"** (clase B):

        exposure = lead_time_days + review_period_days
        SS = z × σ_d × √exposure

    **"simple_pct_lt"** (clase C):

        SS = simple_safety_pct × d̄_daily × lead_time_days
    """
    if params.ss_method == "extended":
        exposure = params.lead_time_days + params.review_period_days
        var_demand_term = exposure * (sigma_demand_daily ** 2)
        var_lt_term = (mean_demand_daily ** 2) * (params.sigma_lt_days ** 2)
        ss = params.z_factor * math.sqrt(var_demand_term + var_lt_term)
    elif params.ss_method == "standard":
        exposure = params.lead_time_days + params.review_period_days
        ss = params.z_factor * sigma_demand_daily * math.sqrt(exposure)
    elif params.ss_method == "simple_pct_lt":
        ss = simple_safety_pct * mean_demand_daily * params.lead_time_days
    else:
        # Fallback: extended formula para métodos desconocidos
        warnings.warn(
            f"compute_safety_stock: ss_method desconocido '{params.ss_method}' — usando extended.",
            stacklevel=2,
        )
        exposure = params.lead_time_days + params.review_period_days
        var_demand_term = exposure * (sigma_demand_daily ** 2)
        var_lt_term = (mean_demand_daily ** 2) * (params.sigma_lt_days ** 2)
        ss = params.z_factor * math.sqrt(var_demand_term + var_lt_term)

    return max(0.0, ss)


# ---------------------------------------------------------------------------
# compute_rop
# ---------------------------------------------------------------------------

def compute_rop(
    mean_demand_daily: float,
    lead_time_days: float,
    safety_stock: float,
) -> float:
    """Calcula el punto de reorden (ROP).

    Parameters
    ----------
    mean_demand_daily : float
        Demanda media diaria en unidades.
    lead_time_days : float
        Lead time del proveedor en días.
    safety_stock : float
        Stock de seguridad en unidades.

    Returns
    -------
    float
        ROP = DDLT + SS = mean_demand_daily × lead_time_days + safety_stock.
        Siempre >= 0.
    """
    rop = mean_demand_daily * lead_time_days + safety_stock
    return max(0.0, rop)


# ---------------------------------------------------------------------------
# compute_sku_safety_stock
# ---------------------------------------------------------------------------

def compute_sku_safety_stock(
    params: InventoryParams,
    demand_series: pd.DataFrame,
    granularity: str = "M",
    simple_safety_pct: float = 0.5,
) -> SafetyStockResult:
    """Calcula safety stock y ROP completos para un SKU.

    Función de alto nivel que combina ``compute_demand_stats``,
    ``compute_safety_stock`` y ``compute_rop`` en un único resultado.

    Parameters
    ----------
    params : InventoryParams
        Parámetros del SKU (de ``get_sku_params``).
    demand_series : pd.DataFrame
        Serie de demanda con columna ``demand`` (salida de ``sku_demand_series``).
    granularity : str
        Granularidad de la serie: ``"D"``, ``"W"`` o ``"M"``.
    simple_safety_pct : float
        Fracción del LT demand usada como SS para clase C. Default 0.5.

    Returns
    -------
    SafetyStockResult
    """
    mean_daily, sigma_daily, n_periods = compute_demand_stats(demand_series, granularity)

    ss = compute_safety_stock(params, mean_daily, sigma_daily, simple_safety_pct)
    rop = compute_rop(mean_daily, params.lead_time_days, ss)

    if mean_daily > 0:
        coverage_ss_days = ss / mean_daily
    else:
        coverage_ss_days = 0.0

    return SafetyStockResult(
        sku=params.sku,
        granularity=granularity,
        mean_demand_daily=mean_daily,
        sigma_demand_daily=sigma_daily,
        safety_stock=ss,
        reorder_point=rop,
        coverage_ss_days=coverage_ss_days,
        ss_method=params.ss_method,
        n_periods=n_periods,
    )
