"""Pipeline de forecast por SKU y por catálogo.

Contiene la lógica de orquestación que antes vivía en ``PlanningService``:

- ``run_sku_forecast`` — horse-race completo para un SKU individual.
- ``run_catalog_forecast`` — batch sobre el catálogo completo; persiste
  el artefacto en ``output/derived/`` (Opción C).
- ``catalog_forecast_status`` — lectura rápida del metadata del store
  sin abrir el parquet.

Separación de responsabilidades
--------------------------------
Las funciones reciben ``service: "PlanningService"`` para acceder al
repositorio, al logger de eventos y a los helpers de clasificación ya
existentes. El ``TYPE_CHECKING`` guard evita el import circular.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from planning_core.classification import (
    detect_outliers,
    select_granularity,
    treat_outliers,
)
from planning_core.forecasting import selector as _forecast_selector
from planning_core.inventory.params import get_sku_params

if TYPE_CHECKING:
    from planning_core.services import PlanningService


_DAYS_PER_PERIOD: dict[str, float] = {"D": 1.0, "W": 7.0, "M": 30.0}
_H_MIN = 1
_H_MAX = 12


def _h_from_lead_time(lead_time_days: float, granularity: str) -> int:
    days = _DAYS_PER_PERIOD.get(granularity, 30.0)
    return max(_H_MIN, min(_H_MAX, math.ceil(lead_time_days / days)))


# ---------------------------------------------------------------------------
# run_sku_forecast
# ---------------------------------------------------------------------------

def run_sku_forecast(
    service: "PlanningService",
    sku: str,
    location: str | None = None,
    granularity: str | None = None,
    h: int | None = None,
    n_windows: int = 3,
    outlier_method: str = "iqr",
    treat_strategy: str = "winsorize",
    use_lgbm: bool = True,
    return_cv: bool = False,
) -> dict:
    """Selecciona el mejor modelo de forecast para un SKU y genera el pronóstico.

    Idéntico al anterior ``PlanningService.sku_forecast()``, ahora delegado
    aquí para desacoplar la orquestación de la fachada.

    Returns
    -------
    dict
        ``{status, model, mase, forecast, backtest, season_length, granularity, h}``
    """
    if granularity is None:
        granularity = service.official_classification_granularity()

    with service.event_logger.span(
        "forecast.sku",
        module="forecasting",
        entity_type="sku",
        entity_id=sku,
        params={
            "location": location,
            "granularity": granularity,
            "h": h,
            "n_windows": n_windows,
            "outlier_method": outlier_method,
            "treat_strategy": treat_strategy,
            "use_lgbm": use_lgbm,
            "return_cv": return_cv,
        },
    ) as span:
        profile = service.classify_single_sku(sku, location=location, granularity=granularity)
        if profile is None:
            result = {
                "status": "no_data",
                "model": None,
                "mase": float("nan"),
                "forecast": pd.DataFrame(),
                "backtest": {},
                "season_length": 12,
                "granularity": granularity,
                "h": h or 3,
            }
            span.set_status("no_data")
            span.set_result(model=None, sb_class=None, abc_class=None, candidate_models=[])
            return result

        service._log_forecast_profile(sku, location, granularity, profile)

        horizon_resolution = "explicit" if h is not None else "derived"
        lead_time_days: float | None = None
        if h is None:
            try:
                params = service.sku_inventory_params(sku, abc_class=profile.get("abc_class"))
                lead_time_days = float(params.get("lead_time_days", 30.0))
                h = _h_from_lead_time(lead_time_days, granularity)
            except Exception:
                h = 3
                horizon_resolution = "fallback_default"
        service._log_forecast_horizon(sku, granularity, int(h), horizon_resolution, lead_time_days)

        clean_df = service.sku_clean_series(
            sku,
            location=location,
            granularity=granularity,
            outlier_method=outlier_method,
            treat_strategy=treat_strategy,
        )

        if clean_df.empty or "demand_clean" not in clean_df.columns:
            model_input = clean_df[["period", "demand"]].copy()
        else:
            model_input = clean_df[["period", "demand_clean"]].rename(
                columns={"demand_clean": "demand"}
            )
        service._log_forecast_series(sku, granularity, model_input, clean_df, treat_strategy)

        result = _forecast_selector.select_and_forecast(
            profile=profile,
            demand_df=model_input,
            granularity=granularity,
            h=h,
            n_windows=n_windows,
            unique_id=sku,
            use_lgbm=use_lgbm,
            return_cv=return_cv,
        )
        result["demand_series"] = model_input

        status = str(result.get("status") or "ok")
        if status != "ok":
            span.set_status(status)

        service._log_forecast_selection(sku, status, result)

        backtest = result.get("backtest", {})
        winner = result.get("model")
        winner_metrics = backtest.get(winner, {}) if winner else {}
        candidate_models = [
            m for m in backtest.keys()
            if isinstance(m, str) and not m.startswith("__")
        ]
        span.set_metrics(
            mase=result.get("mase"),
            bias=result.get("bias"),
            wmape=winner_metrics.get("wmape"),
            rmsse=winner_metrics.get("rmsse"),
            n_obs=int(len(model_input)),
            n_candidates=int(len(candidate_models)),
        )
        span.set_result(
            model=winner,
            sb_class=profile.get("sb_class"),
            abc_class=profile.get("abc_class"),
            season_length=result.get("season_length"),
            granularity=result.get("granularity"),
            h=result.get("h"),
            candidate_models=candidate_models,
        )
        return result


# ---------------------------------------------------------------------------
# run_catalog_forecast  (Opción C — batch que persiste artefacto)
# ---------------------------------------------------------------------------

def run_catalog_forecast(
    service: "PlanningService",
    granularity: str | None = None,
    n_jobs: int = 1,
    use_lgbm: bool = False,
    n_windows: int = 3,
    h: int = 3,
    derived_dir: Path | None = None,
) -> object:
    """Ejecuta el horse-race sobre el catálogo completo y persiste el resultado.

    El artefacto resultante (``output/derived/forecast_catalog_{granularity}.parquet``)
    es consumido automáticamente por ``run_catalog_health_report`` en el próximo
    cálculo de safety stock, conectando el forecast con el motor de reposición.

    Parameters
    ----------
    service : PlanningService
        Servicio con acceso al repositorio y al logger.
    granularity : str, optional
        ``"M"``, ``"W"`` o ``"D"``. Si None, usa la granularidad oficial.
    n_jobs : int
        Procesos paralelos. 1 = secuencial. -1 = todos los CPUs.
    use_lgbm : bool
        Si True, incluye LightGBM en el horse-race.
    n_windows : int
        Ventanas del backtest expanding-window.
    h : int
        Horizonte de forecast en períodos.
    derived_dir : Path, optional
        Directorio para el artefacto. Default: ``output/derived/``.

    Returns
    -------
    CatalogEvalResult
        Resultado completo del batch. El artefacto ya está persistido en disco.
    """
    from planning_core.forecasting.evaluation import EvalConfig, run_catalog_evaluation

    if granularity is None:
        granularity = service.official_classification_granularity()

    config = EvalConfig(
        granularity=granularity,
        h=h,
        n_windows=n_windows,
        use_lgbm=use_lgbm,
    )

    _output_dir = derived_dir or (Path("output") / "derived")

    return run_catalog_evaluation(
        service=service,
        config=config,
        n_jobs=n_jobs,
        verbose=True,
        save_to_derived=True,
        derived_dir=_output_dir,
        event_logger=service.event_logger,
    )


# ---------------------------------------------------------------------------
# catalog_forecast_status  (lectura rápida sin abrir el parquet)
# ---------------------------------------------------------------------------

def catalog_forecast_status(
    service: "PlanningService",
    granularity: str | None = None,
    derived_dir: Path | None = None,
) -> dict:
    """Retorna el estado del artefacto de forecast materializado.

    Lee solo el JSON de metadata — no abre el parquet.

    Returns
    -------
    dict
        ``{status, run_date, n_skus, n_ok, coverage_pct, top_model, is_stale}``
        ``status`` puede ser ``"ok"``, ``"stale"`` o ``"missing"``.
    """
    from planning_core.forecasting.evaluation.forecast_store import ForecastStore

    if granularity is None:
        granularity = service.official_classification_granularity()

    _output_dir = derived_dir or (Path("output") / "derived")
    store = ForecastStore.load(_output_dir, granularity)

    if store is None:
        return {"status": "missing", "granularity": granularity}

    meta = store.metadata()
    is_stale = store.is_stale()

    return {
        "status": "stale" if is_stale else "ok",
        "granularity": granularity,
        "run_date": meta.get("run_date"),
        "n_skus": meta.get("n_skus"),
        "n_ok": meta.get("n_ok"),
        "coverage_pct": meta.get("coverage_pct"),
        "top_model": meta.get("top_model"),
        "is_stale": is_stale,
    }
