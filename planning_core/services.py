from __future__ import annotations

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
from planning_core.forecasting.selector import select_and_forecast
from planning_core.preprocessing import censored_summary, mark_censored_demand
from planning_core.repository import CanonicalRepository
from planning_core.validation import basic_health_report


OFFICIAL_CLASSIFICATION_SCOPE = "network_aggregate"
OFFICIAL_CLASSIFICATION_GRANULARITY = "M"


class PlanningService:
    """Servicios de lectura y agregacion sobre el modelo canonico."""

    def __init__(self, repository: CanonicalRepository):
        self.repository = repository

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

        classification_df = classify_all_skus(transactions, catalog, granularity=granularity)
        classification_df["classification_scope"] = self.classification_scope()
        return self._augment_catalog_classification_with_censoring(
            classification_df=classification_df,
            transactions=transactions,
            inventory=inventory,
            granularity=granularity,
        )

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
            return None

        if granularity is None:
            granularity = self.official_classification_granularity() if location is None else select_granularity(sku_tx)

        profile = classify_sku(sku_tx, sku=sku, granularity=granularity)
        profile["classification_scope"] = self.classification_scope() if location is None else "location"
        return self._augment_profile_with_censoring(profile, sku_tx, sku_inv, granularity)

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
        h: int = 3,
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
        h : int
            Horizonte de pronostico en periodos.
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

        # Obtener perfil de clasificacion
        profile = self.classify_single_sku(sku, location=location, granularity=granularity)
        if profile is None:
            return {
                "status": "no_data",
                "model": None,
                "mase": float("nan"),
                "forecast": pd.DataFrame(),
                "backtest": {},
                "season_length": 12,
                "granularity": granularity,
                "h": h,
            }

        # Serie limpia (outliers tratados) para modelado
        clean_df = self.sku_clean_series(
            sku,
            location=location,
            granularity=granularity,
            outlier_method=outlier_method,
            treat_strategy=treat_strategy,
        )

        # Preparar input para el selector: period + demand_clean renombrado a demand
        if clean_df.empty or "demand_clean" not in clean_df.columns:
            model_input = clean_df[["period", "demand"]].copy()
        else:
            model_input = clean_df[["period", "demand_clean"]].rename(
                columns={"demand_clean": "demand"}
            )

        result = select_and_forecast(
            profile=profile,
            demand_df=model_input,
            granularity=granularity,
            h=h,
            n_windows=n_windows,
            unique_id=sku,
            use_lgbm=use_lgbm,
            return_cv=return_cv,
        )
        # Incluir la serie limpia usada para entrenar (evita re-carga en la UI)
        result["demand_series"] = model_input
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
