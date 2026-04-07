from __future__ import annotations

from pathlib import Path
from typing import IO

import pandas as pd

from planning_core.classification import (
    compute_acf,
    detect_outliers,
    prepare_demand_series,
    select_granularity,
    treat_outliers,
)
from planning_core.inventory.params import get_sku_params
from planning_core.inventory.service_level import get_csl_target
from planning_core.repository import CanonicalRepository
from planning_core.system_log import EventLogger
from planning_core.validation import basic_health_report
import planning_core.pipelines.classification as _classification_pipeline
import planning_core.pipelines.forecast as _forecast_pipeline
import planning_core.pipelines.inventory as _inventory_pipeline
import planning_core.pipelines.purchase as _purchase_pipeline


OFFICIAL_CLASSIFICATION_SCOPE = "network_aggregate"
OFFICIAL_CLASSIFICATION_GRANULARITY = "M"


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

    def classify_catalog(
        self,
        granularity: str | None = None,
        _skip_store: bool = False,
    ) -> pd.DataFrame:
        """Clasifica todos los SKUs del catalogo.

        Intenta cargar primero el ``ClassificationStore`` desde
        ``output/derived/``. Si está fresco, retorna instantáneamente sin
        recalcular (resolve D14). Si el store está ausente, stale, o se pasa
        ``_skip_store=True``, ejecuta el pipeline completo.

        Parameters
        ----------
        granularity : str, optional
            Granularidad temporal. Si None, usa el default oficial del manifest.
        _skip_store : bool
            Si True, ignora el store y fuerza recálculo completo. Usado
            internamente por ``run_catalog_classification`` para materializar
            un artefacto fresco.
        """
        from planning_core.classification.store import ClassificationStore

        if granularity is None:
            granularity = self.official_classification_granularity()

        # Intentar cargar el artefacto persistido (D14)
        if not _skip_store:
            _output_dir = Path("output") / "derived"
            store = ClassificationStore.load(_output_dir, granularity)
            if store is not None and not store.is_stale():
                self.event_logger.emit(
                    event_name="classification.catalog.cache_hit",
                    module="classification",
                    level="INFO",
                    status="ok",
                    entity_type="catalog",
                    entity_id="all",
                    params={"granularity": granularity},
                    metrics={"n_skus": len(store)},
                    result={"source": "classification_store"},
                )
                return store.all_skus_df()

        return _classification_pipeline.run_catalog_classification_full(self, granularity)

    def classify_single_sku(
        self,
        sku: str,
        location: str | None = None,
        granularity: str | None = None,
    ) -> dict | None:
        """Clasifica un SKU individual con logging y enriquecimiento por censura.

        Si no se provee `location`, usa la clasificacion oficial agregada de red.
        """
        return _classification_pipeline.run_sku_classification(
            self, sku, location=location, granularity=granularity,
        )

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

        demand_df, censored, summary = _classification_pipeline.compute_censoring_info(
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
        return _forecast_pipeline.run_sku_forecast(
            self, sku,
            location=location,
            granularity=granularity,
            h=h,
            n_windows=n_windows,
            outlier_method=outlier_method,
            treat_strategy=treat_strategy,
            use_lgbm=use_lgbm,
            return_cv=return_cv,
        )

    def classification_summary(self, granularity: str | None = None) -> dict:
        """Resumen agregado de la clasificacion del catalogo."""
        return _classification_pipeline.run_classification_summary(self, granularity=granularity)

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

        Inyecta señal forward-looking desde ``ForecastStore`` si el artefacto
        está fresco (mismo comportamiento que ``catalog_health_report``).

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
            El campo ``ss_method`` lleva el sufijo ``_forecast`` cuando se usó
            señal forward-looking.
        """
        return _inventory_pipeline.run_sku_safety_stock(
            self, sku,
            abc_class=abc_class,
            granularity=granularity,
            simple_safety_pct=simple_safety_pct,
        )

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
        return _inventory_pipeline.run_catalog_health_report(
            self,
            granularity=granularity,
            simple_safety_pct=simple_safety_pct,
        )

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
        return _purchase_pipeline.run_purchase_plan(
            self,
            granularity=granularity,
            include_equilibrio=include_equilibrio,
            include_sobrestock=include_sobrestock,
            limit=limit,
            simple_safety_pct=simple_safety_pct,
        )

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
        return _purchase_pipeline.run_purchase_plan_by_supplier(
            self,
            granularity=granularity,
            simple_safety_pct=simple_safety_pct,
        )

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
        return _purchase_pipeline.run_purchase_plan_summary(
            self,
            granularity=granularity,
            simple_safety_pct=simple_safety_pct,
        )

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
        return _purchase_pipeline.run_sku_purchase_recommendation(
            self, sku,
            abc_class=abc_class,
            granularity=granularity,
            simple_safety_pct=simple_safety_pct,
        )

    # ---------------------------------------------------------------------------
    # Opción C — batch forecast + status  (nuevos métodos)
    # ---------------------------------------------------------------------------

    def run_catalog_forecast(
        self,
        granularity: str | None = None,
        n_jobs: int = 1,
        use_lgbm: bool = False,
        n_windows: int = 3,
        h: int = 3,
    ) -> object:
        """Ejecuta el horse-race sobre el catálogo completo y persiste el artefacto.

        El artefacto resultante es consumido automáticamente por
        ``catalog_health_report`` en el próximo cálculo de safety stock.
        Ver ``pipelines/forecast.py`` para detalles.
        """
        return _forecast_pipeline.run_catalog_forecast(
            self,
            granularity=granularity,
            n_jobs=n_jobs,
            use_lgbm=use_lgbm,
            n_windows=n_windows,
            h=h,
        )

    def catalog_forecast_status(self, granularity: str | None = None) -> dict:
        """Retorna el estado del artefacto de forecast materializado.

        Lectura rápida del JSON de metadata — no abre el parquet.

        Returns
        -------
        dict
            ``{status, run_date, n_skus, coverage_pct, top_model, is_stale}``
            ``status`` puede ser ``"ok"``, ``"stale"`` o ``"missing"``.
        """
        return _forecast_pipeline.catalog_forecast_status(self, granularity=granularity)

    # ---------------------------------------------------------------------------
    # Clasificación batch + status  (Sprint 2 — ClassificationStore)
    # ---------------------------------------------------------------------------

    def run_catalog_classification(
        self,
        granularity: str | None = None,
        persist: bool = True,
    ) -> pd.DataFrame:
        """Clasifica el catálogo completo y materializa el artefacto en disco.

        El artefacto resultante es consumido automáticamente por
        ``classify_catalog()`` en la próxima llamada, eliminando el recalculo
        en cada request de la API (D14).

        Parameters
        ----------
        granularity : str, optional
            ``"M"``, ``"W"`` o ``"D"``. Si None, usa la oficial del manifest.
        persist : bool
            Si True (default), persiste en ``output/derived/``.

        Returns
        -------
        pd.DataFrame
            DataFrame de clasificación completa.
        """
        return _classification_pipeline.run_catalog_classification(
            self,
            granularity=granularity,
            persist=persist,
        )

    def catalog_classification_status(self, granularity: str | None = None) -> dict:
        """Retorna el estado del artefacto de clasificación materializado.

        Lectura rápida del JSON de metadata — no abre el parquet.

        Returns
        -------
        dict
            ``{status, run_date, n_skus, classification_scope, abc_distribution,
               seasonal_pct, avg_quality_score, is_stale}``
            ``status`` puede ser ``"ok"``, ``"stale"`` o ``"missing"``.
        """
        return _classification_pipeline.catalog_classification_status(
            self, granularity=granularity
        )
