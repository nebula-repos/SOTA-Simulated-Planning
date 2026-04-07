"""Package de clasificación de demanda.

Re-exporta la interfaz pública de ``core`` y ``store`` para que todos los
imports existentes ``from planning_core.classification import X`` sigan
funcionando sin cambios.
"""

from __future__ import annotations

from planning_core.classification.core import (
    classify_all_skus,
    classify_sku,
    classify_lifecycle,
    classify_syntetos_boylan,
    compute_abc_segmentation,
    compute_adi_cv2,
    compute_acf,
    compute_quality_score,
    compute_xyz_class,
    detect_outliers,
    prepare_demand_series,
    select_granularity,
    test_seasonality,
    test_trend,
    treat_outliers,
)
from planning_core.classification.store import ClassificationStore, DEFAULT_MAX_AGE_DAYS

__all__ = [
    # core
    "classify_all_skus",
    "classify_sku",
    "classify_lifecycle",
    "classify_syntetos_boylan",
    "compute_abc_segmentation",
    "compute_adi_cv2",
    "compute_acf",
    "compute_quality_score",
    "compute_xyz_class",
    "detect_outliers",
    "prepare_demand_series",
    "select_granularity",
    "test_seasonality",
    "test_trend",
    "treat_outliers",
    # store
    "ClassificationStore",
    "DEFAULT_MAX_AGE_DAYS",
]
