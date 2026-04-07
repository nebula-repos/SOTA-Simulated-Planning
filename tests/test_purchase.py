"""Tests unitarios para el Motor de Decisión de Reposición (Fase 5).

Cubre:
- compute_recommended_qty: MOQ, pack_size, cantidad cero
- compute_eoq: cálculo básico, parámetros faltantes
- compute_urgency_score: quiebre > substock > equilibrio > sobrestock
- compute_order_deadline: con y sin margen, sin demanda
- compute_excess_carrying_cost: cálculo básico, cero en casos vacíos
- build_purchase_recommendation: integración de campos clave
- aggregate_by_supplier: agrupación y ordenamiento
- purchase_plan_summary: KPIs del plan completo
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from planning_core.purchase.recommendation import (
    PurchaseRecommendation,
    build_purchase_recommendation,
    compute_eoq,
    compute_excess_carrying_cost,
    compute_order_deadline,
    compute_recommended_qty,
    compute_urgency_score,
    generate_purchase_plan,
)
from planning_core.purchase.order_proposal import (
    PurchaseProposal,
    aggregate_by_supplier,
    purchase_plan_summary,
)
from planning_core.inventory.diagnostics import InventoryDiagnosis
from planning_core.inventory.params import InventoryParams


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_params() -> InventoryParams:
    return InventoryParams(
        sku="SKU-TEST",
        lead_time_days=30.0,
        sigma_lt_days=5.0,
        review_period_days=14.0,
        carrying_cost_rate=0.25,
        abc_class="B",
        csl_target=0.945,
        z_factor=1.60,
        ss_method="standard",
    )


def _make_diagnosis(
    sku: str = "SKU-TEST",
    health_status: str = "substock",
    alert_level: str = "naranja",
    suggested_order_qty: float = 50.0,
    excess_units: float = 0.0,
    stockout_probability: float = 0.35,
    stock_efectivo: float = 20.0,
    reorder_point: float = 70.0,
    coverage_net_days: float = 15.0,
    mean_demand_daily: float = 2.0,
    abc_class: str = "B",
) -> InventoryDiagnosis:
    return InventoryDiagnosis(
        sku=sku,
        abc_class=abc_class,
        on_hand=20.0,
        on_order=0.0,
        stock_efectivo=stock_efectivo,
        mean_demand_daily=mean_demand_daily,
        coverage_net_days=coverage_net_days,
        coverage_obj_days=44.0,
        positioning_ratio=coverage_net_days / 44.0,
        safety_stock=10.0,
        reorder_point=reorder_point,
        coverage_ss_days=5.0,
        lead_time_days=30.0,
        review_period_days=14.0,
        health_status=health_status,
        alert_level=alert_level,
        stockout_probability=stockout_probability,
        suggested_order_qty=suggested_order_qty,
        excess_units=excess_units,
        is_dead_stock=False,
        diagnosis_text="texto de prueba",
    )


@pytest.fixture
def substock_diagnosis() -> InventoryDiagnosis:
    return _make_diagnosis()


@pytest.fixture
def sobrestock_diagnosis() -> InventoryDiagnosis:
    return _make_diagnosis(
        health_status="sobrestock_leve",
        alert_level="amarillo",
        suggested_order_qty=0.0,
        excess_units=80.0,
        stockout_probability=0.02,
        stock_efectivo=200.0,
        reorder_point=70.0,
        coverage_net_days=100.0,
    )


@pytest.fixture
def catalog_row() -> pd.Series:
    return pd.Series({
        "sku": "SKU-TEST",
        "name": "Producto de Prueba",
        "supplier": "Proveedor A",
        "cost": 100_000.0,
        "moq": 10.0,
        "base_price": 130_000.0,
    })


# ---------------------------------------------------------------------------
# compute_recommended_qty
# ---------------------------------------------------------------------------

class TestComputeRecommendedQty:
    def test_respects_moq_when_suggested_less(self):
        assert compute_recommended_qty(5, moq=10) == 10.0

    def test_suggested_greater_than_moq(self):
        assert compute_recommended_qty(15, moq=10) == 15.0

    def test_rounds_to_pack_size(self):
        # 50 unidades, pack de 24 → ceil(50/24)*24 = 72
        result = compute_recommended_qty(50, moq=1, pack_size=24)
        assert result == 72.0

    def test_moq_and_pack_size_combined(self):
        # suggested=85, moq=100, pack=6 → base=100, ceil(100/6)*6=102
        result = compute_recommended_qty(85, moq=100, pack_size=6)
        assert result == 102.0

    def test_zero_when_no_need(self):
        assert compute_recommended_qty(0) == 0.0
        assert compute_recommended_qty(-5) == 0.0

    def test_default_moq_pack_size_are_one(self):
        assert compute_recommended_qty(37) == 37.0


# ---------------------------------------------------------------------------
# compute_eoq
# ---------------------------------------------------------------------------

class TestComputeEoq:
    def test_basic_calculation(self):
        # EOQ = sqrt(2 * 1200 * 50000 / (100000 * 0.25)) = sqrt(120000000/25000) = sqrt(4800) ≈ 69.28
        result = compute_eoq(annual_demand=1200, order_cost=50_000, unit_cost=100_000, carrying_rate=0.25)
        assert math.isclose(result, math.sqrt(4800), rel_tol=1e-5)

    def test_returns_zero_when_order_cost_is_zero(self):
        assert compute_eoq(1200, 0, 100_000, 0.25) == 0.0

    def test_returns_zero_when_unit_cost_is_zero(self):
        assert compute_eoq(1200, 50_000, 0, 0.25) == 0.0

    def test_returns_zero_when_carrying_rate_is_zero(self):
        assert compute_eoq(1200, 50_000, 100_000, 0) == 0.0

    def test_returns_zero_when_no_demand(self):
        assert compute_eoq(0, 50_000, 100_000, 0.25) == 0.0


# ---------------------------------------------------------------------------
# compute_urgency_score
# ---------------------------------------------------------------------------

class TestComputeUrgencyScore:
    def test_quiebre_has_highest_score(self):
        score = compute_urgency_score("quiebre_inminente", 0.9, "A", 5.0, 30.0)
        assert score > 90

    def test_substock_lower_than_quiebre(self):
        score_q = compute_urgency_score("quiebre_inminente", 0.5, "B", 10.0, 30.0)
        score_s = compute_urgency_score("substock", 0.5, "B", 10.0, 30.0)
        assert score_q > score_s

    def test_sobrestock_returns_zero(self):
        score = compute_urgency_score("sobrestock_critico", 0.01, "A", 200.0, 30.0)
        assert score == 0.0

    def test_dead_stock_returns_zero(self):
        score = compute_urgency_score("dead_stock", 0.0, "C", 9999.0, 30.0)
        assert score == 0.0

    def test_abc_a_higher_than_c_same_status(self):
        score_a = compute_urgency_score("substock", 0.3, "A", 20.0, 30.0)
        score_c = compute_urgency_score("substock", 0.3, "C", 20.0, 30.0)
        assert score_a > score_c

    def test_score_bounded_0_100(self):
        score = compute_urgency_score("quiebre_inminente", 1.0, "A", 0.0, 1.0)
        assert 0.0 <= score <= 100.0


# ---------------------------------------------------------------------------
# compute_order_deadline
# ---------------------------------------------------------------------------

class TestComputeOrderDeadline:
    def test_deadline_with_margin(self):
        # coverage=45 días, LT=30 → margen=15 días desde referencia
        ref = date(2026, 3, 30)
        deadline = compute_order_deadline(lead_time_days=30, coverage_net_days=45, reference_date=ref)
        assert deadline == "2026-04-14"

    def test_deadline_today_when_already_behind(self):
        ref = date(2026, 3, 30)
        deadline = compute_order_deadline(lead_time_days=30, coverage_net_days=10, reference_date=ref)
        assert deadline == "2026-03-30"

    def test_none_when_no_demand(self):
        # coverage_net_days >= 9000 → sin demanda
        deadline = compute_order_deadline(lead_time_days=30, coverage_net_days=9999.0)
        assert deadline is None

    def test_deadline_at_zero_coverage(self):
        ref = date(2026, 3, 30)
        deadline = compute_order_deadline(lead_time_days=30, coverage_net_days=0, reference_date=ref)
        assert deadline == "2026-03-30"


# ---------------------------------------------------------------------------
# compute_excess_carrying_cost
# ---------------------------------------------------------------------------

class TestComputeExcessCarryingCost:
    def test_basic_calculation(self):
        # 80 units × 100_000 CLP/u × 0.25 rate × (90/365 días)
        expected = 80 * 100_000 * 0.25 * (90 / 365)
        result = compute_excess_carrying_cost(80, 100_000, 0.25, 90)
        assert math.isclose(result, expected, rel_tol=1e-6)

    def test_zero_when_no_excess(self):
        assert compute_excess_carrying_cost(0, 100_000, 0.25, 90) == 0.0

    def test_zero_when_no_unit_cost(self):
        assert compute_excess_carrying_cost(80, 0, 0.25, 90) == 0.0

    def test_zero_when_days_to_normal_is_zero(self):
        assert compute_excess_carrying_cost(80, 100_000, 0.25, 0) == 0.0


# ---------------------------------------------------------------------------
# build_purchase_recommendation
# ---------------------------------------------------------------------------

class TestBuildPurchaseRecommendation:
    def test_substock_has_final_qty_gt_zero(self, substock_diagnosis, base_params, catalog_row):
        rec = build_purchase_recommendation("SKU-TEST", substock_diagnosis, base_params, catalog_row)
        assert rec.final_qty > 0

    def test_sobrestock_has_final_qty_zero(self, sobrestock_diagnosis, base_params, catalog_row):
        rec = build_purchase_recommendation("SKU-TEST", sobrestock_diagnosis, base_params, catalog_row)
        assert rec.final_qty == 0.0

    def test_sobrestock_has_excess_carrying_cost(self, sobrestock_diagnosis, base_params, catalog_row):
        rec = build_purchase_recommendation("SKU-TEST", sobrestock_diagnosis, base_params, catalog_row)
        assert rec.excess_units > 0
        assert rec.excess_carrying_cost > 0

    def test_moq_from_catalog_respected(self, substock_diagnosis, base_params, catalog_row):
        # catalog_row.moq = 10, suggested = 50 → recommended_qty >= 10 rounded to pack=1
        rec = build_purchase_recommendation("SKU-TEST", substock_diagnosis, base_params, catalog_row)
        assert rec.moq == 10.0
        assert rec.recommended_qty >= 10.0

    def test_fields_are_populated(self, substock_diagnosis, base_params, catalog_row):
        rec = build_purchase_recommendation("SKU-TEST", substock_diagnosis, base_params, catalog_row)
        assert rec.sku == "SKU-TEST"
        assert rec.name == "Producto de Prueba"
        assert rec.supplier == "Proveedor A"
        assert rec.abc_class == "B"
        assert 0.0 <= rec.urgency_score <= 100.0
        assert rec.diagnosis_text != ""
        assert rec.action != ""

    def test_substock_has_order_deadline(self, substock_diagnosis, base_params, catalog_row):
        rec = build_purchase_recommendation("SKU-TEST", substock_diagnosis, base_params, catalog_row)
        assert rec.order_deadline is not None

    def test_sobrestock_has_no_deadline(self, sobrestock_diagnosis, base_params, catalog_row):
        rec = build_purchase_recommendation("SKU-TEST", sobrestock_diagnosis, base_params, catalog_row)
        assert rec.order_deadline is None

    def test_unit_cost_from_catalog(self, substock_diagnosis, base_params, catalog_row):
        rec = build_purchase_recommendation("SKU-TEST", substock_diagnosis, base_params, catalog_row)
        assert rec.unit_cost == 100_000.0


# ---------------------------------------------------------------------------
# aggregate_by_supplier
# ---------------------------------------------------------------------------

class TestAggregateBySupplier:
    def _make_rec(self, sku, supplier, final_qty, urgency_score, unit_cost=50_000) -> PurchaseRecommendation:
        return PurchaseRecommendation(
            sku=sku, name=sku, supplier=supplier, abc_class="B",
            health_status="substock", alert_level="naranja",
            stockout_probability=0.3, stock_efectivo=10.0, reorder_point=50.0,
            suggested_order_qty=final_qty, moq=1.0, pack_size=1.0,
            recommended_qty=final_qty, eoq=0.0, final_qty=final_qty,
            urgency_score=urgency_score, days_until_stockout=15.0,
            order_deadline="2026-04-10", excess_units=0.0,
            days_to_normal=0.0, excess_carrying_cost=0.0,
            unit_cost=unit_cost, carrying_cost_rate=0.25,
            diagnosis_text="test", action="Ordenar",
            demand_signal_source="historical",
        )

    def test_groups_by_supplier(self):
        recs = [
            self._make_rec("SKU-001", "Proveedor A", 10, 70),
            self._make_rec("SKU-002", "Proveedor A", 20, 60),
            self._make_rec("SKU-003", "Proveedor B", 15, 80),
        ]
        proposals = aggregate_by_supplier(recs)
        suppliers = [p.supplier for p in proposals]
        assert "Proveedor A" in suppliers
        assert "Proveedor B" in suppliers
        assert len(proposals) == 2

    def test_ordered_by_max_urgency_score(self):
        recs = [
            self._make_rec("SKU-001", "Proveedor A", 10, 60),
            self._make_rec("SKU-002", "Proveedor B", 15, 90),
        ]
        proposals = aggregate_by_supplier(recs)
        assert proposals[0].supplier == "Proveedor B"

    def test_excludes_zero_final_qty(self):
        # SKU con final_qty=0 no debe aparecer en propuestas
        recs = [
            self._make_rec("SKU-001", "Proveedor A", 0, 0),   # sobrestock
            self._make_rec("SKU-002", "Proveedor B", 15, 80),
        ]
        proposals = aggregate_by_supplier(recs)
        assert len(proposals) == 1
        assert proposals[0].supplier == "Proveedor B"

    def test_total_units_sum(self):
        recs = [
            self._make_rec("SKU-001", "Proveedor A", 10, 70),
            self._make_rec("SKU-002", "Proveedor A", 30, 60),
        ]
        proposals = aggregate_by_supplier(recs)
        assert proposals[0].total_units == 40.0

    def test_total_cost_estimate(self):
        recs = [
            self._make_rec("SKU-001", "Proveedor A", 10, 70, unit_cost=100_000),
        ]
        proposals = aggregate_by_supplier(recs)
        assert proposals[0].total_cost_estimate == 10 * 100_000


# ---------------------------------------------------------------------------
# purchase_plan_summary
# ---------------------------------------------------------------------------

class TestPurchasePlanSummary:
    def _make_rec_status(self, sku, health_status, final_qty=0.0, excess_units=0.0) -> PurchaseRecommendation:
        return PurchaseRecommendation(
            sku=sku, name=sku, supplier="Prov", abc_class="B",
            health_status=health_status,
            alert_level="none" if health_status == "equilibrio" else "naranja",
            stockout_probability=0.1, stock_efectivo=50.0, reorder_point=70.0,
            suggested_order_qty=final_qty, moq=1.0, pack_size=1.0,
            recommended_qty=final_qty, eoq=0.0, final_qty=final_qty,
            urgency_score=50.0 if final_qty > 0 else 0.0,
            days_until_stockout=30.0, order_deadline=None,
            excess_units=excess_units, days_to_normal=0.0, excess_carrying_cost=0.0,
            unit_cost=50_000, carrying_cost_rate=0.25,
            diagnosis_text="", action="",
            demand_signal_source="historical",
        )

    def test_counts_by_status(self):
        recs = [
            self._make_rec_status("SKU-001", "quiebre_inminente", final_qty=20),
            self._make_rec_status("SKU-002", "substock", final_qty=10),
            self._make_rec_status("SKU-003", "equilibrio"),
            self._make_rec_status("SKU-004", "sobrestock_leve", excess_units=50),
            self._make_rec_status("SKU-005", "dead_stock"),
        ]
        summary = purchase_plan_summary(recs)
        assert summary["sku_quiebre"] == 1
        assert summary["sku_substock"] == 1
        assert summary["sku_equilibrio"] == 1
        assert summary["sku_sobrestock"] == 1
        assert summary["sku_dead_stock"] == 1
        assert summary["sku_to_order"] == 2
        assert summary["total_units_to_order"] == 30.0
        assert summary["total_excess_units"] == 50.0
