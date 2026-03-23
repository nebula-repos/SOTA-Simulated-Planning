from __future__ import annotations

import pandas as pd

from planning_core.classification import (
    classify_all_skus,
    classify_sku,
    compute_acf,
    detect_outliers,
    prepare_demand_series,
    select_granularity,
)
from planning_core.repository import CanonicalRepository
from planning_core.validation import basic_health_report


class PlanningService:
    """Servicios de lectura y agregacion sobre el modelo canonico."""

    def __init__(self, repository: CanonicalRepository):
        self.repository = repository

    def dataset_overview(self) -> dict:
        catalog = self.repository.load_table("product_catalog")
        inventory = self.repository.load_table("inventory_snapshot")
        central_location = self.central_location()

        return {
            "sku_count": int(catalog["sku"].nunique()),
            "location_count": int(inventory["location"].nunique()),
            "central_location": central_location,
            "date_range": {
                "start": inventory["snapshot_date"].min().date().isoformat(),
                "end": inventory["snapshot_date"].max().date().isoformat(),
            },
            "table_rows": {
                table_name: int(len(self.repository.load_table(table_name)))
                for table_name in self.repository.available_tables()
            },
        }

    def dataset_health(self) -> dict:
        return basic_health_report(self.repository)

    def central_location(self) -> str | None:
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
        inventory = self.repository.load_table("inventory_snapshot")
        return sorted(inventory["location"].dropna().unique().tolist())

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

        Agrega transacciones a nivel SKU global (todas las locations sumadas),
        ejecuta el pipeline completo de clasificacion y retorna un DataFrame
        con una fila por SKU.
        """
        transactions = self.repository.load_table("transactions")
        catalog = self.repository.load_table("product_catalog")
        return classify_all_skus(transactions, catalog, granularity=granularity)

    def classify_single_sku(
        self,
        sku: str,
        location: str | None = None,
        granularity: str | None = None,
    ) -> dict | None:
        """Clasifica un SKU individual.

        Parameters
        ----------
        sku : str
            SKU a clasificar.
        location : str, optional
            Si se provee, filtra transacciones a esa ubicacion.
        granularity : str, optional
            Granularidad forzada. Si es None, se selecciona automaticamente.
        """
        transactions = self.repository.load_table("transactions")
        sku_tx = transactions[transactions["sku"] == sku]

        if location:
            sku_tx = sku_tx[sku_tx["location"] == location]

        if sku_tx.empty:
            return None

        return classify_sku(sku_tx, sku=sku, granularity=granularity)

    def sku_demand_series(
        self,
        sku: str,
        location: str | None = None,
        granularity: str | None = None,
    ) -> pd.DataFrame:
        """Retorna la serie temporal de demanda preparada (con ceros rellenados).

        Util para visualizacion directa de la serie subyacente.
        """
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
        """Retorna la serie de demanda con columna de outliers marcados.

        Returns
        -------
        pd.DataFrame
            Columnas ``[period, demand, is_outlier]``.
        """
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
        """Calcula la funcion de autocorrelacion para un SKU.

        Returns
        -------
        dict
            ``{"lags": list[int], "acf": list[float], "confidence_bound": float}``
        """
        series_df = self.sku_demand_series(sku, location=location, granularity=granularity)

        if series_df.empty:
            return {"lags": [], "acf": [], "confidence_bound": 0.0}

        acf_values = compute_acf(series_df["demand"], max_lags=max_lags)
        n = len(series_df)
        confidence_bound = 1.96 / (n ** 0.5)  # intervalo de confianza al 95%

        return {
            "lags": list(range(len(acf_values))),
            "acf": [round(float(v), 4) for v in acf_values],
            "confidence_bound": round(confidence_bound, 4),
        }

    def classification_summary(self, granularity: str | None = None) -> dict:
        """Resumen agregado de la clasificacion del catalogo.

        Retorna conteos por clase Syntetos-Boylan, ABC, XYZ, lifecycle,
        y la matriz ABC-XYZ.
        """
        df = self.classify_catalog(granularity=granularity)

        sb_counts = df["sb_class"].value_counts().to_dict()
        abc_counts = df["abc_class"].value_counts().to_dict()
        xyz_counts = df["xyz_class"].value_counts().to_dict()
        lifecycle_counts = df["lifecycle"].value_counts().to_dict()
        abc_xyz_counts = df["abc_xyz"].value_counts().to_dict()

        # Matriz ABC-XYZ (3x3)
        abc_xyz_matrix = {}
        for abc in ["A", "B", "C"]:
            abc_xyz_matrix[abc] = {}
            for xyz in ["X", "Y", "Z"]:
                key = f"{abc}{xyz}"
                abc_xyz_matrix[abc][xyz] = int(abc_xyz_counts.get(key, 0))

        return {
            "total_skus": len(df),
            "granularity": df["granularity"].iloc[0] if not df.empty else None,
            "sb_counts": sb_counts,
            "abc_counts": abc_counts,
            "xyz_counts": xyz_counts,
            "lifecycle_counts": lifecycle_counts,
            "abc_xyz_matrix": abc_xyz_matrix,
            "avg_quality_score": round(float(df["quality_score"].mean()), 3) if not df.empty else 0.0,
            "seasonal_pct": round(float(df["is_seasonal"].mean()), 3) if not df.empty else 0.0,
            "trend_pct": round(float(df["has_trend"].mean()), 3) if not df.empty else 0.0,
        }
