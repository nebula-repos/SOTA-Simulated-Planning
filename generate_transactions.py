"""
Generador de Datos Sintéticos de Transacciones para Motor de Forecast
=====================================================================
Genera SKUs con patrones de demanda diversos para testing de:
- Clasificación de patrones (constante, errático, irregular, estacional, tendencia, intermitente, lumpy)
- Segmentación ABC-XYZ
- Motor recomendador de compras

Todos los parámetros se importan desde config.py (ver PROFILE para cambiar dominio).
"""

import numpy as np
import pandas as pd
from datetime import datetime
import random
import os

from config import (
    RANDOM_SEED, N_PRODUCTS, START_DATE, END_DATE, OUTPUT_DIR, PROFILE,
    LOCATIONS, BRANDS, CATEGORIES, DEMAND_PATTERNS,
    ABC_RATIOS, BASE_DEMAND_RANGES,
    SUPPLIER_PROFILES, MOQ_CHOICES, EXTRA_FIELD_NAME, EXTRA_FIELD_CHOICES,
    SEASONALITY_PROFILES, SEASON_STRENGTH_RANGES, DOW_MAP, WEEKDAY_FRACTION,
    TREND_UP_GROWTH_RATE, TREND_DOWN_DECAY_RATE, TREND_DOWN_FLOOR,
    NEW_PRODUCT_RAMP_DAYS, NEW_PRODUCT_GROWTH,
    SPIKE_PARAMS, SPIKE_DURATION, SPIKE_MULTIPLIER, SPIKE_DISCOUNT_RANGE,
    ADI_THRESHOLD_FOR_CONTINUOUS,
    LOGNORMAL_SIGMA_FACTOR, NORMAL_NOISE_FLOOR,
    LOCATIONS_PER_ABC, LOCATION_FACTOR_RANGE,
    XYZ_THRESHOLDS, SYNTETOS_BOYLAN_THRESHOLDS,
)

np.random.seed(RANDOM_SEED)
random.seed(RANDOM_SEED)


def build_public_catalog(catalog):
    """Exporta solo atributos maestros, sin etiquetas internas de generación."""
    public_columns = [
        "sku",
        "name",
        "category",
        "subcategory",
        "brand",
        "supplier",
        "base_price",
        "cost",
        "moq",
        EXTRA_FIELD_NAME,
    ]
    return catalog.loc[:, public_columns].copy()


def build_public_transactions(df_transactions):
    """Exporta solo movimientos transaccionales ERP-like."""
    public_columns = ["date", "sku", "location", "quantity", "unit_price", "total_amount"]
    return df_transactions.loc[:, public_columns].copy()


def ceil_to_multiple(quantity, multiple):
    """Redondea hacia arriba respetando MOQ o múltiplos de empaque."""
    multiple = max(1, int(multiple))
    return int(max(multiple, np.ceil(max(quantity, 0) / multiple) * multiple))


def floor_to_multiple(quantity, multiple):
    """Redondea hacia abajo respetando MOQ o múltiplos de empaque."""
    multiple = max(1, int(multiple))
    return int(np.floor(max(quantity, 0) / multiple) * multiple)


def build_document_id(prefix, date_value, counter):
    date_str = pd.Timestamp(date_value).strftime("%Y%m%d")
    return f"{prefix}-{date_str}-{counter:06d}"


def generate_purchase_data(catalog, daily_demand_by_sku_location, daily_prices_by_sku_location, dates):
    """
    Genera compras coherentes con la demanda:
    - OCs nacen por reposición por sku/location
    - lead time depende del proveedor
    - cantidad pedida respeta MOQ
    - recepciones pueden ser totales o parciales

    Nota de modelo:
    - en industrial, la lógica busca aproximar abastecimiento importado con lead times largos
    - como no existe una tabla de transferencias, la recepción se registra directo
      en la location operativa que luego consume el stock
    """
    purchase_policy = {
        "A": {"history_days": 30, "review_days": 14, "safety_days": 10, "partial_prob": 0.10},
        "B": {"history_days": 45, "review_days": 21, "safety_days": 14, "partial_prob": 0.14},
        "C": {"history_days": 60, "review_days": 30, "safety_days": 21, "partial_prob": 0.18},
    }

    catalog_lookup = {row["sku"]: row for _, row in catalog.iterrows()}
    horizon_days = len(dates)
    purchase_orders = []
    purchase_order_lines = []
    purchase_receipts = []
    inventory_snapshots = []
    transactions = []
    po_counter = 1
    receipt_counter = 1

    for (sku, location), demand in sorted(daily_demand_by_sku_location.items()):
        if demand.sum() <= 0:
            continue

        product = catalog_lookup[sku]
        supplier = product["supplier"]
        supplier_profile = SUPPLIER_PROFILES[supplier]
        abc_class = product["abc_class"]
        policy = purchase_policy[abc_class]

        lead_time_days = int(supplier_profile["avg_lead_time_days"])
        payment_terms_days = int(supplier_profile.get("payment_terms_days", 30))
        moq = max(1, int(product["moq"]))
        base_cost = float(product["cost"])
        prices = daily_prices_by_sku_location[(sku, location)]

        history_days = policy["history_days"]
        review_days = policy["review_days"]
        safety_days = policy["safety_days"]

        seed_window = min(horizon_days, max(history_days, lead_time_days, 14))
        initial_forecast = max(float(np.mean(demand[:seed_window])), 0.05)
        initial_cover_days = lead_time_days + review_days + safety_days
        on_hand = ceil_to_multiple(
            initial_forecast * initial_cover_days * random.uniform(1.05, 1.35),
            moq,
        )
        on_order = 0
        scheduled_receipts = {}

        for day_idx, current_date in enumerate(dates):
            due_receipts = scheduled_receipts.pop(day_idx, [])
            for receipt in due_receipts:
                on_hand += receipt["received_qty"]
                on_order -= receipt["received_qty"]
                purchase_receipts.append(receipt)

            demand_qty = int(demand[day_idx])
            fulfilled_qty = min(on_hand, demand_qty)
            on_hand -= fulfilled_qty

            if fulfilled_qty > 0:
                transactions.append({
                    "date": current_date,
                    "sku": sku,
                    "location": location,
                    "quantity": int(fulfilled_qty),
                    "unit_price": float(prices[day_idx]),
                    "total_amount": float(fulfilled_qty * prices[day_idx]),
                })

            history_start = max(0, day_idx - history_days + 1)
            demand_history = demand[history_start:day_idx + 1]
            trailing_mean = float(np.mean(demand_history)) if len(demand_history) else initial_forecast
            forecast_daily_demand = max(initial_forecast if day_idx < 7 else trailing_mean, 0.05)

            reorder_point = forecast_daily_demand * (lead_time_days + safety_days)
            target_level = forecast_daily_demand * (lead_time_days + safety_days + review_days)
            inventory_position = on_hand + on_order

            if inventory_position <= reorder_point:
                order_qty = ceil_to_multiple(max(target_level - inventory_position, moq), moq)
                unit_cost = float(round(base_cost * random.uniform(0.97, 1.08), 0))

                po_id = build_document_id("PO", current_date, po_counter)
                po_counter += 1
                po_line_id = f"{po_id}-L01"

                expected_receipt_date = current_date + pd.Timedelta(days=lead_time_days)

                purchase_order_lines.append({
                    "po_id": po_id,
                    "po_line_id": po_line_id,
                    "sku": sku,
                    "ordered_qty": int(order_qty),
                    "unit_cost": unit_cost,
                    "line_amount": float(order_qty * unit_cost),
                    "moq_applied": moq,
                })

                receipt_plan = []
                actual_lead_time = int(round(np.random.normal(
                    loc=lead_time_days,
                    scale=max(1.5, lead_time_days * 0.12),
                )))
                min_actual_lead_time = max(1, int(round(lead_time_days * 0.7)))
                max_actual_lead_time = max(min_actual_lead_time, int(round(lead_time_days * 1.35)))
                actual_lead_time = min(max(min_actual_lead_time, actual_lead_time), max_actual_lead_time)
                first_receipt_idx = day_idx + actual_lead_time

                if order_qty >= 2 * moq and random.random() < policy["partial_prob"]:
                    first_qty = floor_to_multiple(order_qty * random.uniform(0.45, 0.75), moq)
                    first_qty = min(max(moq, first_qty), order_qty - moq)
                    remaining_qty = order_qty - first_qty
                    gap_days = max(2, int(round(np.random.normal(loc=max(3, lead_time_days * 0.25), scale=2))))
                    receipt_plan.append((first_receipt_idx, first_qty, "partial"))
                    receipt_plan.append((first_receipt_idx + gap_days, remaining_qty, "received"))
                else:
                    receipt_plan.append((first_receipt_idx, order_qty, "received"))

                received_within_horizon = 0
                for receipt_day_idx, received_qty, receipt_status in receipt_plan:
                    receipt_date = current_date + pd.Timedelta(days=receipt_day_idx - day_idx)
                    receipt_id = build_document_id("GR", receipt_date, receipt_counter)
                    receipt_counter += 1

                    receipt_record = {
                        "receipt_id": receipt_id,
                        "po_id": po_id,
                        "po_line_id": po_line_id,
                        "sku": sku,
                        "supplier": supplier,
                        "location": location,
                        "receipt_date": receipt_date,
                        "received_qty": int(received_qty),
                        "unit_cost": unit_cost,
                        "total_cost": float(received_qty * unit_cost),
                        "receipt_status": receipt_status,
                    }

                    if receipt_day_idx < horizon_days:
                        scheduled_receipts.setdefault(receipt_day_idx, []).append(receipt_record)
                        received_within_horizon += received_qty

                if received_within_horizon == 0:
                    order_status = "open"
                elif received_within_horizon < order_qty:
                    order_status = "partially_received"
                else:
                    order_status = "received"

                purchase_orders.append({
                    "po_id": po_id,
                    "supplier": supplier,
                    "destination_location": location,
                    "order_date": current_date,
                    "expected_receipt_date": expected_receipt_date,
                    "order_status": order_status,
                    "currency": "CLP",
                    "payment_terms_days": payment_terms_days,
                })

                on_order += order_qty

            inventory_snapshots.append({
                "snapshot_date": current_date,
                "sku": sku,
                "location": location,
                "on_hand_qty": int(on_hand),
                "on_order_qty": int(on_order),
            })

    df_purchase_orders = pd.DataFrame(purchase_orders)
    df_purchase_order_lines = pd.DataFrame(purchase_order_lines)
    df_purchase_receipts = pd.DataFrame(purchase_receipts)
    df_inventory_snapshots = pd.DataFrame(inventory_snapshots)
    df_transactions = pd.DataFrame(transactions)

    if not df_transactions.empty:
        df_transactions.sort_values(["date", "sku", "location"], inplace=True)
        df_transactions.reset_index(drop=True, inplace=True)

    if not df_purchase_orders.empty:
        df_purchase_orders.sort_values(["order_date", "po_id"], inplace=True)
        df_purchase_orders.reset_index(drop=True, inplace=True)
    if not df_purchase_order_lines.empty:
        df_purchase_order_lines.sort_values(["po_id", "po_line_id"], inplace=True)
        df_purchase_order_lines.reset_index(drop=True, inplace=True)
    if not df_purchase_receipts.empty:
        df_purchase_receipts.sort_values(["receipt_date", "receipt_id"], inplace=True)
        df_purchase_receipts.reset_index(drop=True, inplace=True)
    if not df_inventory_snapshots.empty:
        df_inventory_snapshots.sort_values(["snapshot_date", "sku", "location"], inplace=True)
        df_inventory_snapshots.reset_index(drop=True, inplace=True)

    return (
        df_transactions,
        df_purchase_orders,
        df_purchase_order_lines,
        df_purchase_receipts,
        df_inventory_snapshots,
    )


# ============================================================
# 1. GENERACIÓN DE CATÁLOGO DE PRODUCTOS
# ============================================================

def generate_catalog(n_products=N_PRODUCTS):
    products = []
    sku_counter = 1

    pattern_names = list(DEMAND_PATTERNS.keys())
    pattern_weights = [DEMAND_PATTERNS[p]["weight"] for p in pattern_names]
    total_w = sum(pattern_weights)
    pattern_weights = [w / total_w for w in pattern_weights]

    abc_assignments = []
    for cls, ratio in ABC_RATIOS.items():
        abc_assignments += [cls] * int(n_products * ratio)
    while len(abc_assignments) < n_products:
        abc_assignments.append("C")
    random.shuffle(abc_assignments)

    for i in range(n_products):
        cat_name = random.choice(list(CATEGORIES.keys()))
        cat = CATEGORIES[cat_name]
        subcat = random.choice(cat["subcats"])
        brand = random.choice(BRANDS)
        supplier = random.choice(list(SUPPLIER_PROFILES.keys()))
        supplier_avg_lead_time_days = SUPPLIER_PROFILES[supplier]["avg_lead_time_days"]

        pattern = np.random.choice(pattern_names, p=pattern_weights)
        abc_class = abc_assignments[i] if i < len(abc_assignments) else "C"

        base_price = round(random.uniform(*cat["price_range"]), 0)
        margin = round(random.uniform(*cat["margin_range"]), 3)
        cost = round(base_price * (1 - margin), 0)

        demand_range = BASE_DEMAND_RANGES[abc_class]
        base_demand = random.uniform(*demand_range)

        moq = random.choice(MOQ_CHOICES)
        extra_field_value = random.choice(EXTRA_FIELD_CHOICES)

        products.append({
            "sku": f"SKU-{sku_counter:05d}",
            "name": f"{brand} {subcat} {random.randint(100,999)}",
            "category": cat_name,
            "subcategory": subcat,
            "brand": brand,
            "supplier": supplier,
            "supplier_avg_lead_time_days": supplier_avg_lead_time_days,
            "demand_pattern": pattern,
            "abc_class": abc_class,
            "base_price": base_price,
            "cost": cost,
            "margin_pct": margin,
            "base_daily_demand": round(base_demand, 2),
            "moq": moq,
            EXTRA_FIELD_NAME: extra_field_value,
            "category_seasonality": cat["seasonality"],
        })
        sku_counter += 1

    return pd.DataFrame(products)


# ============================================================
# 2. MOTOR DE GENERACIÓN DE SERIES TEMPORALES
# ============================================================

def generate_seasonality_factors(dates, season_type, strength=1.0):
    """Genera factores de estacionalidad basados en el mes."""
    months = np.array([d.month for d in dates])
    factors = np.ones(len(dates))

    profile = SEASONALITY_PROFILES.get(season_type)
    if profile is None:
        return factors

    for m, f in profile.items():
        adj_f = 1.0 + (f - 1.0) * strength
        factors[months == m] = adj_f

    return factors


def generate_day_of_week_factors(dates):
    """Efecto día de semana según perfil (retail vs industrial)."""
    dows = np.array([d.weekday() for d in dates])
    factors = np.ones(len(dates))
    for d, f in DOW_MAP.items():
        factors[dows == d] = f
    return factors


def generate_trend(n_days, pattern, base_demand):
    """Genera componente de tendencia."""
    t = np.arange(n_days)
    if pattern == "trend_up":
        growth_rate = random.uniform(*TREND_UP_GROWTH_RATE)
        return base_demand * (1 + growth_rate * t)
    elif pattern == "trend_down":
        decay_rate = random.uniform(*TREND_DOWN_DECAY_RATE)
        return base_demand * np.maximum(TREND_DOWN_FLOOR, 1 - decay_rate * t)
    elif pattern == "new_product":
        ramp_days = random.randint(*NEW_PRODUCT_RAMP_DAYS)
        ramp = np.minimum(1.0, t / ramp_days)
        growth = 1 + random.uniform(*NEW_PRODUCT_GROWTH) * t
        return base_demand * ramp * growth
    else:
        return np.full(n_days, base_demand)


def generate_demand_spikes(n_days, pattern):
    """Genera picos de demanda (promos en retail, proyectos en industrial)."""
    spike_effects = np.ones(n_days)
    spike_flags = np.zeros(n_days, dtype=int)

    if pattern in SPIKE_PARAMS:
        n_spikes = random.randint(*SPIKE_PARAMS[pattern]["n_spikes"])
    else:
        n_spikes = random.randint(*SPIKE_PARAMS["_default"]["n_spikes"])

    for _ in range(n_spikes):
        start = random.randint(0, max(0, n_days - SPIKE_DURATION[1] - 1))
        duration = random.randint(*SPIKE_DURATION)
        multiplier = random.uniform(*SPIKE_MULTIPLIER)
        end = min(start + duration, n_days)
        spike_effects[start:end] = multiplier
        spike_flags[start:end] = 1

    return spike_effects, spike_flags


def generate_intermittent_mask(n_days, adi_target):
    """Genera máscara de intermitencia para demanda esporádica."""
    if adi_target <= ADI_THRESHOLD_FOR_CONTINUOUS:
        return np.ones(n_days)

    # Compensar por días no hábiles (fines de semana cerrados en industrial)
    prob_demand = min(1.0, (1.0 / adi_target) / WEEKDAY_FRACTION)
    mask = np.random.binomial(1, prob_demand, n_days).astype(float)
    return mask


def generate_timeseries(product, dates):
    """Genera la serie temporal completa para un producto."""
    n_days = len(dates)
    pattern = product["demand_pattern"]
    base = product["base_daily_demand"]

    # 1. Tendencia base
    trend = generate_trend(n_days, pattern, base)

    # 2. Estacionalidad
    if pattern in SEASON_STRENGTH_RANGES:
        season_strength = random.uniform(*SEASON_STRENGTH_RANGES[pattern])
    else:
        season_strength = random.uniform(*SEASON_STRENGTH_RANGES["_default"])

    seasonality = generate_seasonality_factors(dates, product["category_seasonality"], season_strength)

    # 3. Día de semana
    dow = generate_day_of_week_factors(dates)

    # 4. Ruido
    cv_low, cv_high = DEMAND_PATTERNS[pattern]["cv_range"]
    cv = random.uniform(cv_low, cv_high)

    if pattern in ["erratic", "lumpy"]:
        noise = np.random.lognormal(0, cv * LOGNORMAL_SIGMA_FACTOR, n_days)
        noise = noise / np.mean(noise)
    else:
        noise = np.maximum(NORMAL_NOISE_FLOOR, np.random.normal(1, cv, n_days))

    # 5. Intermitencia
    adi_low, adi_high = DEMAND_PATTERNS[pattern]["adi_range"]
    adi_target = random.uniform(adi_low, adi_high)
    intermittent_mask = generate_intermittent_mask(n_days, adi_target)

    # 6. Picos de demanda
    spike_effects, spike_flags = generate_demand_spikes(n_days, pattern)

    # Combinar todo
    demand = trend * seasonality * dow * noise * intermittent_mask * spike_effects

    # Redondear a enteros de demanda latente
    demand = np.maximum(0, np.round(demand)).astype(int)

    # Calcular precio con variación por spikes
    base_price = product["base_price"]
    prices = np.full(n_days, base_price)
    discount = random.uniform(*SPIKE_DISCOUNT_RANGE)
    prices[spike_flags == 1] = round(base_price * (1 - discount), 0)

    return demand, prices, spike_flags


# ============================================================
# 3. GENERACIÓN PRINCIPAL
# ============================================================

def main():
    print("=" * 60)
    print("GENERADOR DE DATOS SINTÉTICOS PARA MOTOR DE FORECAST")
    print(f"Perfil activo: {PROFILE.upper()}")
    print("=" * 60)

    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%d")
    dates = pd.date_range(start_dt, end_dt, freq="D")
    date_list = dates.tolist()
    n_days = len(dates)

    print(f"\nGenerando catálogo de {N_PRODUCTS} SKUs...")
    catalog = generate_catalog(N_PRODUCTS)

    print(f"Período: {START_DATE} a {END_DATE} ({n_days} días)")
    print(f"Locaciones: {len(LOCATIONS)} — {', '.join(LOCATIONS[:5])}{'...' if len(LOCATIONS) > 5 else ''}")

    # Estadísticas del catálogo
    print(f"\nDistribución de patrones:")
    for p, count in catalog["demand_pattern"].value_counts().items():
        print(f"  {p:20s}: {count:4d} ({count/N_PRODUCTS*100:.1f}%)")

    print(f"\nDistribución ABC:")
    for c, count in catalog["abc_class"].value_counts().items():
        print(f"  {c}: {count:4d} ({count/N_PRODUCTS*100:.1f}%)")

    # Generar transacciones
    print(f"\nGenerando series temporales...")
    daily_demand_by_sku_location = {}
    daily_prices_by_sku_location = {}
    product_metrics = []

    for idx, product in catalog.iterrows():
        if (idx + 1) % 200 == 0:
            print(f"  Procesando SKU {idx + 1}/{N_PRODUCTS}...")

        # Seleccionar locaciones por producto según clase ABC
        loc_range = LOCATIONS_PER_ABC[product["abc_class"]]
        n_locs = random.randint(loc_range[0], min(loc_range[1], len(LOCATIONS)))
        selected_locations = random.sample(LOCATIONS, n_locs)

        sku_total_qty = 0
        sku_total_revenue = 0
        sku_nonzero_days = 0
        sku_total_days = 0
        all_demands = []

        for location in selected_locations:
            loc_factor = random.uniform(*LOCATION_FACTOR_RANGE)
            demand, prices, _spikes = generate_timeseries(product, date_list)
            demand = np.maximum(0, np.round(demand * loc_factor)).astype(int)
            daily_demand_by_sku_location[(product["sku"], location)] = demand.copy()
            daily_prices_by_sku_location[(product["sku"], location)] = prices.copy()

            all_demands.extend(demand.tolist())
            sku_total_qty += demand.sum()
            sku_total_revenue += (demand * prices).sum()
            sku_nonzero_days += (demand > 0).sum()
            sku_total_days += n_days

        # Métricas por SKU para validación
        nonzero_demands = [d for d in all_demands if d > 0]
        if len(nonzero_demands) > 1:
            actual_cv = np.std(nonzero_demands) / np.mean(nonzero_demands) if np.mean(nonzero_demands) > 0 else 0
            actual_adi = len(all_demands) / len(nonzero_demands) if len(nonzero_demands) > 0 else 999
        else:
            actual_cv = 0
            actual_adi = 999

        product_metrics.append({
            "sku": product["sku"],
            "demand_pattern_assigned": product["demand_pattern"],
            "abc_class": product["abc_class"],
            "total_quantity": int(sku_total_qty),
            "total_revenue": float(sku_total_revenue),
            "avg_daily_demand": round(sku_total_qty / sku_total_days, 3) if sku_total_days > 0 else 0,
            "cv_squared": round(actual_cv ** 2, 4),
            "adi": round(actual_adi, 2),
            "pct_days_with_demand": round(sku_nonzero_days / sku_total_days * 100, 1) if sku_total_days > 0 else 0,
            "n_locations": n_locs,
        })

    # Crear DataFrames
    df_metrics = pd.DataFrame(product_metrics)
    catalog_public = build_public_catalog(catalog)

    print("\nGenerando documentos operacionales...")
    (
        df_transactions,
        df_purchase_orders,
        df_purchase_order_lines,
        df_purchase_receipts,
        df_inventory_snapshots,
    ) = generate_purchase_data(
        catalog,
        daily_demand_by_sku_location,
        daily_prices_by_sku_location,
        dates,
    )
    df_transactions_public = build_public_transactions(df_transactions)

    # XYZ Classification based on CV²
    def classify_xyz(cv2):
        if cv2 < XYZ_THRESHOLDS["X_max_cv2"]:
            return "X"
        elif cv2 < XYZ_THRESHOLDS["Y_max_cv2"]:
            return "Y"
        else:
            return "Z"

    df_metrics["xyz_class"] = df_metrics["cv_squared"].apply(classify_xyz)
    df_metrics["abc_xyz"] = df_metrics["abc_class"] + df_metrics["xyz_class"]

    # Clasificación de patrón según Syntetos-Boylan
    adi_cutoff = SYNTETOS_BOYLAN_THRESHOLDS["adi_cutoff"]
    cv2_cutoff = SYNTETOS_BOYLAN_THRESHOLDS["cv2_cutoff"]

    def classify_syntetos_boylan(row):
        cv2 = row["cv_squared"]
        adi = row["adi"]
        if adi < adi_cutoff:
            return "smooth" if cv2 < cv2_cutoff else "erratic"
        else:
            return "intermittent" if cv2 < cv2_cutoff else "lumpy"

    df_metrics["syntetos_boylan_class"] = df_metrics.apply(classify_syntetos_boylan, axis=1)

    # ============================================================
    # 4. GUARDAR ARCHIVOS
    # ============================================================

    output_dir = OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    tx_path = os.path.join(output_dir, "transactions.csv")
    df_transactions_public.to_csv(tx_path, index=False)
    print(f"\nTransacciones guardadas: {tx_path} ({len(df_transactions_public):,} filas)")

    cat_path = os.path.join(output_dir, "product_catalog.csv")
    catalog_public.to_csv(cat_path, index=False)
    print(f"Catálogo guardado: {cat_path} ({len(catalog_public):,} productos)")

    po_path = os.path.join(output_dir, "purchase_orders.csv")
    df_purchase_orders.to_csv(po_path, index=False)
    print(f"Órdenes de compra guardadas: {po_path} ({len(df_purchase_orders):,} filas)")

    po_lines_path = os.path.join(output_dir, "purchase_order_lines.csv")
    df_purchase_order_lines.to_csv(po_lines_path, index=False)
    print(f"Líneas OC guardadas: {po_lines_path} ({len(df_purchase_order_lines):,} filas)")

    receipts_path = os.path.join(output_dir, "purchase_receipts.csv")
    df_purchase_receipts.to_csv(receipts_path, index=False)
    print(f"Recepciones guardadas: {receipts_path} ({len(df_purchase_receipts):,} filas)")

    inventory_snapshot_path = os.path.join(output_dir, "inventory_snapshot.csv")
    df_inventory_snapshots.to_csv(inventory_snapshot_path, index=False)
    print(f"Snapshots de inventario guardados: {inventory_snapshot_path} ({len(df_inventory_snapshots):,} filas)")

    # Resumen
    print("\n" + "=" * 60)
    print(f"RESUMEN DE DATOS GENERADOS [{PROFILE.upper()}]")
    print("=" * 60)
    print(f"Total transacciones: {len(df_transactions_public):,}")
    print(f"Total SKUs: {N_PRODUCTS}")
    print(f"Período: {START_DATE} → {END_DATE}")
    print(f"Locaciones: {len(LOCATIONS)}")
    print(f"Órdenes de compra: {len(df_purchase_orders):,}")
    print(f"Recepciones: {len(df_purchase_receipts):,}")
    print(f"Snapshots inventario: {len(df_inventory_snapshots):,}")

    print(f"\nSegmentación ABC-XYZ:")
    abc_xyz_dist = df_metrics["abc_xyz"].value_counts().sort_index()
    for seg, count in abc_xyz_dist.items():
        print(f"  {seg}: {count:4d} ({count/N_PRODUCTS*100:.1f}%)")

    print(f"\nClasificación Syntetos-Boylan:")
    sb_dist = df_metrics["syntetos_boylan_class"].value_counts()
    for cls, count in sb_dist.items():
        print(f"  {cls:15s}: {count:4d} ({count/N_PRODUCTS*100:.1f}%)")

    print(f"\nRevenue por clase ABC:")
    for abc in sorted(ABC_RATIOS.keys()):
        rev = df_metrics[df_metrics["abc_class"] == abc]["total_revenue"].sum()
        pct = rev / df_metrics["total_revenue"].sum() * 100
        print(f"  {abc}: ${rev:,.0f} ({pct:.1f}%)")

    if not df_purchase_orders.empty:
        print(f"\nEstado de órdenes de compra:")
        for status, count in df_purchase_orders["order_status"].value_counts().items():
            print(f"  {status:18s}: {count:4d} ({count/len(df_purchase_orders)*100:.1f}%)")

    return (
        df_transactions_public,
        catalog_public,
        df_metrics,
        df_purchase_orders,
        df_purchase_order_lines,
        df_purchase_receipts,
        df_inventory_snapshots,
    )


if __name__ == "__main__":
    df_tx, df_cat, df_met, df_po, df_po_lines, df_receipts, df_inventory = main()
