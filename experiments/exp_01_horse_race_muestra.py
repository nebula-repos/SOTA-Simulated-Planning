"""
Experimento 01 — Horse-race de modelos sobre muestra del catálogo
=================================================================

Objetivo
--------
Medir qué modelos ganan el horse-race (menor MASE) en una muestra
aleatoria de SKUs, agrupando resultados por clasificación SB.

Esto permite responder:
  - ¿AutoETS realmente supera a SeasonalNaive en series smooth?
  - ¿CrostonSBA gana consistentemente sobre ADIDA en intermittent?
  - ¿Qué fracción de series queda como "series_too_short"?

Uso
---
    python3 -m experiments.exp_01_horse_race_muestra

Ajustar N_SAMPLE y GRANULARITY según el experimento deseado.
"""

from __future__ import annotations

import random
import time

import pandas as pd

from planning_core.repository import CanonicalRepository
from planning_core.services import PlanningService
from planning_core.forecasting.backtest import backtest_summary

# ---------------------------------------------------------------------------
# Parámetros del experimento
# ---------------------------------------------------------------------------

N_SAMPLE = 30          # SKUs a evaluar (aumentar para más estadística)
GRANULARITY = "M"      # "M", "W" o "D"
H = 3                  # horizonte de forecast
N_WINDOWS = 3          # ventanas del backtest
RANDOM_SEED = 42
OUTPUT_CSV = "experiments/results_exp01.csv"

# ---------------------------------------------------------------------------

random.seed(RANDOM_SEED)

repo = CanonicalRepository()
service = PlanningService(repo)

catalog_df = service.classify_catalog(granularity=GRANULARITY)
skus_all = catalog_df["sku"].tolist()
skus_sample = random.sample(skus_all, min(N_SAMPLE, len(skus_all)))

print(f"Corriendo horse-race en {len(skus_sample)} SKUs (granularidad={GRANULARITY}, h={H})")
print("-" * 60)

rows = []
t0 = time.time()

for i, sku in enumerate(skus_sample, 1):
    sku_profile = catalog_df[catalog_df["sku"] == sku].iloc[0]
    sb_class = sku_profile.get("sb_class", "unknown")

    try:
        result = service.sku_forecast(
            sku,
            granularity=GRANULARITY,
            h=H,
            n_windows=N_WINDOWS,
        )
        row = {
            "sku": sku,
            "sb_class": sb_class,
            "abc_class": sku_profile.get("abc_class", "?"),
            "status": result["status"],
            "model_winner": result.get("model"),
            "mase": result.get("mase"),
        }
        # agregar MASE de cada candidato
        for model_name, metrics in result.get("backtest", {}).items():
            row[f"mase_{model_name}"] = metrics.get("mase")

    except Exception as exc:
        row = {"sku": sku, "sb_class": sb_class, "status": "error", "error": str(exc)}

    rows.append(row)
    elapsed = time.time() - t0
    print(f"  [{i:3d}/{len(skus_sample)}] {sku}  sb={sb_class:<13} winner={row.get('model_winner','?'):<15} MASE={row.get('mase', float('nan')):.3f}  ({elapsed:.1f}s)")

results_df = pd.DataFrame(rows)
results_df.to_csv(OUTPUT_CSV, index=False)
print()
print("=" * 60)
print(f"Resultados guardados en: {OUTPUT_CSV}")
print()

# Resumen por clasificación
ok = results_df[results_df["status"].isin(["ok", "fallback"])]
if not ok.empty:
    print("Ganadores por clasificación SB:")
    print(ok.groupby(["sb_class", "model_winner"]).size().rename("n_skus").reset_index().to_string(index=False))
    print()
    print(f"MASE promedio global : {ok['mase'].mean():.3f}")
    print(f"MASE mediana global  : {ok['mase'].median():.3f}")
    print(f"Series too short     : {(results_df['status'] == 'no_forecast').sum() + results_df['status'].str.contains('short', na=False).sum()}")
    print(f"Errores              : {(results_df['status'] == 'error').sum()}")
