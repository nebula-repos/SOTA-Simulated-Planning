"""Pipeline de decisión de reposición (Fase 5).

Contiene la lógica de orquestación de las funciones de compra extraída de
``PlanningService``. Todas las funciones reciben ``service`` para acceder al
repositorio, al logger y al pipeline de inventario.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from planning_core.inventory.params import get_sku_params
from planning_core.inventory.safety_stock import compute_sku_safety_stock
from planning_core.purchase.order_proposal import (
    aggregate_by_supplier,
    purchase_plan_summary,
)
from planning_core.purchase.recommendation import (
    build_purchase_recommendation,
    generate_purchase_plan,
)

if TYPE_CHECKING:
    from planning_core.services import PlanningService


def _counts_dict(values: pd.Series) -> dict[str, int]:
    if values.empty:
        return {}
    return {str(k): int(v) for k, v in values.value_counts(dropna=False).items()}


# ---------------------------------------------------------------------------
# run_purchase_plan
# ---------------------------------------------------------------------------

def run_purchase_plan(
    service: "PlanningService",
    granularity: str | None = None,
    include_equilibrio: bool = False,
    include_sobrestock: bool = True,
    limit: int = 500,
    simple_safety_pct: float = 0.5,
) -> list[dict]:
    """Genera el plan de reposición priorizado para el catálogo.

    Llama a ``catalog_health_report`` (que ya incluye señal de forecast) y
    convierte cada diagnóstico en una ``PurchaseRecommendation``.

    Returns
    -------
    list[dict]
        PurchaseRecommendation.to_dict() ordenados por urgency_score desc.
    """
    with service.event_logger.span(
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
        health_df = service.catalog_health_report(
            granularity=granularity,
            simple_safety_pct=simple_safety_pct,
        )
        if health_df.empty:
            span.set_status("empty")
            span.set_result(n_items=0, n_actionable=0)
            return []

        if granularity is None:
            granularity = service.official_classification_granularity()

        catalog = service.repository.load_table("product_catalog")
        manifest = service.repository.load_manifest()

        params_map: dict[str, object] = {}
        for _, row in health_df.iterrows():
            sku = row["sku"]
            params_map[sku] = get_sku_params(
                sku, row.get("abc_class"), row.get("supplier"),
                service.repository, manifest,
            )

        recommendations = generate_purchase_plan(
            health_rows=health_df.to_dict(orient="records"),
            catalog_df=catalog,
            params_map=params_map,
            manifest_config=manifest,
            include_equilibrio=include_equilibrio,
            include_sobrestock=include_sobrestock,
        )

        rec_dicts = [r.to_dict() for r in recommendations[:limit]]
        actionable = sum(1 for item in rec_dicts if float(item.get("final_qty") or 0.0) > 0.0)
        span.set_metrics(n_items=len(rec_dicts), n_actionable=actionable)
        span.set_result(
            health_status_distribution=_counts_dict(pd.Series([item.get("health_status") for item in rec_dicts])),
            max_urgency_score=max((float(item.get("urgency_score") or 0.0) for item in rec_dicts), default=0.0),
        )
        return rec_dicts


# ---------------------------------------------------------------------------
# run_purchase_plan_by_supplier
# ---------------------------------------------------------------------------

def run_purchase_plan_by_supplier(
    service: "PlanningService",
    granularity: str | None = None,
    simple_safety_pct: float = 0.5,
) -> list[dict]:
    """Agrupa el plan de compra por proveedor (solo final_qty > 0).

    Returns
    -------
    list[dict]
        PurchaseProposal.to_dict() ordenados por max_urgency_score desc.
    """
    with service.event_logger.span(
        "purchase.plan_by_supplier",
        module="purchase",
        entity_type="catalog",
        entity_id="all",
        params={"granularity": granularity, "simple_safety_pct": simple_safety_pct},
    ) as span:
        health_df = service.catalog_health_report(
            granularity=granularity,
            simple_safety_pct=simple_safety_pct,
        )
        if health_df.empty:
            span.set_status("empty")
            span.set_result(n_suppliers=0, n_items=0)
            return []

        if granularity is None:
            granularity = service.official_classification_granularity()

        catalog = service.repository.load_table("product_catalog")
        manifest = service.repository.load_manifest()

        params_map: dict[str, object] = {}
        for _, row in health_df.iterrows():
            sku = row["sku"]
            params_map[sku] = get_sku_params(
                sku, row.get("abc_class"), row.get("supplier"),
                service.repository, manifest,
            )

        recommendations = generate_purchase_plan(
            health_rows=health_df.to_dict(orient="records"),
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
            max_urgency_score=max(
                (float(item.get("max_urgency_score") or 0.0) for item in proposal_dicts), default=0.0
            ),
        )
        return proposal_dicts


# ---------------------------------------------------------------------------
# run_purchase_plan_summary
# ---------------------------------------------------------------------------

def run_purchase_plan_summary(
    service: "PlanningService",
    granularity: str | None = None,
    simple_safety_pct: float = 0.5,
) -> dict:
    """KPIs ejecutivos del plan de reposición completo.

    Returns
    -------
    dict
        Contadores por health_status + totales de unidades y capital.
    """
    with service.event_logger.span(
        "purchase.plan_summary",
        module="purchase",
        entity_type="catalog",
        entity_id="all",
        params={"granularity": granularity, "simple_safety_pct": simple_safety_pct},
    ) as span:
        health_df = service.catalog_health_report(
            granularity=granularity,
            simple_safety_pct=simple_safety_pct,
        )
        if health_df.empty:
            span.set_status("empty")
            span.set_result(summary={})
            return {}

        if granularity is None:
            granularity = service.official_classification_granularity()

        catalog = service.repository.load_table("product_catalog")
        manifest = service.repository.load_manifest()

        params_map: dict[str, object] = {}
        for _, row in health_df.iterrows():
            sku = row["sku"]
            params_map[sku] = get_sku_params(
                sku, row.get("abc_class"), row.get("supplier"),
                service.repository, manifest,
            )

        recommendations = generate_purchase_plan(
            health_rows=health_df.to_dict(orient="records"),
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


# ---------------------------------------------------------------------------
# run_sku_purchase_recommendation
# ---------------------------------------------------------------------------

def run_sku_purchase_recommendation(
    service: "PlanningService",
    sku: str,
    abc_class: str | None = None,
    granularity: str | None = None,
    simple_safety_pct: float = 0.5,
) -> dict | None:
    """Genera la recomendación de reposición para un SKU individual.

    Más rápido que correr el catálogo completo — útil para el detalle de SKU.

    Returns
    -------
    dict or None
        PurchaseRecommendation.to_dict() o None si el SKU no existe.
    """
    from planning_core.inventory.diagnostics import diagnose_sku

    if granularity is None:
        granularity = service.official_classification_granularity()

    summary = service.sku_summary(sku)
    if summary is None:
        return None

    if abc_class is None:
        profile = service.classify_single_sku(sku, granularity=granularity)
        abc_class = profile.get("abc_class") if profile else None

    catalog = service.repository.load_table("product_catalog")
    manifest = service.repository.load_manifest()
    cat_row = catalog.loc[catalog["sku"] == sku]
    supplier = cat_row["supplier"].iloc[0] if not cat_row.empty else None

    with service.event_logger.span(
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
        params = get_sku_params(sku, abc_class, supplier, service.repository, manifest)
        demand_series = service.sku_demand_series(sku, granularity=granularity)
        ss_result = compute_sku_safety_stock(
            params, demand_series, granularity=granularity, simple_safety_pct=simple_safety_pct
        )

        inventory = service.repository.load_table("inventory_snapshot")
        latest_date = inventory["snapshot_date"].max()
        sku_inv = inventory[(inventory["snapshot_date"] == latest_date) & (inventory["sku"] == sku)]
        on_hand = float(sku_inv["on_hand_qty"].sum()) if not sku_inv.empty else 0.0
        on_order = float(sku_inv["on_order_qty"].sum()) if not sku_inv.empty else 0.0

        transactions = service.repository.load_table("transactions")
        sku_tx = transactions[transactions["sku"] == sku]
        if not sku_tx.empty and "date" in sku_tx.columns:
            last_mv = pd.to_datetime(sku_tx["date"]).max()
            days_since = int((pd.to_datetime(transactions["date"]).max() - last_mv).days)
        else:
            days_since = 9999

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
