# Referencia: Mapa Completo de planning_core

## Tabla de contenidos
1. [Estructura general](#1-estructura-general)
2. [services.py — Fachada principal](#2-servicespy--fachada-principal)
3. [classification/ — Segmentación de demanda](#3-classification--segmentación-de-demanda)
4. [forecasting/ — Modelos y evaluación](#4-forecasting--modelos-y-evaluación)
5. [inventory/ — Safety stock y diagnóstico](#5-inventory--safety-stock-y-diagnóstico)
6. [purchase/ — Motor de recomendaciones](#6-purchase--motor-de-recomendaciones)
7. [pipelines/ — Orquestación](#7-pipelines--orquestación)
8. [preprocessing.py y validation.py](#8-preprocessingpy-y-validationpy)
9. [repository.py y manifest](#9-repositorypy-y-manifest)
10. [Deuda técnica activa](#10-deuda-técnica-activa)
11. [Mejoras técnicas propuestas](#11-mejoras-técnicas-propuestas)

---

## 1. Estructura general

```
planning_core/
├── services.py              # PlanningService — fachada (~1.100 líneas)
├── classification/
│   ├── __init__.py          # Re-exporta interfaz pública
│   ├── core.py              # Lógica pura de clasificación (~800 líneas)
│   └── store.py             # ClassificationStore — artefacto persistido (~150 líneas)
├── forecasting/
│   ├── selector.py          # Horse-race y selección de modelos (~400 líneas)
│   ├── metrics.py           # MASE, WMAPE, RMSSE, Bias (~200 líneas)
│   ├── utils.py             # Helpers: season_length, to_nixtla_df, FREQ_MAP
│   ├── models/
│   │   ├── arima.py         # fit_predict_arima() — statsmodels AutoARIMA
│   │   ├── ets.py           # fit_predict_ets() — statsmodels ExponentialSmoothing
│   │   ├── mstl.py          # fit_predict_mstl() — StatsForecast MSTL (multi-seasonal)
│   │   ├── lgbm.py          # fit_predict_lgbm() — LightGBM + feature engineering
│   │   ├── naive.py         # fit_predict_naive() — SeasonalNaive
│   │   └── sba.py           # fit_predict_sba(), fit_predict_adida() — CrostonSBA + ADIDA
│   └── evaluation/
│       ├── backtest.py      # run_backtest() — expanding-window (~220 líneas)
│       ├── forecast_store.py# ForecastStore — artefacto persistido (~200 líneas)
│       ├── comparator.py    # Comparación entre modelos
│       ├── aggregator.py    # Agregación de resultados de catálogo
│       ├── catalog_runner.py# Ejecución por lotes
│       └── run_store.py     # Orquestación de ForecastStore
├── inventory/
│   ├── params.py            # InventoryParams (dataclass) + get_sku_params()
│   ├── safety_stock.py      # SafetyStockResult (dataclass) + compute_sku_safety_stock()
│   ├── diagnostics.py       # InventoryDiagnosis (dataclass) + diagnose_sku()
│   └── service_level.py     # CSL defaults por ABC, csl_to_z(), get_csl_target()
├── purchase/
│   ├── recommendation.py    # PurchaseRecommendation + build_purchase_recommendation()
│   └── order_proposal.py    # PurchaseProposal + aggregate_by_supplier()
├── pipelines/
│   ├── classification.py    # run_catalog_classification(), augment_censoring
│   ├── forecast.py          # run_sku_forecast(), run_catalog_forecast()
│   ├── inventory.py         # run_catalog_health_report()
│   └── purchase.py          # run_purchase_plan(), run_purchase_plan_by_supplier()
├── preprocessing.py         # mark_censored_demand() — detección de lost sales
├── validation.py            # basic_health_report() — 6 checks básicos (D08: incompleto)
└── repository.py            # CanonicalRepository — carga parquet/CSV desde output/
```

**Tablas canónicas en** `output/`: `product_catalog`, `inventory_snapshot`, `transactions`, `purchase_order_lines`, `purchase_receipts`, `internal_transfers`

**Artefactos derivados en** `output/derived/`: `classification_catalog_{gran}.parquet`, `forecast_catalog_{gran}.parquet` + JSONs de metadata

---

## 2. services.py — Fachada principal

**Clase**: `PlanningService(repository, manifest)`

### Métodos por sección

**Dataset y configuración**
- `dataset_overview()` → resumen general del catálogo
- `dataset_health()` → resultado de `basic_health_report()`
- `currency_code()` → moneda del manifest
- `classification_config()` → parámetros de clasificación del manifest
- `location_model()` → "central" o "multi-location"
- `central_location()` → nombre de la ubicación central
- `service_level_config()` → CSL targets por clase ABC

**Catálogo y maestros**
- `list_skus(location, granularity)` → lista con clasificación enriquecida
- `list_categories()` → categorías únicas del catálogo
- `list_suppliers()` → proveedores únicos
- `list_locations()` → ubicaciones activas

**Detalle de SKU**
- `sku_summary(sku, location, granularity)` → resumen completo (inventario + clasificación)
- `sku_timeseries(sku, location, granularity)` → serie temporal de transacciones
- `sku_demand_series(sku, location, granularity)` → demanda agregada por período
- `sku_outlier_series(sku, location, granularity)` → máscara de outliers
- `sku_acf(sku, location, granularity, max_lags)` → autocorrelación

**Clasificación**
- `classify_catalog(scope, granularity)` → DataFrame con toda la clasificación (usa ClassificationStore si fresco)
- `classify_single_sku(sku, location, granularity)` → clasificación individual
- `run_catalog_classification(granularity)` → ejecuta y materializa en ClassificationStore
- `catalog_classification_status(granularity)` → metadata del store: frescura, ABC distribution

**Preprocesamiento**
- `sku_clean_series(sku, location, granularity, method, strategy)` → serie limpia (outliers tratados)
- `sku_censored_mask(sku, location, granularity, threshold)` → máscara de períodos censurados

**Forecasting**
- `sku_forecast(sku, location, granularity, h, n_windows, outlier_method, treat_strategy, use_lgbm, return_cv)` → resultado completo del horse-race
- `run_catalog_forecast(granularity, n_jobs, use_lgbm, n_windows, h)` → materializa ForecastStore
- `catalog_forecast_status(granularity)` → metadata: frescura, coverage_pct, top_model

**Inventario**
- `sku_inventory_params(sku, abc_class, granularity)` → InventoryParams
- `sku_safety_stock(sku, location, granularity, abc_class)` → SafetyStockResult
- `catalog_health_report(granularity, simple_safety_pct)` → DataFrame con diagnóstico completo

**Compras**
- `purchase_plan(granularity, include_equilibrio, include_sobrestock, limit, simple_safety_pct)` → lista de PurchaseRecommendation
- `purchase_plan_by_supplier(granularity, simple_safety_pct)` → lista de PurchaseProposal
- `purchase_plan_summary(granularity, simple_safety_pct)` → KPIs ejecutivos
- `sku_purchase_recommendation(sku, abc_class, granularity, simple_safety_pct)` → recomendación individual

### Parámetros clave
- `granularity`: `"M"` (mensual), `"W"` (semanal), `"D"` (diario)
- `location`: código de sucursal o `None` (network aggregate)
- `n_windows`: ventanas de backtest (min 3)
- `h`: horizonte de forecast (1-12 periodos; si no se especifica, se deriva del lead time)
- `simple_safety_pct`: default 0.5 — factor para SS de clase C

### Nota de arquitectura
`services.py` tiene ~1.100 líneas y mezcla orquestación con lógica de negocio. La refactorización hacia thin facade está pendiente — toda la lógica pesada debería vivir en los pipelines. Ver deuda técnica.

---

## 3. classification/ — Segmentación de demanda

### classification/core.py — Funciones principales

```python
prepare_demand_series(sku_tx, granularity) → DataFrame[period, demand]
    # Rellena ceros, resamplea a D/W/M
    # Parámetros: granularity = "D"|"W"|"M"

select_granularity(sku_tx) → str
    # Heurística: si >50 periodos, usa granularidad mayor

detect_outliers(demand, method="iqr") → pd.Series[bool]
    # method: "iqr" (default) | "zscore"

treat_outliers(demand, mask, strategy="winsorize") → pd.Series
    # strategy: "winsorize" (default) | "mean"

compute_acf(demand, max_lags=24) → np.ndarray
    # Autocorrelación para test de estacionalidad

test_seasonality(demand, season_length) → bool
    # ACF en lag=season_length > 0.3

test_trend(demand) → bool
    # Mann-Kendall test p-value < 0.05

compute_adi_cv2(demand) → float
    # ADI = Σperiodos / Σperiodos_con_demanda_positiva
    # CV² = var(demanda_no_nula) / mean(demanda_no_nula)²

classify_syntetos_boylan(adi, cv2) → str
    # Returns: "smooth" | "erratic" | "intermittent" | "lumpy"
    # Umbrales: ADI=1.32, CV²=0.49

compute_abc_segmentation(catalog, scope) → pd.Series
    # scope: afecta si ABC se calcula por red o por ubicación
    # Pareto: A=80%, B=15%, C=5% del valor anual

compute_xyz_class(adi_cv2) → str
    # X: CV² < 0.49, Y: 0.49 ≤ CV² < 1.5, Z: CV² ≥ 1.5

compute_quality_score(adi_cv2, has_trend, is_seasonal, series_length) → float
    # Score 0–1 base, penalizado por censura en el pipeline

classify_sku(sku_tx, sku_inv, scope, granularity) → dict
    # Output completo: sku, abc_class, sb_class, xyz_class, is_seasonal,
    # has_trend, quality_score, adi_cv2, season_length

classify_all_skus(service, scope, granularity) → DataFrame
    # Una fila/SKU con clasificación + censura
```

**Limitación conocida**: los umbrales ADI=1.32, CV²=0.49 están hardcoded. Una mejora valiosa sería exponerlos como parámetros en el manifest.

### classification/store.py — ClassificationStore

```python
ClassificationStore.load(base_dir: Path, granularity: str) → ClassificationStore | None
    # Carga desde output/derived/classification_catalog_{gran}.parquet + .json

store.is_stale() → bool
    # Frescura: {"M": 35, "W": 9, "D": 2} días

store.all_skus_df() → DataFrame
    # Todas las filas del store

store.get_sku(sku_id: str) → dict | None
    # Lookup O(1)

store.metadata() → dict
    # {run_date, n_skus, scope, abc_distribution, ...}

store.save(base_dir: Path, granularity: str)
    # Escritura atómica (write-then-rename)
```

**Columnas requeridas del store**: sku, abc_class, sb_class, xyz_class, is_seasonal, has_trend, quality_score, granularity, adi_cv2, censored_pct, censored_demand_pct, has_censored_demand

---

## 4. forecasting/ — Modelos y evaluación

### forecasting/selector.py

```python
select_and_forecast(
    profile: dict,         # output de classify_sku()
    demand_df: DataFrame,  # [period, demand_clean]
    granularity: str,      # "D"|"W"|"M"
    h: int,                # horizonte de forecast (periodos)
    n_windows: int,        # ventanas de backtest (min 3)
    unique_id: str,        # identificador del SKU
    use_lgbm: bool = False,# incluir LightGBM en candidatos
    return_cv: bool = False,# retornar detalles de validación cruzada
) → dict
```

**Resultado**:
```python
{
    "status": "ok" | "fallback" | "no_forecast" | "error",
    "model": str,          # nombre del modelo ganador o "Ensemble"
    "mase": float,
    "bias": float,
    "wmape": float,
    "forecast": DataFrame, # [ds, yhat, yhat_lo80, yhat_hi80]
    "backtest": {model: {mase, bias, wmape, rmsse, status}, ...},
    "season_length": int,
    "granularity": str,
    "h": int,
}
```

**Reglas de candidatura**:
```
smooth + seasonal → AutoETS, AutoARIMA, MSTL, SeasonalNaive
smooth + not seasonal → AutoETS, AutoARIMA, SeasonalNaive
erratic → AutoETS, AutoARIMA, SeasonalNaive
intermittent → CrostonSBA, ADIDA
lumpy → CrostonSBA, ADIDA
inactive → no forecast
```

**Lógica de selección del ganador** (`_pick_winner`):
1. Filtrar válidos (status=ok, MASE not NaN)
2. Ordenar por MASE ascendente
3. Tiebreak por RMSSE si |MASE₁ - MASE₂| < 0.02
4. Si ganador tiene |Bias| > 0.20 Y existe alternativa con MASE ≤ ganador×1.20 Y menor |Bias| → preferir alternativa

**Ensemble** (`_apply_ensemble`): si ≥ 2 modelos con MASE ≤ ganador×1.25 → promediar yhat (hasta k=3). Etiquetado como "Ensemble".

**Bias correction**: `yhat_corr = yhat / (1 + bias)` acotado a ±30% si |bias| ≥ 0.02.

### forecasting/metrics.py

```python
compute_all_metrics(
    actual: np.ndarray,
    forecast: np.ndarray,
    season_length: int = 12,
    naive_type: str = "seasonal",  # "seasonal" | "lag1" | "mean"
) → dict  # {mase, wmape, rmsse, bias, mae, rmse}
```

**Tipos de naive**:
- `"seasonal"`: replica último ciclo estacional → para series con estacionalidad
- `"lag1"`: replica el último período → para smooth/erratic sin estacionalidad
- `"mean"`: promedio histórico → para intermittent/lumpy

### forecasting/models/ — Interfaz común

Todos los modelos retornan `(forecast_df: DataFrame, point_forecast: float, rmse_backtest: float, status: str)`. Todos manejan series cortas y edge cases.

| Módulo | Función principal | Modelo base |
|--------|------------------|-------------|
| arima.py | `fit_predict_arima(demand_df, h, season_length, gran)` | statsmodels AutoARIMA |
| ets.py | `fit_predict_ets(demand_df, h, season_length, gran)` | statsmodels ExponentialSmoothing auto |
| mstl.py | `fit_predict_mstl(demand_df, h, season_length, gran)` | StatsForecast MSTL (multi-seasonal) |
| lgbm.py | `fit_predict_lgbm(demand_df, h, lags, gran)` | LightGBM + feature engineering |
| naive.py | `fit_predict_naive(demand_df, h, season_length)` | SeasonalNaive (lag-m) |
| sba.py | `fit_predict_sba(demand_df, h)` / `fit_predict_adida(demand_df, h)` | StatsForecast CrostonSBA + ADIDA |

### forecasting/evaluation/backtest.py

```python
run_backtest(
    demand_df: DataFrame,
    model_instances: list,
    model_names: list,
    granularity: str = "M",
    h: int = 3,
    n_windows: int = 3,
    unique_id: str = "SKU",
    target_col: str = "demand",
    naive_type: str = "seasonal",
    return_cv: bool = False,
) → dict  # {model_name: {mase, bias, wmape, rmsse, status}, ...}
```

Mínimo de datos: `season_length + h × n_windows` observaciones.

### forecasting/evaluation/forecast_store.py — ForecastStore

Análogo a ClassificationStore. Persiste en `output/derived/forecast_catalog_{gran}.parquet` + JSON.

**Frescura**: `{"M": 35, "W": 9, "D": 2}` días.

**Integración con safety stock**: cuando el ForecastStore está fresco, `pipelines/inventory.py` inyecta `forecast_mean_daily` y `forecast_sigma_daily` directamente en el cálculo de SS/ROP → SS forward-looking (Opción C del diseño).

---

## 5. inventory/ — Safety stock y diagnóstico

### inventory/params.py

```python
@dataclass
class InventoryParams:
    sku: str
    lead_time_days: float        # calculado desde historial de purchase receipts
    sigma_lt_days: float         # std dev del lead time real
    review_period_days: float    # por clase ABC (manifest)
    carrying_cost_rate: float    # tasa anual de mantener inventario
    abc_class: str | None
    csl_target: float            # 0.985 (A) | 0.945 (B) | 0.885 (C)
    z_factor: float              # cuantil normal para el CSL

get_sku_params(sku, abc_class, supplier, repository, manifest) → InventoryParams
# Fuentes de lead time por prioridad:
# 1. Override por SKU en manifest
# 2. Lead time calculado por proveedor (desde purchase receipts)
# 3. Defaults por clase ABC en manifest
# 4. Global defaults
```

### inventory/safety_stock.py

```python
@dataclass
class SafetyStockResult:
    sku: str
    granularity: str
    mean_demand_daily: float
    sigma_demand_daily: float
    safety_stock: float          # unidades
    reorder_point: float         # ROP = d̄_daily × LT + SS
    coverage_ss_days: float      # días cubiertos por SS
    ss_method: str               # "extended" | "standard" | "simple_pct_lt"
    n_periods: int

compute_sku_safety_stock(
    params: InventoryParams,
    demand_series: DataFrame,    # [period, demand]
    granularity: str,
    simple_safety_pct: float = 0.5,
) → SafetyStockResult
```

**Conversión de granularidad**: `_DAYS_PER_PERIOD = {"D": 1.0, "W": 7.0, "M": 365.25/12}`

### inventory/diagnostics.py

```python
@dataclass
class InventoryDiagnosis:
    sku: str
    abc_class: str | None
    on_hand: float
    on_order: float              # stock en tránsito (ya incluido en cálculo)
    stock_efectivo: float        # = on_hand + on_order
    mean_demand_daily: float
    coverage_net_days: float     # stock_efectivo / d̄_daily
    coverage_obj_days: float     # LT + R + SS_coverage_days
    positioning_ratio: float     # coverage_net / coverage_obj
    health_status: str           # ver tabla de bandas
    alert_level: str             # "rojo" | "naranja" | "amarillo" | "gris" | "none"
    stockout_probability: float  # P(D_LT+R > stock_efectivo)
    days_since_last_sale: int    # para detección de dead_stock

diagnose_sku(
    sku, on_hand, on_order,
    ss_result: SafetyStockResult,
    params: InventoryParams,
    mean_demand_daily: float,
    days_since_last_sale: int,
    manifest_config: dict,
) → InventoryDiagnosis
```

**Bandas de posicionamiento**:
- `positioning_ratio < 0.3` → `quiebre_inminente` (rojo)
- `0.3 – 0.7` → `substock` (naranja)
- `0.7 – 1.3` → `equilibrio` (none)
- `1.3 – 2.0` → `sobrestock_leve` (amarillo)
- `> 2.0` → `sobrestock_critico` (gris)
- sin venta > 90d → `dead_stock` (gris)

### inventory/service_level.py

```python
CSL_DEFAULTS = {"A": 0.985, "B": 0.945, "C": 0.885}

csl_to_z(csl: float) → float       # interpolación lineal sobre tabla
get_csl_target(abc_class, manifest_config) → ServiceLevelConfig
```

---

## 6. purchase/ — Motor de recomendaciones

### purchase/recommendation.py

```python
@dataclass
class PurchaseRecommendation:
    sku: str
    name: str
    supplier: str | None
    abc_class: str | None
    health_status: str
    alert_level: str
    stockout_probability: float
    stock_efectivo: float
    reorder_point: float
    suggested_order_qty: float   # bruto: max(ROP - stock_efectivo, 0)
    moq: float
    pack_size: float
    recommended_qty: float       # ajustado a MOQ + pack_size
    eoq: float
    final_qty: float             # qty real a ordenar (0 si sobrestock/equilibrio)
    urgency_score: float         # 0–100
    days_until_stockout: float
    order_deadline: str | None   # ISO date: fecha límite para colocar el pedido
    excess_units: float
    days_to_normal: float        # días para consumir el exceso naturalmente
    excess_carrying_cost: float  # costo de mantener el exceso
    demand_signal_source: str    # "historical" | "forecast"
```

**Funciones clave**:

```python
compute_recommended_qty(suggested, moq, pack_size) → float
# Ajusta al múltiplo de pack_size ≥ max(suggested, moq)

compute_eoq(annual_demand, order_cost, unit_cost, carrying_rate) → float
# EOQ = √(2 × D × K / h)

compute_urgency_score(health_status, P_stockout, abc_class, days_until_stockout, lead_time_days) → float
# Score 0–100 compuesto:
# - health_status: quiebre=50pts, substock=20pts, equilibrio=0, sobrestock=-20
# - P(stockout) amplifica el score
# - Clase A multiplica el score
# - days_until_stockout / lead_time_days para urgencia temporal

compute_days_until_stockout(coverage_net_days, lead_time_days) → float
# Días hasta que el stock efectivo cae a cero

compute_order_deadline(lead_time_days, days_until_stockout, ref_date) → str | None
# Fecha límite = ref_date + (days_until_stockout - lead_time_days)

build_purchase_recommendation(sku, diagnosis, params, catalog_row, manifest) → PurchaseRecommendation
generate_purchase_plan(health_rows, catalog, params_map, manifest, include_equilibrio, include_sobrestock) → list[PurchaseRecommendation]
```

### purchase/order_proposal.py

```python
@dataclass
class PurchaseProposal:
    supplier: str | None
    sku_count: int
    total_units: float
    total_cost_estimate: float
    max_urgency_score: float
    alert_levels: list[str]
    skus: list[PurchaseRecommendation]

aggregate_by_supplier(recommendations: list[PurchaseRecommendation]) → list[PurchaseProposal]
# Solo final_qty > 0, ordenado por max_urgency_score desc

purchase_plan_summary(recommendations) → dict
# KPIs ejecutivos:
# {sku_quiebre, sku_substock, sku_equilibrio, sku_sobrestock_leve, sku_sobrestock_critico,
#  sku_dead_stock, total_cost_estimate, supplier_count, max_urgency_score, avg_urgency_score}
```

---

## 7. pipelines/ — Orquestación

### pipelines/classification.py

```python
run_sku_classification(service, sku, location, granularity) → dict
# Clasificación individual con logging de eventos

run_catalog_classification_full(service, granularity) → DataFrame
# Cálculo limpio (no usa store)

run_catalog_classification(service, granularity, persist=True) → DataFrame
# Clasificación + materialización en ClassificationStore

catalog_classification_status(service, granularity) → dict
# {status: "ok"|"stale"|"missing", run_date, n_skus, abc_distribution, is_stale}

compute_censoring_info(sku_tx, sku_inv, granularity, stockout_threshold) → (demand_df, censored_mask, summary)
augment_profile_with_censoring(profile, sku_tx, sku_inv, granularity) → dict
augment_catalog_classification_with_censoring(classification_df, transactions, inventory, granularity) → DataFrame
```

### pipelines/forecast.py

```python
run_sku_forecast(
    service, sku, location, granularity, h,
    n_windows, outlier_method, treat_strategy,
    use_lgbm, return_cv
) → dict  # Mismo formato que selector.py

run_catalog_forecast(
    service, granularity, n_jobs=1,
    use_lgbm=False, n_windows=3, h=3
) → None  # Materializa ForecastStore

catalog_forecast_status(service, granularity) → dict
# {status, run_date, n_skus, coverage_pct, top_model, is_stale}
```

**Horizonte dinámico (D20)**: `h = ceil(lead_time_days / days_per_period)` clamped a [1, 12].

### pipelines/inventory.py

```python
run_catalog_health_report(
    service, granularity,
    simple_safety_pct=0.5,
    derived_dir: Path = None,
) → DataFrame
# Intenta cargar ForecastStore al inicio
# Si fresco: inyecta forecast_mean_daily, forecast_sigma_daily → SS forward-looking
# Si ausente/stale: cae a histórico (con WARN en log)
```

### pipelines/purchase.py

```python
run_purchase_plan(service, granularity, include_equilibrio, include_sobrestock, limit, simple_safety_pct) → list[dict]
run_purchase_plan_by_supplier(service, granularity, simple_safety_pct) → list[dict]
run_purchase_plan_summary(service, granularity, simple_safety_pct) → dict
run_sku_purchase_recommendation(service, sku, abc_class, granularity, simple_safety_pct) → dict | None
```

---

## 8. preprocessing.py y validation.py

### preprocessing.py

```python
mark_censored_demand(
    demand_df: DataFrame,         # [period, demand]
    inventory_df: DataFrame,      # [snapshot_date, on_hand_qty, location]
    granularity: str = "M",
    stockout_threshold: float = 0.0,
) → pd.Series[bool]
# Lógica: agrega inventario por período tomando MÍNIMO (si toca cero algún día → período censurado)
# Un período con min(on_hand) ≤ stockout_threshold → True (censurado)

censored_summary(censored_mask, demand_df) → dict
# {censored_periods, total_periods, censored_pct, censored_demand, censored_demand_pct}
```

**Limitaciones conocidas**:
- Asume on_hand=0 → quiebre (no considera on_order)
- No distingue quiebre operacional de planificado
- En modelo central, inventario de sucursales puede ser 0 mientras central tiene stock

### validation.py

```python
basic_health_report(repository: CanonicalRepository) → dict
```

**Checks implementados actualmente** (muy básicos — D08):
1. Duplicados de clave en transactions, inventory, transfers, PO, receipts
2. Cantidades negativas en transactions, on_hand, on_order, transfers, receipts
3. Transfers abiertos sin receipt_date

**Checks faltantes** (D08, prioridad alta):
- FK checks: sku en transactions debe existir en product_catalog
- FK checks: location en inventory_snapshot debe existir en manifest
- Temporal checks: receipt_date >= po.created_at
- Over-receipt: receipt_qty no debería superar po_qty en más de X%
- Reconciliación de inventario: on_hand[t] ≈ on_hand[t-1] + receipts[t] − sales[t] − transfers[t]
- Gaps temporales inexplicables en series de demanda (posible cambio de sistema ERP)
- Cambios de unidad de medida no registrados

---

## 9. repository.py y manifest

### CanonicalRepository

Carga datos desde `output/` en cada llamada. Sin caché interno (caché en la UI con `@st.cache_data`).

```python
repo.get_transactions() → DataFrame
repo.get_inventory_snapshot() → DataFrame
repo.get_product_catalog() → DataFrame
repo.get_purchase_order_lines() → DataFrame
repo.get_purchase_receipts() → DataFrame
repo.get_internal_transfers() → DataFrame
repo.get_manifest() → dict
```

### Manifest (output/manifest.json)

Parámetros de configuración del sistema:

```json
{
  "profile": "...",
  "currency": "CLP",
  "central_location": "CENTRAL",
  "classification_scope": "network_aggregate",
  "classification_default_granularity": "M",
  "inventory_params": {
    "A": {"review_period_days": 7, "carrying_cost_rate": 0.25, "csl_target": 0.985},
    "B": {"review_period_days": 14, "carrying_cost_rate": 0.25, "csl_target": 0.945},
    "C": {"review_period_days": 30, "carrying_cost_rate": 0.25, "csl_target": 0.885}
  }
}
```

---

## 10. Deuda técnica activa

| ID | Prioridad | Descripción | Impacto |
|----|-----------|-------------|---------|
| **D08** | **Alta** | `validation.py` con solo 6 checks básicos (faltan FK, temporal, over-receipt, reconciliación) | Decisiones sobre datos corruptos. FK inválidos, inventario no reconciliado, over-receipt inflan métricas |
| **D09** | Media | Cobertura 0 en `classification/core.py`, `preprocessing.py`, `validation.py` | Regressions silenciosas en clasificación y censura — cambios sin tests que rompan algo |
| D14 | Media | API no garantiza que `classify_catalog()` use store-first | Recálculo costoso en cada request a la API; afecta latencia de todos los endpoints que usen clasificación |
| D33 | Media | Sin dashboard agregado de calidad de forecast en la UI | No hay visibilidad de qué SKUs están degradándose en precisión |
| — | Media | `services.py` con ~1.100 líneas (thin facade pendiente) | Difícil de mantener; mezcla orquestación y lógica de negocio |
| D21 | Baja | Falta notebook reproducible del sweep de parametrización | Dificulta calibración de umbrales y validación de parámetros del manifest |

---

## 11. Mejoras técnicas propuestas

### Alta prioridad técnica

**D08 — Completar validation.py**:
```python
# FK check ejemplo:
valid_skus = set(catalog["sku"])
invalid_tx = transactions[~transactions["sku"].isin(valid_skus)]
if len(invalid_tx) > 0:
    issues.append({"check": "fk_sku_in_catalog", "count": len(invalid_tx), "severity": "error"})

# Reconciliación de inventario:
# on_hand[t] = on_hand[t-1] + receipts[t] - sales[t] - transfers_out[t]
# Tolerancia: ±5% por diferencias de timing
```

**Safety stock probabilístico para intermitente/lumpy**:
```python
# En forecasting/evaluation/forecast_store.py: agregar forecast_p80_daily, forecast_p95_daily
# En inventory/safety_stock.py: para sb_class in ["intermittent", "lumpy"]:
#   usar ss_percentile = percentile(forecast_distribution, csl_target) - mean_forecast
#   en lugar de la fórmula normal
```

**Concept drift detection**:
```python
# En ForecastStore: almacenar mase_at_selection por SKU
# En catalog_forecast_status(): calcular mase_current vs mase_at_selection
# Si ratio > 1.5 → flag "degraded" → forzar re-run del horse-race
```

### Media prioridad

**Umbrales ADI/CV² configurables en manifest**:
```json
"classification_params": {
  "adi_threshold": 1.32,
  "cv2_threshold": 0.49,
  "seasonality_acf_threshold": 0.3,
  "dead_stock_days": 90
}
```

**GMROI por SKU en el diagnóstico**:
```python
# Requiere: unit_cost (en product_catalog) + gross_margin_rate (en manifest o por categoría)
# gmroi = (annual_demand × unit_cost × margin_rate) / (mean_inventory × unit_cost)
# Agregar a InventoryDiagnosis y al health report
```

**Consolidación de órdenes por ventana temporal**:
```python
# En run_purchase_plan_by_supplier(): agrupar SKUs del mismo proveedor
# cuyo order_deadline está dentro de los próximos N días
# Mostrar ahorro potencial en costos de flete
```

**Tests para classification/core.py** (D09):
```python
# test_classification.py debe cubrir:
# - compute_adi_cv2() con series de demanda conocida
# - classify_syntetos_boylan() en los 4 cuadrantes del espacio ADI-CV²
# - test_seasonality() con serie estacional artificial
# - classify_all_skus() como test de integración con datos de fixture
```
