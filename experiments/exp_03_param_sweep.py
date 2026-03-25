"""
Experimento 03 — Barrido de parametrización (h × n_windows)
=============================================================

Corre ``run_catalog_evaluation()`` para cada combinación viable de
``(h, n_windows)`` y compara los MASE por segmento de producto para
determinar qué parámetros funcionan mejor para cada tipo de demanda.

Grid evaluado (restricción: min_obs = 12 + h × n_windows ≤ 36 meses)
----------------------------------------------------------------------
  h=3: n_windows ∈ {3, 4, 5, 6}   →  min_obs: 21 / 24 / 27 / 30
  h=6: n_windows ∈ {3, 4}         →  min_obs: 30 / 36

Uso
---
    python3 -m experiments.exp_03_param_sweep

    # Re-correr todo desde cero (borrar runs previos del sweep o setear):
    SKIP_EXISTING = False

Salida
------
  1. Tabla global: mase_median / mase_p75 / mase_p90 / fallback_rate por config
  2. Pivot MASE mediana por sb_class × config
  3. Pivot MASE mediana por is_seasonal × config
  4. Pivot MASE mediana por abc_class × config
  5. % SKUs que cambiaron modelo ganador respecto al baseline (h=3, w=3)

Ver plan completo: docs/forecasting_param_sweep_plan.md
"""

from __future__ import annotations

import textwrap

import pandas as pd

from planning_core.forecasting.evaluation import (
    EvalConfig,
    aggregator,
    comparator,
    run_catalog_evaluation,
    run_store,
)
from planning_core.repository import CanonicalRepository
from planning_core.services import PlanningService

# ---------------------------------------------------------------------------
# Parámetros del sweep — ajustar aquí
# ---------------------------------------------------------------------------

PARAM_GRID = [
    dict(h=3, n_windows=3),   # baseline trimestral
    dict(h=3, n_windows=4),
    dict(h=3, n_windows=5),
    dict(h=3, n_windows=6),
    dict(h=6, n_windows=3),   # semestral
    dict(h=6, n_windows=4),   # semestral, límite exacto de datos
]

SKIP_EXISTING = True   # False = re-corre aunque ya exista el run
N_JOBS        = 32     # paralelismo por config (-1 = todos los CPUs)
USE_LGBM      = False  # True incluye LightGBM (~3x más lento por config)
BASE_DIR      = "output/eval_runs"
GRANULARITY   = "M"

# ---------------------------------------------------------------------------


def _run_name(h: int, n_windows: int) -> str:
    return f"sweep_h{h}_w{n_windows}"


def _find_existing_run_id(name: str) -> str | None:
    """Retorna el run_id más reciente que tenga run_name == name, o None."""
    runs = run_store.list_runs(BASE_DIR)
    if runs.empty or "run_name" not in runs.columns:
        return None
    matches = runs[runs["run_name"] == name]
    if matches.empty:
        return None
    return str(matches.iloc[0]["run_id"])


def _header(text: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Setup del servicio
# ---------------------------------------------------------------------------

repo    = CanonicalRepository()
service = PlanningService(repo)

# ---------------------------------------------------------------------------
# Fase 1: correr todas las configs del grid
# ---------------------------------------------------------------------------

_header("FASE 1 — Corriendo configs del sweep")

sweep_run_ids: list[str] = []

for params in PARAM_GRID:
    h          = params["h"]
    n_windows  = params["n_windows"]
    name       = _run_name(h, n_windows)

    existing_id = _find_existing_run_id(name)
    if SKIP_EXISTING and existing_id:
        print(f"  [skip]  {name}  →  run_id={existing_id}")
        sweep_run_ids.append(existing_id)
        continue

    print(f"\n  [run]   {name}  (h={h}, n_windows={n_windows})")
    config = EvalConfig(
        granularity = GRANULARITY,
        h           = h,
        n_windows   = n_windows,
        use_lgbm    = USE_LGBM,
        run_name    = name,
        sample_n    = None,
        random_seed = 42,
    )
    result = run_catalog_evaluation(
        service,
        config,
        verbose=True,
        n_jobs=N_JOBS,
    )
    run_dir = run_store.save_run(result, BASE_DIR)
    print(f"  Guardado en: {run_dir}   (MASE mediana={result.mase_global_median:.4f})")
    sweep_run_ids.append(result.run_id)

# Etiquetas legibles para tablas: h3_w3, h6_w4, etc.
label_map: dict[str, str] = {}
for rid, params in zip(sweep_run_ids, PARAM_GRID):
    label_map[rid] = f"h{params['h']}_w{params['n_windows']}"

# ---------------------------------------------------------------------------
# Fase 2: análisis comparativo
# ---------------------------------------------------------------------------

_header("FASE 2 — Comparación global")

global_df = comparator.compare_runs(sweep_run_ids, base_dir=BASE_DIR)
global_df["config"]        = global_df["run_id"].map(label_map)
global_df["fallback_rate"] = (global_df["n_fallback"] / global_df["n_skus"]).round(4)

display_cols = ["config", "h", "n_windows", "n_skus", "n_ok", "n_fallback",
                "fallback_rate", "mase_median", "mase_mean", "mase_p75", "mase_p90",
                "elapsed_s"]
display_cols = [c for c in display_cols if c in global_df.columns]
print(global_df[display_cols].sort_values(["h", "n_windows"]).to_string(index=False))

# ---------------------------------------------------------------------------
# Fase 3: pivots MASE por segmento
# ---------------------------------------------------------------------------

SEGMENTS = [
    ("sb_class",    "PIVOT — MASE mediana por sb_class × config"),
    ("is_seasonal", "PIVOT — MASE mediana por is_seasonal × config"),
    ("abc_class",   "PIVOT — MASE mediana por abc_class × config"),
]

for seg_col, title in SEGMENTS:
    _header(title)
    pivot = comparator.compare_runs_by_segment(
        sweep_run_ids,
        segment_col=seg_col,
        base_dir=BASE_DIR,
        metric="mase",
        agg="median",
    )
    if pivot.empty:
        print(f"  (sin datos para segmento '{seg_col}')")
        continue

    # Renombrar columnas de run_id/run_name a etiquetas cortas
    pivot.columns = [label_map.get(c, c) for c in pivot.columns]

    # Marcar la columna con menor MASE por fila
    pivot = pivot.sort_index()
    print(pivot.round(4).to_string())

    # Resumen: mejor config por segmento
    print(f"\n  Mejor config por {seg_col}:")
    for idx in pivot.index:
        row = pivot.loc[idx].dropna()
        if row.empty:
            continue
        best_col = row.idxmin()
        print(f"    {idx:<18} → {best_col}  (MASE={row[best_col]:.4f})")

# ---------------------------------------------------------------------------
# Fase 4: fallback por segmento × config
# ---------------------------------------------------------------------------

_header("FALLBACK — Tasa de fallback por sb_class × config")

fallback_rows = []
for rid in sweep_run_ids:
    result     = run_store.load_run(rid, BASE_DIR)
    label      = label_map.get(rid, rid)
    skus       = result.sku_results
    if "sb_class" not in skus.columns:
        continue
    for sb, grp in skus.groupby("sb_class", dropna=False):
        n_total    = len(grp)
        n_fallback = int((grp["status"] == "fallback").sum())
        n_nofc     = int((grp["status"] == "no_forecast").sum())
        fallback_rows.append({
            "config":   label,
            "sb_class": sb,
            "n_total":  n_total,
            "n_fallback": n_fallback,
            "fb_rate":  round(n_fallback / max(n_total - n_nofc, 1), 4),
        })

if fallback_rows:
    fb_df  = pd.DataFrame(fallback_rows)
    fb_piv = fb_df.pivot_table(index="sb_class", columns="config",
                                values="fb_rate", aggfunc="first")
    # ordenar columnas por h, n_windows
    ordered_cols = [label_map[rid] for rid in sweep_run_ids if label_map[rid] in fb_piv.columns]
    fb_piv = fb_piv[[c for c in ordered_cols if c in fb_piv.columns]]
    print(fb_piv.round(4).to_string())

# ---------------------------------------------------------------------------
# Fase 5: estabilidad del modelo ganador vs. baseline
# ---------------------------------------------------------------------------

_header("ESTABILIDAD — % SKUs que cambiaron modelo ganador vs. baseline (h3_w3)")

baseline_id = sweep_run_ids[0]   # primer elemento = h=3, w=3
for rid in sweep_run_ids[1:]:
    label   = label_map.get(rid, rid)
    changes = comparator.find_winner_changes(baseline_id, rid, base_dir=BASE_DIR)
    total_comparable = len(run_store.load_run(rid, BASE_DIR).sku_results[
        run_store.load_run(rid, BASE_DIR).sku_results["status"].isin(["ok", "fallback"])
    ])
    n_changes = len(changes)
    pct       = round(n_changes / max(total_comparable, 1) * 100, 1)
    print(f"  h3_w3 → {label:<10}  {n_changes:>4} cambios / {total_comparable} SKUs  ({pct}%)")
    if not changes.empty and len(changes) <= 30:
        top_changes = changes.groupby(["model_a", "model_b"]).size().reset_index(name="n")
        top_changes = top_changes.sort_values("n", ascending=False).head(5)
        for _, row in top_changes.iterrows():
            print(f"      {row['model_a']} → {row['model_b']}: {row['n']} SKUs")

_header("FIN del sweep")
print(textwrap.dedent(f"""
  Runs guardados en: {BASE_DIR}/
  Para análisis adicional en notebook:

      from planning_core.forecasting.evaluation import comparator, run_store
      runs = run_store.list_runs("{BASE_DIR}")
      sweep = runs[runs["run_name"].str.startswith("sweep_")]
      pivot = comparator.compare_runs_by_segment(
          sweep["run_id"].tolist(), segment_col="sb_class"
      )
"""))
