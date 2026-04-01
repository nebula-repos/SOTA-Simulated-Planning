from __future__ import annotations

import math
from pathlib import Path
from typing import IO

import pandas as pd

from planning_core.classification import (
    classify_all_skus,
    classify_sku,
    compute_acf,
    detect_outliers,
    prepare_demand_series,
    select_granularity,
    treat_outliers,
)
from planning_core.forecasting import selector as _forecast_selector  # lazy-loaded per call (D22)
from planning_core.inventory.diagnostics import InventoryDiagnosis, diagnose_sku
from planning_core.inventory.params import get_sku_params
from planning_core.inventory.safety_stock import compute_sku_safety_stock, SafetyStockResult
from planning_core.inventory.service_level import get_csl_target
from planning_core.preprocessing import censored_summary, mark_censored_demand
from planning_core.purchase.order_proposal import (
    PurchaseProposal,
    aggregate_by_supplier,
    purchase_plan_summary,
)
from planning_core.purchase.recommendation import (
    PurchaseRecommendation,
    build_purchase_recommendation,
    generate_purchase_plan,
)
from planning_core.repository import CanonicalRepository
from planning_core.system_log import EventLogger
from planning_core.validation import basic_health_report


OFFICIAL_CLASSIFICATION_SCOPE = "network_aggregate"
OFFICIAL_CLASSIFICATION_GRANULARITY = "M"

# D20: horizonte dinamico por SKU
_DAYS_PER_PERIOD: dict[str, float] = {"D": 1.0, "W": 7.0, "M": 30.0}
_H_MIN = 1
_H_MAX = 12


def _h_from_lead_time(lead_time_days: float, granularity: str) -> int:
    """Convierte lead_time_days a periodos de forecast redondeando hacia arriba.

    Usado por ``sku_forecast`` para derivar el horizonte h por SKU en lugar
    de usar el valor global h=3 (D20). Resultado acotado en [1, 12].
    """
    days = _DAYS_PER_PERIOD.get(granularity, 30.0)
    return max(_H_MIN, min(_H_MAX, math.ceil(lead_time_days / days)))


def _counts_dict(values: pd.Series) -> dict[str, int]:
    if values.empty:
        return {}
    counts = values.value_counts(dropna=False).to_dict()
    return {str(key): int(value) for key, value in counts.items()}


def _top_backtest_models(backtest: dict, top_k: int = 3) -> list[dict]:
    ranked: list[dict] = []
    for model_name, metrics in backtest.items():
        if not isinstance(model_name, str) or model_name.startswith("__") or not isinstance(metrics, dict):
            continue
        mase = metrics.get("mase")
        if mase is None or pd.isna(mase):
            continue
        ranked.append(
            {
                "model": model_name,
                "mase": float(mase),
                "bias": float(metrics["bias"]) if metrics.get("bias") is not None and not pd.isna(metrics.get("bias")) else None,
                "rmsse": float(metrics["rmsse"]) if metrics.get("rmsse") is not None and not pd.isna(metrics.get("rmsse")) else None,
                "status": metrics.get("status"),
            }
        )
    ranked.sort(key=lambda item: item["mase"])
    return ranked[:top_k]


class PlanningService:
    """Servicios de lectura y agregacion sobre el modelo canonico."""

    def __init__(
        self,
        repository: CanonicalRepository,
        event_logger: EventLogger | None = None,
        *,
        log_source: str = "service",
        enable_console_log: bool | None = None,
        console_use_color: bool | None = None,
        console_stream: IO[str] | None = None,
        system_log_dir: str | Path | None = None,
    ):
        self.repository = repository
        self.event_logger = event_logger or EventLogger.default(
            source=log_source,
            enable_console=enable_console_log,
            use_color=console_use_color,
            stream=console_stream,
            base_dir=system_log_dir,
        )

    def _log_forecast_profile(self, sku: str, location: str | None, granularity: str, profile: dict) -> None:
        quality_flags = profile.get("quality_flags")
        if not isinstance(quality_flags, list):
            quality_flags = []
        self.event_logger.emit(
            event_name="forecast.sku.profile.completed",
            module="forecasting",
            level="WARN" if profile.get("sb_class") == "inactive" else "INFO",
            status="inactive" if profile.get("sb_class") == "inactive" else "ok",
            entity_type="sku",
            entity_id=sku,
            params={"location": location, "granularity": granularity},
            metrics={
                "quality_score": profile.get("quality_score"),
                "censored_pct": profile.get("censored_pct"),
                "censored_demand_pct": profile.get("censored_demand_pct"),
            },
            result={
                "sb_class": profile.get("sb_class"),
                "abc_class": profile.get("abc_class"),
                "xyz_class": profile.get("xyz_class"),
                "abc_xyz": profile.get("abc_xyz"),
                "is_seasonal": profile.get("is_seasonal"),
                "has_trend": profile.get("has_trend"),
                "classification_scope": profile.get("classification_scope"),
                "quality_flags": quality_flags or None,
            },
        )

    def _log_forecast_horizon(
        self,
        sku: str,
        granularity: str,
        h: int,
        resolution: str,
        lead_time_days: float | None,
    ) -> None:
        self.event_logger.emit(
            event_name="forecast.sku.horizon.completed",
            module="forecasting",
            status="ok",
            entity_type="sku",
            entity_id=sku,
            metrics={"h": h, "lead_time_days": lead_time_days},
            result={"granularity": granularity, "resolution": resolution},
        )

    def _log_forecast_series(
        self,
        sku: str,
        granularity: str,
        model_input: pd.DataFrame,
        clean_df: pd.DataFrame,
        treat_strategy: str,
    ) -> None:
        outlier_count = int(clean_df["is_outlier"].sum()) if "is_outlier" in clean_df.columns else 0
        zero_demand_pct = round(float((model_input["demand"] == 0).mean()), 4) if not model_input.empty else 0.0
        self.event_logger.emit(
            event_name="forecast.sku.series.completed",
            module="forecasting",
            status="ok",
            entity_type="sku",
            entity_id=sku,
            metrics={
                "n_obs": int(len(model_input)),
                "outlier_count": outlier_count,
                "zero_demand_pct": zero_demand_pct,
            },
            result={
                "granularity": granularity,
                "series_source": "demand_clean" if "demand_clean" in clean_df.columns else "demand",
                "treat_strategy": treat_strategy,
            },
        )

    def _log_forecast_selection(self, sku: str, status: str, result: dict) -> None:
        backtest = result.get("backtest", {})
        winner = result.get("model")
        ensemble_models = result.get("ensemble_models")  # lista de modelos constituyentes o None

        # Para Ensemble, winner_metrics no existe en el backtest dict.
        # Usamos el promedio de las métricas de los modelos constituyentes como proxy.
        if winner == "Ensemble" and ensemble_models:
            constituent_metrics = [backtest[m] for m in ensemble_models if m in backtest and isinstance(backtest[m], dict)]
            wmapes = [m.get("wmape") for m in constituent_metrics if m.get("wmape") is not None and not pd.isna(m.get("wmape"))]
            rmsses = [m.get("rmsse") for m in constituent_metrics if m.get("rmsse") is not None and not pd.isna(m.get("rmsse"))]
            winner_wmape = float(sum(wmapes) / len(wmapes)) if wmapes else None
            winner_rmsse = float(sum(rmsses) / len(rmsses)) if rmsses else None
        else:
            winner_metrics = backtest.get(winner, {}) if winner else {}
            winner_wmape = winner_metrics.get("wmape")
            winner_rmsse = winner_metrics.get("rmsse")

        candidate_models = [
            model_name
            for model_name in backtest.keys()
            if isinstance(model_name, str) and not model_name.startswith("__")
        ]
        failed_models = [
            m for m in candidate_models
            if backtest[m].get("status") not in ("ok", None) and isinstance(backtest[m], dict)
        ]
        self.event_logger.emit(
            event_name="forecast.sku.selection.completed",
            module="forecasting",
            level="WARN" if status != "ok" else "INFO",
            status=status,
            entity_type="sku",
            entity_id=sku,
            metrics={
                "mase": result.get("mase"),
                "bias": result.get("bias"),
                "wmape": winner_wmape,
                "rmsse": winner_rmsse,
                "n_candidates": len(candidate_models),
                "n_failed": len(failed_models) if failed_models else None,
            },
            result={
                "model": winner,
                "ensemble_models": ensemble_models,
                "failed_models": failed_models if failed_models else None,
                "top_models": _top_backtest_models(backtest),
                "granularity": result.get("granularity"),
                "h": result.get("h"),
            },
        )

    def dataset_overview(self) -> dict:
        catalog = self.repository.load_table("product_catalog")
        inventory = self.repository.load_table("inventory_snapshot")
        manifest = self.repository.load_manifest()
        central_location = self.central_location()
        classification_config = self.classification_config()

        return {
            "profile": manifest.get("profile"),
            "currency": manifest.get("currency", self.currency_code()),
            "sku_count": int(catalog["sku"].nunique()),
            "location_count": int(inventory["location"].nunique()),
            "central_location": central_location,
            "classification_scope": classification_config["scope"],
            "classification_default_granularity": classification_config["default_granularity"],
            "date_range": {
                "start": inventory["snapshot_date"].min().date().isoformat(),
                "end": inventory["snapshot_date"].max().date().isoformat(),
            },
            "table_rows": {
                table_name: int(len(self.repository.load_table(table_name)))
                for table_name in self.repository.available_tables()
            },
        }

    def currency_code(self) -> str:
        manifest = self.repository.load_manifest()
        manifest_currency = manifest.get("currency")
        if manifest_currency:
            return str(manifest_currency)

        purchase_orders = self.repository.load_table("purchase_orders")
        if "currency" in purchase_orders.columns:
            currencies = [
                currency
                for currency in purchase_orders["currency"].dropna().astype(str).unique().tolist()
                if currency
            ]
            if len(currencies) == 1:
                return currencies[0]

        return "CLP"

    def dataset_health(self) -> dict:
        return basic_health_report(self.repository)

    def classification_config(self) -> dict:
        manifest = self.repository.load_manifest()
        config = manifest.get("classification", {})
        return {
            "scope": str(config.get("scope", OFFICIAL_CLASSIFICATION_SCOPE)),
            "default_granularity": str(config.get("default_granularity", OFFICIAL_CLASSIFICATION_GRANULARITY)),
        }

    def official_classification_granularity(self) -> str:
        return self.classification_config()["default_granularity"]

    def classification_scope(self) -> str:
        return self.classification_config()["scope"]

    def location_model(self) -> dict:
        manifest = self.repository.load_manifest()
        location_model = manifest.get("location_model", {})
        manifest_central = location_model.get("central_location") or manifest.get("central_location")

        inventory = self.repository.load_table("inventory_snapshot")
        inventory_locations = sorted(inventory["location"].dropna().unique().tolist())

        branch_locations = location_model.get("branch_locations")
        if branch_locations is None:
            branch_locations = [
                location
                for location in inventory_locations
                if location and location != manifest_central
            ]

        all_locations = location_model.get("all_locations")
        if all_locations is None:
            all_locations = list(branch_locations)
            if manifest_central and manifest_central not in all_locations:
                all_locations.append(manifest_central)
            all_locations.extend([
                location
                for location in inventory_locations
                if location not in all_locations
            ])

        return {
            "all_locations": list(all_locations),
            "branch_locations": list(branch_locations),
            "central_location": manifest_central,
            "central_supply_mode": bool(location_model.get("central_supply_mode", manifest.get("central_supply_mode", False))),
            "central_node_sales_mode": bool(location_model.get("central_node_sales_mode", False)),
        }

    def central_location(self) -> str | None:
        manifest_central = self.location_model()["central_location"]
        if manifest_central:
            return manifest_central

        purchase_orders = self.repository.load_table("purchase_orders")
        purchase_destinations = [
            location
            for location in purchase_orders["destination_location"].dropna().unique().tolist()
            if location
        ]
        if len(purchase_destinations) == 1:
            return purchase_destinations[0]

        transfers = self.repository.load_table("internal_transfers")
        transfer_sources = [
            location
            for location in transfers["source_location"].dropna().unique().tolist()
            if location
        ]
        if len(transfer_sources) == 1:
            return transfer_sources[0]

        return None

    def list_categories(self) -> list[str]:
        catalog = self.repository.load_table("product_catalog")
        return sorted(catalog["category"].dropna().unique().tolist())

    def list_suppliers(self) -> list[str]:
        catalog = self.repository.load_table("product_catalog")
        return sorted(catalog["supplier"].dropna().unique().tolist())

    def list_skus(
        self,
        search: str | None = None,
        category: str | None = None,
        supplier: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        catalog = self.repository.load_table("product_catalog")
        if search:
            search_mask = (
                catalog["sku"].str.contains(search, case=False, na=False)
                | catalog["name"].str.contains(search, case=False, na=False)
                | catalog["category"].str.contains(search, case=False, na=False)
                | catalog["supplier"].str.contains(search, case=False, na=False)
            )
            catalog = catalog.loc[search_mask]
        if category:
            catalog = catalog.loc[catalog["category"] == category]
        if supplier:
            catalog = catalog.loc[catalog["supplier"] == supplier]

        catalog = catalog.sort_values(["sku"]).reset_index(drop=True)
        if limit is not None:
            catalog = catalog.head(limit)
        return catalog.to_dict(orient="records")

    def list_locations(self) -> list[str]:
        return self.location_model()["all_locations"]

    def list_sku_locations(self, sku: str) -> list[str]:
        inventory = self.repository.load_table("inventory_snapshot")
        filtered = inventory.loc[inventory["sku"] == sku, "location"].dropna().unique().tolist()
        return sorted(filtered)

    def sku_summary(self, sku: str) -> dict | None:
        catalog = self.repository.load_table("product_catalog")
        sku_catalog = catalog.loc[catalog["sku"] == sku]
        if sku_catalog.empty:
            return None

        transactions = self.repository.load_table("transactions")
        inventory = self.repository.load_table("inventory_snapshot")
        receipts = self.repository.load_table("purchase_receipts")
        transfers = self.repository.load_table("internal_transfers")

        sku_transactions = transactions.loc[transactions["sku"] == sku]
        sku_inventory = inventory.loc[inventory["sku"] == sku]
        sku_receipts = receipts.loc[receipts["sku"] == sku]
        sku_transfers = transfers.loc[transfers["sku"] == sku]
        central_location = self.central_location()

        latest_snapshot_date = sku_inventory["snapshot_date"].max()
        latest_snapshot = sku_inventory.loc[sku_inventory["snapshot_date"] == latest_snapshot_date]
        if central_location:
            central_snapshot = latest_snapshot.loc[latest_snapshot["location"] == central_location]
        else:
            central_snapshot = latest_snapshot.iloc[0:0]

        return {
            "sku": sku,
            "catalog": sku_catalog.iloc[0].to_dict(),
            "central_location": central_location,
            "active_locations": int(sku_inventory["location"].nunique()),
            "sales_qty_total": int(sku_transactions["quantity"].sum()) if not sku_transactions.empty else 0,
            "sales_amount_total": float(sku_transactions["total_amount"].sum()) if not sku_transactions.empty else 0.0,
            "purchase_receipt_qty_total": int(sku_receipts["received_qty"].sum()) if not sku_receipts.empty else 0,
            "transfer_qty_total": int(sku_transfers["transfer_qty"].sum()) if not sku_transfers.empty else 0,
            "last_on_hand_total": int(latest_snapshot["on_hand_qty"].sum()) if not latest_snapshot.empty else 0,
            "last_on_order_total": int(latest_snapshot["on_order_qty"].sum()) if not latest_snapshot.empty else 0,
            "central_on_hand": int(central_snapshot["on_hand_qty"].sum()) if not central_snapshot.empty else 0,
            "central_on_order": int(central_snapshot["on_order_qty"].sum()) if not central_snapshot.empty else 0,
        }

    def sku_timeseries(self, sku: str, location: str | None = None) -> pd.DataFrame:
        inventory = self.repository.load_table("inventory_snapshot")
        transactions = self.repository.load_table("transactions")
        receipts = self.repository.load_table("purchase_receipts")
        transfers = self.repository.load_table("internal_transfers")

        inventory = inventory.loc[inventory["sku"] == sku].copy()
        transactions = transactions.loc[transactions["sku"] == sku].copy()
        receipts = receipts.loc[receipts["sku"] == sku].copy()
        transfers = transfers.loc[transfers["sku"] == sku].copy()

        if location:
            inventory = inventory.loc[inventory["location"] == location]
            transactions = transactions.loc[transactions["location"] == location]
            receipts = receipts.loc[receipts["location"] == location]
            transfers_in = transfers.loc[transfers["destination_location"] == location]
            transfers_out = transfers.loc[transfers["source_location"] == location]
        else:
            transfers_in = transfers
            transfers_out = transfers

        if inventory.empty:
            return pd.DataFrame(
                columns=[
                    "date",
                    "sales_qty",
                    "sales_amount",
                    "purchase_receipt_qty",
                    "transfer_in_qty",
                    "transfer_out_qty",
                    "on_hand_qty",
                    "on_order_qty",
                ]
            )

        daily_inventory = (
            inventory.groupby("snapshot_date", as_index=False)[["on_hand_qty", "on_order_qty"]]
            .sum()
            .rename(columns={"snapshot_date": "date"})
        )
        daily_sales = (
            transactions.groupby("date", as_index=False)[["quantity", "total_amount"]]
            .sum()
            .rename(columns={"quantity": "sales_qty", "total_amount": "sales_amount"})
        )
        daily_receipts = (
            receipts.groupby("receipt_date", as_index=False)[["received_qty"]]
            .sum()
            .rename(columns={"receipt_date": "date", "received_qty": "purchase_receipt_qty"})
        )

        transfers_in = transfers_in.dropna(subset=["receipt_date"])
        daily_transfers_in = (
            transfers_in.groupby("receipt_date", as_index=False)[["transfer_qty"]]
            .sum()
            .rename(columns={"receipt_date": "date", "transfer_qty": "transfer_in_qty"})
        )
        daily_transfers_out = (
            transfers_out.groupby("ship_date", as_index=False)[["transfer_qty"]]
            .sum()
            .rename(columns={"ship_date": "date", "transfer_qty": "transfer_out_qty"})
        )

        dataframe = daily_inventory.merge(daily_sales, on="date", how="left")
        dataframe = dataframe.merge(daily_receipts, on="date", how="left")
        dataframe = dataframe.merge(daily_transfers_in, on="date", how="left")
        dataframe = dataframe.merge(daily_transfers_out, on="date", how="left")

        numeric_columns = [
            "sales_qty",
            "sales_amount",
            "purchase_receipt_qty",
            "transfer_in_qty",
            "transfer_out_qty",
            "on_hand_qty",
            "on_order_qty",
        ]
        dataframe[numeric_columns] = dataframe[numeric_columns].fillna(0)
        dataframe[numeric_columns] = dataframe[numeric_columns].astype(
            {
                "sales_qty": int,
                "purchase_receipt_qty": int,
                "transfer_in_qty": int,
                "transfer_out_qty": int,
                "on_hand_qty": int,
                "on_order_qty": int,
            }
        )
        dataframe["sales_amount"] = dataframe["sales_amount"].astype(float)
        return dataframe.sort_values("date").reset_index(drop=True)

    def purchase_receipts_for_sku(self, sku: str) -> pd.DataFrame:
        receipts = self.repository.load_table("purchase_receipts")
        columns = ["receipt_date", "location", "po_id", "po_line_id", "received_qty", "unit_cost", "total_cost"]
        return receipts.loc[receipts["sku"] == sku, columns].sort_values("receipt_date").reset_index(drop=True)

    def internal_transfers_for_sku(self, sku: str, location: str | None = None) -> pd.DataFrame:
        transfers = self.repository.load_table("internal_transfers")
        filtered = transfers.loc[transfers["sku"] == sku]
        if location:
            filtered = filtered.loc[
                (filtered["source_location"] == location) | (filtered["destination_location"] == location)
            ]
        columns = [
            "ship_date",
            "expected_receipt_date",
            "receipt_date",
            "source_location",
            "destination_location",
            "transfer_qty",
            "transfer_status",
        ]
        return filtered.loc[:, columns].sort_values("ship_date").reset_index(drop=True)

    # ------------------------------------------------------------------
    # Clasificacion de demanda (Fase 1)
    # ------------------------------------------------------------------

    def classify_catalog(self, granularity: str | None = None) -> pd.DataFrame:
        """Clasifica todos los SKUs del catalogo.

        La clasificacion oficial del repo se calcula a nivel SKU agregado de red.
        Si no se fuerza granularidad, usa el default oficial definido en el manifest.
        """
        transactions = self.repository.load_table("transactions")
        catalog = self.repository.load_table("product_catalog")
        inventory = self.repository.load_table("inventory_snapshot")

        if granularity is None:
            granularity = self.official_classification_granularity()

        with self.event_logger.span(
            "classification.catalog",
            module="classification",
            entity_type="catalog",
            entity_id="all",
            params={"granularity": granularity},
        ) as span:
            classification_df = classify_all_skus(transactions, catalog, granularity=granularity)
            classification_df["classification_scope"] = self.classification_scope()
            classification_df = self._augment_catalog_classification_with_censoring(
                classification_df=classification_df,
                transactions=transactions,
                inventory=inventory,
                granularity=granularity,
            )
            span.set_metrics(
                n_skus=int(len(classification_df)),
                seasonal_pct=round(float(classification_df["is_seasonal"].mean()), 4) if not classification_df.empty else 0.0,
                censored_sku_pct=round(float(classification_df["has_censored_demand"].mean()), 4) if not classification_df.empty else 0.0,
            )
            span.set_result(
                granularity=granularity,
                classification_scope=self.classification_scope(),
                sb_distribution=_counts_dict(classification_df["sb_class"]) if "sb_class" in classification_df else {},
                abc_distribution=_counts_dict(classification_df["abc_class"]) if "abc_class" in classification_df else {},
            )
            return classification_df

    def classify_single_sku(
        self,
        sku: str,
        location: str | None = None,
        granularity: str | None = None,
    ) -> dict | None:
        """Clasifica un SKU individual.

        Si no se provee `location`, usa la clasificacion oficial agregada de red.
        """
        transactions = self.repository.load_table("transactions")
        inventory = self.repository.load_table("inventory_snapshot")

        sku_tx = transactions[transactions["sku"] == sku]
        sku_inv = inventory[inventory["sku"] == sku]

        if location:
            sku_tx = sku_tx[sku_tx["location"] == location]
            sku_inv = sku_inv[sku_inv["location"] == location]

        if sku_tx.empty:
            granularity_out = granularity or self.official_classification_granularity()
            result = {
                "sku": sku,
                "sb_class": "inactive",
                "abc_class": None,
                "xyz_class": None,
                "abc_xyz": None,
                "is_seasonal": False,
                "has_trend": False,
                "quality_score": 0.0,
                "quality_flags": ["sin_transacciones"],
                "granularity": granularity_out,
                "classification_scope": self.classification_scope() if location is None else "location",
            }
            self.event_logger.emit(
                event_name="classification.sku.completed",
                module="classification",
                level="WARN",
                status="inactive",
                entity_type="sku",
                entity_id=sku,
                params={"location": location, "granularity": granularity_out},
                result={
                    "sb_class": result["sb_class"],
                    "abc_class": result["abc_class"],
                    "xyz_class": result["xyz_class"],
                    "classification_scope": result["classification_scope"],
                },
                metrics={"quality_score": result["quality_score"]},
            )
            return result

        if granularity is None:
            granularity = self.official_classification_granularity() if location is None else select_granularity(sku_tx)

        with self.event_logger.span(
            "classification.sku",
            module="classification",
            entity_type="sku",
            entity_id=sku,
            params={"location": location, "granularity": granularity},
        ) as span:
            profile = classify_sku(sku_tx, sku=sku, granularity=granularity)
            profile["classification_scope"] = self.classification_scope() if location is None else "location"
            profile = self._augment_profile_with_censoring(profile, sku_tx, sku_inv, granularity)
            if profile.get("sb_class") == "inactive":
                span.set_status("inactive")
            span.set_metrics(
                quality_score=profile.get("quality_score"),
                quality_score_base=profile.get("quality_score_base"),
                censored_pct=profile.get("censored_pct"),
                censored_demand_pct=profile.get("censored_demand_pct"),
            )
            span.set_result(
                sb_class=profile.get("sb_class"),
                abc_class=profile.get("abc_class"),
                xyz_class=profile.get("xyz_class"),
                abc_xyz=profile.get("abc_xyz"),
                is_seasonal=profile.get("is_seasonal"),
                has_trend=profile.get("has_trend"),
                classification_scope=profile.get("classification_scope"),
            )
            return profile

    def sku_demand_series(
        self,
        sku: str,
        location: str | None = None,
        granularity: str | None = None,
    ) -> pd.DataFrame:
        """Retorna la serie temporal de demanda preparada (con ceros rellenados)."""
        transactions = self.repository.load_table("transactions")
        sku_tx = transactions[transactions["sku"] == sku]

        if location:
            sku_tx = sku_tx[sku_tx["location"] == location]

        if granularity is None:
            granularity = select_granularity(sku_tx)

        return prepare_demand_series(sku_tx, granularity=granularity)

    def sku_outlier_series(
        self,
        sku: str,
        location: str | None = None,
        granularity: str | None = None,
        method: str = "iqr",
    ) -> pd.DataFrame:
        """Retorna la serie de demanda con columna de outliers marcados."""
        series_df = self.sku_demand_series(sku, location=location, granularity=granularity)

        if series_df.empty:
            series_df["is_outlier"] = pd.Series(dtype=bool)
            return series_df

        outlier_mask = detect_outliers(series_df["demand"], method=method)
        series_df["is_outlier"] = outlier_mask.values
        return series_df

    def sku_acf(
        self,
        sku: str,
        location: str | None = None,
        granularity: str | None = None,
        max_lags: int = 40,
    ) -> dict:
        """Calcula la funcion de autocorrelacion para un SKU."""
        series_df = self.sku_demand_series(sku, location=location, granularity=granularity)

        if series_df.empty:
            return {"lags": [], "acf": [], "confidence_bound": 0.0}

        acf_values = compute_acf(series_df["demand"], max_lags=max_lags)
        n = len(series_df)
        confidence_bound = 1.96 / (n ** 0.5)

        return {
            "lags": list(range(len(acf_values))),
            "acf": [round(float(v), 4) for v in acf_values],
            "confidence_bound": round(confidence_bound, 4),
        }

    # ------------------------------------------------------------------
    # Preprocesamiento para modelado (Fase 2a / 2b)
    # ------------------------------------------------------------------

    def sku_clean_series(
        self,
        sku: str,
        location: str | None = None,
        granularity: str | None = None,
        outlier_method: str = "iqr",
        treat_strategy: str = "winsorize",
    ) -> pd.DataFrame:
        """Devuelve la serie de demanda con outliers tratados, lista para modelado."""
        series_df = self.sku_demand_series(sku, location=location, granularity=granularity)
        if series_df.empty:
            series_df["demand_clean"] = pd.Series(dtype=float)
            series_df["is_outlier"] = pd.Series(dtype=bool)
            return series_df

        outlier_mask = detect_outliers(series_df["demand"], method=outlier_method)
        clean = treat_outliers(series_df["demand"], outlier_mask, strategy=treat_strategy)
        series_df["is_outlier"] = outlier_mask.values
        series_df["demand_clean"] = clean.values
        return series_df

    def sku_censored_mask(
        self,
        sku: str,
        location: str | None = None,
        granularity: str | None = None,
        stockout_threshold: float = 0.0,
    ) -> dict:
        """Identifica periodos de demanda censurada para un SKU."""
        transactions = self.repository.load_table("transactions")
        inventory = self.repository.load_table("inventory_snapshot")

        sku_tx = transactions[transactions["sku"] == sku]
        sku_inv = inventory[inventory["sku"] == sku]

        if location:
            sku_tx = sku_tx[sku_tx["location"] == location]
            sku_inv = sku_inv[sku_inv["location"] == location]

        if granularity is None:
            granularity = select_granularity(sku_tx)

        demand_df, censored, summary = self._compute_censoring_info(
            sku_tx=sku_tx,
            sku_inv=sku_inv,
            granularity=granularity,
            stockout_threshold=stockout_threshold,
        )

        result_df = demand_df.copy()
        result_df["is_censored"] = censored.values
        result_df["is_stockout_no_sale"] = result_df["is_censored"] & (result_df["demand"] == 0)

        stockout_no_sale_periods = int(result_df["is_stockout_no_sale"].sum())
        total_periods = len(result_df)
        summary = {
            **summary,
            "stockout_no_sale_periods": stockout_no_sale_periods,
            "stockout_no_sale_pct": round(stockout_no_sale_periods / total_periods, 4) if total_periods > 0 else 0.0,
        }

        return {"series": result_df, "censored_mask": censored, "summary": summary}

    def sku_forecast(
        self,
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
        """Selecciona el mejor modelo de forecast para un SKU y genera el pronostico.

        Usa ``demand_clean`` (serie con outliers tratados) como input para
        el backtest y el forecast final. El modelo ganador se elige por menor
        MASE en el horse-race expanding-window.

        Parameters
        ----------
        sku : str
            Codigo del SKU.
        location : str, optional
            Si se provee, filtra la serie a esa sucursal.
            Si es None, usa el agregado de red (clasificacion oficial).
        granularity : str, optional
            ``"D"``, ``"W"`` o ``"M"``. Si es None, usa la granularidad oficial.
        h : int or None
            Horizonte de pronostico en periodos. Si None (default), se deriva
            automaticamente de ``lead_time_days / days_per_period`` del SKU
            (D20: horizonte dinamico por SKU). Rango: [1, 12] periodos.
        n_windows : int
            Ventanas del backtest (minimo 3 para MASE estable).
        outlier_method : str
            Metodo de deteccion de outliers antes de entrenar (``"iqr"`` por defecto).
        treat_strategy : str
            Estrategia de tratamiento de outliers (``"winsorize"`` por defecto).

        Returns
        -------
        dict
            ``{"status", "model", "mase", "forecast", "backtest", "season_length",
               "granularity", "h"}``

            ``forecast`` es un pd.DataFrame con ``[ds, yhat, yhat_lo80, yhat_hi80]``.
            ``status`` puede ser: ``"ok"``, ``"no_forecast"``, ``"fallback"``, ``"error"``.
        """
        if granularity is None:
            granularity = self.official_classification_granularity()

        with self.event_logger.span(
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
            profile = self.classify_single_sku(sku, location=location, granularity=granularity)
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

            self._log_forecast_profile(sku, location, granularity, profile)

            horizon_resolution = "explicit" if h is not None else "derived"
            lead_time_days: float | None = None
            if h is None:
                try:
                    params = self.sku_inventory_params(sku, abc_class=profile.get("abc_class"))
                    lead_time_days = float(params.get("lead_time_days", 30.0))
                    h = _h_from_lead_time(lead_time_days, granularity)
                except Exception:
                    h = 3
                    horizon_resolution = "fallback_default"
            self._log_forecast_horizon(sku, granularity, int(h), horizon_resolution, lead_time_days)

            clean_df = self.sku_clean_series(
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
            self._log_forecast_series(sku, granularity, model_input, clean_df, treat_strategy)

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

            self._log_forecast_selection(sku, status, result)

            backtest = result.get("backtest", {})
            winner = result.get("model")
            winner_metrics = backtest.get(winner, {}) if winner else {}
            candidate_models = [
                model_name
                for model_name in backtest.keys()
                if isinstance(model_name, str) and not model_name.startswith("__")
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

    def classification_summary(self, granularity: str | None = None) -> dict:
        """Resumen agregado de la clasificacion del catalogo."""
        df = self.classify_catalog(granularity=granularity)

        sb_counts = df["sb_class"].value_counts().to_dict()
        abc_counts = df["abc_class"].value_counts().to_dict()
        xyz_counts = df["xyz_class"].value_counts().to_dict()
        lifecycle_counts = df["lifecycle"].value_counts().to_dict()
        abc_xyz_counts = df["abc_xyz"].value_counts().to_dict()

        abc_xyz_matrix = {}
        for abc in ["A", "B", "C"]:
            abc_xyz_matrix[abc] = {}
            for xyz in ["X", "Y", "Z"]:
                key = f"{abc}{xyz}"
                abc_xyz_matrix[abc][xyz] = int(abc_xyz_counts.get(key, 0))

        return {
            "total_skus": len(df),
            "granularity": df["granularity"].iloc[0] if not df.empty else None,
            "classification_scope": df["classification_scope"].iloc[0] if not df.empty else self.classification_scope(),
            "sb_counts": sb_counts,
            "abc_counts": abc_counts,
            "xyz_counts": xyz_counts,
            "lifecycle_counts": lifecycle_counts,
            "abc_xyz_matrix": abc_xyz_matrix,
            "avg_quality_score": round(float(df["quality_score"].mean()), 3) if not df.empty else 0.0,
            "seasonal_pct": round(float(df["is_seasonal"].mean()), 3) if not df.empty else 0.0,
            "trend_pct": round(float(df["has_trend"].mean()), 3) if not df.empty else 0.0,
            "censored_sku_pct": round(float(df["has_censored_demand"].mean()), 3) if not df.empty else 0.0,
        }

    def _compute_censoring_info(
        self,
        sku_tx: pd.DataFrame,
        sku_inv: pd.DataFrame,
        granularity: str,
        stockout_threshold: float = 0.0,
    ) -> tuple[pd.DataFrame, pd.Series, dict]:
        demand_df = prepare_demand_series(sku_tx, granularity=granularity)
        censored = mark_censored_demand(
            demand_df,
            sku_inv,
            granularity=granularity,
            stockout_threshold=stockout_threshold,
        )
        summary = censored_summary(censored, demand_df)
        return demand_df, censored, summary

    def _compute_censoring_penalty(self, summary: dict) -> float:
        penalty = (0.15 * float(summary.get("censored_pct", 0.0))) + (0.35 * float(summary.get("censored_demand_pct", 0.0)))
        return round(min(0.25, penalty), 3)

    def _augment_profile_with_censoring(
        self,
        profile: dict,
        sku_tx: pd.DataFrame,
        sku_inv: pd.DataFrame,
        granularity: str,
    ) -> dict:
        demand_df, censored, summary = self._compute_censoring_info(sku_tx, sku_inv, granularity=granularity)
        stockout_no_sale_periods = int(((censored.values) & (demand_df["demand"].values == 0)).sum()) if not demand_df.empty else 0
        total_periods = len(demand_df)
        penalty = self._compute_censoring_penalty(summary)

        base_quality = float(profile.get("quality_score", 0.0))
        quality_flags = profile.get("quality_flags", [])
        if not isinstance(quality_flags, list):
            quality_flags = []

        if summary["censored_periods"] > 0:
            quality_flags.append(
                f"demanda_censurada ({summary['censored_periods']}/{summary['total_periods']} periodos; {summary['censored_demand_pct']:.1%} volumen)"
            )
        if stockout_no_sale_periods > 0:
            quality_flags.append(f"sin_venta_por_stockout ({stockout_no_sale_periods} periodos)")

        profile["has_censored_demand"] = bool(summary["censored_periods"] > 0)
        profile["censored_periods"] = int(summary["censored_periods"])
        profile["censored_pct"] = float(summary["censored_pct"])
        profile["censored_demand"] = float(summary["censored_demand"])
        profile["censored_demand_pct"] = float(summary["censored_demand_pct"])
        profile["stockout_no_sale_periods"] = stockout_no_sale_periods
        profile["stockout_no_sale_pct"] = round(stockout_no_sale_periods / total_periods, 4) if total_periods > 0 else 0.0
        profile["quality_score_base"] = round(base_quality, 3)
        profile["censoring_penalty"] = penalty
        profile["quality_score"] = round(max(0.0, base_quality - penalty), 3)
        profile["quality_flags"] = quality_flags
        return profile

    def _augment_catalog_classification_with_censoring(
        self,
        classification_df: pd.DataFrame,
        transactions: pd.DataFrame,
        inventory: pd.DataFrame,
        granularity: str,
    ) -> pd.DataFrame:
        tx_groups = {sku: frame.copy() for sku, frame in transactions.groupby("sku")}
        inv_groups = {sku: frame.copy() for sku, frame in inventory.groupby("sku")}

        enriched_rows = []
        empty_tx = transactions.iloc[0:0].copy()
        empty_inv = inventory.iloc[0:0].copy()
        for _, row in classification_df.iterrows():
            sku = row["sku"]
            enriched_rows.append(
                self._augment_profile_with_censoring(
                    row.to_dict(),
                    sku_tx=tx_groups.get(sku, empty_tx),
                    sku_inv=inv_groups.get(sku, empty_inv),
                    granularity=granularity,
                )
            )

        return pd.DataFrame(enriched_rows)

    # -----------------------------------------------------------------------
    # Módulo de inventario
    # -----------------------------------------------------------------------

    def service_level_config(self) -> dict:
        """Retorna la política CSL activa para cada clase ABC.

        Lee primero ``manifest["service_level_policy"]`` y aplica los defaults
        del PDF sección 8.4 donde no haya override explícito.

        Returns
        -------
        dict
            ``{"A": float, "B": float, "C": float}`` — CSL objetivo por clase.
        """
        manifest = self.repository.load_manifest()
        policy = manifest.get("service_level_policy")
        return {abc: get_csl_target(abc, policy) for abc in ("A", "B", "C")}

    def sku_inventory_params(self, sku: str, abc_class: str | None = None) -> dict:
        """Retorna los parámetros de inventario para un SKU como dict.

        Parameters
        ----------
        sku : str
            Identificador del SKU.
        abc_class : str or None
            Clase ABC del SKU. Si no se provee, se usa None (review period = default C).

        Returns
        -------
        dict
            Campos: ``sku, lead_time_days, sigma_lt_days, review_period_days,
            carrying_cost_rate, abc_class``.
        """
        catalog = self.repository.load_table("product_catalog")
        row = catalog.loc[catalog["sku"] == sku]
        supplier = row["supplier"].iloc[0] if not row.empty else None
        manifest = self.repository.load_manifest()
        params = get_sku_params(sku, abc_class, supplier, self.repository, manifest)
        return params.to_dict()

    def sku_safety_stock(
        self,
        sku: str,
        abc_class: str | None = None,
        granularity: str | None = None,
        simple_safety_pct: float = 0.5,
    ) -> dict:
        """Calcula safety stock y ROP para un SKU.

        Parameters
        ----------
        sku : str
            Identificador del SKU.
        abc_class : str or None
            Clase ABC del SKU. Si no se provee, se deriva de ``classify_single_sku()``.
        granularity : str or None
            Granularidad de la serie de demanda. Si no se provee, usa la oficial.
        simple_safety_pct : float
            Fracción del LT demand usada como SS para clase C. Default 0.5.

        Returns
        -------
        dict
            Campos de ``SafetyStockResult.to_dict()``:
            ``sku, granularity, mean_demand_daily, sigma_demand_daily,
            safety_stock, reorder_point, coverage_ss_days, ss_method, n_periods``.
        """
        if granularity is None:
            granularity = self.official_classification_granularity()

        if abc_class is None:
            profile = self.classify_single_sku(sku, granularity=granularity)
            abc_class = profile.get("abc_class") if profile else None

        catalog = self.repository.load_table("product_catalog")
        row = catalog.loc[catalog["sku"] == sku]
        supplier = row["supplier"].iloc[0] if not row.empty else None
        manifest = self.repository.load_manifest()

        with self.event_logger.span(
            "inventory.safety_stock",
            module="inventory",
            entity_type="sku",
            entity_id=sku,
            params={
                "abc_class": abc_class,
                "granularity": granularity,
                "simple_safety_pct": simple_safety_pct,
            },
        ) as span:
            params = get_sku_params(sku, abc_class, supplier, self.repository, manifest)
            demand_series = self.sku_demand_series(sku, granularity=granularity)

            result = compute_sku_safety_stock(
                params, demand_series, granularity=granularity, simple_safety_pct=simple_safety_pct
            )
            result_dict = result.to_dict()
            span.set_metrics(
                safety_stock=result_dict.get("safety_stock"),
                reorder_point=result_dict.get("reorder_point"),
                coverage_ss_days=result_dict.get("coverage_ss_days"),
                n_periods=result_dict.get("n_periods"),
            )
            span.set_result(
                ss_method=result_dict.get("ss_method"),
                abc_class=abc_class,
                granularity=granularity,
            )
            return result_dict

    def catalog_health_report(
        self,
        granularity: str | None = None,
        simple_safety_pct: float = 0.5,
    ) -> pd.DataFrame:
        """Diagnóstica el estado de salud de inventario de todo el catálogo activo.

        Ejecuta el pipeline completo de diagnóstico (§11.2 del PDF) para cada
        SKU activo: estado actual → necesidad futura → ratio → clasificación.

        Parameters
        ----------
        granularity : str or None
            Granularidad de la serie de demanda. Si None, usa la oficial del manifest.
        simple_safety_pct : float
            Fracción del LT demand usada como SS para clase C. Default 0.5.

        Returns
        -------
        pd.DataFrame
            Una fila por SKU activo. Columnas: todos los campos de
            ``InventoryDiagnosis`` más ``days_since_last_movement``.
        """
        if granularity is None:
            granularity = self.official_classification_granularity()
        with self.event_logger.span(
            "inventory.health_report",
            module="inventory",
            entity_type="catalog",
            entity_id="all",
            params={"granularity": granularity, "simple_safety_pct": simple_safety_pct},
        ) as span:
            classification_df = self.classify_catalog(granularity=granularity)

            inventory = self.repository.load_table("inventory_snapshot")
            latest_date = inventory["snapshot_date"].max()
            latest_inv = inventory[inventory["snapshot_date"] == latest_date]

            stock_by_sku = (
                latest_inv.groupby("sku")[["on_hand_qty", "on_order_qty"]]
                .sum()
                .rename(columns={"on_hand_qty": "on_hand", "on_order_qty": "on_order"})
            )

            transactions = self.repository.load_table("transactions")
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

            catalog = self.repository.load_table("product_catalog")
            manifest = self.repository.load_manifest()

            _CAT_COLS = ["sku", "category", "subcategory", "supplier", "brand", "base_price", "cost"]
            _available_cat_cols = [c for c in _CAT_COLS if c in catalog.columns]
            catalog_index = catalog[_available_cat_cols].set_index("sku")

            diagnoses: list[dict] = []
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

                params = get_sku_params(sku, abc_class, supplier, self.repository, manifest)

                sku_tx_raw = tx_by_sku.get(sku, pd.DataFrame(columns=transactions.columns))
                demand_series = prepare_demand_series(sku_tx_raw, granularity=granularity)
                ss_result = compute_sku_safety_stock(
                    params, demand_series, granularity=granularity, simple_safety_pct=simple_safety_pct
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
                total_excess_capital=float(report_df["excess_capital"].sum()) if "excess_capital" in report_df else 0.0,
                total_stockout_capital=float(report_df["stockout_capital"].sum()) if "stockout_capital" in report_df else 0.0,
            )
            span.set_result(
                health_status_distribution=_counts_dict(report_df["health_status"]) if "health_status" in report_df else {},
                alert_level_distribution=_counts_dict(report_df["alert_level"]) if "alert_level" in report_df else {},
            )
            return report_df

    # ---------------------------------------------------------------------------
    # Fase 5 — Motor de Decisión de Reposición
    # ---------------------------------------------------------------------------

    def purchase_plan(
        self,
        granularity: str | None = None,
        include_equilibrio: bool = False,
        include_sobrestock: bool = True,
        limit: int = 500,
        simple_safety_pct: float = 0.5,
    ) -> list[dict]:
        """Genera el plan de reposición priorizado para el catálogo.

        Ejecuta el diagnóstico completo de inventario y convierte cada fila en
        una PurchaseRecommendation: canal de compra para substock/quiebre,
        canal de exceso para sobrestock.

        Parameters
        ----------
        granularity : str or None
            Granularidad de la serie de demanda. None = oficial del manifest.
        include_equilibrio : bool
            Si True, incluye SKUs en equilibrio (urgency_score bajo). Default False.
        include_sobrestock : bool
            Si True, incluye SKUs en sobrestock con canal de exceso. Default True.
        limit : int
            Número máximo de recomendaciones a retornar. Default 500.
        simple_safety_pct : float
            Fracción del LT demand para SS de clase C. Default 0.5.

        Returns
        -------
        list[dict]
            Lista de PurchaseRecommendation.to_dict(), ordenada por urgency_score desc.
        """
        with self.event_logger.span(
            "purchase.plan",
            module="purchase",
            entity_type="catalog",
            entity_id="all",
            params={
                "granularity": granularity,
                "include_equilibrio": include_equilibrio,
                "include_sobrestock": include_sobrestock,
                "limit": limit,
                "simple_safety_pct": simple_safety_pct,
            },
        ) as span:
            health_df = self.catalog_health_report(
                granularity=granularity,
                simple_safety_pct=simple_safety_pct,
            )
            if health_df.empty:
                span.set_status("empty")
                span.set_result(n_items=0, n_actionable=0)
                return []

            if granularity is None:
                granularity = self.official_classification_granularity()

            catalog = self.repository.load_table("product_catalog")
            manifest = self.repository.load_manifest()

            params_map: dict[str, object] = {}
            for _, row in health_df.iterrows():
                sku = row["sku"]
                abc_class = row.get("abc_class")
                supplier = row.get("supplier")
                params_map[sku] = get_sku_params(sku, abc_class, supplier, self.repository, manifest)

            health_rows = health_df.to_dict(orient="records")

            recommendations = generate_purchase_plan(
                health_rows=health_rows,
                catalog_df=catalog,
                params_map=params_map,
                manifest_config=manifest,
                include_equilibrio=include_equilibrio,
                include_sobrestock=include_sobrestock,
            )

            recommendation_dicts = [r.to_dict() for r in recommendations[:limit]]
            actionable = sum(1 for item in recommendation_dicts if float(item.get("final_qty") or 0.0) > 0.0)
            span.set_metrics(n_items=len(recommendation_dicts), n_actionable=actionable)
            span.set_result(
                health_status_distribution=_counts_dict(pd.Series([item.get("health_status") for item in recommendation_dicts])),
                max_urgency_score=max((float(item.get("urgency_score") or 0.0) for item in recommendation_dicts), default=0.0),
            )
            return recommendation_dicts

    def purchase_plan_by_supplier(
        self,
        granularity: str | None = None,
        simple_safety_pct: float = 0.5,
    ) -> list[dict]:
        """Agrupa el plan de compra por proveedor.

        Solo incluye SKUs con final_qty > 0 (canal de compra activo).
        Los SKUs de sobrestock se excluyen de las propuestas de proveedor.

        Parameters
        ----------
        granularity : str or None
            Granularidad de la serie de demanda. None = oficial del manifest.
        simple_safety_pct : float
            Fracción del LT demand para SS de clase C. Default 0.5.

        Returns
        -------
        list[dict]
            Lista de PurchaseProposal.to_dict(), ordenada por max_urgency_score desc.
        """
        with self.event_logger.span(
            "purchase.plan_by_supplier",
            module="purchase",
            entity_type="catalog",
            entity_id="all",
            params={"granularity": granularity, "simple_safety_pct": simple_safety_pct},
        ) as span:
            health_df = self.catalog_health_report(
                granularity=granularity,
                simple_safety_pct=simple_safety_pct,
            )
            if health_df.empty:
                span.set_status("empty")
                span.set_result(n_suppliers=0, n_items=0)
                return []

            if granularity is None:
                granularity = self.official_classification_granularity()

            catalog = self.repository.load_table("product_catalog")
            manifest = self.repository.load_manifest()

            params_map: dict[str, object] = {}
            for _, row in health_df.iterrows():
                sku = row["sku"]
                abc_class = row.get("abc_class")
                supplier = row.get("supplier")
                params_map[sku] = get_sku_params(sku, abc_class, supplier, self.repository, manifest)

            health_rows = health_df.to_dict(orient="records")
            recommendations = generate_purchase_plan(
                health_rows=health_rows,
                catalog_df=catalog,
                params_map=params_map,
                manifest_config=manifest,
                include_equilibrio=False,
                include_sobrestock=False,
            )

            proposals = aggregate_by_supplier(recommendations)
            proposal_dicts = [p.to_dict() for p in proposals]
            span.set_metrics(n_suppliers=len(proposal_dicts), n_items=len(recommendations))
            span.set_result(
                supplier_count=len(proposal_dicts),
                max_urgency_score=max((float(item.get("max_urgency_score") or 0.0) for item in proposal_dicts), default=0.0),
            )
            return proposal_dicts

    def purchase_plan_summary(
        self,
        granularity: str | None = None,
        simple_safety_pct: float = 0.5,
    ) -> dict:
        """KPIs ejecutivos del plan de reposición completo.

        Incluye todos los estados (quiebre, substock, equilibrio, sobrestock, dead_stock)
        para dar una visión completa de la salud del catálogo.

        Returns
        -------
        dict
            KPIs: sku_quiebre, sku_substock, sku_equilibrio, sku_sobrestock,
            sku_dead_stock, sku_to_order, total_units_to_order, total_cost_estimate,
            total_excess_units, total_excess_cost, supplier_count.
        """
        with self.event_logger.span(
            "purchase.plan_summary",
            module="purchase",
            entity_type="catalog",
            entity_id="all",
            params={"granularity": granularity, "simple_safety_pct": simple_safety_pct},
        ) as span:
            health_df = self.catalog_health_report(
                granularity=granularity,
                simple_safety_pct=simple_safety_pct,
            )
            if health_df.empty:
                span.set_status("empty")
                span.set_result(summary={})
                return {}

            if granularity is None:
                granularity = self.official_classification_granularity()

            catalog = self.repository.load_table("product_catalog")
            manifest = self.repository.load_manifest()

            params_map: dict[str, object] = {}
            for _, row in health_df.iterrows():
                sku = row["sku"]
                abc_class = row.get("abc_class")
                supplier = row.get("supplier")
                params_map[sku] = get_sku_params(sku, abc_class, supplier, self.repository, manifest)

            health_rows = health_df.to_dict(orient="records")
            recommendations = generate_purchase_plan(
                health_rows=health_rows,
                catalog_df=catalog,
                params_map=params_map,
                manifest_config=manifest,
                include_equilibrio=True,
                include_sobrestock=True,
            )

            summary = purchase_plan_summary(recommendations)
            span.set_metrics(
                sku_to_order=summary.get("sku_to_order"),
                total_units_to_order=summary.get("total_units_to_order"),
                total_cost_estimate=summary.get("total_cost_estimate"),
            )
            span.set_result(summary=summary)
            return summary

    def sku_purchase_recommendation(
        self,
        sku: str,
        abc_class: str | None = None,
        granularity: str | None = None,
        simple_safety_pct: float = 0.5,
    ) -> dict | None:
        """Genera la recomendación de reposición para un SKU individual.

        Más rápido que correr el catálogo completo — útil para el detalle de SKU.

        Parameters
        ----------
        sku : str
            Identificador del SKU.
        abc_class : str or None
            Clase ABC. Si None, se deriva desde la clasificación.
        granularity : str or None
            Granularidad de demanda. None = oficial.
        simple_safety_pct : float
            Fracción del LT demand para SS de clase C. Default 0.5.

        Returns
        -------
        dict or None
            PurchaseRecommendation.to_dict() o None si el SKU no existe.
        """
        if granularity is None:
            granularity = self.official_classification_granularity()

        summary = self.sku_summary(sku)
        if summary is None:
            return None

        if abc_class is None:
            profile = self.classify_single_sku(sku, granularity=granularity)
            abc_class = profile.get("abc_class") if profile else None

        catalog = self.repository.load_table("product_catalog")
        manifest = self.repository.load_manifest()
        cat_row = catalog.loc[catalog["sku"] == sku]
        supplier = cat_row["supplier"].iloc[0] if not cat_row.empty else None

        with self.event_logger.span(
            "purchase.recommendation",
            module="purchase",
            entity_type="sku",
            entity_id=sku,
            params={
                "abc_class": abc_class,
                "granularity": granularity,
                "simple_safety_pct": simple_safety_pct,
            },
        ) as span:
            params = get_sku_params(sku, abc_class, supplier, self.repository, manifest)
            demand_series = self.sku_demand_series(sku, granularity=granularity)
            ss_result = compute_sku_safety_stock(
                params, demand_series, granularity=granularity, simple_safety_pct=simple_safety_pct
            )

            inventory = self.repository.load_table("inventory_snapshot")
            latest_date = inventory["snapshot_date"].max()
            sku_inv = inventory[(inventory["snapshot_date"] == latest_date) & (inventory["sku"] == sku)]
            on_hand = float(sku_inv["on_hand_qty"].sum()) if not sku_inv.empty else 0.0
            on_order = float(sku_inv["on_order_qty"].sum()) if not sku_inv.empty else 0.0

            transactions = self.repository.load_table("transactions")
            sku_tx = transactions[transactions["sku"] == sku]
            if not sku_tx.empty and "date" in sku_tx.columns:
                last_mv = pd.to_datetime(sku_tx["date"]).max()
                days_since = int((pd.to_datetime(transactions["date"]).max() - last_mv).days)
            else:
                days_since = 9999

            from planning_core.inventory.diagnostics import diagnose_sku  # noqa: PLC0415
            diagnosis = diagnose_sku(
                sku=sku,
                on_hand=on_hand,
                on_order=on_order,
                ss_result=ss_result,
                params=params,
                abc_class=abc_class,
                days_since_last_movement=days_since,
            )

            catalog_row = cat_row.iloc[0] if not cat_row.empty else None
            rec = build_purchase_recommendation(
                sku=sku,
                diagnosis=diagnosis,
                params=params,
                catalog_row=catalog_row,
                manifest_config=manifest,
            )
            rec_dict = rec.to_dict()
            span.set_metrics(
                urgency_score=rec_dict.get("urgency_score"),
                final_qty=rec_dict.get("final_qty"),
                stockout_probability=rec_dict.get("stockout_probability"),
                stock_efectivo=on_hand + on_order,
            )
            span.set_result(
                health_status=rec_dict.get("health_status"),
                alert_level=rec_dict.get("alert_level"),
                action=rec_dict.get("action"),
                order_deadline=rec_dict.get("order_deadline"),
            )
            return rec_dict
