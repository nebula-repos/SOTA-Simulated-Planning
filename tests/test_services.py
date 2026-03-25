import json

import pandas as pd
import pytest

from planning_core.repository import CanonicalRepository
from planning_core.services import PlanningService


def _write_minimal_dataset(base_path):
    catalog = pd.DataFrame([
        {
            "sku": "SKU-001",
            "name": "Producto Test",
            "category": "Categoria",
            "subcategory": "Subcategoria",
            "brand": "Marca",
            "supplier": "Proveedor",
            "base_price": 100.0,
            "cost": 80.0,
            "moq": 1,
            "warranty_months": 12,
        }
    ])
    catalog.to_csv(base_path / "product_catalog.csv", index=False)

    transactions = pd.DataFrame([
        {"date": "2024-01-01", "sku": "SKU-001", "location": "Sucursal Norte", "quantity": 5, "unit_price": 100.0, "total_amount": 500.0},
        {"date": "2024-01-15", "sku": "SKU-001", "location": "CD Santiago", "quantity": 3, "unit_price": 100.0, "total_amount": 300.0},
    ])
    transactions.to_csv(base_path / "transactions.csv", index=False)

    inventory_rows = []
    for day in pd.date_range("2024-01-01", "2024-01-31", freq="D"):
        for location in ["Sucursal Norte", "CD Santiago"]:
            if day == pd.Timestamp("2024-01-01") or day == pd.Timestamp("2024-01-15"):
                on_hand = 10
            else:
                on_hand = 0
            inventory_rows.append(
                {
                    "snapshot_date": day.strftime("%Y-%m-%d"),
                    "sku": "SKU-001",
                    "location": location,
                    "on_hand_qty": on_hand,
                    "on_order_qty": 0,
                }
            )
    pd.DataFrame(inventory_rows).to_csv(base_path / "inventory_snapshot.csv", index=False)

    manifest = {
        "profile": "industrial",
        "currency": "CLP",
        "start_date": "2024-01-01",
        "end_date": "2024-01-31",
        "n_products": 1,
        "central_supply_mode": True,
        "central_location": "CD Santiago",
        "location_model": {
            "all_locations": ["Sucursal Norte", "CD Santiago"],
            "branch_locations": ["Sucursal Norte"],
            "central_location": "CD Santiago",
            "central_supply_mode": True,
            "central_node_sales_mode": True,
        },
        "classification": {
            "scope": "network_aggregate",
            "default_granularity": "M",
        },
        "table_rows": {
            "product_catalog": 1,
            "transactions": 2,
            "inventory_snapshot": len(inventory_rows),
        },
    }
    with open(base_path / "dataset_manifest.json", "w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, ensure_ascii=True, indent=2)


def test_location_metadata_is_read_from_manifest(tmp_path):
    _write_minimal_dataset(tmp_path)

    service = PlanningService(CanonicalRepository(tmp_path))

    assert service.central_location() == "CD Santiago"
    assert service.list_locations() == ["Sucursal Norte", "CD Santiago"]
    overview = service.dataset_overview()
    assert overview["classification_scope"] == "network_aggregate"
    assert overview["classification_default_granularity"] == "M"


def test_classification_default_is_monthly_and_includes_censoring(tmp_path):
    _write_minimal_dataset(tmp_path)

    service = PlanningService(CanonicalRepository(tmp_path))

    classification_df = service.classify_catalog()
    assert classification_df.loc[0, "granularity"] == "M"
    assert classification_df.loc[0, "classification_scope"] == "network_aggregate"
    assert bool(classification_df.loc[0, "has_censored_demand"]) is True
    assert classification_df.loc[0, "censored_periods"] == 1
    assert classification_df.loc[0, "quality_score"] <= classification_df.loc[0, "quality_score_base"]
    assert any("demanda_censurada" in flag for flag in classification_df.loc[0, "quality_flags"])

    censor_info = service.sku_censored_mask("SKU-001", granularity="D")
    assert censor_info["summary"]["stockout_no_sale_periods"] > 0
    assert bool(censor_info["series"]["is_stockout_no_sale"].any()) is True


def test_sku_forecast_includes_demand_series(tmp_path):
    """sku_forecast debe incluir 'demand_series' en el resultado."""
    _write_minimal_dataset(tmp_path)
    service = PlanningService(CanonicalRepository(tmp_path))
    result = service.sku_forecast("SKU-001", h=1, n_windows=1)
    assert "demand_series" in result, "demand_series debe estar presente en el resultado"
    assert isinstance(result["demand_series"], pd.DataFrame)
    assert not result["demand_series"].empty


def test_sku_forecast_no_data_for_unknown_sku(tmp_path):
    """sku_forecast sobre SKU inexistente devuelve status='no_data'."""
    _write_minimal_dataset(tmp_path)
    service = PlanningService(CanonicalRepository(tmp_path))
    result = service.sku_forecast("SKU-INEXISTENTE", h=1, n_windows=1)
    assert result["status"] == "no_data"
