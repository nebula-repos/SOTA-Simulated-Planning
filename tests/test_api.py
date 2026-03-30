"""Tests de integración para apps/api/main.py usando FastAPI TestClient.

Cubre:
- Códigos de respuesta HTTP correctos (200, 404, 422, 503)
- Esquema JSON mínimo de cada endpoint
- Validación de parámetros (granularity inválida, location desconocida)
- Comportamiento con SKU inexistente (404)
- Serialización sin errores (no timestamps sin convertir, no NaN/Inf en JSON)
"""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app

client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Fixtures — SKUs conocidos del dataset canónico
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def known_sku() -> str:
    """Devuelve el primer SKU activo del catálogo."""
    resp = client.get("/skus", params={"limit": 1})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) > 0, "El catálogo está vacío — tests de API requieren datos"
    return items[0]["sku"]


@pytest.fixture(scope="module")
def known_location() -> str:
    """Devuelve la primera location conocida. list_locations() retorna list[str]."""
    resp = client.get("/locations")
    assert resp.status_code == 200
    locs = resp.json()
    assert len(locs) > 0, "No hay locations — tests de API requieren datos"
    return locs[0]


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_returns_200(self):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_response_has_status_ok(self):
        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"

    def test_response_has_dataset_and_quality(self):
        resp = client.get("/health")
        data = resp.json()
        assert "dataset" in data
        assert "quality" in data


# ---------------------------------------------------------------------------
# GET /skus
# ---------------------------------------------------------------------------

class TestListSkus:
    def test_returns_200(self):
        resp = client.get("/skus")
        assert resp.status_code == 200

    def test_returns_list(self):
        resp = client.get("/skus")
        data = resp.json()
        assert isinstance(data, list)

    def test_limit_respected(self):
        resp = client.get("/skus", params={"limit": 5})
        data = resp.json()
        assert len(data) <= 5

    def test_search_filters_results(self, known_sku):
        resp = client.get("/skus", params={"search": known_sku})
        data = resp.json()
        assert any(item["sku"] == known_sku for item in data)

    def test_search_no_match_returns_empty(self):
        resp = client.get("/skus", params={"search": "XXXXXXXNOMATCH"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_limit_out_of_range_422(self):
        resp = client.get("/skus", params={"limit": 9999})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /locations
# ---------------------------------------------------------------------------

class TestListLocations:
    def test_returns_200(self):
        resp = client.get("/locations")
        assert resp.status_code == 200

    def test_returns_list_of_strings(self):
        """list_locations() retorna list[str] con nombres de ubicaciones."""
        resp = client.get("/locations")
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert all(isinstance(loc, str) for loc in data)


# ---------------------------------------------------------------------------
# GET /sku/{sku}/summary
# ---------------------------------------------------------------------------

class TestSkuSummary:
    def test_known_sku_returns_200(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/summary")
        assert resp.status_code == 200

    def test_unknown_sku_returns_404(self):
        resp = client.get("/sku/SKU-INEXISTENTE-9999/summary")
        assert resp.status_code == 404

    def test_response_contains_sku_field(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/summary")
        data = resp.json()
        assert "sku" in data or "total_demand" in data  # schema mínimo


# ---------------------------------------------------------------------------
# GET /sku/{sku}/timeseries
# ---------------------------------------------------------------------------

class TestSkuTimeseries:
    def test_known_sku_returns_200(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/timeseries")
        assert resp.status_code == 200

    def test_returns_list_of_records(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/timeseries")
        data = resp.json()
        assert isinstance(data, list)

    def test_unknown_sku_returns_404(self):
        resp = client.get("/sku/SKU-INEXISTENTE-9999/timeseries")
        assert resp.status_code == 404

    def test_invalid_location_returns_422(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/timeseries", params={"location": "LOC-INEXISTENTE-XYZ"})
        assert resp.status_code == 422

    def test_valid_location_returns_200(self, known_sku, known_location):
        resp = client.get(f"/sku/{known_sku}/timeseries", params={"location": known_location})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /sku/{sku}/supply
# ---------------------------------------------------------------------------

class TestSkuSupply:
    def test_known_sku_returns_200(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/supply")
        assert resp.status_code == 200

    def test_response_has_receipts_and_transfers(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/supply")
        data = resp.json()
        assert "purchase_receipts" in data
        assert "internal_transfers" in data

    def test_unknown_sku_returns_404(self):
        resp = client.get("/sku/SKU-INEXISTENTE-9999/supply")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /classification
# ---------------------------------------------------------------------------

class TestClassification:
    def test_returns_200(self):
        resp = client.get("/classification")
        assert resp.status_code == 200

    def test_response_schema(self):
        resp = client.get("/classification", params={"limit": 10})
        data = resp.json()
        assert "total" in data
        assert "items" in data
        assert "offset" in data
        assert "limit" in data

    def test_invalid_granularity_returns_422(self):
        resp = client.get("/classification", params={"granularity": "X"})
        assert resp.status_code == 422

    def test_valid_granularity_m_returns_200(self):
        resp = client.get("/classification", params={"granularity": "M", "limit": 5})
        assert resp.status_code == 200

    def test_filter_by_abc_class(self):
        resp = client.get("/classification", params={"abc_class": "A", "limit": 50})
        data = resp.json()
        assert resp.status_code == 200
        for item in data["items"]:
            assert item["abc_class"] == "A"

    def test_pagination_offset(self):
        resp_p1 = client.get("/classification", params={"limit": 5, "offset": 0})
        resp_p2 = client.get("/classification", params={"limit": 5, "offset": 5})
        ids_p1 = [i["sku"] for i in resp_p1.json()["items"]]
        ids_p2 = [i["sku"] for i in resp_p2.json()["items"]]
        assert set(ids_p1).isdisjoint(set(ids_p2))


# ---------------------------------------------------------------------------
# GET /classification/summary
# ---------------------------------------------------------------------------

class TestClassificationSummary:
    def test_returns_200(self):
        resp = client.get("/classification/summary")
        assert resp.status_code == 200

    def test_invalid_granularity_returns_422(self):
        resp = client.get("/classification/summary", params={"granularity": "Q"})
        assert resp.status_code == 422

    def test_response_is_dict(self):
        resp = client.get("/classification/summary")
        assert isinstance(resp.json(), dict)


# ---------------------------------------------------------------------------
# GET /sku/{sku}/classification
# ---------------------------------------------------------------------------

class TestSkuClassification:
    def test_known_sku_returns_200(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/classification")
        assert resp.status_code == 200

    def test_unknown_sku_returns_inactive_profile(self):
        """SKUs desconocidos retornan perfil con sb_class='inactive', no 404.

        El endpoint está diseñado así: un SKU no en el catálogo retorna
        inactive en lugar de 404, igual que SKUs sin transacciones.
        """
        resp = client.get("/sku/SKU-INEXISTENTE-9999/classification")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sb_class"] == "inactive"

    def test_invalid_granularity_returns_422(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/classification", params={"granularity": "Z"})
        assert resp.status_code == 422

    def test_invalid_location_returns_422(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/classification", params={"location": "LOC-INEXISTENTE"})
        assert resp.status_code == 422

    def test_response_has_sb_class(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/classification")
        data = resp.json()
        assert "sb_class" in data


# ---------------------------------------------------------------------------
# GET /sku/{sku}/demand-series
# ---------------------------------------------------------------------------

class TestSkuDemandSeries:
    def test_known_sku_returns_200(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/demand-series")
        assert resp.status_code == 200

    def test_returns_list(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/demand-series")
        assert isinstance(resp.json(), list)

    def test_unknown_sku_returns_404(self):
        resp = client.get("/sku/SKU-INEXISTENTE-9999/demand-series")
        assert resp.status_code == 404

    def test_invalid_granularity_returns_422(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/demand-series", params={"granularity": "X"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /sku/{sku}/acf
# ---------------------------------------------------------------------------

class TestSkuAcf:
    def test_known_sku_returns_200(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/acf")
        assert resp.status_code == 200

    def test_unknown_sku_returns_404(self):
        resp = client.get("/sku/SKU-INEXISTENTE-9999/acf")
        assert resp.status_code == 404

    def test_invalid_granularity_returns_422(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/acf", params={"granularity": "X"})
        assert resp.status_code == 422

    def test_max_lags_out_of_range_422(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/acf", params={"max_lags": 999})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /sku/{sku}/forecast
# ---------------------------------------------------------------------------

class TestSkuForecast:
    def test_known_sku_returns_200(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/forecast", params={"h": 3, "n_windows": 2})
        assert resp.status_code == 200

    def test_unknown_sku_returns_404(self):
        resp = client.get("/sku/SKU-INEXISTENTE-9999/forecast", params={"h": 3, "n_windows": 2})
        assert resp.status_code == 404

    def test_invalid_granularity_returns_422(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/forecast", params={"granularity": "X", "h": 3})
        assert resp.status_code == 422

    def test_invalid_location_returns_422(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/forecast", params={"location": "LOC-FAKE-XYZ", "h": 3})
        assert resp.status_code == 422

    def test_h_out_of_range_422(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/forecast", params={"h": 100})
        assert resp.status_code == 422

    def test_response_schema(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/forecast", params={"h": 3, "n_windows": 2})
        data = resp.json()
        assert "status" in data
        assert "model" in data
        assert "forecast" in data

    def test_forecast_is_list(self, known_sku):
        resp = client.get(f"/sku/{known_sku}/forecast", params={"h": 3, "n_windows": 2})
        data = resp.json()
        assert isinstance(data["forecast"], list)

    def test_forecast_no_nan_in_yhat(self, known_sku):
        """yhat no debe contener NaN ni Inf — la API debe serializar valores finitos."""
        resp = client.get(f"/sku/{known_sku}/forecast", params={"h": 3, "n_windows": 2})
        data = resp.json()
        for record in data["forecast"]:
            yhat = record.get("yhat")
            if yhat is not None:
                assert math.isfinite(yhat), f"yhat no finito: {yhat}"

    def test_demand_series_not_exposed(self, known_sku):
        """demand_series es un campo interno — no debe aparecer en la respuesta de la API."""
        resp = client.get(f"/sku/{known_sku}/forecast", params={"h": 3, "n_windows": 2})
        data = resp.json()
        assert "demand_series" not in data

    def test_ds_column_is_string(self, known_sku):
        """ds debe ser string ISO, no objeto Timestamp (no serializable en JSON)."""
        resp = client.get(f"/sku/{known_sku}/forecast", params={"h": 3, "n_windows": 2})
        data = resp.json()
        for record in data["forecast"]:
            assert isinstance(record["ds"], str)
