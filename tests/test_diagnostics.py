"""Tests para planning_core/inventory/diagnostics.py — D26.

Cubre: diagnose_sku con todas las bandas de salud, casos límite de demanda
cero, dead stock, P(quiebre), sentinels, y catalog_health_report (D27).
"""
from __future__ import annotations

import math
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from planning_core.inventory.diagnostics import (
    DEAD_STOCK_DAYS_THRESHOLD,
    HEALTH_BANDS,
    InventoryDiagnosis,
    _INF_COVERAGE_SENTINEL,
    _INF_RATIO_SENTINEL,
    _classify_ratio,
    _standard_normal_cdf,
    diagnose_sku,
)
from planning_core.inventory.params import InventoryParams
from planning_core.inventory.safety_stock import SafetyStockResult


# ---------------------------------------------------------------------------
# Fixtures reutilizables
# ---------------------------------------------------------------------------

def _make_params(
    ss_method: str = "standard",
    lead_time_days: float = 30.0,
    review_period_days: float = 7.0,
    z_factor: float = 1.645,
    sigma_lt_days: float = 5.0,
) -> InventoryParams:
    return InventoryParams(
        sku="SKU-TEST",
        abc_class="B",
        lead_time_days=lead_time_days,
        review_period_days=review_period_days,
        sigma_lt_days=sigma_lt_days,
        carrying_cost_rate=0.25,
        z_factor=z_factor,
        ss_method=ss_method,
        csl_target=0.945,
    )


def _make_ss(
    sku: str = "SKU-TEST",
    mean_demand_daily: float = 5.0,
    sigma_demand_daily: float = 2.0,
    safety_stock: float = 50.0,
    reorder_point: float = 200.0,
    coverage_ss_days: float = 10.0,
    ss_method: str = "standard",
) -> SafetyStockResult:
    return SafetyStockResult(
        sku=sku,
        granularity="M",
        mean_demand_daily=mean_demand_daily,
        sigma_demand_daily=sigma_demand_daily,
        safety_stock=safety_stock,
        reorder_point=reorder_point,
        coverage_ss_days=coverage_ss_days,
        ss_method=ss_method,
        n_periods=24,
    )


# ---------------------------------------------------------------------------
# _classify_ratio
# ---------------------------------------------------------------------------

class TestClassifyRatio(unittest.TestCase):
    def test_dead_stock_overrides_ratio(self):
        status, alert = _classify_ratio(1.0, is_dead_stock=True)
        self.assertEqual(status, "dead_stock")
        self.assertEqual(alert, "gris")

    def test_quiebre_inminente(self):
        status, alert = _classify_ratio(0.1, is_dead_stock=False)
        self.assertEqual(status, "quiebre_inminente")
        self.assertEqual(alert, "rojo")

    def test_substock(self):
        status, alert = _classify_ratio(0.5, is_dead_stock=False)
        self.assertEqual(status, "substock")
        self.assertEqual(alert, "naranja")

    def test_equilibrio(self):
        status, alert = _classify_ratio(1.0, is_dead_stock=False)
        self.assertEqual(status, "equilibrio")
        self.assertEqual(alert, "none")

    def test_sobrestock_leve(self):
        status, alert = _classify_ratio(1.5, is_dead_stock=False)
        self.assertEqual(status, "sobrestock_leve")
        self.assertEqual(alert, "amarillo")

    def test_sobrestock_critico(self):
        status, alert = _classify_ratio(2.5, is_dead_stock=False)
        self.assertEqual(status, "sobrestock_critico")
        self.assertEqual(alert, "gris")

    def test_boundary_quiebre_substock(self):
        # ratio = 0.3 → entra en substock (umbral exclusivo superior de quiebre)
        status, _ = _classify_ratio(0.3, is_dead_stock=False)
        self.assertEqual(status, "substock")

    def test_boundary_substock_equilibrio(self):
        status, _ = _classify_ratio(0.7, is_dead_stock=False)
        self.assertEqual(status, "equilibrio")

    def test_boundary_equilibrio_sobrestock_leve(self):
        status, _ = _classify_ratio(1.3, is_dead_stock=False)
        self.assertEqual(status, "sobrestock_leve")

    def test_boundary_sobrestock_critico(self):
        status, _ = _classify_ratio(2.0, is_dead_stock=False)
        self.assertEqual(status, "sobrestock_critico")


# ---------------------------------------------------------------------------
# _standard_normal_cdf
# ---------------------------------------------------------------------------

class TestStandardNormalCdf(unittest.TestCase):
    def test_z0_is_half(self):
        self.assertAlmostEqual(_standard_normal_cdf(0.0), 0.5, places=5)

    def test_large_positive_z(self):
        self.assertAlmostEqual(_standard_normal_cdf(4.0), 1.0, places=3)

    def test_large_negative_z(self):
        self.assertAlmostEqual(_standard_normal_cdf(-4.0), 0.0, places=3)

    def test_z165_approx(self):
        # Φ(1.645) ≈ 0.95
        self.assertAlmostEqual(_standard_normal_cdf(1.645), 0.95, places=2)


# ---------------------------------------------------------------------------
# diagnose_sku — flujo normal
# ---------------------------------------------------------------------------

class TestDiagnoseSkuNormal(unittest.TestCase):
    def setUp(self):
        self.params = _make_params()
        self.ss = _make_ss(mean_demand_daily=5.0, safety_stock=50.0, reorder_point=200.0)

    def test_returns_inventory_diagnosis(self):
        d = diagnose_sku("SKU-TEST", on_hand=300.0, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertIsInstance(d, InventoryDiagnosis)

    def test_stock_efectivo_includes_on_order(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=50.0, ss_result=self.ss, params=self.params)
        self.assertAlmostEqual(d.stock_efectivo, 150.0)

    def test_equilibrio_band(self):
        # stock que da ratio ~1.0
        coverage_obj = self.params.lead_time_days + self.params.review_period_days + self.ss.coverage_ss_days
        stock = 5.0 * coverage_obj  # exactamente coverage_obj días
        d = diagnose_sku("SKU-TEST", on_hand=stock, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertEqual(d.health_status, "equilibrio")

    def test_quiebre_inminente_low_stock(self):
        d = diagnose_sku("SKU-TEST", on_hand=1.0, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertIn(d.health_status, ("quiebre_inminente", "substock"))

    def test_sobrestock_critico_high_stock(self):
        d = diagnose_sku("SKU-TEST", on_hand=9999.0, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertEqual(d.health_status, "sobrestock_critico")

    def test_suggested_order_zero_in_equilibrio(self):
        coverage_obj = self.params.lead_time_days + self.params.review_period_days + self.ss.coverage_ss_days
        stock = 5.0 * coverage_obj
        d = diagnose_sku("SKU-TEST", on_hand=stock, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertEqual(d.suggested_order_qty, 0.0)

    def test_suggested_order_positive_in_substock(self):
        d = diagnose_sku("SKU-TEST", on_hand=1.0, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertGreater(d.suggested_order_qty, 0.0)

    def test_excess_units_zero_in_substock(self):
        d = diagnose_sku("SKU-TEST", on_hand=1.0, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertEqual(d.excess_units, 0.0)

    def test_excess_units_positive_in_sobrestock(self):
        d = diagnose_sku("SKU-TEST", on_hand=9999.0, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertGreater(d.excess_units, 0.0)

    def test_stockout_probability_in_01(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertGreaterEqual(d.stockout_probability, 0.0)
        self.assertLessEqual(d.stockout_probability, 1.0)

    def test_diagnosis_text_not_empty(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertGreater(len(d.diagnosis_text), 0)

    def test_to_dict_has_all_fields(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0, ss_result=self.ss, params=self.params)
        d_dict = d.to_dict()
        for field in ("sku", "health_status", "alert_level", "stockout_probability",
                      "suggested_order_qty", "excess_units", "is_dead_stock", "diagnosis_text"):
            self.assertIn(field, d_dict)

    def test_sku_propagated(self):
        d = diagnose_sku("SKU-XYZ", on_hand=100.0, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertEqual(d.sku, "SKU-XYZ")

    def test_abc_class_from_params_when_not_provided(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0, ss_result=self.ss, params=self.params)
        self.assertEqual(d.abc_class, "B")

    def test_abc_class_overrides_params(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0,
                         ss_result=self.ss, params=self.params, abc_class="A")
        self.assertEqual(d.abc_class, "A")


# ---------------------------------------------------------------------------
# diagnose_sku — demanda cero (edge case crítico)
# ---------------------------------------------------------------------------

class TestDiagnoseSkuZeroDemand(unittest.TestCase):
    def setUp(self):
        self.params = _make_params()
        self.ss_zero = _make_ss(
            mean_demand_daily=0.0,
            sigma_demand_daily=0.0,
            safety_stock=0.0,
            reorder_point=0.0,
            coverage_ss_days=0.0,
        )

    def test_no_stock_zero_demand(self):
        d = diagnose_sku("SKU-TEST", on_hand=0.0, on_order=0.0,
                         ss_result=self.ss_zero, params=self.params)
        self.assertEqual(d.coverage_net_days, 0.0)
        self.assertEqual(d.positioning_ratio, 0.0)

    def test_stock_with_zero_demand_uses_sentinel(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0,
                         ss_result=self.ss_zero, params=self.params)
        # coverage_net = inf → sentinel
        self.assertEqual(d.coverage_net_days, _INF_COVERAGE_SENTINEL)

    def test_ratio_sentinel_when_inf(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0,
                         ss_result=self.ss_zero, params=self.params)
        self.assertEqual(d.positioning_ratio, _INF_RATIO_SENTINEL)

    def test_stockout_prob_zero_when_no_demand(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0,
                         ss_result=self.ss_zero, params=self.params)
        self.assertEqual(d.stockout_probability, 0.0)

    def test_coverage_net_zero_when_no_stock_no_demand(self):
        d = diagnose_sku("SKU-TEST", on_hand=0.0, on_order=0.0,
                         ss_result=self.ss_zero, params=self.params)
        self.assertEqual(d.coverage_net_days, 0.0)


# ---------------------------------------------------------------------------
# diagnose_sku — dead stock
# ---------------------------------------------------------------------------

class TestDiagnoseSkuDeadStock(unittest.TestCase):
    def setUp(self):
        self.params = _make_params()
        self.ss = _make_ss(mean_demand_daily=5.0)

    def test_dead_stock_at_threshold(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0,
                         ss_result=self.ss, params=self.params,
                         days_since_last_movement=DEAD_STOCK_DAYS_THRESHOLD)
        self.assertTrue(d.is_dead_stock)
        self.assertEqual(d.health_status, "dead_stock")
        self.assertEqual(d.alert_level, "gris")

    def test_not_dead_stock_below_threshold(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0,
                         ss_result=self.ss, params=self.params,
                         days_since_last_movement=DEAD_STOCK_DAYS_THRESHOLD - 1)
        self.assertFalse(d.is_dead_stock)
        self.assertNotEqual(d.health_status, "dead_stock")

    def test_dead_stock_text_mentions_liquidation(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0,
                         ss_result=self.ss, params=self.params,
                         days_since_last_movement=DEAD_STOCK_DAYS_THRESHOLD)
        self.assertIn("liquidación", d.diagnosis_text.lower())

    def test_zero_days_not_dead_stock(self):
        d = diagnose_sku("SKU-TEST", on_hand=100.0, on_order=0.0,
                         ss_result=self.ss, params=self.params,
                         days_since_last_movement=0)
        self.assertFalse(d.is_dead_stock)


# ---------------------------------------------------------------------------
# diagnose_sku — métodos de SS (extended, simple_pct_lt)
# ---------------------------------------------------------------------------

class TestDiagnoseSkuSsMethods(unittest.TestCase):
    def test_extended_method(self):
        params = _make_params(ss_method="extended")
        ss = _make_ss(ss_method="extended")
        d = diagnose_sku("SKU-A", on_hand=500.0, on_order=0.0,
                         ss_result=ss, params=params, abc_class="A")
        self.assertIsInstance(d, InventoryDiagnosis)

    def test_simple_pct_lt_method(self):
        params = _make_params(ss_method="simple_pct_lt")
        ss = _make_ss(ss_method="simple_pct_lt", safety_stock=75.0)
        d = diagnose_sku("SKU-C", on_hand=200.0, on_order=0.0,
                         ss_result=ss, params=params, abc_class="C")
        self.assertIsInstance(d, InventoryDiagnosis)


# ---------------------------------------------------------------------------
# catalog_health_report (D27) — via PlanningService
# ---------------------------------------------------------------------------

class TestCatalogHealthReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from planning_core.repository import CanonicalRepository
        from planning_core.services import PlanningService
        data_dir = os.path.join(os.path.dirname(__file__), "..", "output")
        cls.service = PlanningService(CanonicalRepository(data_dir))

    def test_returns_dataframe(self):
        df = self.service.catalog_health_report()
        self.assertIsInstance(df, pd.DataFrame)

    def test_not_empty(self):
        df = self.service.catalog_health_report()
        self.assertFalse(df.empty)

    def test_has_required_columns(self):
        df = self.service.catalog_health_report()
        required = [
            "sku", "health_status", "alert_level", "positioning_ratio",
            "stockout_probability", "suggested_order_qty", "excess_units",
            "is_dead_stock", "excess_capital", "stockout_capital",
        ]
        for col in required:
            self.assertIn(col, df.columns, f"Columna faltante: {col}")

    def test_health_status_valid_values(self):
        df = self.service.catalog_health_report()
        valid = {
            "quiebre_inminente", "substock", "equilibrio",
            "sobrestock_leve", "sobrestock_critico", "dead_stock",
        }
        unexpected = set(df["health_status"].unique()) - valid
        self.assertEqual(unexpected, set(), f"health_status inesperado: {unexpected}")

    def test_positioning_ratio_nonnegative(self):
        df = self.service.catalog_health_report()
        self.assertTrue((df["positioning_ratio"] >= 0).all())

    def test_stockout_probability_in_01(self):
        df = self.service.catalog_health_report()
        self.assertTrue((df["stockout_probability"] >= 0).all())
        self.assertTrue((df["stockout_probability"] <= 1).all())

    def test_excess_capital_nonnegative(self):
        df = self.service.catalog_health_report()
        self.assertTrue((df["excess_capital"] >= 0).all())

    def test_stockout_capital_nonnegative(self):
        df = self.service.catalog_health_report()
        self.assertTrue((df["stockout_capital"] >= 0).all())

    def test_no_inf_or_nan_in_numeric_cols(self):
        df = self.service.catalog_health_report()
        numeric_cols = [
            "positioning_ratio", "coverage_net_days", "coverage_obj_days",
            "stockout_probability", "suggested_order_qty", "excess_units",
            "excess_capital", "stockout_capital",
        ]
        for col in numeric_cols:
            if col in df.columns:
                self.assertFalse(df[col].isna().any(), f"NaN en {col}")
                self.assertFalse(df[col].apply(math.isinf).any(), f"inf en {col}")

    def test_one_row_per_sku(self):
        df = self.service.catalog_health_report()
        self.assertEqual(df["sku"].nunique(), len(df))

    def test_has_catalog_attributes(self):
        df = self.service.catalog_health_report()
        for col in ("category", "supplier"):
            self.assertIn(col, df.columns)

    def test_dead_stock_skus_have_gris_alert(self):
        df = self.service.catalog_health_report()
        dead = df[df["is_dead_stock"]]
        if not dead.empty:
            self.assertTrue((dead["alert_level"] == "gris").all())


if __name__ == "__main__":
    unittest.main()
