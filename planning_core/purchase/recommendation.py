"""Motor de Decisión de Reposición — recomendaciones por SKU.

Implementa la capa 5 del sistema analítico de decisión de stock (§11 del PDF):
dadas las salidas del diagnóstico de inventario (InventoryDiagnosis + InventoryParams),
genera una recomendación accionable con:

- Canal de compra  (substock / quiebre): cantidad ajustada a MOQ y pack size,
  EOQ teórico, score de urgencia, fecha límite de orden.
- Canal de exceso  (sobrestock): cantidad 0, días hasta consumo natural,
  costo del exceso mantenido hasta agotamiento.

Fórmulas de referencia
-----------------------
§5.2  EOQ = √(2 × D_annual × K / (unit_cost × carrying_rate))
§9.2  cantidad ajustada: max(suggested, moq) redondeado al múltiplo de pack_size
§11.3 Urgency score (0–100): combinación de health_status, P(quiebre) y clase ABC
§11.3 Días hasta quiebre: cobertura_neta / d̄_daily

Funciones públicas
------------------
compute_recommended_qty(suggested, moq, pack_size) → float
compute_eoq(annual_demand, order_cost, unit_cost, carrying_rate) → float
compute_urgency_score(health_status, stockout_prob, abc_class, days_until_stockout, lt_days) → float
compute_days_until_stockout(coverage_net_days, lead_time_days) → float
compute_order_deadline(lead_time_days, days_until_stockout, reference_date) → str | None
compute_excess_carrying_cost(excess_units, unit_cost, carrying_rate, days_to_normal) → float
build_purchase_recommendation(sku, diagnosis, params, catalog_row, manifest_config) → PurchaseRecommendation
generate_purchase_plan(health_rows, catalog_df, params_map, manifest_config) → list[PurchaseRecommendation]
"""

from __future__ import annotations

import math
import warnings
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from planning_core.inventory.diagnostics import InventoryDiagnosis
from planning_core.inventory.params import InventoryParams


# ---------------------------------------------------------------------------
# Dataclass principal
# ---------------------------------------------------------------------------

@dataclass
class PurchaseRecommendation:
    """Recomendación de reposición para un SKU.

    Attributes
    ----------
    sku : str
        Identificador del SKU.
    name : str
        Nombre del producto (del catálogo).
    supplier : str or None
        Proveedor del SKU.
    abc_class : str or None
        Clase ABC (A / B / C).
    health_status : str
        Estado de salud: quiebre_inminente | substock | equilibrio |
        sobrestock_leve | sobrestock_critico | dead_stock.
    alert_level : str
        Nivel de alerta: rojo | naranja | amarillo | gris | none.
    stockout_probability : float
        P(quiebre) estimada (0–1).
    stock_efectivo : float
        Posición de inventario efectiva (on_hand + on_order).
    reorder_point : float
        Punto de reorden en unidades.
    suggested_order_qty : float
        Cantidad bruta sugerida antes de ajustes (max(ROP - stock, 0)).
    moq : float
        Cantidad mínima de orden del proveedor.
    pack_size : float
        Múltiplo de entrega (unidades por pack).
    recommended_qty : float
        Cantidad ajustada a MOQ y pack_size. 0 si no hay necesidad de compra.
    eoq : float
        EOQ teórico en unidades. 0 si no hay parámetros de costo disponibles.
    final_qty : float
        Cantidad final recomendada = max(recommended_qty, eoq ajustado).
        0 para sobrestock/equilibrio.
    urgency_score : float
        Score de priorización 0–100. Mayor = más urgente.
    days_until_stockout : float
        Días estimados hasta quiebre de stock al ritmo actual de demanda.
        inf si no hay demanda. Para sobrestock, representa cobertura excedente.
    order_deadline : str or None
        Fecha ISO límite para emitir la orden sin incurrir en quiebre.
        None si no aplica (sobrestock, equilibrio o sin demanda).
    excess_units : float
        Unidades en exceso respecto a cobertura objetivo. 0 si no hay sobrestock.
    days_to_normal : float
        Días estimados para que el exceso se consuma naturalmente. 0 si no aplica.
    excess_carrying_cost : float
        Costo estimado de mantener el exceso hasta consumo natural (moneda del dataset).
        0 si no aplica.
    unit_cost : float
        Costo unitario del SKU (del catálogo).
    carrying_cost_rate : float
        Tasa anual de costo de mantener inventario.
    diagnosis_text : str
        Texto explicativo del estado de inventario (del InventoryDiagnosis).
    action : str
        Acción recomendada en lenguaje natural:
        "Ordenar N unidades urgente" | "Ordenar N unidades" | "No ordenar" |
        "Evaluar reducción / liquidación" | "Sin acción".
    demand_signal_source : str
        Origen de la señal de demanda usada para SS/ROP:
        ``"forecast"`` si se usó ForecastStore, ``"historical"`` si se usó señal histórica.
    """

    sku: str
    name: str
    supplier: str | None
    abc_class: str | None
    # Estado
    health_status: str
    alert_level: str
    stockout_probability: float
    stock_efectivo: float
    reorder_point: float
    # Canal compra
    suggested_order_qty: float
    moq: float
    pack_size: float
    recommended_qty: float
    eoq: float
    final_qty: float
    urgency_score: float
    days_until_stockout: float
    order_deadline: str | None
    # Canal exceso
    excess_units: float
    days_to_normal: float
    excess_carrying_cost: float
    # Costos
    unit_cost: float
    carrying_cost_rate: float
    # Explicabilidad
    diagnosis_text: str
    action: str
    demand_signal_source: str  # "forecast" | "historical"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Cálculo de cantidad ajustada a MOQ / pack size (§9.2)
# ---------------------------------------------------------------------------

def compute_recommended_qty(
    suggested: float,
    moq: float = 1.0,
    pack_size: float = 1.0,
) -> float:
    """Ajusta la cantidad sugerida a las restricciones de lote del proveedor.

    Parameters
    ----------
    suggested : float
        Cantidad bruta calculada (max(ROP - stock_efectivo, 0)).
    moq : float
        Cantidad mínima de orden. Default 1.
    pack_size : float
        Múltiplo de entrega. Default 1 (sin restricción de pack).

    Returns
    -------
    float
        Cantidad ajustada >= 0. Si suggested <= 0, retorna 0.

    Examples
    --------
    >>> compute_recommended_qty(85, moq=100, pack_size=1)
    100.0
    >>> compute_recommended_qty(50, moq=1, pack_size=24)
    72.0
    >>> compute_recommended_qty(0, moq=10, pack_size=5)
    0.0
    """
    if suggested <= 0:
        return 0.0

    moq = max(1.0, float(moq))
    pack_size = max(1.0, float(pack_size))

    base = max(float(suggested), moq)
    if pack_size > 1.0:
        base = math.ceil(base / pack_size) * pack_size

    return float(base)


# ---------------------------------------------------------------------------
# EOQ (§5.2)
# ---------------------------------------------------------------------------

def compute_eoq(
    annual_demand: float,
    order_cost: float,
    unit_cost: float,
    carrying_rate: float,
) -> float:
    """Calcula la cantidad económica de pedido (EOQ).

    EOQ = √(2 × D × K / h),  h = unit_cost × carrying_rate

    Parameters
    ----------
    annual_demand : float
        Demanda anual estimada en unidades.
    order_cost : float
        Costo fijo por orden (K). 0 → EOQ no computable → retorna 0.
    unit_cost : float
        Costo unitario del SKU. 0 → retorna 0.
    carrying_rate : float
        Tasa anual de costo de mantener inventario (fracción). 0 → retorna 0.

    Returns
    -------
    float
        EOQ en unidades. 0 si algún parámetro hace el cálculo imposible.

    Examples
    --------
    >>> compute_eoq(annual_demand=1200, order_cost=50000, unit_cost=100000, carrying_rate=0.25)
    69.28...
    """
    if annual_demand <= 0 or order_cost <= 0 or unit_cost <= 0 or carrying_rate <= 0:
        return 0.0

    h = unit_cost * carrying_rate
    eoq = math.sqrt(2 * annual_demand * order_cost / h)
    return float(eoq)


# ---------------------------------------------------------------------------
# Urgency score (§11.3)
# ---------------------------------------------------------------------------

_ABC_WEIGHT: dict[str, float] = {"A": 1.0, "B": 0.6, "C": 0.3}

_STATUS_BASE_SCORE: dict[str, float] = {
    "quiebre_inminente": 100.0,
    "substock":           60.0,
    "equilibrio":         10.0,
    "sobrestock_leve":     0.0,
    "sobrestock_critico":  0.0,
    "dead_stock":          0.0,
}


def compute_urgency_score(
    health_status: str,
    stockout_probability: float,
    abc_class: str | None,
    days_until_stockout: float,
    lead_time_days: float,
) -> float:
    """Calcula el score de urgencia de reposición (0–100).

    Score más alto = acción más urgente.

    Fórmula
    -------
    base    = según health_status (ver _STATUS_BASE_SCORE)
    prob    = stockout_probability × 30
    abc     = _ABC_WEIGHT[abc_class] × 10
    margin  = -(days_until_stockout / max(lead_time_days, 1)) × 10  (urgencia temporal)

    score = clamp(base + prob + abc + margin, 0, 100)

    Parameters
    ----------
    health_status : str
        Estado del diagnóstico de inventario.
    stockout_probability : float
        P(quiebre) estimada (0–1).
    abc_class : str or None
        Clase ABC del SKU.
    days_until_stockout : float
        Días hasta quiebre proyectado. inf si sin demanda.
    lead_time_days : float
        Lead time del proveedor en días (denominador del componente temporal).

    Returns
    -------
    float
        Score en [0, 100].
    """
    base = _STATUS_BASE_SCORE.get(health_status, 0.0)
    prob_component = stockout_probability * 30.0
    abc_component = _ABC_WEIGHT.get(abc_class or "", 0.3) * 10.0

    if math.isinf(days_until_stockout) or lead_time_days <= 0:
        margin_component = 0.0
    else:
        margin_component = -(days_until_stockout / lead_time_days) * 10.0

    score = base + prob_component + abc_component + margin_component
    return float(max(0.0, min(100.0, score)))


# ---------------------------------------------------------------------------
# Días hasta quiebre y fecha límite de orden
# ---------------------------------------------------------------------------

def compute_days_until_stockout(
    coverage_net_days: float,
    lead_time_days: float,
) -> float:
    """Días de margen antes de que el stock se agote respecto al lead time.

    Retorna coverage_net_days directamente (días de cobertura neta disponible).
    Para sobrestock, coverage_net_days > coverage_obj_days — retorna el valor tal cual.
    Para quiebre, puede ser 0 o cercano a 0.

    Returns
    -------
    float
        Días de cobertura neta. inf si la cobertura es infinita (sin demanda).
    """
    return float(coverage_net_days)


def compute_order_deadline(
    lead_time_days: float,
    coverage_net_days: float,
    reference_date: date | None = None,
) -> str | None:
    """Fecha ISO límite para emitir la orden sin incurrir en quiebre.

    deadline = reference_date + (coverage_net_days - lead_time_days) días

    Si coverage_net_days <= 0 → ya en quiebre → retorna hoy.
    Si coverage_net_days >= 9999 (inf sentinel) → None (sin demanda).
    Si coverage_net_days > lead_time_days → hay margen → calcula fecha.

    Parameters
    ----------
    lead_time_days : float
        Lead time del proveedor en días.
    coverage_net_days : float
        Días de cobertura neta actual.
    reference_date : date or None
        Fecha de referencia. Default = hoy.

    Returns
    -------
    str or None
        Fecha ISO 'YYYY-MM-DD' o None si no aplica.
    """
    if reference_date is None:
        reference_date = date.today()

    # Sin demanda estimada (cobertura infinita) → no hay deadline
    if coverage_net_days >= 9000:
        return None

    margin_days = coverage_net_days - lead_time_days
    if margin_days <= 0:
        return reference_date.isoformat()

    deadline = reference_date + timedelta(days=int(margin_days))
    return deadline.isoformat()


# ---------------------------------------------------------------------------
# Costo del exceso (canal sobrestock)
# ---------------------------------------------------------------------------

def compute_excess_carrying_cost(
    excess_units: float,
    unit_cost: float,
    carrying_rate: float,
    days_to_normal: float,
) -> float:
    """Costo estimado de mantener el exceso hasta consumo natural.

    carrying_cost = excess_units × unit_cost × carrying_rate × (days_to_normal / 365)

    Parameters
    ----------
    excess_units : float
        Unidades en exceso respecto a cobertura objetivo.
    unit_cost : float
        Costo unitario del SKU.
    carrying_rate : float
        Tasa anual de costo de mantener inventario (fracción).
    days_to_normal : float
        Días hasta que el exceso se consume naturalmente.

    Returns
    -------
    float
        Costo en la moneda del dataset. 0 si algún parámetro es 0.
    """
    if excess_units <= 0 or unit_cost <= 0 or carrying_rate <= 0 or days_to_normal <= 0:
        return 0.0

    return float(excess_units * unit_cost * carrying_rate * (days_to_normal / 365.0))


# ---------------------------------------------------------------------------
# Construcción de recomendación individual
# ---------------------------------------------------------------------------

def _get_catalog_field(catalog_row: pd.Series | None, field: str, default: Any) -> Any:
    """Extrae un campo del catálogo con fallback al default."""
    if catalog_row is None or catalog_row.empty:
        return default
    val = catalog_row.get(field)
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return default
    return val


def _build_action_text(
    health_status: str,
    final_qty: float,
    excess_units: float,
    days_to_normal: float,
) -> str:
    """Genera texto de acción accionable en lenguaje natural."""
    if health_status == "quiebre_inminente":
        return f"Ordenar {final_qty:,.0f} u — URGENTE (quiebre inminente)"
    if health_status == "substock":
        return f"Ordenar {final_qty:,.0f} u"
    if health_status == "equilibrio":
        return "Sin acción — stock en equilibrio"
    if health_status == "sobrestock_leve":
        return f"No ordenar — exceso de {excess_units:,.0f} u (se consume en ~{days_to_normal:.0f} días)"
    if health_status == "sobrestock_critico":
        return f"No ordenar — exceso crítico de {excess_units:,.0f} u. Evaluar liquidación/promoción"
    if health_status == "dead_stock":
        return "Sin movimiento — candidato a liquidación"
    return "Sin acción"


def _demand_signal_from_ss_method(ss_method: str | None) -> str:
    """Deriva 'forecast' o 'historical' desde el campo ss_method."""
    if ss_method and ss_method.endswith("_forecast"):
        return "forecast"
    return "historical"


def build_purchase_recommendation(
    sku: str,
    diagnosis: InventoryDiagnosis,
    params: InventoryParams,
    catalog_row: pd.Series | None = None,
    manifest_config: dict | None = None,
    reference_date: date | None = None,
    demand_signal_source: str | None = None,
) -> PurchaseRecommendation:
    """Construye una PurchaseRecommendation a partir del diagnóstico e inventario params.

    Parameters
    ----------
    sku : str
        Identificador del SKU.
    diagnosis : InventoryDiagnosis
        Resultado del diagnóstico de salud de inventario.
    params : InventoryParams
        Parámetros de inventario (LT, carrying_rate, etc.).
    catalog_row : pd.Series or None
        Fila del product_catalog (name, cost, moq, supplier, base_price).
    manifest_config : dict or None
        Manifest completo — permite overrides de MOQ/pack_size por SKU/proveedor.
    reference_date : date or None
        Fecha de referencia para calcular order_deadline. Default hoy.

    Returns
    -------
    PurchaseRecommendation
    """
    # --- Datos del catálogo ---
    name = str(_get_catalog_field(catalog_row, "name", sku))
    supplier = diagnosis.abc_class and params.abc_class  # usa params como fallback
    supplier = str(_get_catalog_field(catalog_row, "supplier", None)) if catalog_row is not None else None
    unit_cost = float(_get_catalog_field(catalog_row, "cost", 0.0))

    # MOQ del catálogo (columna moq), con fallback a manifest o default=1
    catalog_moq = float(_get_catalog_field(catalog_row, "moq", 1.0))
    moq = _resolve_moq(sku, supplier, catalog_moq, manifest_config)
    pack_size = _resolve_pack_size(sku, manifest_config)
    order_cost = _resolve_order_cost(manifest_config)

    # --- Canal compra ---
    suggested = diagnosis.suggested_order_qty
    recommended_qty = compute_recommended_qty(suggested, moq, pack_size)

    # EOQ teórico
    mean_daily = diagnosis.mean_demand_daily
    annual_demand = mean_daily * 365.0
    eoq_raw = compute_eoq(annual_demand, order_cost, unit_cost, params.carrying_cost_rate)
    # EOQ ajustado a pack_size (solo si hay demanda real de compra)
    if eoq_raw > 0 and diagnosis.health_status in ("quiebre_inminente", "substock"):
        eoq_adjusted = compute_recommended_qty(eoq_raw, moq, pack_size)
    else:
        eoq_adjusted = 0.0

    # Cantidad final: max entre recommended y EOQ ajustado
    if diagnosis.health_status in ("quiebre_inminente", "substock"):
        final_qty = max(recommended_qty, eoq_adjusted)
    else:
        final_qty = 0.0

    # Urgency score
    coverage_net = diagnosis.coverage_net_days
    urgency = compute_urgency_score(
        diagnosis.health_status,
        diagnosis.stockout_probability,
        diagnosis.abc_class,
        coverage_net,
        params.lead_time_days,
    )

    # Days until stockout y deadline
    days_until_stockout = compute_days_until_stockout(coverage_net, params.lead_time_days)
    if diagnosis.health_status in ("quiebre_inminente", "substock"):
        order_deadline = compute_order_deadline(params.lead_time_days, coverage_net, reference_date)
    else:
        order_deadline = None

    # --- Canal exceso ---
    excess_units = diagnosis.excess_units
    if excess_units > 0 and mean_daily > 0:
        # Días hasta que el exceso se consume al ritmo actual
        days_to_normal = excess_units / mean_daily
    else:
        days_to_normal = 0.0

    excess_carrying_cost = compute_excess_carrying_cost(
        excess_units, unit_cost, params.carrying_cost_rate, days_to_normal
    )

    # --- Texto de acción ---
    action = _build_action_text(
        diagnosis.health_status, final_qty, excess_units, days_to_normal
    )

    return PurchaseRecommendation(
        sku=sku,
        name=name,
        supplier=supplier,
        abc_class=diagnosis.abc_class,
        health_status=diagnosis.health_status,
        alert_level=diagnosis.alert_level,
        stockout_probability=diagnosis.stockout_probability,
        stock_efectivo=diagnosis.stock_efectivo,
        reorder_point=diagnosis.reorder_point,
        suggested_order_qty=suggested,
        moq=moq,
        pack_size=pack_size,
        recommended_qty=recommended_qty,
        eoq=eoq_raw,
        final_qty=final_qty,
        urgency_score=urgency,
        days_until_stockout=days_until_stockout,
        order_deadline=order_deadline,
        excess_units=excess_units,
        days_to_normal=days_to_normal,
        excess_carrying_cost=excess_carrying_cost,
        unit_cost=unit_cost,
        carrying_cost_rate=params.carrying_cost_rate,
        diagnosis_text=diagnosis.diagnosis_text,
        action=action,
        demand_signal_source=demand_signal_source or "historical",
    )


# ---------------------------------------------------------------------------
# Resolución de parámetros desde manifest
# ---------------------------------------------------------------------------

def _resolve_moq(
    sku: str,
    supplier: str | None,
    catalog_moq: float,
    manifest_config: dict | None,
) -> float:
    """Resuelve MOQ: override SKU > override proveedor > catálogo > default 1."""
    if manifest_config is None:
        return max(1.0, catalog_moq)

    purchase_params = manifest_config.get("purchase_params", {})

    # Override por SKU
    moq_by_sku = purchase_params.get("moq_by_sku", {})
    if sku in moq_by_sku:
        return float(moq_by_sku[sku])

    # Override por proveedor
    if supplier:
        moq_by_supplier = purchase_params.get("moq_by_supplier", {})
        if supplier in moq_by_supplier:
            return float(moq_by_supplier[supplier])

    # Catálogo (columna moq del product_catalog)
    return max(1.0, float(catalog_moq))


def _resolve_pack_size(sku: str, manifest_config: dict | None) -> float:
    """Resuelve pack_size desde manifest. Default = 1."""
    if manifest_config is None:
        return 1.0
    purchase_params = manifest_config.get("purchase_params", {})
    pack_by_sku = purchase_params.get("pack_size_by_sku", {})
    return float(pack_by_sku.get(sku, 1.0))


def _resolve_order_cost(manifest_config: dict | None) -> float:
    """Resuelve costo fijo de ordenar desde manifest. Default = 0 (EOQ no computable)."""
    if manifest_config is None:
        return 0.0
    purchase_params = manifest_config.get("purchase_params", {})
    return float(purchase_params.get("order_cost", 0.0))


# ---------------------------------------------------------------------------
# Generación del plan completo de catálogo
# ---------------------------------------------------------------------------

def generate_purchase_plan(
    health_rows: list[dict],
    catalog_df: pd.DataFrame,
    params_map: dict[str, InventoryParams],
    manifest_config: dict | None = None,
    reference_date: date | None = None,
    include_equilibrio: bool = False,
    include_sobrestock: bool = True,
) -> list[PurchaseRecommendation]:
    """Genera la lista priorizada de recomendaciones de reposición para el catálogo.

    Parameters
    ----------
    health_rows : list[dict]
        Salida de ``catalog_health_report()`` convertida a lista de dicts.
        Cada dict corresponde a un ``InventoryDiagnosis``.
    catalog_df : pd.DataFrame
        product_catalog con columnas name, cost, moq, supplier, etc.
    params_map : dict[str, InventoryParams]
        Mapa sku → InventoryParams precalculado.
    manifest_config : dict or None
        Contenido del manifest para overrides de MOQ/pack_size/order_cost.
    reference_date : date or None
        Fecha de referencia. Default hoy.
    include_equilibrio : bool
        Si True, incluye SKUs en equilibrio en la salida (urgency_score bajo).
    include_sobrestock : bool
        Si True, incluye SKUs en sobrestock (con final_qty=0, excess info).

    Returns
    -------
    list[PurchaseRecommendation]
        Lista ordenada por urgency_score descendente.
    """
    catalog_indexed = catalog_df.set_index("sku") if "sku" in catalog_df.columns else catalog_df

    _EXCLUDED_STATUSES: set[str] = set()
    if not include_equilibrio:
        _EXCLUDED_STATUSES.add("equilibrio")
    if not include_sobrestock:
        _EXCLUDED_STATUSES.update({"sobrestock_leve", "sobrestock_critico"})

    recommendations: list[PurchaseRecommendation] = []

    for row in health_rows:
        sku = row.get("sku", "")
        health_status = row.get("health_status", "equilibrio")

        if health_status in _EXCLUDED_STATUSES:
            continue

        params = params_map.get(sku)
        if params is None:
            warnings.warn(
                f"generate_purchase_plan: sin InventoryParams para {sku!r} — omitiendo.",
                stacklevel=2,
            )
            continue

        # Reconstruir InventoryDiagnosis desde el dict del health report
        try:
            diagnosis = _dict_to_diagnosis(row)
        except Exception as exc:
            warnings.warn(
                f"generate_purchase_plan: error reconstruyendo diagnóstico de {sku!r}: {exc}",
                stacklevel=2,
            )
            continue

        catalog_row = catalog_indexed.loc[sku] if sku in catalog_indexed.index else None

        rec = build_purchase_recommendation(
            sku=sku,
            diagnosis=diagnosis,
            params=params,
            catalog_row=catalog_row,
            manifest_config=manifest_config,
            reference_date=reference_date,
            demand_signal_source=_demand_signal_from_ss_method(row.get("ss_method")),
        )
        recommendations.append(rec)

    # Ordenar por urgency_score descendente, luego por stockout_probability desc
    recommendations.sort(key=lambda r: (-r.urgency_score, -r.stockout_probability))
    return recommendations


def _dict_to_diagnosis(row: dict) -> InventoryDiagnosis:
    """Reconstruye un InventoryDiagnosis desde un dict del health report."""
    from planning_core.inventory.diagnostics import InventoryDiagnosis  # noqa: PLC0415
    return InventoryDiagnosis(
        sku=row["sku"],
        abc_class=row.get("abc_class"),
        on_hand=float(row.get("on_hand", 0)),
        on_order=float(row.get("on_order", 0)),
        stock_efectivo=float(row.get("stock_efectivo", 0)),
        mean_demand_daily=float(row.get("mean_demand_daily", 0)),
        coverage_net_days=float(row.get("coverage_net_days", 0)),
        coverage_obj_days=float(row.get("coverage_obj_days", 0)),
        positioning_ratio=float(row.get("positioning_ratio", 0)),
        safety_stock=float(row.get("safety_stock", 0)),
        reorder_point=float(row.get("reorder_point", 0)),
        coverage_ss_days=float(row.get("coverage_ss_days", 0)),
        lead_time_days=float(row.get("lead_time_days", 30)),
        review_period_days=float(row.get("review_period_days", 21)),
        health_status=row.get("health_status", "equilibrio"),
        alert_level=row.get("alert_level", "none"),
        stockout_probability=float(row.get("stockout_probability", 0)),
        suggested_order_qty=float(row.get("suggested_order_qty", 0)),
        excess_units=float(row.get("excess_units", 0)),
        is_dead_stock=bool(row.get("is_dead_stock", False)),
        diagnosis_text=str(row.get("diagnosis_text", "")),
    )
