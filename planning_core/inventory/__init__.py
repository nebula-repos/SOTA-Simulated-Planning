"""Módulo de gestión de inventario.

Submodules
----------
params
    Datos maestros de inventario por SKU: lead time, review period, carrying cost.
service_level
    Política de nivel de servicio (CSL) por segmento ABC y factor z.
safety_stock
    Cálculo de safety stock (SS) y punto de reorden (ROP).
"""

from planning_core.inventory.params import (
    InventoryParams,
    compute_supplier_lead_times,
    get_sku_params,
)
from planning_core.inventory.service_level import (
    CSL_DEFAULTS,
    ServiceLevelConfig,
    csl_to_z,
    get_csl_target,
    get_service_level_config,
    get_z_factor,
)
from planning_core.inventory.safety_stock import (
    SafetyStockResult,
    compute_demand_stats,
    compute_safety_stock,
    compute_rop,
    compute_sku_safety_stock,
)

__all__ = [
    "InventoryParams",
    "compute_supplier_lead_times",
    "get_sku_params",
    "CSL_DEFAULTS",
    "ServiceLevelConfig",
    "csl_to_z",
    "get_csl_target",
    "get_service_level_config",
    "get_z_factor",
    "SafetyStockResult",
    "compute_demand_stats",
    "compute_safety_stock",
    "compute_rop",
    "compute_sku_safety_stock",
]
