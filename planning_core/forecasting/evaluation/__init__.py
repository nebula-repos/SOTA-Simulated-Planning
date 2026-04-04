"""Módulo de evaluación de forecast a nivel de catálogo.

API pública
-----------
    from planning_core.forecasting.evaluation import (
        EvalConfig,
        CatalogEvalResult,
        run_catalog_evaluation,
        aggregator,
        comparator,
        run_store,
    )

Desacoplamiento
---------------
- ``aggregator``, ``comparator``, ``run_store``: puro pandas, sin dependencias
  de planning_core. Portables de forma independiente.
- ``catalog_runner``: importa ``select_and_forecast`` directamente (no
  ``PlanningService``). El worker es una función pura.
- ``_types``: dataclasses sin dependencias externas.
"""

from planning_core.forecasting.evaluation._types import CatalogEvalResult, EvalConfig
from planning_core.forecasting.evaluation.catalog_runner import run_catalog_evaluation
from planning_core.forecasting.evaluation.forecast_store import (
    ForecastStore,
    ForecastStoreEntry,
    build_store_entries,
    DEFAULT_MAX_AGE_DAYS,
)
from planning_core.forecasting.evaluation import aggregator, comparator, run_store

__all__ = [
    "EvalConfig",
    "CatalogEvalResult",
    "run_catalog_evaluation",
    "ForecastStore",
    "ForecastStoreEntry",
    "build_store_entries",
    "DEFAULT_MAX_AGE_DAYS",
    "aggregator",
    "comparator",
    "run_store",
]
