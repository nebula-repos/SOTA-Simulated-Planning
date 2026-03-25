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

Paralelización
--------------
- ``n_jobs > 1``: usa ``ProcessPoolExecutor`` con ``as_completed`` para
  progreso en tiempo real.
- ``n_jobs = -1``: usa todos los CPUs disponibles.
- ``checkpoint_every``: guarda progreso parcial cada N SKUs (modo secuencial).
- ``resume=True``: continúa desde el último checkpoint si existe en
  ``checkpoint_dir``.

Uso típico
----------
>>> from planning_core.forecasting.evaluation import EvalConfig, run_catalog_evaluation
>>> config = EvalConfig(granularity="M", h=3, n_windows=3, run_name="baseline")
>>> result = run_catalog_evaluation(service, config, n_jobs=4)
"""

from __future__ import annotations

import json
import math
import multiprocessing
import os
import random
import shutil
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
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

_CKPT_FILE = "checkpoint.parquet"
_CKPT_META = "checkpoint_meta.json"


# ---------------------------------------------------------------------------
# Punto de entrada público
# ---------------------------------------------------------------------------

def run_catalog_evaluation(
    service: "PlanningService",
    config: EvalConfig | None = None,
    skus: list[str] | None = None,
    verbose: bool = True,
    n_jobs: int = 1,
    checkpoint_every: int = 50,
    resume: bool = False,
    checkpoint_dir: str | Path | None = None,
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
        Si True, imprime progreso.
    n_jobs : int
        Número de procesos paralelos. 1 = secuencial, -1 = todos los CPUs.
    checkpoint_every : int
        Guardar checkpoint parcial cada N SKUs (solo modo secuencial).
    resume : bool
        Si True y existe checkpoint en ``checkpoint_dir``, continúa desde ahí.
    checkpoint_dir : str | Path | None
        Directorio para checkpoints. Default: ``output/eval_runs/_tmp_<run_id>``.

    Returns
    -------
    CatalogEvalResult
        Resultado en memoria. Usar ``run_store.save_run()`` para persistir.
    """
    if config is None:
        config = EvalConfig()

    effective_jobs = os.cpu_count() if n_jobs == -1 else n_jobs
    effective_jobs = max(1, effective_jobs or 1)

    # ── Generar run_id ───────────────────────────────────────────────────────
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_name_part = f"_{config.run_name}" if config.run_name else ""
    run_id = f"{ts}{run_name_part}"

    # ── Pre-carga única ──────────────────────────────────────────────────────
    if verbose:
        print("[eval] Pre-cargando datos y clasificando catálogo...")

    transactions = service.repository.load_table("transactions")
    inventory    = service.repository.load_table("inventory_snapshot")

    catalog_df = service.classify_catalog(granularity=config.granularity)
    catalog_index: dict[str, dict] = catalog_df.set_index("sku").to_dict(orient="index")

    tx_by_sku  = {sku: df.copy() for sku, df in transactions.groupby("sku")}
    inv_by_sku = {sku: df.copy() for sku, df in inventory.groupby("sku")}

    # ── Lista de SKUs ────────────────────────────────────────────────────────
    all_skus: list[str] = skus or catalog_df["sku"].tolist()

    if config.sample_n is not None and config.sample_n < len(all_skus):
        rng = random.Random(config.random_seed)
        all_skus = rng.sample(all_skus, config.sample_n)

    # ── Checkpoint / Resume ──────────────────────────────────────────────────
    ckpt_base = Path(checkpoint_dir) if checkpoint_dir else Path(f"output/eval_runs/_tmp_{run_id}")
    rows: list[dict] = []
    done_skus: set[str] = set()

    if resume:
        ckpt_file = ckpt_base / _CKPT_FILE
        meta_file = ckpt_base / _CKPT_META
        if ckpt_file.exists():
            prev_df = pd.read_parquet(ckpt_file)
            rows = prev_df.to_dict(orient="records")
            done_skus = set(prev_df["sku"].tolist())
            if meta_file.exists():
                with open(meta_file) as f:
                    run_id = json.load(f).get("run_id", run_id)
            if verbose:
                print(f"[eval] Resume: {len(done_skus)} SKUs cargados de {ckpt_file}")

    remaining_skus = [s for s in all_skus if s not in done_skus]

    if verbose:
        lgbm_tag = "lgbm=ON" if config.use_lgbm else "lgbm=OFF"
        mode_tag = f"jobs={effective_jobs}" if effective_jobs > 1 else "sequential"
        print(
            f"[eval] {len(remaining_skus)} SKUs  "
            f"gran={config.granularity}  h={config.h}  "
            f"n_windows={config.n_windows}  {lgbm_tag}  {mode_tag}"
        )
        print("-" * 72)

    t0 = time.time()

    # ── Ejecución ────────────────────────────────────────────────────────────
    if effective_jobs == 1:
        rows = _run_sequential(
            remaining_skus=remaining_skus,
            catalog_index=catalog_index,
            tx_by_sku=tx_by_sku,
            inv_by_sku=inv_by_sku,
            config=config,
            verbose=verbose,
            rows=rows,
            checkpoint_every=checkpoint_every,
            ckpt_base=ckpt_base,
            run_id=run_id,
            total=len(all_skus),
        )
    else:
        rows = _run_parallel(
            remaining_skus=remaining_skus,
            catalog_index=catalog_index,
            tx_by_sku=tx_by_sku,
            inv_by_sku=inv_by_sku,
            config=config,
            verbose=verbose,
            rows=rows,
            effective_jobs=effective_jobs,
            total=len(all_skus),
        )

    # Limpiar checkpoint al completar
    if ckpt_base.exists():
        shutil.rmtree(ckpt_base, ignore_errors=True)

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
# Modo secuencial
# ---------------------------------------------------------------------------

def _run_sequential(
    remaining_skus: list[str],
    catalog_index: dict,
    tx_by_sku: dict,
    inv_by_sku: dict,
    config: EvalConfig,
    verbose: bool,
    rows: list[dict],
    checkpoint_every: int,
    ckpt_base: Path,
    run_id: str,
    total: int,
) -> list[dict]:
    n_done_before = len(rows)
    t0 = time.time()

    for i, sku in enumerate(remaining_skus, 1):
        t_sku = time.time()
        row = _evaluate_sku(
            sku=sku,
            profile=catalog_index.get(sku, {}),
            sku_tx=tx_by_sku.get(sku, pd.DataFrame()),
            sku_inv=inv_by_sku.get(sku, pd.DataFrame()),
            config=config,
        )
        row["elapsed_sku_s"] = round(time.time() - t_sku, 2)
        rows.append(row)

        global_i = n_done_before + i

        if verbose:
            elapsed = time.time() - t0
            print(
                f"  [{global_i:4d}/{total}] {sku:<12}"
                f"  sb={str(row.get('sb_class', '?')):<13}"
                f"  winner={str(row.get('model_winner') or '—'):<15}"
                f"  MASE={_fmt(row.get('mase'))}"
                f"  ({elapsed:.0f}s)"
            )

        if checkpoint_every and i % checkpoint_every == 0:
            _save_checkpoint(rows, ckpt_base, run_id)

    return rows


# ---------------------------------------------------------------------------
# Modo paralelo
# ---------------------------------------------------------------------------

def _run_parallel(
    remaining_skus: list[str],
    catalog_index: dict,
    tx_by_sku: dict,
    inv_by_sku: dict,
    config: EvalConfig,
    verbose: bool,
    rows: list[dict],
    effective_jobs: int,
    total: int,
) -> list[dict]:
    tasks = [
        (
            sku,
            catalog_index.get(sku, {}),
            tx_by_sku.get(sku, pd.DataFrame()),
            inv_by_sku.get(sku, pd.DataFrame()),
            config,
        )
        for sku in remaining_skus
    ]

    n_done_before = len(rows)
    completed = 0
    t0 = time.time()

    # fork evita re-importar el módulo principal en cada worker (problema de spawn en macOS)
    _ctx = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=effective_jobs, mp_context=_ctx) as executor:
        futures = {executor.submit(_parallel_task, task): task[0] for task in tasks}

        for fut in as_completed(futures):
            sku_name = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:
                row = _error_row(sku_name, config, str(exc))

            rows.append(row)
            completed += 1
            global_i = n_done_before + completed

            if verbose:
                elapsed = time.time() - t0
                print(
                    f"  [{global_i:4d}/{total}] {sku_name:<12}"
                    f"  sb={str(row.get('sb_class', '?')):<13}"
                    f"  winner={str(row.get('model_winner') or '—'):<15}"
                    f"  MASE={_fmt(row.get('mase'))}"
                    f"  ({elapsed:.0f}s)"
                )

    return rows


# ---------------------------------------------------------------------------
# Worker picklable — nivel de módulo para ProcessPoolExecutor
# ---------------------------------------------------------------------------

def _parallel_task(args: tuple) -> dict:
    """Worker para ProcessPoolExecutor. Debe estar a nivel de módulo para ser picklable."""
    sku, profile, sku_tx, sku_inv, config = args
    t_sku = time.time()
    row = _evaluate_sku(sku=sku, profile=profile, sku_tx=sku_tx, sku_inv=sku_inv, config=config)
    row["elapsed_sku_s"] = round(time.time() - t_sku, 2)
    return row


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

    row: dict = {
        "sku":                 sku,
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
    for m in _MODEL_NAMES:
        row[f"mase_{m}"] = float("nan")

    try:
        if sku_tx.empty:
            # SKU sin transacciones → inactivo, no se genera forecast (no es un error)
            row.update({
                "status": "no_forecast",
                "model_winner": None,
                "mase": float("nan"), "wape": float("nan"),
                "bias": float("nan"), "mae": float("nan"), "rmse": float("nan"),
                "granularity": config.granularity, "h": config.h,
                "season_length": None, "n_obs": 0, "error_msg": None,
            })
            return row

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
        winner_metrics = backtest.get(winner, {}) if winner else {}

        row.update({
            "status":        result.get("status"),
            "model_winner":  winner,
            "mase":          result.get("mase"),
            "wape":          winner_metrics.get("wape", float("nan")),
            "bias":          winner_metrics.get("bias", float("nan")),
            "mae":           winner_metrics.get("mae",  float("nan")),
            "rmse":          winner_metrics.get("rmse", float("nan")),
            "granularity":   result.get("granularity"),
            "h":             result.get("h"),
            "season_length": result.get("season_length"),
            "n_obs":         n_obs,
            "error_msg":     None,
        })

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


def _error_row(sku: str, config: EvalConfig, msg: str) -> dict:
    """Fila de error para futuros que lanzan excepción en modo paralelo."""
    row: dict = {"sku": sku, "elapsed_sku_s": 0.0}
    for m in _MODEL_NAMES:
        row[f"mase_{m}"] = float("nan")
    row.update({
        "status": "error", "model_winner": None,
        "mase": float("nan"), "wape": float("nan"),
        "bias": float("nan"), "mae": float("nan"), "rmse": float("nan"),
        "granularity": config.granularity, "h": config.h,
        "season_length": None, "n_obs": None, "error_msg": msg,
    })
    return row


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _save_checkpoint(rows: list[dict], ckpt_dir: Path, run_id: str) -> None:
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(ckpt_dir / _CKPT_FILE, index=False)
    with open(ckpt_dir / _CKPT_META, "w") as f:
        json.dump({"run_id": run_id, "n_done": len(rows)}, f)


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
