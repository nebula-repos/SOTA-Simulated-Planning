"""Orquestador de evaluación de forecast sobre el catálogo completo.

Itera sobre todos los SKUs (o una submuestra), corre el horse-race
via ``select_and_forecast`` y recopila métricas en un DataFrame.

Desacoplamiento
---------------
- El worker ``_evaluate_sku`` es una función pura: recibe DataFrames y
  dicts, sin dependencia de ``PlanningService``.
- ``run_catalog_evaluation`` acepta un ``PlanningService`` como entrada
  conveniente para pre-cargar datos, pero no lo pasa al worker.
- ``aggregator``, ``comparator`` y ``run_store`` son puro pandas.

Uso típico
----------
>>> from planning_core.forecasting.evaluation import EvalConfig, run_catalog_evaluation
>>> config = EvalConfig(granularity="M", h=3, n_windows=3, run_name="baseline")
>>> result = run_catalog_evaluation(service, config)
"""

from __future__ import annotations

import math
import random
import time
import warnings
from typing import TYPE_CHECKING

import pandas as pd

from planning_core.classification import (
    detect_outliers,
    prepare_demand_series,
    treat_outliers,
)
from planning_core.forecasting.evaluation._types import CatalogEvalResult, EvalConfig
from planning_core.forecasting.selector import select_and_forecast

if TYPE_CHECKING:
    from planning_core.services import PlanningService


# Modelos conocidos — columnas mase_* garantizadas en sku_results
_MODEL_NAMES = [
    "AutoETS", "AutoARIMA", "MSTL", "SeasonalNaive",
    "CrostonSBA", "ADIDA", "LightGBM",
]


# ---------------------------------------------------------------------------
# Punto de entrada público
# ---------------------------------------------------------------------------

def run_catalog_evaluation(
    service: "PlanningService",
    config: EvalConfig | None = None,
    skus: list[str] | None = None,
    verbose: bool = True,
) -> CatalogEvalResult:
    """Evalúa el forecast sobre el catálogo completo o un subconjunto.

    Parameters
    ----------
    service : PlanningService
        Servicio con acceso al repositorio de datos.
    config : EvalConfig, optional
        Configuración de la corrida. Si None, usa defaults.
    skus : list[str], optional
        Lista explícita de SKUs a evaluar. Si None, usa el catálogo completo.
    verbose : bool
        Si True, imprime progreso línea a línea.

    Returns
    -------
    CatalogEvalResult
        Resultado en memoria. Usar ``run_store.save_run()`` para persistir.
    """
    if config is None:
        config = EvalConfig()

    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_name_part = f"_{config.run_name}" if config.run_name else ""
    run_id = f"{ts}{run_name_part}"

    # ── Pre-carga única ──────────────────────────────────────────────────────
    if verbose:
        print(f"[eval] Pre-cargando datos y clasificando catálogo...")

    transactions = service.repository.load_table("transactions")
    inventory    = service.repository.load_table("inventory_snapshot")

    # Clasificación completa del catálogo (una sola llamada)
    catalog_df = service.classify_catalog(granularity=config.granularity)
    catalog_index: dict[str, dict] = catalog_df.set_index("sku").to_dict(orient="index")

    # Índices de transacciones e inventario por SKU (evita filtrar 683k filas 800 veces)
    tx_by_sku  = {sku: df.copy() for sku, df in transactions.groupby("sku")}
    inv_by_sku = {sku: df.copy() for sku, df in inventory.groupby("sku")}

    # ── Lista de SKUs ────────────────────────────────────────────────────────
    all_skus: list[str] = skus or catalog_df["sku"].tolist()

    if config.sample_n is not None and config.sample_n < len(all_skus):
        rng = random.Random(config.random_seed)
        all_skus = rng.sample(all_skus, config.sample_n)

    if verbose:
        lgbm_tag = "lgbm=ON" if config.use_lgbm else "lgbm=OFF"
        print(
            f"[eval] {len(all_skus)} SKUs  "
            f"gran={config.granularity}  h={config.h}  "
            f"n_windows={config.n_windows}  {lgbm_tag}"
        )
        print("-" * 72)

    # ── Loop principal ───────────────────────────────────────────────────────
    rows: list[dict] = []
    t0 = time.time()

    for i, sku in enumerate(all_skus, 1):
        t_sku = time.time()
        row = _evaluate_sku(
            sku=sku,
            profile=catalog_index.get(sku, {}),
            sku_tx=tx_by_sku.get(sku, transactions.iloc[0:0]),
            sku_inv=inv_by_sku.get(sku, inventory.iloc[0:0]),
            config=config,
        )
        row["elapsed_sku_s"] = round(time.time() - t_sku, 2)
        rows.append(row)

        if verbose:
            elapsed = time.time() - t0
            print(
                f"  [{i:4d}/{len(all_skus)}] {sku:<12}"
                f"  sb={str(row.get('sb_class', '?')):<13}"
                f"  winner={str(row.get('model_winner') or '—'):<15}"
                f"  MASE={_fmt(row.get('mase'))}"
                f"  ({elapsed:.0f}s)"
            )

    sku_results = pd.DataFrame(rows)
    elapsed_total = round(time.time() - t0, 1)
    status_counts = sku_results["status"].value_counts()

    result = CatalogEvalResult(
        config=config,
        run_id=run_id,
        sku_results=sku_results,
        elapsed_seconds=elapsed_total,
        n_ok=int(status_counts.get("ok", 0)),
        n_fallback=int(status_counts.get("fallback", 0)),
        n_no_forecast=int(status_counts.get("no_forecast", 0)),
        n_error=int(status_counts.get("error", 0)),
    )

    if verbose:
        print()
        print("=" * 72)
        _print_summary(result)

    return result


# ---------------------------------------------------------------------------
# Worker puro — sin dependencia de PlanningService
# ---------------------------------------------------------------------------

def _evaluate_sku(
    sku: str,
    profile: dict,
    sku_tx: pd.DataFrame,
    sku_inv: pd.DataFrame,
    config: EvalConfig,
) -> dict:
    """Evalúa un SKU individual. Función pura — solo DataFrames y dicts."""

    # Fila base con metadata de clasificación
    row: dict = {
        "sku": sku,
        "sb_class":            profile.get("sb_class"),
        "abc_class":           profile.get("abc_class"),
        "xyz_class":           profile.get("xyz_class"),
        "abc_xyz":             profile.get("abc_xyz"),
        "is_seasonal":         profile.get("is_seasonal"),
        "lifecycle":           profile.get("lifecycle"),
        "quality_score":       profile.get("quality_score"),
        "has_censored_demand": profile.get("has_censored_demand"),
        "total_periods":       profile.get("total_periods"),
    }
    # Columnas mase_* garantizadas (NaN si el modelo no compitió)
    for m in _MODEL_NAMES:
        row[f"mase_{m}"] = float("nan")

    try:
        # Serie de demanda limpia (outliers tratados)
        if sku_tx.empty:
            raise ValueError("Sin transacciones")

        demand_df = prepare_demand_series(sku_tx, granularity=config.granularity)
        if demand_df.empty:
            raise ValueError("Serie de demanda vacía")

        outlier_mask = detect_outliers(demand_df["demand"], method=config.outlier_method)
        clean = treat_outliers(demand_df["demand"], outlier_mask, strategy=config.treat_strategy)
        model_input = demand_df[["period"]].copy()
        model_input["demand"] = clean.values

        n_obs = len(model_input)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = select_and_forecast(
                profile=profile,
                demand_df=model_input,
                granularity=config.granularity,
                h=config.h,
                n_windows=config.n_windows,
                unique_id=sku,
                use_lgbm=config.use_lgbm,
            )

        backtest = result.get("backtest", {})
        winner   = result.get("model")

        # Métricas del ganador
        winner_metrics = backtest.get(winner, {}) if winner else {}

        row.update({
            "status":       result.get("status"),
            "model_winner": winner,
            "mase":         result.get("mase"),
            "wape":         winner_metrics.get("wape", float("nan")),
            "bias":         winner_metrics.get("bias", float("nan")),
            "mae":          winner_metrics.get("mae",  float("nan")),
            "rmse":         winner_metrics.get("rmse", float("nan")),
            "granularity":  result.get("granularity"),
            "h":            result.get("h"),
            "season_length":result.get("season_length"),
            "n_obs":        n_obs,
            "error_msg":    None,
        })

        # MASE individual de cada candidato
        for model_name, metrics in backtest.items():
            col = f"mase_{model_name}"
            if col in row:
                row[col] = metrics.get("mase", float("nan"))

    except Exception as exc:
        row.update({
            "status":        "error",
            "model_winner":  None,
            "mase":          float("nan"),
            "wape":          float("nan"),
            "bias":          float("nan"),
            "mae":           float("nan"),
            "rmse":          float("nan"),
            "granularity":   config.granularity,
            "h":             config.h,
            "season_length": None,
            "n_obs":         None,
            "error_msg":     str(exc),
        })

    return row


# ---------------------------------------------------------------------------
# Helpers de display
# ---------------------------------------------------------------------------

def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return " N/A  "
    return f"{v:.3f} "


def _print_summary(result: CatalogEvalResult) -> None:
    print(f"Run ID        : {result.run_id}")
    print(f"SKUs          : {result.n_evaluated}  (ok={result.n_ok}  fallback={result.n_fallback}  no_forecast={result.n_no_forecast}  error={result.n_error})")
    print(f"MASE mediana  : {result.mase_global_median:.3f}")
    print(f"MASE media    : {result.mase_global_mean:.3f}")
    print(f"Tiempo total  : {result.elapsed_seconds:.1f}s")
