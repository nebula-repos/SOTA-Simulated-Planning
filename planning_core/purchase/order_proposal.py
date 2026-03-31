"""Motor de Decisión de Reposición — agregación por proveedor.

Agrupa las PurchaseRecommendation individuales en PurchaseProposal por proveedor,
facilitando la consolidación de órdenes de compra reales.

Funciones públicas
------------------
aggregate_by_supplier(recommendations) → list[PurchaseProposal]
purchase_plan_summary(recommendations) → dict
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from planning_core.purchase.recommendation import PurchaseRecommendation


# ---------------------------------------------------------------------------
# Dataclass de propuesta por proveedor
# ---------------------------------------------------------------------------

@dataclass
class PurchaseProposal:
    """Propuesta de compra consolidada para un proveedor.

    Attributes
    ----------
    supplier : str or None
        Nombre del proveedor. None para SKUs sin proveedor asignado.
    sku_count : int
        Número de SKUs a ordenar a este proveedor.
    total_units : float
        Total de unidades a ordenar (suma de final_qty).
    total_cost_estimate : float
        Estimación del costo total (sum final_qty × unit_cost).
    max_urgency_score : float
        Score de urgencia más alto entre los SKUs del proveedor.
        Determina la prioridad del proveedor en la lista.
    alert_levels : list[str]
        Niveles de alerta únicos presentes en los SKUs del proveedor.
    skus : list[PurchaseRecommendation]
        Lista de recomendaciones individuales, ordenadas por urgency_score desc.
    """

    supplier: str | None
    sku_count: int
    total_units: float
    total_cost_estimate: float
    max_urgency_score: float
    alert_levels: list[str]
    skus: list[PurchaseRecommendation] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["skus"] = [s.to_dict() for s in self.skus]
        return d


# ---------------------------------------------------------------------------
# Agregación por proveedor
# ---------------------------------------------------------------------------

def aggregate_by_supplier(
    recommendations: list[PurchaseRecommendation],
) -> list[PurchaseProposal]:
    """Agrupa las recomendaciones de compra por proveedor.

    Solo incluye SKUs con final_qty > 0 (canal de compra activo).
    Los SKUs de sobrestock (final_qty == 0) no generan propuesta de compra
    — se comunican por separado vía el campo excess_* de cada recomendación.

    Parameters
    ----------
    recommendations : list[PurchaseRecommendation]
        Salida de ``generate_purchase_plan()``.

    Returns
    -------
    list[PurchaseProposal]
        Lista ordenada por max_urgency_score descendente. Un elemento por
        proveedor (o None si el SKU no tiene proveedor asignado).
    """
    groups: dict[str | None, list[PurchaseRecommendation]] = {}

    for rec in recommendations:
        if rec.final_qty <= 0:
            continue
        key = rec.supplier
        groups.setdefault(key, []).append(rec)

    proposals: list[PurchaseProposal] = []
    for supplier, recs in groups.items():
        recs_sorted = sorted(recs, key=lambda r: -r.urgency_score)
        total_units = sum(r.final_qty for r in recs_sorted)
        total_cost = sum(r.final_qty * r.unit_cost for r in recs_sorted)
        max_score = max(r.urgency_score for r in recs_sorted)
        alert_levels = sorted({r.alert_level for r in recs_sorted if r.alert_level != "none"})

        proposals.append(PurchaseProposal(
            supplier=supplier,
            sku_count=len(recs_sorted),
            total_units=total_units,
            total_cost_estimate=total_cost,
            max_urgency_score=max_score,
            alert_levels=alert_levels,
            skus=recs_sorted,
        ))

    proposals.sort(key=lambda p: -p.max_urgency_score)
    return proposals


# ---------------------------------------------------------------------------
# Resumen ejecutivo del plan
# ---------------------------------------------------------------------------

def purchase_plan_summary(
    recommendations: list[PurchaseRecommendation],
) -> dict:
    """Genera KPIs resumen del plan de reposición completo.

    Parameters
    ----------
    recommendations : list[PurchaseRecommendation]
        Salida de ``generate_purchase_plan()``, incluye todos los estados.

    Returns
    -------
    dict
        KPIs del plan:
        - ``sku_quiebre``        : SKUs en quiebre inminente (🔴)
        - ``sku_substock``       : SKUs en substock (🟠)
        - ``sku_equilibrio``     : SKUs en equilibrio (🟢)
        - ``sku_sobrestock``     : SKUs en sobrestock leve o crítico
        - ``sku_dead_stock``     : SKUs sin movimiento
        - ``sku_to_order``       : SKUs con final_qty > 0 (requieren compra)
        - ``total_units_to_order``: Unidades totales a ordenar
        - ``total_cost_estimate`` : Estimación de costo total de las compras
        - ``total_excess_units``  : Unidades totales en exceso (canal sobrestock)
        - ``total_excess_cost``   : Costo estimado del exceso inmovilizado
        - ``supplier_count``      : Proveedores únicos con órdenes pendientes
    """
    sku_quiebre = sum(1 for r in recommendations if r.health_status == "quiebre_inminente")
    sku_substock = sum(1 for r in recommendations if r.health_status == "substock")
    sku_equilibrio = sum(1 for r in recommendations if r.health_status == "equilibrio")
    sku_sobrestock = sum(
        1 for r in recommendations
        if r.health_status in ("sobrestock_leve", "sobrestock_critico")
    )
    sku_dead = sum(1 for r in recommendations if r.health_status == "dead_stock")
    to_order = [r for r in recommendations if r.final_qty > 0]

    suppliers_with_orders = {r.supplier for r in to_order}

    return {
        "sku_quiebre": sku_quiebre,
        "sku_substock": sku_substock,
        "sku_equilibrio": sku_equilibrio,
        "sku_sobrestock": sku_sobrestock,
        "sku_dead_stock": sku_dead,
        "sku_to_order": len(to_order),
        "total_units_to_order": sum(r.final_qty for r in to_order),
        "total_cost_estimate": sum(r.final_qty * r.unit_cost for r in to_order),
        "total_excess_units": sum(r.excess_units for r in recommendations),
        "total_excess_cost": sum(r.excess_carrying_cost for r in recommendations),
        "supplier_count": len(suppliers_with_orders),
    }
