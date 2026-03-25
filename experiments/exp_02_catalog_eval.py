"""
Experimento 02 — Evaluación de forecast sobre el catálogo completo
==================================================================

Corre el horse-race de modelos para todos los SKUs (o una submuestra),
guarda los resultados en ``output/eval_runs/<run_id>/`` y muestra un
resumen por segmento.

Uso
---
    python3 -m experiments.exp_02_catalog_eval

Para comparar dos runs desde Python:

    from planning_core.forecasting.evaluation import run_store, comparator, aggregator

    # Listar runs disponibles
    print(run_store.list_runs())

    # Cargar un run
    result = run_store.load_run("20260325_143201_baseline")
    print(aggregator.compute_global_metrics(result.sku_results))
    print(aggregator.compute_segment_metrics(result.sku_results))

    # Comparar dos runs
    comp = comparator.compare_runs_by_segment(
        ["run_id_a", "run_id_b"], segment_col="sb_class"
    )
    print(comp)
"""

from __future__ import annotations

from planning_core.repository import CanonicalRepository
from planning_core.services import PlanningService
from planning_core.forecasting.evaluation import (
    EvalConfig,
    aggregator,
    run_catalog_evaluation,
    run_store,
)

# ---------------------------------------------------------------------------
# Parámetros del experimento — ajustar aquí
# ---------------------------------------------------------------------------

CONFIG = EvalConfig(
    granularity = "M",
    h           = 3,
    n_windows   = 3,
    use_lgbm    = False,   # True para incluir LightGBM (~3x más lento)
    run_name    = "baseline",
    sample_n    = None,    # None = catálogo completo; int = submuestra aleatoria
    random_seed = 42,
)

N_JOBS           = 8     # 1 = secuencial | -1 = todos los CPUs
CHECKPOINT_EVERY = 50    # guardar progreso cada N SKUs (modo secuencial)
RESUME           = False # True = continuar desde checkpoint existente

BASE_DIR = "output/eval_runs"

# ---------------------------------------------------------------------------

repo    = CanonicalRepository()
service = PlanningService(repo)

result = run_catalog_evaluation(
    service,
    CONFIG,
    verbose=True,
    n_jobs=N_JOBS,
    checkpoint_every=CHECKPOINT_EVERY,
    resume=RESUME,
)

# Persistir
run_dir = run_store.save_run(result, BASE_DIR)
print(f"\nResultados guardados en: {run_dir}")

# Resumen por segmento
print("\n=== MASE mediana por sb_class ===")
seg = aggregator.compute_segment_metrics(
    result.sku_results, segment_cols=["sb_class"]
)
sb_view = seg[seg["segment_col"] == "sb_class"][
    ["segment_value", "n_skus", "n_ok", "n_fallback", "mase_median", "mase_p90", "top_model", "top_model_pct"]
]
print(sb_view.to_string(index=False))

print("\n=== MASE mediana por abc_class ===")
abc_view = seg[seg["segment_col"] == "abc_class"][
    ["segment_value", "n_skus", "mase_median", "mase_p90", "top_model"]
]
print(abc_view.to_string(index=False))

print("\n=== Distribución de modelos ganadores ===")
print(aggregator.compute_model_selection_summary(result.sku_results).to_string(index=False))
