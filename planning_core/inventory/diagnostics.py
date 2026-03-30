"""Diagnóstico de salud de inventario por SKU.

Implementa la lógica de diagnóstico del documento "Gestión de Inventario
Orientada a Decisiones" (Marzo 2026):

- Sección 2.3 — Ratio de posicionamiento y bandas de clasificación:
  ``Cobertura Neta = Stock Efectivo / Demanda Diaria``
  ``Cobertura Objetivo = LT + R + coverage_ss_days``
  ``Ratio = Cobertura Neta / Cobertura Objetivo``

- Sección 2.4 — Probabilidad de quiebre:
  ``P(Quiebre) = P(D_{LT+R} > Stock Efectivo)`` via Normal

- Sección 11.3 — Lógica de recomendación:
  Substock: ``suggested_order = max(ROP - stock_efectivo, 0)``
  Sobrestock: ``excess = max(stock_efectivo - coverage_obj_days × d̄, 0)``

- Sección 11.4 — Alertas diferenciadas (rojo, naranja, amarillo, gris)

- Sección 11.5 — Explicabilidad: texto generado automáticamente

Stock efectivo
--------------
En nuestro modelo canónico no existe "stock comprometido" como campo separado.
Se aproxima como:

    Stock Efectivo ≈ on_hand + on_order

donde ``on_order`` es el stock en tránsito (pedidos pendientes de recepción).

Funciones públicas
------------------
diagnose_sku(sku, on_hand, on_order, ss_result, params, ...) → InventoryDiagnosis
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from planning_core.inventory.params import InventoryParams
from planning_core.inventory.safety_stock import SafetyStockResult


# ---------------------------------------------------------------------------
# Bandas de clasificación (§2.3) — configurables
# ---------------------------------------------------------------------------

# Umbrales del ratio de posicionamiento (Cobertura Neta / Cobertura Objetivo)
HEALTH_BANDS: list[tuple[float, str, str]] = [
    # (umbral_superior_exclusivo, health_status, alert_level)
    (0.3, "quiebre_inminente", "rojo"),
    (0.7, "substock",         "naranja"),
    (1.3, "equilibrio",       "none"),
    (2.0, "sobrestock_leve",  "amarillo"),
    (float("inf"), "sobrestock_critico", "gris"),
]

# SKU sin movimiento > este número de días → dead_stock
DEAD_STOCK_DAYS_THRESHOLD = 90

# Valores sentinel usados para reemplazar infinito en los campos de salida
# (evita NaN/inf en DataFrames y serialización JSON)
_INF_COVERAGE_SENTINEL: float = 9999.0   # sustituye inf en coverage_net_days
_INF_RATIO_SENTINEL: float = 999.0       # sustituye inf en positioning_ratio


# ---------------------------------------------------------------------------
# Dataclass de resultado
# ---------------------------------------------------------------------------

@dataclass
class InventoryDiagnosis:
    """Diagnóstico completo de salud de inventario para un SKU.

    Attributes
    ----------
    sku : str
        Identificador del SKU.
    abc_class : str or None
        Clase ABC del SKU.
    on_hand : float
        Stock disponible físicamente (unidades).
    on_order : float
        Stock en tránsito / pedido pendiente (unidades).
    stock_efectivo : float
        Posición de inventario efectiva = on_hand + on_order.
    mean_demand_daily : float
        Demanda media diaria estimada (unidades/día).
    coverage_net_days : float
        Días de demanda cubiertos por el stock efectivo.
        0 si demanda diaria es 0.
    coverage_obj_days : float
        Días de cobertura objetivo = LT + R + coverage_ss_days.
    positioning_ratio : float
        Ratio de posicionamiento = coverage_net / coverage_obj.
        inf si coverage_obj es 0. 0 si coverage_net es 0.
    safety_stock : float
        Stock de seguridad en unidades.
    reorder_point : float
        Punto de reorden en unidades.
    coverage_ss_days : float
        Días equivalentes de stock de seguridad = SS / d̄_daily.
    lead_time_days : float
        Lead time del proveedor en días.
    review_period_days : float
        Período de revisión en días.
    health_status : str
        Estado de salud: ``quiebre_inminente`` | ``substock`` |
        ``equilibrio`` | ``sobrestock_leve`` | ``sobrestock_critico`` |
        ``dead_stock``.
    alert_level : str
        Nivel de alerta: ``rojo`` | ``naranja`` | ``amarillo`` |
        ``gris`` | ``none``.
    stockout_probability : float
        P(quiebre) = probabilidad de stockout durante el ciclo de
        reposición, estimada via distribución Normal. Rango [0, 1].
    suggested_order_qty : float
        Cantidad sugerida de reorden para substock/quiebre.
        ``max(ROP - stock_efectivo, 0)``. 0 si en equilibrio o sobrestock.
    excess_units : float
        Unidades en exceso respecto a la cobertura objetivo.
        ``max(stock_efectivo - coverage_obj_days × d̄, 0)``.
        0 si en substock o quiebre.
    is_dead_stock : bool
        True si el SKU no ha tenido movimiento en más de
        ``DEAD_STOCK_DAYS_THRESHOLD`` días.
    diagnosis_text : str
        Texto explicativo en lenguaje natural (§11.5).
    """

    sku: str
    abc_class: str | None
    # Posición de stock
    on_hand: float
    on_order: float
    stock_efectivo: float
    # Demanda
    mean_demand_daily: float
    # Cobertura
    coverage_net_days: float
    coverage_obj_days: float
    positioning_ratio: float
    # SS / ROP
    safety_stock: float
    reorder_point: float
    coverage_ss_days: float
    lead_time_days: float
    review_period_days: float
    # Salud
    health_status: str
    alert_level: str
    # Probabilístico
    stockout_probability: float
    # Recomendación
    suggested_order_qty: float
    excess_units: float
    is_dead_stock: bool
    # Explicabilidad
    diagnosis_text: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Funciones internas
# ---------------------------------------------------------------------------

def _classify_ratio(ratio: float, is_dead_stock: bool) -> tuple[str, str]:
    """Retorna (health_status, alert_level) para el ratio dado."""
    if is_dead_stock:
        return "dead_stock", "gris"
    for threshold, status, alert in HEALTH_BANDS:
        if ratio < threshold:
            return status, alert
    return "sobrestock_critico", "gris"


def _stockout_probability(
    stock_efectivo: float,
    mean_demand_daily: float,
    ss_result: SafetyStockResult,
    params: InventoryParams,
) -> float:
    """Estima P(quiebre) = P(D_{LT+R} > stock_efectivo) via Normal (§2.4).

    Si la demanda media o la desviación son 0, retorna 0 (sin riesgo) o 1
    (sin stock, sin posibilidad de cubrir nada).
    """
    exposure = params.lead_time_days + params.review_period_days
    mu_ddlt = mean_demand_daily * exposure

    # Varianza acumulada sobre el período de exposición
    sigma_d = ss_result.sigma_demand_daily
    sigma_lt = params.sigma_lt_days

    if params.ss_method == "extended":
        var_ddlt = (
            exposure * (sigma_d ** 2)
            + (mean_demand_daily ** 2) * (sigma_lt ** 2)
        )
    elif params.ss_method == "standard":
        var_ddlt = exposure * (sigma_d ** 2)
    else:
        # simple_pct_lt (clase C): solo varianza de demanda sobre LT
        var_ddlt = params.lead_time_days * (sigma_d ** 2)

    sigma_ddlt = math.sqrt(max(var_ddlt, 0.0))

    if sigma_ddlt == 0:
        # Demanda determinística: quiebre si stock < DDLT, sin quiebre si >=
        return 1.0 if stock_efectivo < mu_ddlt else 0.0

    # z = (stock_efectivo - mu_ddlt) / sigma_ddlt
    z = (stock_efectivo - mu_ddlt) / sigma_ddlt
    # P(quiebre) = 1 - Φ(z) usando aproximación de la normal estándar
    return max(0.0, min(1.0, 1.0 - _standard_normal_cdf(z)))


def _standard_normal_cdf(z: float) -> float:
    """CDF de la distribución normal estándar Φ(z) via math.erfc."""
    # Φ(z) = 0.5 × erfc(-z / √2)
    return 0.5 * math.erfc(-z / math.sqrt(2))


def _build_diagnosis_text(
    sku: str,
    health_status: str,
    coverage_net_days: float,
    coverage_obj_days: float,
    positioning_ratio: float,
    excess_units: float,
    suggested_order_qty: float,
    stockout_probability: float,
    is_dead_stock: bool,
) -> str:
    """Genera texto explicativo en lenguaje natural (§11.5)."""
    if is_dead_stock:
        return (
            f"{sku} no registra movimiento en más de {DEAD_STOCK_DAYS_THRESHOLD} días. "
            "Candidato a liquidación o revisión de ciclo de vida."
        )

    ratio_pct = positioning_ratio * 100
    net_r = round(coverage_net_days, 1)
    obj_r = round(coverage_obj_days, 1)

    if health_status == "quiebre_inminente":
        return (
            f"{sku} tiene {net_r} días de cobertura vs objetivo de {obj_r} días "
            f"(ratio {ratio_pct:.0f}%). Riesgo de quiebre inminente — "
            f"P(quiebre)={stockout_probability:.0%}. "
            f"Se recomienda ordenar {suggested_order_qty:,.0f} u de forma urgente."
        )
    if health_status == "substock":
        return (
            f"{sku} tiene {net_r} días de cobertura vs objetivo de {obj_r} días "
            f"(ratio {ratio_pct:.0f}%). Stock insuficiente para cubrir demanda con seguridad razonable. "
            f"Acelerar pedido pendiente o generar orden de {suggested_order_qty:,.0f} u."
        )
    if health_status == "equilibrio":
        return (
            f"{sku} tiene {net_r} días de cobertura vs objetivo de {obj_r} días "
            f"(ratio {ratio_pct:.0f}%). Stock alineado con la necesidad — operación saludable."
        )
    if health_status == "sobrestock_leve":
        return (
            f"{sku} tiene {net_r} días de cobertura vs objetivo de {obj_r} días "
            f"(ratio {ratio_pct:.0f}%). Exceso de {excess_units:,.0f} u dentro de rango manejable. "
            "Evaluar reducción en próxima compra."
        )
    # sobrestock_critico
    return (
        f"{sku} tiene {net_r} días de cobertura vs objetivo de {obj_r} días "
        f"(ratio {ratio_pct:.0f}%). Exceso significativo de {excess_units:,.0f} u — "
        "capital inmovilizado con riesgo de obsolescencia. Detener compras; evaluar liquidación."
    )


# ---------------------------------------------------------------------------
# Función pública principal
# ---------------------------------------------------------------------------

def diagnose_sku(
    sku: str,
    on_hand: float,
    on_order: float,
    ss_result: SafetyStockResult,
    params: InventoryParams,
    abc_class: str | None = None,
    days_since_last_movement: int = 0,
) -> InventoryDiagnosis:
    """Diagnóstica el estado de salud de inventario de un SKU.

    Parameters
    ----------
    sku : str
        Identificador del SKU.
    on_hand : float
        Stock disponible físicamente (unidades). Fuente: inventory_snapshot.
    on_order : float
        Stock en tránsito / pedidos pendientes (unidades).
    ss_result : SafetyStockResult
        Resultado de ``compute_sku_safety_stock()`` — contiene SS, ROP,
        demanda media diaria, sigma, etc.
    params : InventoryParams
        Parámetros de inventario del SKU (LT, R, z, ss_method, etc.).
    abc_class : str or None
        Clase ABC. Si None, se lee de ``params.abc_class``.
    days_since_last_movement : int
        Días desde el último movimiento de inventario registrado.
        Si >= ``DEAD_STOCK_DAYS_THRESHOLD``, se clasifica como dead_stock.

    Returns
    -------
    InventoryDiagnosis
    """
    abc = abc_class or params.abc_class

    # --- Stock efectivo ---
    stock_efectivo = max(0.0, float(on_hand) + float(on_order))

    # --- Demanda media diaria ---
    mean_demand_daily = ss_result.mean_demand_daily

    # --- Cobertura neta (días) ---
    if mean_demand_daily > 0:
        coverage_net_days = stock_efectivo / mean_demand_daily
    else:
        # Sin demanda estimada: cobertura infinita (no hay consumo)
        coverage_net_days = float("inf") if stock_efectivo > 0 else 0.0

    # --- Cobertura objetivo (días): LT + R + SS_days ---
    coverage_obj_days = (
        params.lead_time_days
        + params.review_period_days
        + ss_result.coverage_ss_days
    )

    # --- Ratio de posicionamiento ---
    if coverage_obj_days > 0:
        if math.isinf(coverage_net_days):
            positioning_ratio = float("inf")
        else:
            positioning_ratio = coverage_net_days / coverage_obj_days
    else:
        positioning_ratio = float("inf") if coverage_net_days > 0 else 0.0

    # --- Dead stock ---
    is_dead_stock = days_since_last_movement >= DEAD_STOCK_DAYS_THRESHOLD

    # --- Health status y alert level ---
    ratio_for_band = positioning_ratio if not math.isinf(positioning_ratio) else _INF_RATIO_SENTINEL
    health_status, alert_level = _classify_ratio(ratio_for_band, is_dead_stock)

    # --- Probabilidad de quiebre ---
    stockout_prob = _stockout_probability(stock_efectivo, mean_demand_daily, ss_result, params)

    # --- Recomendación: cantidad sugerida de orden ---
    rop = ss_result.reorder_point
    if health_status in ("quiebre_inminente", "substock"):
        suggested_order_qty = max(0.0, rop - stock_efectivo)
    else:
        suggested_order_qty = 0.0

    # --- Exceso de unidades ---
    if health_status in ("sobrestock_leve", "sobrestock_critico"):
        target_stock = coverage_obj_days * mean_demand_daily
        excess_units = max(0.0, stock_efectivo - target_stock)
    else:
        excess_units = 0.0

    # --- Texto explicativo ---
    diagnosis_text = _build_diagnosis_text(
        sku=sku,
        health_status=health_status,
        coverage_net_days=coverage_net_days if not math.isinf(coverage_net_days) else _INF_COVERAGE_SENTINEL,
        coverage_obj_days=coverage_obj_days,
        positioning_ratio=ratio_for_band,
        excess_units=excess_units,
        suggested_order_qty=suggested_order_qty,
        stockout_probability=stockout_prob,
        is_dead_stock=is_dead_stock,
    )

    return InventoryDiagnosis(
        sku=sku,
        abc_class=abc,
        on_hand=float(on_hand),
        on_order=float(on_order),
        stock_efectivo=stock_efectivo,
        mean_demand_daily=mean_demand_daily,
        coverage_net_days=coverage_net_days if not math.isinf(coverage_net_days) else _INF_COVERAGE_SENTINEL,
        coverage_obj_days=coverage_obj_days,
        positioning_ratio=ratio_for_band,
        safety_stock=ss_result.safety_stock,
        reorder_point=rop,
        coverage_ss_days=ss_result.coverage_ss_days,
        lead_time_days=params.lead_time_days,
        review_period_days=params.review_period_days,
        health_status=health_status,
        alert_level=alert_level,
        stockout_probability=stockout_prob,
        suggested_order_qty=suggested_order_qty,
        excess_units=excess_units,
        is_dead_stock=is_dead_stock,
        diagnosis_text=diagnosis_text,
    )
