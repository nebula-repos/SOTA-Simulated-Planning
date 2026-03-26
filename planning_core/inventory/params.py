"""Datos maestros de inventario por SKU.

Calcula y expone los parámetros operativos que cada SKU necesita para el
cálculo de safety stock, ROP y diagnóstico de inventario:

- ``lead_time_days``    : lead time medio por proveedor (desde purchase history)
- ``sigma_lt_days``     : variabilidad del lead time (confiabilidad del proveedor)
- ``review_period_days``: frecuencia de revisión por clase ABC
- ``carrying_cost_rate``: tasa anual de costo de mantener inventario (global)

Fuente de lead time
-------------------
Se calcula desde ``purchase_receipts`` (receipt_date) unido con
``purchase_orders`` (order_date) por ``po_id``. Solo se consideran órdenes
con ``receipt_status = "received"`` para evitar lead times proyectados.
El resultado se agrupa por proveedor (nivel con más observaciones por grupo).

Prioridad de fuentes para cada parámetro
-----------------------------------------
1. Override explícito por SKU en manifest (``inventory_params.overrides.{sku}``)
2. Lead time calculado por proveedor desde purchase history
3. Default por clase ABC en manifest (``inventory_params.defaults_by_abc``)
4. Global default (``inventory_params.defaults``)

Defaults de manifest
--------------------
Agregar en ``dataset_manifest.json``::

    "inventory_params": {
      "defaults": {
        "lead_time_days": 30,
        "sigma_lt_days": 7,
        "review_period_days": 21,
        "carrying_cost_rate": 0.25
      },
      "defaults_by_abc": {
        "A": {"review_period_days": 14},
        "B": {"review_period_days": 21},
        "C": {"review_period_days": 30}
      }
    }
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from planning_core.inventory.service_level import get_service_level_config
from planning_core.repository import CanonicalRepository


# ---------------------------------------------------------------------------
# Defaults hardcoded (usados si el manifest no los declara)
# ---------------------------------------------------------------------------

_DEFAULT_LEAD_TIME_DAYS = 30.0
_DEFAULT_SIGMA_LT_DAYS = 7.0
_DEFAULT_REVIEW_PERIOD_DAYS = 21.0
_DEFAULT_CARRYING_COST_RATE = 0.25

_REVIEW_PERIOD_BY_ABC: dict[str, float] = {
    "A": 14.0,  # bisemanal — SKUs críticos, detección temprana de substock
    "B": 21.0,  # cada 3 semanas
    "C": 30.0,  # mensual — bajo movimiento, carga administrativa mínima
}


# ---------------------------------------------------------------------------
# Dataclass de resultado
# ---------------------------------------------------------------------------

@dataclass
class InventoryParams:
    """Parámetros operativos completos de inventario para un SKU.

    Agrupa tanto los parámetros logísticos (lead time, review period) como la
    política de servicio (CSL objetivo, factor z, método de SS), de modo que
    todo lo necesario para calcular safety stock y ROP esté en un solo objeto.

    Attributes
    ----------
    sku : str
        Identificador del SKU.
    lead_time_days : float
        Lead time medio del proveedor en días (desde purchase history).
    sigma_lt_days : float
        Desviación estándar del lead time — confiabilidad del proveedor.
        Usada en la fórmula extendida: SS = z × √(LT·σ_d² + d̄²·σ_LT²).
    review_period_days : float
        Frecuencia de revisión de inventario en días (por clase ABC).
    carrying_cost_rate : float
        Tasa anual de costo de mantener inventario (fracción del valor).
        Incluye costo financiero, almacenamiento, seguros y obsolescencia.
    abc_class : str or None
        Clase ABC del SKU (``"A"``, ``"B"``, ``"C"``). None si no clasificado.
    csl_target : float
        Cycle Service Level objetivo (0–1). Tabla 8.4 del PDF: A=98.5%, B=94.5%, C=88.5%.
    z_factor : float
        Factor z de la distribución normal correspondiente al CSL objetivo.
    ss_method : str
        Método de cálculo de safety stock recomendado para este segmento:
        ``"extended"`` (A) | ``"standard"`` (B) | ``"simple_pct_lt"`` (C).
    """

    sku: str
    lead_time_days: float
    sigma_lt_days: float
    review_period_days: float
    carrying_cost_rate: float
    abc_class: str | None
    csl_target: float
    z_factor: float
    ss_method: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Cálculo de lead times desde purchase history
# ---------------------------------------------------------------------------

def compute_supplier_lead_times(repository: CanonicalRepository) -> pd.DataFrame:
    """Calcula lead time medio y variabilidad por proveedor desde el historial de compras.

    Usa ``purchase_receipts.receipt_date - purchase_orders.order_date`` como
    medida del lead time real. Solo considera órdenes con
    ``receipt_status = "received"``.

    Parameters
    ----------
    repository : CanonicalRepository
        Repositorio canónico con acceso a purchase_orders y purchase_receipts.

    Returns
    -------
    pd.DataFrame
        Columnas: ``[supplier, lt_mean_days, lt_std_days, n_orders]``.
        Un fila por proveedor. Si hay menos de 2 recepciones para un proveedor,
        ``lt_std_days`` toma el valor de la mediana global de desviaciones.
    """
    orders = repository.load_table("purchase_orders")[["po_id", "supplier", "order_date"]]
    receipts = repository.load_table("purchase_receipts")
    receipts = receipts[receipts["receipt_status"] == "received"][["po_id", "receipt_date"]]

    merged = orders.merge(receipts, on="po_id", how="inner")
    merged["lt_days"] = (
        pd.to_datetime(merged["receipt_date"]) - pd.to_datetime(merged["order_date"])
    ).dt.days.astype(float)

    # Descartar lead times negativos o extremos (> 720 días)
    merged = merged[(merged["lt_days"] > 0) & (merged["lt_days"] <= 720)]

    grouped = merged.groupby("supplier")["lt_days"].agg(
        lt_mean_days="mean",
        lt_std_days="std",
        n_orders="count",
    ).reset_index()

    # Proveedores con solo 1 recepción no tienen std computable → NaN
    # Usar la mediana global de las desviaciones como fallback
    global_std_fallback = grouped["lt_std_days"].median()
    if pd.isna(global_std_fallback):
        global_std_fallback = _DEFAULT_SIGMA_LT_DAYS

    grouped["lt_std_days"] = grouped["lt_std_days"].fillna(global_std_fallback)

    return grouped.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Resolución de parámetros por SKU
# ---------------------------------------------------------------------------

def get_sku_params(
    sku: str,
    abc_class: str | None,
    supplier: str | None,
    repository: CanonicalRepository,
    manifest_config: dict | None = None,
) -> InventoryParams:
    """Retorna los parámetros de inventario para un SKU.

    Prioridad de fuentes (de mayor a menor):
    1. Override explícito por SKU en manifest (``inventory_params.overrides.{sku}``)
    2. Lead time calculado por proveedor desde purchase history
    3. Default por clase ABC en manifest (``inventory_params.defaults_by_abc``)
    4. Global default (``inventory_params.defaults`` o hardcoded)

    Parameters
    ----------
    sku : str
        Identificador del SKU.
    abc_class : str or None
        Clase ABC del SKU para resolver review_period_days.
    supplier : str or None
        Proveedor del SKU (columna ``supplier`` del product_catalog).
    repository : CanonicalRepository
        Repositorio canónico.
    manifest_config : dict or None
        Contenido completo del manifest (``repository.load_manifest()``).

    Returns
    -------
    InventoryParams
    """
    inv_cfg = {}
    if manifest_config:
        inv_cfg = manifest_config.get("inventory_params", {})

    global_defaults = inv_cfg.get("defaults", {})
    abc_defaults = inv_cfg.get("defaults_by_abc", {})
    sku_overrides = inv_cfg.get("overrides", {}).get(sku, {})

    # --- carrying_cost_rate: siempre global ---
    carrying_cost_rate = float(
        sku_overrides.get(
            "carrying_cost_rate",
            global_defaults.get("carrying_cost_rate", _DEFAULT_CARRYING_COST_RATE),
        )
    )

    # --- review_period_days: por ABC class, luego global default ---
    if "review_period_days" in sku_overrides:
        review_period_days = float(sku_overrides["review_period_days"])
    elif abc_class and abc_class in _REVIEW_PERIOD_BY_ABC:
        abc_override = abc_defaults.get(abc_class, {})
        review_period_days = float(
            abc_override.get(
                "review_period_days",
                _REVIEW_PERIOD_BY_ABC[abc_class],
            )
        )
    else:
        review_period_days = float(
            global_defaults.get("review_period_days", _DEFAULT_REVIEW_PERIOD_DAYS)
        )

    # --- lead_time_days y sigma_lt_days: override > purchase data > global default ---
    if "lead_time_days" in sku_overrides:
        lead_time_days = float(sku_overrides["lead_time_days"])
        sigma_lt_days = float(
            sku_overrides.get(
                "sigma_lt_days",
                global_defaults.get("sigma_lt_days", _DEFAULT_SIGMA_LT_DAYS),
            )
        )
    elif supplier is not None:
        lt_df = compute_supplier_lead_times(repository)
        row = lt_df[lt_df["supplier"] == supplier]
        if not row.empty:
            lead_time_days = float(row["lt_mean_days"].iloc[0])
            sigma_lt_days = float(row["lt_std_days"].iloc[0])
        else:
            lead_time_days = float(
                global_defaults.get("lead_time_days", _DEFAULT_LEAD_TIME_DAYS)
            )
            sigma_lt_days = float(
                global_defaults.get("sigma_lt_days", _DEFAULT_SIGMA_LT_DAYS)
            )
    else:
        lead_time_days = float(
            global_defaults.get("lead_time_days", _DEFAULT_LEAD_TIME_DAYS)
        )
        sigma_lt_days = float(
            global_defaults.get("sigma_lt_days", _DEFAULT_SIGMA_LT_DAYS)
        )

    sl_cfg = get_service_level_config(
        abc_class,
        manifest_config.get("service_level_policy") if manifest_config else None,
    )

    return InventoryParams(
        sku=sku,
        lead_time_days=lead_time_days,
        sigma_lt_days=sigma_lt_days,
        review_period_days=review_period_days,
        carrying_cost_rate=carrying_cost_rate,
        abc_class=abc_class,
        csl_target=sl_cfg.csl_target,
        z_factor=sl_cfg.z_factor,
        ss_method=sl_cfg.ss_method,
    )
