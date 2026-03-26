"""Tests para planning_core.inventory.

Cubre:
- service_level: csl_to_z, get_csl_target, get_z_factor, get_service_level_config
- params: compute_supplier_lead_times, get_sku_params, InventoryParams
- PlanningService: service_level_config, sku_inventory_params
"""

from __future__ import annotations

import math
from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from planning_core.inventory.service_level import (
    CSL_DEFAULTS,
    ServiceLevelConfig,
    csl_to_z,
    get_csl_target,
    get_service_level_config,
    get_z_factor,
)
from planning_core.inventory.params import (
    InventoryParams,
    compute_supplier_lead_times,
    get_sku_params,
)


# ---------------------------------------------------------------------------
# Fixtures mínimos para tests de params sin repository real
# ---------------------------------------------------------------------------

class _FakeRepository:
    """Repository sintético que sirve purchase_orders y purchase_receipts."""

    def __init__(self, orders_df: pd.DataFrame, receipts_df: pd.DataFrame):
        self._orders = orders_df
        self._receipts = receipts_df

    def load_table(self, table_name: str) -> pd.DataFrame:
        if table_name == "purchase_orders":
            return self._orders.copy()
        if table_name == "purchase_receipts":
            return self._receipts.copy()
        return pd.DataFrame()


def _make_fake_repo(
    suppliers: list[str] | None = None,
    lt_days_list: list[float] | None = None,
) -> _FakeRepository:
    """Crea un repo sintético con POs para los proveedores dados."""
    if suppliers is None:
        suppliers = ["ProvA", "ProvB", "ProvC"]
    if lt_days_list is None:
        lt_days_list = [30.0, 45.0, 60.0]

    orders_rows = []
    receipts_rows = []
    base_date = pd.Timestamp("2023-01-01")

    po_counter = 0
    for supplier, lt in zip(suppliers, lt_days_list):
        for i in range(5):  # 5 POs por proveedor para tener std computable
            po_counter += 1
            po_id = f"PO-{po_counter:04d}"
            order_date = base_date + pd.Timedelta(days=i * 30)
            receipt_date = order_date + pd.Timedelta(days=lt + float(i % 3 - 1))  # +/- 1-2 días de variación
            orders_rows.append({
                "po_id": po_id,
                "supplier": supplier,
                "order_date": order_date,
                "expected_receipt_date": order_date + pd.Timedelta(days=lt),
            })
            receipts_rows.append({
                "po_id": po_id,
                "receipt_date": receipt_date,
                "receipt_status": "received",
                "supplier": supplier,
            })

    return _FakeRepository(
        orders_df=pd.DataFrame(orders_rows),
        receipts_df=pd.DataFrame(receipts_rows),
    )


# ===========================================================================
# TestCslToZ
# ===========================================================================

class TestCslToZ:
    def test_known_values_from_pdf(self):
        """Valores exactos de la tabla del PDF sección 4.2."""
        assert csl_to_z(0.95) == pytest.approx(1.65, abs=0.01)
        assert csl_to_z(0.98) == pytest.approx(2.05, abs=0.01)
        assert csl_to_z(0.99) == pytest.approx(2.33, abs=0.01)
        assert csl_to_z(0.90) == pytest.approx(1.28, abs=0.01)

    def test_interpolation_monotone(self):
        """z crece estrictamente con CSL en todo el rango de la tabla."""
        csl_values = np.linspace(0.85, 0.995, 50)
        z_values = [csl_to_z(c) for c in csl_values]
        for i in range(len(z_values) - 1):
            assert z_values[i] <= z_values[i + 1], (
                f"z no es monótono en CSL={csl_values[i]:.4f}: z={z_values[i]:.4f} > z={z_values[i+1]:.4f}"
            )

    def test_clamp_low(self):
        """CSL por debajo del mínimo → z mínimo de la tabla."""
        z_min = csl_to_z(0.85)
        assert csl_to_z(0.50) == pytest.approx(z_min, abs=1e-9)
        assert csl_to_z(0.01) == pytest.approx(z_min, abs=1e-9)

    def test_clamp_high(self):
        """CSL por encima del máximo → z máximo de la tabla."""
        z_max = csl_to_z(0.995)
        assert csl_to_z(0.9999) == pytest.approx(z_max, abs=1e-9)
        assert csl_to_z(1.0) == pytest.approx(z_max, abs=1e-9)

    def test_returns_float(self):
        assert isinstance(csl_to_z(0.95), float)


# ===========================================================================
# TestGetCslTarget
# ===========================================================================

class TestGetCslTarget:
    def test_defaults_match_pdf(self):
        """Defaults de la tabla 8.4 del PDF."""
        assert get_csl_target("A") == pytest.approx(0.985)
        assert get_csl_target("B") == pytest.approx(0.945)
        assert get_csl_target("C") == pytest.approx(0.885)

    def test_manifest_override(self):
        """Override de manifest tiene prioridad sobre el default."""
        policy = {"A": {"csl_target": 0.99}, "B": {"csl_target": 0.96}, "C": {"csl_target": 0.90}}
        assert get_csl_target("A", policy) == pytest.approx(0.99)
        assert get_csl_target("B", policy) == pytest.approx(0.96)
        assert get_csl_target("C", policy) == pytest.approx(0.90)

    def test_partial_manifest_override(self):
        """Si solo A tiene override, B y C siguen usando defaults."""
        policy = {"A": {"csl_target": 0.99}}
        assert get_csl_target("A", policy) == pytest.approx(0.99)
        assert get_csl_target("B", policy) == pytest.approx(CSL_DEFAULTS["B"])
        assert get_csl_target("C", policy) == pytest.approx(CSL_DEFAULTS["C"])

    def test_none_abc_falls_back_to_c(self):
        """abc_class=None → fallback conservador CSL_DEFAULTS["C"]."""
        assert get_csl_target(None) == pytest.approx(CSL_DEFAULTS["C"])

    def test_unknown_abc_falls_back_to_c(self):
        assert get_csl_target("Z") == pytest.approx(CSL_DEFAULTS["C"])

    def test_hierarchy_a_gt_b_gt_c(self):
        """A > B > C siempre."""
        assert get_csl_target("A") > get_csl_target("B") > get_csl_target("C")


# ===========================================================================
# TestGetZFactor
# ===========================================================================

class TestGetZFactor:
    def test_z_a_gt_b_gt_c(self):
        """Factor z de A > B > C."""
        assert get_z_factor("A") > get_z_factor("B") > get_z_factor("C")

    def test_z_consistent_with_csl(self):
        """csl_to_z(get_csl_target(abc)) == get_z_factor(abc)."""
        for abc in ("A", "B", "C"):
            expected = csl_to_z(get_csl_target(abc))
            assert get_z_factor(abc) == pytest.approx(expected, rel=1e-6)


# ===========================================================================
# TestServiceLevelConfig
# ===========================================================================

class TestServiceLevelConfig:
    def test_all_fields_present(self):
        config = get_service_level_config("A")
        assert isinstance(config, ServiceLevelConfig)
        assert hasattr(config, "abc_class")
        assert hasattr(config, "csl_target")
        assert hasattr(config, "z_factor")
        assert hasattr(config, "ss_method")

    def test_ss_method_by_abc(self):
        """Método de SS según tabla 8.4: A→extended, B→standard, C→simple_pct_lt."""
        assert get_service_level_config("A").ss_method == "extended"
        assert get_service_level_config("B").ss_method == "standard"
        assert get_service_level_config("C").ss_method == "simple_pct_lt"

    def test_none_abc_returns_valid_config(self):
        config = get_service_level_config(None)
        assert not math.isnan(config.z_factor)
        assert config.z_factor > 0
        assert config.ss_method == "simple_pct_lt"

    def test_csl_and_z_consistent(self):
        config = get_service_level_config("B")
        assert config.z_factor == pytest.approx(csl_to_z(config.csl_target), rel=1e-6)


# ===========================================================================
# TestComputeSupplierLeadTimes
# ===========================================================================

class TestComputeSupplierLeadTimes:
    def test_returns_dataframe_with_columns(self):
        repo = _make_fake_repo()
        result = compute_supplier_lead_times(repo)
        for col in ("supplier", "lt_mean_days", "lt_std_days", "n_orders"):
            assert col in result.columns, f"Falta columna: {col}"

    def test_lead_times_positive(self):
        repo = _make_fake_repo()
        result = compute_supplier_lead_times(repo)
        assert (result["lt_mean_days"] > 0).all()

    def test_covers_all_suppliers(self):
        suppliers = ["ProvA", "ProvB", "ProvC"]
        repo = _make_fake_repo(suppliers=suppliers)
        result = compute_supplier_lead_times(repo)
        found = set(result["supplier"].tolist())
        for s in suppliers:
            assert s in found, f"Proveedor {s} no está en el resultado"

    def test_sigma_lt_finite(self):
        """Todos los proveedores deben tener sigma_lt_days finito y >= 0."""
        repo = _make_fake_repo()
        result = compute_supplier_lead_times(repo)
        assert result["lt_std_days"].notna().all()
        assert (result["lt_std_days"] >= 0).all()

    def test_single_receipt_uses_fallback_std(self):
        """Proveedor con solo 1 recepción → sigma no es NaN (usa mediana global)."""
        orders_df = pd.DataFrame([{
            "po_id": "PO-0001",
            "supplier": "ProvSolo",
            "order_date": pd.Timestamp("2023-01-01"),
            "expected_receipt_date": pd.Timestamp("2023-02-01"),
        }])
        receipts_df = pd.DataFrame([{
            "po_id": "PO-0001",
            "receipt_date": pd.Timestamp("2023-02-05"),
            "receipt_status": "received",
            "supplier": "ProvSolo",
        }])
        repo = _FakeRepository(orders_df, receipts_df)
        result = compute_supplier_lead_times(repo)
        assert result["lt_std_days"].notna().all()

    def test_ignores_non_received_status(self):
        """Órdenes con status != 'received' no deben afectar el cálculo."""
        orders_df = pd.DataFrame([
            {"po_id": "PO-001", "supplier": "Prov", "order_date": pd.Timestamp("2023-01-01"), "expected_receipt_date": pd.Timestamp("2023-02-01")},
            {"po_id": "PO-002", "supplier": "Prov", "order_date": pd.Timestamp("2023-02-01"), "expected_receipt_date": pd.Timestamp("2023-03-01")},
        ])
        receipts_df = pd.DataFrame([
            {"po_id": "PO-001", "receipt_date": pd.Timestamp("2023-02-01"), "receipt_status": "received", "supplier": "Prov"},
            {"po_id": "PO-002", "receipt_date": pd.Timestamp("2023-03-01"), "receipt_status": "partial", "supplier": "Prov"},
        ])
        repo = _FakeRepository(orders_df, receipts_df)
        result = compute_supplier_lead_times(repo)
        # Solo PO-001 cuenta → n_orders = 1
        assert result.loc[result["supplier"] == "Prov", "n_orders"].iloc[0] == 1


# ===========================================================================
# TestGetSkuParams
# ===========================================================================

class TestGetSkuParams:
    def test_returns_inventory_params(self):
        repo = _make_fake_repo(["ProvX"])
        params = get_sku_params("SKU-001", "A", "ProvX", repo, manifest_config=None)
        assert isinstance(params, InventoryParams)
        assert params.sku == "SKU-001"

    def test_lead_time_from_purchase_data(self):
        """Lead time se toma de los datos de compra cuando el proveedor tiene historial."""
        repo = _make_fake_repo(["ProvX"], lt_days_list=[45.0])
        params = get_sku_params("SKU-001", "B", "ProvX", repo, manifest_config=None)
        assert params.lead_time_days > 0
        # El LT calculado debería estar cerca de 45 días (con algo de variación)
        assert 40 < params.lead_time_days < 55

    def test_review_period_by_abc(self):
        """review_period_days sigue los valores por ABC class."""
        repo = _make_fake_repo(["Prov"])
        assert get_sku_params("SKU", "A", "Prov", repo).review_period_days == pytest.approx(14.0)
        assert get_sku_params("SKU", "B", "Prov", repo).review_period_days == pytest.approx(21.0)
        assert get_sku_params("SKU", "C", "Prov", repo).review_period_days == pytest.approx(30.0)

    def test_unknown_supplier_uses_default(self):
        """SKU con proveedor sin historial → usa global default de lead time."""
        repo = _make_fake_repo(["ProvKnown"])
        params = get_sku_params("SKU-001", "B", "ProvUnknown", repo, manifest_config=None)
        assert params.lead_time_days == pytest.approx(30.0)  # hardcoded default

    def test_none_supplier_uses_default(self):
        repo = _make_fake_repo()
        params = get_sku_params("SKU-001", "C", None, repo, manifest_config=None)
        assert params.lead_time_days == pytest.approx(30.0)

    def test_sigma_lt_finite_and_nonneg(self):
        repo = _make_fake_repo(["ProvX"])
        params = get_sku_params("SKU-001", "A", "ProvX", repo)
        assert not math.isnan(params.sigma_lt_days)
        assert params.sigma_lt_days >= 0

    def test_carrying_cost_rate_default(self):
        repo = _make_fake_repo()
        params = get_sku_params("SKU-001", "B", "ProvX", repo)
        assert params.carrying_cost_rate == pytest.approx(0.25)

    def test_manifest_override_lead_time(self):
        """Override explícito por SKU en manifest tiene prioridad."""
        repo = _make_fake_repo(["ProvX"], lt_days_list=[60.0])
        manifest = {
            "inventory_params": {
                "overrides": {
                    "SKU-001": {"lead_time_days": 90.0, "sigma_lt_days": 5.0}
                }
            }
        }
        params = get_sku_params("SKU-001", "A", "ProvX", repo, manifest_config=manifest)
        assert params.lead_time_days == pytest.approx(90.0)
        assert params.sigma_lt_days == pytest.approx(5.0)

    def test_manifest_review_period_override_by_abc(self):
        """Manifest puede ajustar review_period_days por ABC class."""
        repo = _make_fake_repo()
        manifest = {
            "inventory_params": {
                "defaults_by_abc": {"A": {"review_period_days": 7.0}}
            }
        }
        params = get_sku_params("SKU", "A", None, repo, manifest_config=manifest)
        assert params.review_period_days == pytest.approx(7.0)

    def test_to_dict_serializable(self):
        """to_dict retorna un dict serializable con todos los campos esperados."""
        repo = _make_fake_repo(["ProvX"])
        params = get_sku_params("SKU-001", "B", "ProvX", repo)
        d = params.to_dict()
        expected_fields = (
            "sku", "lead_time_days", "sigma_lt_days", "review_period_days",
            "carrying_cost_rate", "abc_class", "csl_target", "z_factor", "ss_method",
        )
        for field in expected_fields:
            assert field in d, f"Falta campo: {field}"

    def test_csl_target_by_abc(self):
        """csl_target se asigna correctamente según ABC class."""
        repo = _make_fake_repo(["Prov"])
        from planning_core.inventory.service_level import CSL_DEFAULTS
        for abc in ("A", "B", "C"):
            params = get_sku_params("SKU", abc, "Prov", repo)
            assert params.csl_target == pytest.approx(CSL_DEFAULTS[abc])

    def test_z_factor_positive(self):
        repo = _make_fake_repo(["Prov"])
        params = get_sku_params("SKU", "A", "Prov", repo)
        assert params.z_factor > 0

    def test_ss_method_by_abc(self):
        repo = _make_fake_repo(["Prov"])
        assert get_sku_params("SKU", "A", "Prov", repo).ss_method == "extended"
        assert get_sku_params("SKU", "B", "Prov", repo).ss_method == "standard"
        assert get_sku_params("SKU", "C", "Prov", repo).ss_method == "simple_pct_lt"


# ===========================================================================
# Tests de integración con PlanningService (usando datos reales del repo)
# ===========================================================================

try:
    from planning_core.repository import CanonicalRepository
    from planning_core.services import PlanningService

    _repo = CanonicalRepository()
    _svc = PlanningService(_repo)
    _HAS_REAL_DATA = True
except Exception:
    _HAS_REAL_DATA = False


@pytest.mark.skipif(not _HAS_REAL_DATA, reason="Datos reales no disponibles")
class TestPlanningServiceInventory:
    def test_service_level_config_returns_dict(self):
        result = _svc.service_level_config()
        assert isinstance(result, dict)
        assert set(result.keys()) == {"A", "B", "C"}

    def test_service_level_config_hierarchy(self):
        result = _svc.service_level_config()
        assert result["A"] > result["B"] > result["C"]

    def test_service_level_config_values_in_range(self):
        result = _svc.service_level_config()
        for abc, csl in result.items():
            assert 0.80 <= csl <= 1.0, f"CSL fuera de rango para {abc}: {csl}"

    def test_sku_inventory_params_returns_dict(self):
        catalog = _repo.load_table("product_catalog")
        sku = catalog["sku"].iloc[0]
        result = _svc.sku_inventory_params(sku, abc_class="B")
        assert isinstance(result, dict)
        for field in ("sku", "lead_time_days", "sigma_lt_days", "review_period_days",
                      "carrying_cost_rate", "abc_class", "csl_target", "z_factor", "ss_method"):
            assert field in result

    def test_sku_inventory_params_lead_time_positive(self):
        catalog = _repo.load_table("product_catalog")
        sku = catalog["sku"].iloc[0]
        result = _svc.sku_inventory_params(sku, abc_class="A")
        assert result["lead_time_days"] > 0

    def test_sku_inventory_params_review_period_by_abc(self):
        catalog = _repo.load_table("product_catalog")
        sku = catalog["sku"].iloc[0]
        assert _svc.sku_inventory_params(sku, abc_class="A")["review_period_days"] == pytest.approx(14.0)
        assert _svc.sku_inventory_params(sku, abc_class="C")["review_period_days"] == pytest.approx(30.0)

    def test_compute_supplier_lead_times_covers_catalog(self):
        """Todos los proveedores del catálogo deben tener lead time calculado."""
        catalog = _repo.load_table("product_catalog")
        catalog_suppliers = set(catalog["supplier"].dropna().unique())
        lt_df = compute_supplier_lead_times(_repo)
        lt_suppliers = set(lt_df["supplier"].tolist())
        missing = catalog_suppliers - lt_suppliers
        assert len(missing) == 0, f"Proveedores sin lead time: {missing}"
