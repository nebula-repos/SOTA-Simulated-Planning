"""Pipeline de inventario — health report con señal de forecast integrada.

Contiene la lógica de orquestación de ``catalog_health_report`` extraída de
``PlanningService``. La diferencia clave respecto a la implementación original:

**Opción C — integración de forecast**:
    Al inicio del pipeline se intenta cargar el ``ForecastStore`` desde
    ``output/derived/``. Si está fresco y disponible, los valores
    ``forecast_mean_daily`` y ``forecast_sigma_daily`` de cada SKU reemplazan
    la señal histórica en ``compute_sku_safety_stock``, haciendo que Safety
    Stock y ROP sean forward-looking (capturan tendencia y estacionalidad del
    modelo ganador del horse-race).

    Si el store está ausente o stale, el pipeline cae silenciosamente a la
    señal histórica y emite un ``WARN`` en el log de eventos.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from planning_core.classification import prepare_demand_series
from planning_core.inventory.diagnostics import diagnose_sku
from planning_core.inventory.params import get_sku_params
from planning_core.inventory.safety_stock import compute_sku_safety_stock

if TYPE_CHECKING:
    from planning_core.services import PlanningService


def _counts_dict(values: pd.Series) -> dict[str, int]:
    if values.empty:
        return {}
    return {str(k): int(v) for k, v in values.value_counts(dropna=False).items()}


# ---------------------------------------------------------------------------
# run_catalog_health_report
# ---------------------------------------------------------------------------

def run_catalog_health_report(
    service: "PlanningService",
    granularity: str | None = None,
    simple_safety_pct: float = 0.5,
    derived_dir: Path | None = None,
) -> pd.DataFrame:
    """Diagnóstica el estado de salud de inventario de todo el catálogo activo.

    Ejecuta el pipeline completo (§11.2 del PDF) para cada SKU activo:
    estado actual → necesidad futura (SS/ROP) → ratio → clasificación.

    La señal de forecast se inyecta automáticamente si el artefacto está
    disponible y fresco. El sufijo ``_forecast`` en ``ss_method`` confirma
    cuándo se usó señal forward-looking.

    Parameters
    ----------
    service : PlanningService
        Servicio con acceso al repositorio.
    granularity : str, optional
        Granularidad de la serie. Si None, usa la oficial del manifest.
    simple_safety_pct : float
        Fracción del LT demand para SS clase C. Default 0.5.
    derived_dir : Path, optional
        Directorio del artefacto de forecast. Default: ``output/derived/``.

    Returns
    -------
    pd.DataFrame
        Una fila por SKU activo con diagnóstico completo.
    """
    if granularity is None:
        granularity = service.official_classification_granularity()

    with service.event_logger.span(
        "inventory.health_report",
        module="inventory",
        entity_type="catalog",
        entity_id="all",
        params={"granularity": granularity, "simple_safety_pct": simple_safety_pct},
    ) as span:

        # ------------------------------------------------------------------
        # Opción C: intentar cargar ForecastStore
        # ------------------------------------------------------------------
        from planning_core.forecasting.evaluation.forecast_store import ForecastStore

        _output_dir = derived_dir or (Path("output") / "derived")
        forecast_store = ForecastStore.load(_output_dir, granularity)

        if forecast_store is None:
            service.event_logger.emit(
                event_name="inventory.health_report.forecast_store",
                module="inventory",
                level="WARN",
                status="missing",
                entity_type="catalog",
                entity_id="all",
                result={
                    "forecast_signal": "historical",
                    "reason": "store_not_found",
                    "hint": f"Ejecutar: python apps/batch_forecast.py --granularity {granularity}",
                },
            )
            forecast_store = None
        elif forecast_store.is_stale():
            meta = forecast_store.metadata()
            service.event_logger.emit(
                event_name="inventory.health_report.forecast_store",
                module="inventory",
                level="WARN",
                status="stale",
                entity_type="catalog",
                entity_id="all",
                result={
                    "forecast_signal": "historical",
                    "reason": "store_stale",
                    "run_date": meta.get("run_date"),
                    "hint": f"Re-ejecutar: python apps/batch_forecast.py --granularity {granularity}",
                },
            )
            forecast_store = None
        else:
            meta = forecast_store.metadata()
            service.event_logger.emit(
                event_name="inventory.health_report.forecast_store",
                module="inventory",
                level="INFO",
                status="ok",
                entity_type="catalog",
                entity_id="all",
                metrics={"n_skus": meta.get("n_skus"), "coverage_pct": meta.get("coverage_pct")},
                result={
                    "forecast_signal": "forecast",
                    "run_date": meta.get("run_date"),
                    "top_model": meta.get("top_model"),
                },
            )

        # ------------------------------------------------------------------
        # Datos base
        # ------------------------------------------------------------------
        classification_df = service.classify_catalog(granularity=granularity)

        inventory = service.repository.load_table("inventory_snapshot")
        latest_date = inventory["snapshot_date"].max()
        latest_inv = inventory[inventory["snapshot_date"] == latest_date]

        stock_by_sku = (
            latest_inv.groupby("sku")[["on_hand_qty", "on_order_qty"]]
            .sum()
            .rename(columns={"on_hand_qty": "on_hand", "on_order_qty": "on_order"})
        )

        transactions = service.repository.load_table("transactions")
        if not transactions.empty and "date" in transactions.columns:
            last_movement = (
                transactions.groupby("sku")["date"]
                .max()
                .rename("last_movement_date")
            )
            latest_tx_date = pd.to_datetime(transactions["date"]).max()
            tx_by_sku: dict[str, pd.DataFrame] = {
                sku: grp for sku, grp in transactions.groupby("sku")
            }
        else:
            last_movement = pd.Series(dtype="object", name="last_movement_date")
            latest_tx_date = pd.Timestamp.now()
            tx_by_sku = {}

        catalog = service.repository.load_table("product_catalog")
        manifest = service.repository.load_manifest()

        _CAT_COLS = ["sku", "category", "subcategory", "supplier", "brand", "base_price", "cost"]
        _available_cat_cols = [c for c in _CAT_COLS if c in catalog.columns]
        catalog_index = catalog[_available_cat_cols].set_index("sku")

        # ------------------------------------------------------------------
        # Loop por SKU
        # ------------------------------------------------------------------
        diagnoses: list[dict] = []
        n_forecast_used = 0

        for _, row in classification_df.iterrows():
            sku = row["sku"]
            abc_class = row.get("abc_class")

            if sku in stock_by_sku.index:
                on_hand = float(stock_by_sku.loc[sku, "on_hand"])
                on_order = float(stock_by_sku.loc[sku, "on_order"])
            else:
                on_hand, on_order = 0.0, 0.0

            if sku in last_movement.index and pd.notna(last_movement[sku]):
                last_mv = pd.to_datetime(last_movement[sku])
                days_since = int((latest_tx_date - last_mv).days)
            else:
                days_since = 9999

            if sku in catalog_index.index:
                cat_row = catalog_index.loc[sku]
                supplier = cat_row.get("supplier") if "supplier" in catalog_index.columns else None
                category = cat_row.get("category")
                subcategory = cat_row.get("subcategory")
                brand = cat_row.get("brand")
                base_price = float(cat_row.get("base_price") or 0.0)
                unit_cost = float(cat_row.get("cost") or 0.0)
            else:
                supplier = category = subcategory = brand = None
                base_price = unit_cost = 0.0

            params = get_sku_params(sku, abc_class, supplier, service.repository, manifest)

            sku_tx_raw = tx_by_sku.get(sku, pd.DataFrame(columns=transactions.columns))
            demand_series = prepare_demand_series(sku_tx_raw, granularity=granularity)

            # Señal forward-looking desde ForecastStore (Opción C)
            forecast_mean_daily: float | None = None
            forecast_sigma_daily: float | None = None
            if forecast_store is not None:
                entry = forecast_store.get(sku)
                if entry is not None and entry.status == "ok":
                    forecast_mean_daily = entry.forecast_mean_daily
                    forecast_sigma_daily = entry.forecast_sigma_daily
                    if forecast_mean_daily is not None and forecast_mean_daily > 0:
                        n_forecast_used += 1

            ss_result = compute_sku_safety_stock(
                params,
                demand_series,
                granularity=granularity,
                simple_safety_pct=simple_safety_pct,
                forecast_mean_daily=forecast_mean_daily,
                forecast_sigma_daily=forecast_sigma_daily,
            )

            diagnosis = diagnose_sku(
                sku=sku,
                on_hand=on_hand,
                on_order=on_order,
                ss_result=ss_result,
                params=params,
                abc_class=abc_class,
                days_since_last_movement=days_since,
            )
            d = diagnosis.to_dict()
            d["days_since_last_movement"] = days_since
            d["category"] = category
            d["subcategory"] = subcategory
            d["supplier"] = supplier
            d["brand"] = brand
            d["base_price"] = base_price
            d["unit_cost"] = unit_cost
            d["excess_capital"] = diagnosis.excess_units * unit_cost
            d["stockout_capital"] = diagnosis.suggested_order_qty * unit_cost
            diagnoses.append(d)

        if not diagnoses:
            span.set_status("empty")
            span.set_result(health_status_distribution={}, alert_level_distribution={})
            return pd.DataFrame()

        report_df = pd.DataFrame(diagnoses)
        span.set_metrics(
            n_skus=int(len(report_df)),
            n_forecast_used=n_forecast_used,
            total_excess_capital=float(report_df["excess_capital"].sum()) if "excess_capital" in report_df else 0.0,
            total_stockout_capital=float(report_df["stockout_capital"].sum()) if "stockout_capital" in report_df else 0.0,
        )
        span.set_result(
            forecast_signal="forecast" if forecast_store is not None else "historical",
            health_status_distribution=_counts_dict(report_df["health_status"]) if "health_status" in report_df else {},
            alert_level_distribution=_counts_dict(report_df["alert_level"]) if "alert_level" in report_df else {},
        )
        return report_df
