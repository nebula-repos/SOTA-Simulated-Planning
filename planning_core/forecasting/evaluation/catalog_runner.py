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

Estados posibles por SKU
------------------------
- ``"ok"``: modelo seleccionado y forecast generado correctamente.
- ``"fallback"``: serie muy corta para backtest — usa SeasonalNaive o HistoricAverage.
- ``"no_forecast"``: SKU inactivo (sin transacciones) — no se genera forecast, no es error.
- ``"error"``: excepción inesperada en ``_evaluate_sku``.
- ``"series_too_short"``: backtest imposible por datos insuficientes (contabilizado en n_error).
- ``"model_column_missing"``: columna de modelo ausente en cv_df (contabilizado en n_error).

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
from typing import IO, TYPE_CHECKING

import pandas as pd

from planning_core.classification import (
    detect_outliers,
    prepare_demand_series,
    treat_outliers,
)
from planning_core.forecasting.evaluation._types import CatalogEvalResult, EvalConfig
from planning_core.forecasting.selector import select_and_forecast
from planning_core.system_log import EventLogger

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
    event_logger: EventLogger | None = None,
    enable_console_log: bool | None = None,
    console_use_color: bool | None = None,
    console_stream: IO[str] | None = None,
    save_to_derived: bool = False,
    derived_dir: Path | None = None,
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
    logger = event_logger or EventLogger.default(
        source="batch_eval",
        enable_console=verbose if enable_console_log is None else enable_console_log,
        use_color=console_use_color,
        stream=console_stream,
    )

    with logger.span(
        "forecast.batch",
        module="forecasting",
        entity_type="catalog",
        entity_id="all",
        params={
            "granularity": config.granularity,
            "h": config.h,
            "n_windows": config.n_windows,
            "use_lgbm": config.use_lgbm,
            "n_jobs": effective_jobs,
            "checkpoint_every": checkpoint_every,
            "resume": resume,
        },
    ) as span:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_name_part = f"_{config.run_name}" if config.run_name else ""
        run_id = f"{ts}{run_name_part}"

        transactions = service.repository.load_table("transactions")
        inventory = service.repository.load_table("inventory_snapshot")

        catalog_df = service.classify_catalog(granularity=config.granularity)
        catalog_index: dict[str, dict] = catalog_df.set_index("sku").to_dict(orient="index")

        tx_by_sku = {sku: df.copy() for sku, df in transactions.groupby("sku")}
        inv_by_sku = {sku: df.copy() for sku, df in inventory.groupby("sku")}

        all_skus: list[str] = skus or catalog_df["sku"].tolist()

        if config.sample_n is not None and config.sample_n < len(all_skus):
            rng = random.Random(config.random_seed)
            all_skus = rng.sample(all_skus, config.sample_n)

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
                logger.emit(
                    event_name="forecast.batch.resume.completed",
                    module="forecasting",
                    status="ok",
                    entity_type="catalog",
                    entity_id="all",
                    metrics={"n_resumed": len(done_skus)},
                    result={"run_id": run_id, "checkpoint_dir": str(ckpt_file)},
                )

        remaining_skus = [s for s in all_skus if s not in done_skus]
        t0 = time.time()

        if effective_jobs == 1:
            rows = _run_sequential(
                remaining_skus=remaining_skus,
                catalog_index=catalog_index,
                tx_by_sku=tx_by_sku,
                inv_by_sku=inv_by_sku,
                config=config,
                rows=rows,
                checkpoint_every=checkpoint_every,
                ckpt_base=ckpt_base,
                run_id=run_id,
                event_logger=logger,
            )
        else:
            rows = _run_parallel(
                remaining_skus=remaining_skus,
                catalog_index=catalog_index,
                tx_by_sku=tx_by_sku,
                inv_by_sku=inv_by_sku,
                config=config,
                rows=rows,
                effective_jobs=effective_jobs,
                event_logger=logger,
            )

        if ckpt_base.exists():
            shutil.rmtree(ckpt_base, ignore_errors=True)

        sku_results = pd.DataFrame(rows)
        elapsed_total = round(time.time() - t0, 1)
        status_counts = sku_results["status"].value_counts()

        _KNOWN_ERROR_STATUSES = {"error", "series_too_short", "model_column_missing"}
        n_error = int(sum(status_counts.get(s, 0) for s in _KNOWN_ERROR_STATUSES))

        result = CatalogEvalResult(
            config=config,
            run_id=run_id,
            sku_results=sku_results,
            elapsed_seconds=elapsed_total,
            n_ok=int(status_counts.get("ok", 0)),
            n_fallback=int(status_counts.get("fallback", 0)),
            n_no_forecast=int(status_counts.get("no_forecast", 0)),
            n_error=n_error,
        )

        span.set_metrics(
            n_skus=result.n_evaluated,
            n_ok=result.n_ok,
            n_fallback=result.n_fallback,
            n_no_forecast=result.n_no_forecast,
            n_error=result.n_error,
            mase_global_median=result.mase_global_median,
        )
        span.set_result(
            run_id=run_id,
            granularity=config.granularity,
            h=config.h,
            n_windows=config.n_windows,
            jobs=effective_jobs,
        )

        if save_to_derived:
            from planning_core.forecasting.evaluation.forecast_store import (
                ForecastStore,
                build_store_entries,
            )
            _output_dir = derived_dir or (Path("output") / "derived")
            entries = build_store_entries(sku_results, config.granularity)
            ForecastStore.save(entries, _output_dir, config.granularity)

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
    rows: list[dict],
    checkpoint_every: int,
    ckpt_base: Path,
    run_id: str,
    event_logger: EventLogger,
) -> list[dict]:
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
        _emit_batch_sku_event(event_logger, row)

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
    rows: list[dict],
    effective_jobs: int,
    event_logger: EventLogger,
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
            _emit_batch_sku_event(event_logger, row)

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
                "mase": float("nan"), "wmape": float("nan"), "rmsse": float("nan"),
                "bias": float("nan"), "mae": float("nan"), "rmse": float("nan"),
                "granularity": config.granularity, "h": config.h,
                "season_length": None, "n_obs": 0, "error_msg": None,
                "forecast_mean_daily": None, "forecast_sigma_daily": None,
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

        # Capturar señal forward-looking para ForecastStore (Opción C)
        forecast_df = result.get("forecast")
        rmse = winner_metrics.get("rmse")
        if forecast_df is not None and not forecast_df.empty and result.get("status") == "ok":
            _days = {"M": 365.25 / 12, "W": 7.0, "D": 1.0}.get(config.granularity, 365.25 / 12)
            row["forecast_mean_daily"] = float(forecast_df["yhat"].mean()) / _days
            _raw_rmse = float(rmse) if rmse is not None and not math.isnan(float(rmse)) else None
            row["forecast_sigma_daily"] = (_raw_rmse / math.sqrt(_days)) if _raw_rmse is not None else None
        else:
            row["forecast_mean_daily"] = None
            row["forecast_sigma_daily"] = None

        row.update({
            "status":        result.get("status"),
            "model_winner":  winner,
            "mase":          result.get("mase"),
            "wmape":         winner_metrics.get("wmape", float("nan")),
            "rmsse":         winner_metrics.get("rmsse", float("nan")),
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
            "wmape":         float("nan"),
            "rmsse":         float("nan"),
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
        "mase": float("nan"), "wmape": float("nan"), "rmsse": float("nan"),
        "bias": float("nan"), "mae": float("nan"), "rmse": float("nan"),
        "granularity": config.granularity, "h": config.h,
        "season_length": None, "n_obs": None, "error_msg": msg,
    })
    return row


def _emit_batch_sku_event(event_logger: EventLogger, row: dict) -> None:
    status = str(row.get("status") or "ok")
    level = _level_from_status(status)
    event_logger.emit(
        event_name="forecast.batch.sku.completed",
        module="forecasting",
        level=level,
        status=status,
        entity_type="sku",
        entity_id=str(row.get("sku")),
        duration_ms=int(float(row.get("elapsed_sku_s") or 0.0) * 1000),
        metrics={
            "mase": row.get("mase"),
            "bias": row.get("bias"),
            "elapsed_sku_s": row.get("elapsed_sku_s"),
        },
        result={
            "model": row.get("model_winner"),
            "sb_class": row.get("sb_class"),
            "granularity": row.get("granularity"),
            "h": row.get("h"),
        },
        error={"message": row.get("error_msg")} if row.get("error_msg") else None,
    )


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


def _level_from_status(status: str) -> str:
    if status in {"error", "failed"}:
        return "ERROR"
    if status in {"fallback", "no_forecast", "series_too_short", "model_column_missing"}:
        return "WARN"
    return "INFO"


def _print_summary(result: CatalogEvalResult) -> None:
    print(f"Run ID        : {result.run_id}")
    print(f"SKUs          : {result.n_evaluated}  (ok={result.n_ok}  fallback={result.n_fallback}  no_forecast={result.n_no_forecast}  error={result.n_error})")
    print(f"MASE mediana  : {result.mase_global_median:.3f}")
    print(f"MASE media    : {result.mase_global_mean:.3f}")
    print(f"Tiempo total  : {result.elapsed_seconds:.1f}s")
