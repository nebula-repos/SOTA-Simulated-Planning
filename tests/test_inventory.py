"""Tests para planning_core.inventory.

Cubre:
- service_level: csl_to_z, get_csl_target, get_z_factor, get_service_level_config
- params: compute_supplier_lead_times, get_sku_params, InventoryParams
- safety_stock: compute_demand_stats, compute_safety_stock, compute_rop, compute_sku_safety_stock
- PlanningService: service_level_config, sku_inventory_params, sku_safety_stock
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
from planning_core.inventory.safety_stock import (
    SafetyStockResult,
    compute_demand_stats,
    compute_safety_stock,
    compute_rop,
    compute_sku_safety_stock,
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
        """Método de SS según diseño: A→extended, B→extended, C→simple_pct_lt."""
        assert get_service_level_config("A").ss_method == "extended"
        assert get_service_level_config("B").ss_method == "extended"
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
        assert get_sku_params("SKU", "B", "Prov", repo).ss_method == "extended"
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


# ---------------------------------------------------------------------------
# Helpers para tests de safety stock
# ---------------------------------------------------------------------------

def _make_demand_df(values: list[float], granularity: str = "M") -> pd.DataFrame:
    """Crea un DataFrame de demanda sintético para los tests."""
    n = len(values)
    if granularity == "M":
        periods = pd.date_range("2023-01-01", periods=n, freq="MS")
    elif granularity == "W":
        periods = pd.date_range("2023-01-02", periods=n, freq="W-MON")
    else:
        periods = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.DataFrame({"period": periods, "demand": values})


def _make_params(
    lead_time_days: float = 30.0,
    sigma_lt_days: float = 5.0,
    review_period_days: float = 14.0,
    z_factor: float = 1.65,
    ss_method: str = "extended",
    sku: str = "SKU-TEST",
) -> InventoryParams:
    return InventoryParams(
        sku=sku,
        lead_time_days=lead_time_days,
        sigma_lt_days=sigma_lt_days,
        review_period_days=review_period_days,
        carrying_cost_rate=0.25,
        abc_class="A",
        csl_target=0.95,
        z_factor=z_factor,
        ss_method=ss_method,
    )


# ===========================================================================
# TestComputeDemandStats
# ===========================================================================

class TestComputeDemandStats:
    def test_monthly_mean_correct(self):
        """mean_daily = mean_period / 30.4375 para granularidad mensual."""
        demand = [100.0, 200.0, 300.0, 400.0]
        df = _make_demand_df(demand)
        mean_daily, _, n = compute_demand_stats(df, granularity="M")
        expected_mean = 250.0 / (365.25 / 12)
        assert mean_daily == pytest.approx(expected_mean, rel=1e-4)
        assert n == 4

    def test_monthly_sigma_correct(self):
        """sigma_daily = std_period / sqrt(30.4375)."""
        import math as _math
        demand = [100.0, 200.0, 300.0, 400.0]
        df = _make_demand_df(demand)
        _, sigma_daily, _ = compute_demand_stats(df, granularity="M")
        days = 365.25 / 12
        std_period = float(np.std(demand, ddof=1))
        expected_sigma = std_period / _math.sqrt(days)
        assert sigma_daily == pytest.approx(expected_sigma, rel=1e-4)

    def test_weekly_converts_to_daily(self):
        """Para granularidad W: mean_daily = mean_period / 7."""
        demand = [70.0, 140.0, 210.0, 280.0]
        df = _make_demand_df(demand, granularity="W")
        mean_daily, sigma_daily, n = compute_demand_stats(df, granularity="W")
        assert mean_daily == pytest.approx(175.0 / 7.0, rel=1e-4)
        assert n == 4

    def test_daily_granularity_no_scaling(self):
        """Para granularidad D: mean_daily == mean_period."""
        demand = [5.0, 10.0, 15.0, 20.0]
        df = _make_demand_df(demand, granularity="D")
        mean_daily, _, _ = compute_demand_stats(df, granularity="D")
        assert mean_daily == pytest.approx(12.5, rel=1e-4)

    def test_empty_series_returns_zeros_with_warning(self):
        df = pd.DataFrame({"demand": []})
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mean, sigma, n = compute_demand_stats(df)
        assert mean == 0.0
        assert sigma == 0.0
        assert n == 0
        assert len(w) >= 1

    def test_short_series_warns_and_returns_zeros(self):
        """n < 3 → warning + retorna (0, 0, n)."""
        df = _make_demand_df([100.0, 200.0])
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mean, sigma, n = compute_demand_stats(df)
        assert mean == 0.0
        assert sigma == 0.0
        assert n == 2
        assert len(w) >= 1

    def test_all_zeros_returns_zero_sigma(self):
        """Serie con demanda cero → sigma=0, sin excepción."""
        df = _make_demand_df([0.0, 0.0, 0.0, 0.0])
        mean_daily, sigma_daily, n = compute_demand_stats(df)
        assert mean_daily == 0.0
        assert sigma_daily == 0.0
        assert n == 4

    def test_missing_demand_column_warns(self):
        """DataFrame sin columna 'demand' → warning + retorna (0, 0, 0)."""
        df = pd.DataFrame({"period": pd.date_range("2023-01", periods=3, freq="MS"), "qty": [1, 2, 3]})
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mean, sigma, n = compute_demand_stats(df)
        assert mean == 0.0
        assert n == 0
        assert len(w) >= 1


# ===========================================================================
# TestComputeSafetyStock
# ===========================================================================

class TestComputeSafetyStock:
    def test_extended_formula_correct(self):
        """SS = z × √((LT+R)·σ_d² + d̄²·σ_LT²) para método extended."""
        import math as _math
        params = _make_params(lead_time_days=30.0, sigma_lt_days=5.0,
                              review_period_days=14.0, z_factor=1.65)
        mean_daily = 10.0   # 10 unidades/día
        sigma_daily = 3.0   # σ_d = 3 unidades/día

        exposure = 30.0 + 14.0
        var_d = exposure * (sigma_daily ** 2)
        var_lt = (mean_daily ** 2) * (5.0 ** 2)
        expected_ss = 1.65 * _math.sqrt(var_d + var_lt)

        ss = compute_safety_stock(params, mean_daily, sigma_daily)
        assert ss == pytest.approx(expected_ss, rel=1e-6)

    def test_extended_with_zero_sigma_lt_equals_classic(self):
        """Con σ_LT=0 la fórmula extendida se reduce a z·σ_d·√exposure."""
        import math as _math
        params = _make_params(lead_time_days=30.0, sigma_lt_days=0.0,
                              review_period_days=14.0, z_factor=1.65)
        mean_daily = 10.0
        sigma_daily = 3.0
        exposure = 44.0
        expected_ss = 1.65 * sigma_daily * _math.sqrt(exposure)

        ss = compute_safety_stock(params, mean_daily, sigma_daily)
        assert ss == pytest.approx(expected_ss, rel=1e-6)

    def test_simple_pct_lt(self):
        """SS = pct × d̄_daily × lead_time_days para método simple_pct_lt."""
        params = _make_params(lead_time_days=30.0, ss_method="simple_pct_lt")
        mean_daily = 5.0
        expected_ss = 0.5 * 5.0 * 30.0

        ss = compute_safety_stock(params, mean_daily, 2.0, simple_safety_pct=0.5)
        assert ss == pytest.approx(expected_ss, rel=1e-6)

    def test_simple_pct_custom(self):
        """simple_safety_pct configurable."""
        params = _make_params(lead_time_days=20.0, ss_method="simple_pct_lt")
        ss = compute_safety_stock(params, mean_demand_daily=4.0,
                                  sigma_demand_daily=1.0, simple_safety_pct=0.75)
        assert ss == pytest.approx(0.75 * 4.0 * 20.0, rel=1e-6)

    def test_ss_nonnegative(self):
        """SS siempre >= 0."""
        params = _make_params()
        assert compute_safety_stock(params, 0.0, 0.0) >= 0.0
        assert compute_safety_stock(params, -1.0, 0.0) >= 0.0

    def test_zero_demand_zero_ss_extended(self):
        """Demanda cero → SS=0 sin excepción (sin varianza ni media)."""
        params = _make_params(sigma_lt_days=5.0)
        ss = compute_safety_stock(params, mean_demand_daily=0.0, sigma_demand_daily=0.0)
        assert ss == pytest.approx(0.0, abs=1e-9)

    def test_higher_z_higher_ss(self):
        """Mayor z_factor → mayor SS."""
        p_low = _make_params(z_factor=1.28)
        p_high = _make_params(z_factor=2.33)
        mean, sigma = 10.0, 3.0
        ss_low = compute_safety_stock(p_low, mean, sigma)
        ss_high = compute_safety_stock(p_high, mean, sigma)
        assert ss_high > ss_low

    def test_higher_sigma_lt_higher_ss(self):
        """Mayor σ_LT → mayor SS en fórmula extended."""
        p_low = _make_params(sigma_lt_days=0.0)
        p_high = _make_params(sigma_lt_days=10.0)
        mean, sigma = 10.0, 3.0
        ss_low = compute_safety_stock(p_low, mean, sigma)
        ss_high = compute_safety_stock(p_high, mean, sigma)
        assert ss_high > ss_low

    def test_unknown_method_warns_and_uses_extended(self):
        """Método desconocido → warning + usa extended como fallback."""
        import warnings
        params = _make_params(ss_method="unknown_method")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ss = compute_safety_stock(params, 10.0, 3.0)
        assert ss > 0
        assert len(w) >= 1


# ===========================================================================
# TestComputeRop
# ===========================================================================

class TestComputeRop:
    def test_basic_formula(self):
        """ROP = mean_daily × lead_time_days + SS."""
        mean_daily = 5.0
        lt = 20.0
        ss = 30.0
        assert compute_rop(mean_daily, lt, ss) == pytest.approx(5.0 * 20.0 + 30.0)

    def test_rop_ge_ss(self):
        """ROP siempre >= SS (porque DDLT >= 0)."""
        mean = 8.0
        lt = 15.0
        ss = 40.0
        rop = compute_rop(mean, lt, ss)
        assert rop >= ss

    def test_rop_nonnegative(self):
        """ROP siempre >= 0."""
        assert compute_rop(0.0, 0.0, 0.0) >= 0.0
        assert compute_rop(0.0, 10.0, 0.0) >= 0.0

    def test_zero_ss_rop_equals_ddlt(self):
        """Con SS=0, ROP = mean_daily × LT (DDLT)."""
        mean = 3.0
        lt = 25.0
        assert compute_rop(mean, lt, 0.0) == pytest.approx(3.0 * 25.0)


# ===========================================================================
# TestComputeSkuSafetyStock
# ===========================================================================

class TestComputeSkuSafetyStock:
    def test_returns_safety_stock_result(self):
        params = _make_params()
        df = _make_demand_df([100.0, 120.0, 80.0, 110.0, 90.0])
        result = compute_sku_safety_stock(params, df)
        assert isinstance(result, SafetyStockResult)

    def test_all_fields_finite_with_valid_series(self):
        params = _make_params()
        df = _make_demand_df([100.0, 120.0, 80.0, 110.0, 90.0])
        result = compute_sku_safety_stock(params, df)
        assert not math.isnan(result.safety_stock)
        assert not math.isnan(result.reorder_point)
        assert not math.isnan(result.mean_demand_daily)
        assert not math.isnan(result.sigma_demand_daily)
        assert not math.isnan(result.coverage_ss_days)

    def test_safety_stock_nonnegative(self):
        params = _make_params()
        df = _make_demand_df([50.0, 60.0, 70.0, 80.0, 90.0])
        result = compute_sku_safety_stock(params, df)
        assert result.safety_stock >= 0.0

    def test_rop_ge_safety_stock(self):
        """ROP = DDLT + SS → siempre >= SS."""
        params = _make_params()
        df = _make_demand_df([50.0, 60.0, 70.0, 80.0, 90.0])
        result = compute_sku_safety_stock(params, df)
        assert result.reorder_point >= result.safety_stock

    def test_sigma_lt_increases_ss(self):
        """Con mayor σ_LT → SS mayor."""
        df = _make_demand_df([100.0, 120.0, 80.0, 110.0, 90.0])
        p_low = _make_params(sigma_lt_days=0.0)
        p_high = _make_params(sigma_lt_days=15.0)
        ss_low = compute_sku_safety_stock(p_low, df).safety_stock
        ss_high = compute_sku_safety_stock(p_high, df).safety_stock
        assert ss_high > ss_low

    def test_coverage_ss_days_correct(self):
        """coverage_ss_days = SS / mean_daily."""
        params = _make_params(sigma_lt_days=0.0)
        df = _make_demand_df([100.0, 100.0, 100.0, 100.0, 100.0])
        result = compute_sku_safety_stock(params, df)
        if result.mean_demand_daily > 0:
            expected = result.safety_stock / result.mean_demand_daily
            assert result.coverage_ss_days == pytest.approx(expected, rel=1e-6)

    def test_coverage_ss_days_zero_when_no_demand(self):
        """coverage_ss_days = 0 cuando mean_daily = 0."""
        params = _make_params(ss_method="simple_pct_lt")
        df = _make_demand_df([0.0, 0.0, 0.0, 0.0])
        result = compute_sku_safety_stock(params, df)
        assert result.coverage_ss_days == 0.0

    def test_ss_method_propagated(self):
        params = _make_params(ss_method="simple_pct_lt")
        df = _make_demand_df([50.0, 60.0, 70.0, 80.0])
        result = compute_sku_safety_stock(params, df)
        assert result.ss_method == "simple_pct_lt"

    def test_n_periods_matches_series_length(self):
        params = _make_params()
        df = _make_demand_df([10.0, 20.0, 30.0, 40.0, 50.0])
        result = compute_sku_safety_stock(params, df)
        assert result.n_periods == 5

    def test_sku_propagated(self):
        params = _make_params(sku="MY-SKU")
        df = _make_demand_df([10.0, 20.0, 30.0, 40.0])
        result = compute_sku_safety_stock(params, df)
        assert result.sku == "MY-SKU"

    def test_to_dict_has_all_fields(self):
        params = _make_params()
        df = _make_demand_df([100.0, 120.0, 80.0, 110.0, 90.0])
        d = compute_sku_safety_stock(params, df).to_dict()
        for field in ("sku", "granularity", "mean_demand_daily", "sigma_demand_daily",
                      "safety_stock", "reorder_point", "coverage_ss_days",
                      "ss_method", "n_periods"):
            assert field in d, f"Falta campo en to_dict(): {field}"

    def test_short_series_returns_zero_ss(self):
        """Serie < 3 períodos → SS=0 (stats retornan ceros)."""
        params = _make_params()
        df = _make_demand_df([100.0, 200.0])
        import warnings
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = compute_sku_safety_stock(params, df)
        assert result.safety_stock == 0.0


# ===========================================================================
# TestPlanningServiceSafetyStock (integración con datos reales)
# ===========================================================================

@pytest.mark.skipif(not _HAS_REAL_DATA, reason="Datos reales no disponibles")
class TestPlanningServiceSafetyStock:
    def test_sku_safety_stock_returns_dict(self):
        catalog = _repo.load_table("product_catalog")
        sku = catalog["sku"].iloc[0]
        result = _svc.sku_safety_stock(sku, abc_class="A")
        assert isinstance(result, dict)

    def test_safety_stock_has_all_fields(self):
        catalog = _repo.load_table("product_catalog")
        sku = catalog["sku"].iloc[0]
        result = _svc.sku_safety_stock(sku, abc_class="B")
        for field in ("sku", "granularity", "mean_demand_daily", "sigma_demand_daily",
                      "safety_stock", "reorder_point", "coverage_ss_days",
                      "ss_method", "n_periods"):
            assert field in result, f"Falta campo: {field}"

    def test_safety_stock_nonneg_for_active_sku(self):
        """safety_stock >= 0 para cualquier SKU activo."""
        df = _svc.classify_catalog()
        active_skus = df[df["abc_class"].notna()]["sku"].head(5).tolist()
        for sku in active_skus:
            result = _svc.sku_safety_stock(sku)
            assert result["safety_stock"] >= 0.0, f"SS negativo para {sku}"

    def test_rop_ge_ss_for_active_sku(self):
        """ROP >= SS para cualquier SKU activo."""
        df = _svc.classify_catalog()
        active_skus = df[df["abc_class"].notna()]["sku"].head(5).tolist()
        for sku in active_skus:
            result = _svc.sku_safety_stock(sku)
            assert result["reorder_point"] >= result["safety_stock"], (
                f"ROP < SS para {sku}: ROP={result['reorder_point']}, SS={result['safety_stock']}"
            )

    def test_abc_class_inferred_when_not_provided(self):
        """Si no se provee abc_class, se deriva de classify_single_sku."""
        df = _svc.classify_catalog()
        active_skus = df[df["abc_class"].notna()]["sku"].head(1).tolist()
        if active_skus:
            sku = active_skus[0]
            result = _svc.sku_safety_stock(sku)
            assert isinstance(result, dict)
            assert result["safety_stock"] >= 0.0

    def test_ss_method_extended_for_class_a(self):
        """SKU clase A debe usar método 'extended'."""
        df = _svc.classify_catalog()
        a_skus = df[df["abc_class"] == "A"]["sku"].head(1).tolist()
        if a_skus:
            result = _svc.sku_safety_stock(a_skus[0], abc_class="A")
            assert result["ss_method"] == "extended"

    def test_ss_method_simple_for_class_c(self):
        """SKU clase C debe usar método 'simple_pct_lt'."""
        df = _svc.classify_catalog()
        c_skus = df[df["abc_class"] == "C"]["sku"].head(1).tolist()
        if c_skus:
            result = _svc.sku_safety_stock(c_skus[0], abc_class="C")
            assert result["ss_method"] == "simple_pct_lt"
