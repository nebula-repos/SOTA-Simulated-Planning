# SOTA - Simulated Planning

Sistema de demand planning con forecasting, clasificación de series temporales, gestión de inventario y recomendación de compra para catálogos industriales.

**Demo en vivo:** [sota-simulated-planning.streamlit.app](https://sota-simulated-planning.streamlit.app/)

## Propósito

Este repositorio implementa un pipeline completo de **demand planning** que abarca:

1. **Generación de datos sintéticos** realistas para desarrollo y testing
2. **Clasificación automática** de patrones de demanda (Syntetos-Boylan ADI-CV²)
3. **Segmentación ABC-XYZ** por valor y predictibilidad
4. **Forecasting** con selección automática de modelos por tipo de demanda
5. **Gestión de inventario**: Safety Stock, ROP y diagnóstico de salud por SKU
6. **Health Status Report**: vista agregada de salud de inventario con métricas financieras

## Estado actual

Fases 0-4 del pipeline completadas. El repo es un sistema funcional end-to-end: desde la generación de datos hasta el diagnóstico de salud del catálogo completo.

### Qué existe hoy

| Componente | Descripción | Estado |
|---|---|---|
| Generador de datos | Series diarias con 10 patrones de demanda, multi-perfil | Funcional |
| Modelo de datos (7 tablas) | Catálogo, transacciones, snapshots inventario, OC, recepciones, transferencias | Funcional |
| `planning_core` | Repository, servicios, agregaciones y health checks básicos | Funcional |
| Clasificación de demanda | Syntetos-Boylan, ABC-XYZ, estacionalidad, tendencia, outliers, quality score, censura | Funcional |
| Preprocessing | Limpieza de outliers (IQR/Hampel) y detección de demanda censurada por stockout | Funcional |
| Modelos de forecast | AutoETS, AutoARIMA, MSTL, CrostonSBA, ADIDA, SeasonalNaive, LightGBM | Funcional |
| Backtest + selector | `run_backtest` expanding-window, horse-race por MASE adaptativo, `select_and_forecast` | Funcional |
| Evaluación de catálogo | `run_catalog_evaluation` — horse-race masivo en paralelo, checkpoints, resume | Funcional |
| Comparación de runs | `run_store`, `aggregator`, `comparator` — persistencia y análisis multi-run | Funcional |
| `PlanningService.sku_forecast()` | Selección automática de modelo + forecast por SKU | Funcional |
| **Módulo de inventario** | Parámetros por SKU, Safety Stock (3 métodos), ROP, nivel de servicio CSL | **Funcional** |
| **Diagnóstico de salud** | `diagnose_sku`, ratio de posicionamiento, P(quiebre), bandas de alerta, texto explicativo | **Funcional** |
| **`catalog_health_report()`** | Diagnóstico masivo del catálogo con métricas financieras (capital exceso/riesgo) | **Funcional** |
| UI | Clasificación, Forecast (IC 80%, backtest), **Inventario por SKU**, **Health tab** | Funcional |
| API | Endpoints REST para catálogo, clasificación, timeseries, forecast e inventario por SKU | Funcional |
| Tests | **170 tests** unitarios e integración, 100% passing | Funcional |
| Experimentación | `exp_02_catalog_eval.py`, `exp_03_param_sweep.py` — evaluación batch + barrido h×n_windows | Funcional |
| Notebooks | `02_catalog_evaluation.ipynb` — análisis post-evaluación batch | Funcional |

### Resultado del barrido de parametrización (2026-03-25)

Se evaluó un grid de 6 configuraciones (h ∈ {3,6}, n_windows ∈ {3..6}) sobre el catálogo completo de 800 SKUs.
**Config de producción decidida: `h=3, n_windows=3`** — mejor MASE global (0.7475), menor fallback (4.6%), máxima cobertura.
Ver `docs/forecasting_param_sweep_results.md` para el análisis completo.

### Qué viene a continuación

- Motor de recomendación de compra (Fase 5): generación de órdenes de compra sugeridas con ROP/s-S/s-Q + MOQ + forecast
- Prophet / NeuralProphet para estacionalidad compleja con calendarios (Fase 3.4)
- Métricas operacionales para intermittent/lumpy: Fill Rate, Cycle Service Level (D18)
- Fortalecimiento de `validation.py` y cobertura de tests por capa (D08, D09, D26, D27)

## Estructura del repositorio

```
SOTA-Simulated-Planning/
├── apps/
│   ├── api/                     # API REST (FastAPI) — catálogo, clasificación, forecast, inventario
│   ├── simulator/               # Generador del dataset canónico
│   └── viz/                     # UI exploratoria (Streamlit) — clasificación, forecast, inventario, health
├── planning_core/
│   ├── classification.py        # Clasificador Syntetos-Boylan + ABC-XYZ
│   ├── preprocessing.py         # Outliers + demanda censurada
│   ├── services.py              # PlanningService — punto de entrada principal
│   ├── repository.py            # Carga del dataset canónico
│   ├── validation.py            # Health checks básicos
│   ├── inventory/
│   │   ├── params.py            # InventoryParams, lead times por proveedor, períodos de revisión
│   │   ├── service_level.py     # CSL por ABC, factor z, ServiceLevelConfig
│   │   ├── safety_stock.py      # Safety Stock (extended/standard/simple), ROP, SafetyStockResult
│   │   └── diagnostics.py       # diagnose_sku, InventoryDiagnosis, bandas de salud, P(quiebre)
│   └── forecasting/
│       ├── metrics.py           # MASE adaptativo, WMAPE, RMSSE, WAPE, Bias, MAE, RMSE
│       ├── backtest.py          # run_backtest expanding-window
│       ├── selector.py          # select_and_forecast — horse-race completo
│       ├── utils.py             # FREQ_MAP, to_nixtla_df
│       ├── models/              # naive, ets, arima, mstl, sba, lgbm
│       └── evaluation/          # catalog_runner, run_store, aggregator, comparator
├── experiments/
│   ├── exp_02_catalog_eval.py   # Evaluación batch del catálogo completo
│   └── exp_03_param_sweep.py    # Barrido de parametrización h x n_windows
├── notebooks/
│   └── 02_catalog_evaluation.ipynb  # Análisis post-evaluación batch
├── tests/                       # 170 tests unitarios e integración
├── output/                      # Datos generados (CSVs canónicos versionados)
├── pyproject.toml               # Dependencias por capa via extras
└── docs/
    ├── forecasting_models_plan.md          # Estado del módulo de forecasting
    ├── forecasting_param_sweep_results.md  # Resultados del barrido de parametrización
    ├── forecasting_param_sweep_plan.md     # Diseño del experimento de sweep
    ├── forecasting_benchmark_selection.md  # MASE adaptativo por tipo de producto
    ├── forecasting_parametrizacion.md      # Guía de parámetros h, n_windows, granularity
    ├── technical_debt_register.md          # Backlog de deuda técnica priorizada
    ├── business_logic_simulation.md
    ├── data_health_checks.md
    ├── output_er_model.md
    └── purchase_data_schema.md
```

## Generador de datos sintéticos

### Qué genera

El generador produce **3 años de datos diarios** (2022-2024) para un catálogo configurable de productos, simulando condiciones realistas de demanda y abastecimiento.

**Tablas de salida** (en `output/`):

| Archivo | Descripción | Clave |
|---|---|---|
| `product_catalog.csv` | Maestro de productos (SKU, categoría, proveedor, precio, costo, MOQ) | `sku` |
| `transactions.csv` | Transacciones reales de salida por SKU y ubicación | `date + sku + location` |
| `inventory_snapshot.csv` | Posición diaria de inventario por SKU y ubicación | `snapshot_date + sku + location` |
| `internal_transfers.csv` | Traslados internos entre nodo central y sucursales | `transfer_id` |
| `purchase_orders.csv` | Cabeceras de órdenes de compra | `po_id` |
| `purchase_order_lines.csv` | Detalle de productos por orden de compra | `po_line_id` |
| `purchase_receipts.csv` | Recepciones efectivas de mercadería | `receipt_id` |

### Patrones de demanda simulados

El generador cubre los cuatro cuadrantes de la clasificación Syntetos-Boylan más patrones adicionales:

| Patrón | ADI | CV | Descripción |
|---|---|---|---|
| constant | < 1.32 | 0.05 - 0.20 | Demanda estable y predecible |
| smooth | < 1.32 | 0.15 - 0.35 | Regular con variación moderada |
| erratic | < 1.32 | 0.50 - 1.30 | Frecuente pero con volumen variable |
| seasonal | < 1.32 | 0.30 - 0.65 | Con estacionalidad marcada |
| trend_up | < 1.32 | 0.10 - 0.35 | Tendencia creciente |
| trend_down | < 1.32 | 0.10 - 0.35 | Tendencia decreciente |
| intermittent | >= 1.32 | 0.05 - 0.35 | Esporádica, volumen similar |
| lumpy | >= 1.32 | 0.60 - 1.80 | Esporádica y volumen muy variable |
| new_product | variable | 0.30 - 0.80 | Rampa de lanzamiento |
| project_driven | variable | 0.50 - 1.20 | Demanda por proyecto |

### Perfiles disponibles

Se seleccionan cambiando `PROFILE` en `apps/simulator/config.py`:

**Industrial** (`"industrial"`) — Oleohidráulica:
- 800 productos, 6 ubicaciones (Santiago, Antofagasta, Copiapó, Concepción, Lima + CD Santiago)
- 12 categorías (bombas hidráulicas, motores, válvulas, filtros, instrumentación, etc.)
- Precios: 5,000 - 8,000,000 CLP
- Lead times por proveedor: 21 - 180 días (distribuidor local → importación Asia)

**Retail** (`"retail"`) — Supermercado:
- 1,200 productos, 10 tiendas
- 8 categorías (bebidas, lácteos, snacks, limpieza, etc.)
- Precios: 300 - 15,000 CLP
- Lead times: 3 - 21 días

### Cómo ejecutar el generador

```bash
# Instalar dependencias base
pip install -r requirements.txt

# Seleccionar perfil en apps/simulator/config.py
# PROFILE = "industrial"  o  PROFILE = "retail"

# Ejecutar
python3 -m apps.simulator.generate_canonical_dataset
```

Los archivos CSV se generan en el directorio `output/`.

### Capas livianas del monorepo

La arquitectura modular del repo está documentada en [docs/lightweight_monorepo_architecture.md](docs/lightweight_monorepo_architecture.md).

Los criterios de calidad y auditoría del dato están documentados en [docs/data_health_checks.md](docs/data_health_checks.md).

La decisión de modelado de moneda está documentada en [docs/currency_modeling.md](docs/currency_modeling.md).

Instalación sugerida por capa:

```bash
# Core + simulador
python3 -m pip install -e .

# Visualizadora
python3 -m pip install -e .[viz]

# API
python3 -m pip install -e .[api]

# Forecasting
python3 -m pip install -e .[forecast]

# Desarrollo / tests
python3 -m pip install -e .[dev]
```

Ejecución básica:

```bash
# Simulador
python3 -m apps.simulator.generate_canonical_dataset

# Visualizadora
python3 -m streamlit run apps/viz/app.py

# API
python3 -m uvicorn apps.api.main:app --reload

# Tests
python3 -m pytest tests/ -q
```

### Características del generador

- **Tendencia**: Crecimiento, declive y rampa de lanzamiento para productos nuevos
- **Estacionalidad**: Factores mensuales por categoría (mining_peak, summer_peak, etc.)
- **Día de semana**: Factor de día laboral configurable (5/7 para industrial, 7/7 para retail)
- **Ruido**: Lognormal para patrones erráticos/lumpy, normal para el resto
- **Intermitencia**: Máscara probabilística de demanda cero por período
- **Spikes**: Picos de demanda por promociones o proyectos
- **Stockouts**: Quiebres emergentes cuando la demanda supera el stock disponible
- **Compras**: Órdenes generadas por política de reposición con recepciones parciales
- **Transferencias**: Reposición desde nodo central hacia sucursales con lead times internos
- **Inventario**: Snapshot diario de stock disponible y stock en orden por SKU/ubicación

## Módulo de inventario

Implementado en `planning_core/inventory/` siguiendo el documento "Gestión de Inventario Orientada a Decisiones" (Marzo 2026).

### Safety Stock (3 métodos según clase ABC)

| Método | Clase | Fórmula |
|---|---|---|
| `extended` | A | `SS = z × √((LT+R)×σ_d² + d̄²×σ_LT²)` — incorpora variabilidad de lead time |
| `standard` | B | `SS = z × σ_d × √(LT+R)` — lead time fijo |
| `simple_pct_lt` | C | `SS = pct × d̄ × LT` — regla simple sin factor z |

### Diagnóstico de salud (bandas de ratio §2.3)

| Ratio (Cob. neta / Cob. objetivo) | Estado | Alerta |
|---|---|---|
| < 0.3 | quiebre_inminente | 🔴 rojo |
| 0.3 – 0.7 | substock | 🟠 naranja |
| 0.7 – 1.3 | equilibrio | ✅ none |
| 1.3 – 2.0 | sobrestock_leve | 🟡 amarillo |
| > 2.0 | sobrestock_crítico | ⚫ gris |

`P(quiebre)` calculada vía distribución Normal acumulada sobre la demanda durante el ciclo de reposición (LT+R).

### Health Status Report

`catalog_health_report()` recorre todo el catálogo activo y retorna un DataFrame con:
- Diagnóstico completo por SKU (ratio, estado, P(quiebre), SS, ROP)
- Métricas financieras: `excess_capital` (capital inmovilizado) y `stockout_capital` (capital en riesgo)
- Agrupación por proveedor, categoría, subcategoría y clase ABC

La **vista Health** en la UI muestra: KPI strip, scatter de posicionamiento, histograma de ratios, radar de salud multidimensional, análisis financiero por grupo y tabla detallada con textos explicativos automáticos (§11.5).

## Modelo de datos

El modelo relacional sigue la convención operativa:
- **Salidas** = `transactions.csv` (ventas/consumo realmente registrados)
- **Entradas** = `purchase_receipts.csv` (recepciones de compra)
- **Reabastecimiento interno** = `internal_transfers.csv`
- **Posición** = `inventory_snapshot.csv` (estado diario de inventario)

```
product_catalog.sku
    -> transactions.sku
    -> inventory_snapshot.sku
    -> internal_transfers.sku
    -> purchase_order_lines.sku
    -> purchase_receipts.sku

purchase_orders.po_id
    -> purchase_order_lines.po_id
    -> purchase_receipts.po_id

purchase_order_lines.po_line_id
    -> purchase_receipts.po_line_id
```

El modelo canónico operacional **no** incluye métricas, promedios, ADI, CV, ABC-XYZ, clasificaciones Syntetos-Boylan ni diagnósticos de inventario. Todo eso se considera data derivada para capas analíticas posteriores.

## Lógica del modelo

Reglas particulares de esta simulación:

- `transactions.csv` representa solo ventas o consumos realmente atendidos; no incluye demanda latente ni ventas perdidas.
- `inventory_snapshot.csv` se materializa para cada par `sku + location` activo durante todo el horizonte diario.
- Un SKU del catálogo puede no aparecer en tablas operativas si su demanda total simulada es cero en todo el período.
- En perfil `industrial`, los fines de semana no generan ventas operativas por defecto.
- Los quiebres de stock existen de forma implícita cuando `on_hand_qty` llega a cero; no se exportan como tabla separada.
- En perfil `industrial`, la compra se piensa como abastecimiento centralizado de importación, con lead times variables por proveedor (21–180 días).
- La compra llega a un nodo central de abastecimiento (`CD Santiago`) y luego se redistribuye a sucursales vía `internal_transfers`.
- El nodo central es híbrido: recibe compra, abastece sucursales y también puede vender directo, por lo que puede aparecer en `transactions.csv`.

Regla actual de clasificación:

- La clasificación oficial se calcula a nivel `SKU` agregado de red (suma de transactions de todas las locations).
- La clasificación por sucursal queda fuera de este repo.

Ver [docs/output_er_model.md](docs/output_er_model.md) para el diagrama E/R completo.

## Roadmap técnico

### Fase 0 — Generación de datos ✅ Completa
- [x] Generador de datos sintéticos multi-perfil
- [x] Modelo de datos relacional (7 tablas)
- [x] Documentación de esquema E/R

### Fase 1 — Clasificación y preprocesamiento ✅ Completa
- [x] Clasificador ADI-CV² (Syntetos-Boylan) desde `transactions`
- [x] Segmentación ABC-XYZ calculada
- [x] Test de estacionalidad por autocorrelación
- [x] Test de tendencia por Mann-Kendall
- [x] Detección y tratamiento de outliers (`IQR`, `Hampel`)
- [x] Quality score con penalización por censura
- [x] Detección de demanda censurada (stockout detection via inventory snapshot)

### Fase 2 — Forecasting base ✅ Completa
- [x] `AutoETS`, `AutoARIMA`, `MSTL`, `CrostonSBA`, `ADIDA`, `SeasonalNaive`, `LightGBM`
- [x] Métricas: `MASE` adaptativo (lag-1/lag-12/mean), `WMAPE`, `RMSSE`, `WAPE`, `Bias`, `MAE`, `RMSE`
- [x] Backtest expanding-window + selector horse-race
- [x] `PlanningService.sku_forecast()` + API + UI

### Fase 2.5 — Evaluación de catálogo ✅ Completa
- [x] `run_catalog_evaluation` en paralelo con checkpoints y resume
- [x] Persistencia de runs, aggregator, comparator
- [x] Barrido h×n_windows; config fijada: `h=3, n_windows=3`

### Fase 3 — Forecasting avanzado ✅ Parcialmente completa
- [x] AutoARIMA / SARIMA automático
- [x] MSTL — descomposición STL + AutoETS
- [x] LightGBM con features temporales vía MLForecast
- [ ] Prophet / NeuralProphet para estacionalidad compleja con calendarios

### Fase 4 — Inventario y gestión de stock ✅ Completa
- [x] Parámetros por SKU: lead time real por proveedor, período de revisión por ABC, σ_LT
- [x] Nivel de servicio CSL por segmento ABC + factor z
- [x] Safety Stock: método `extended` (A), `standard` (B), `simple_pct_lt` (C)
- [x] ROP = DDLT + SS
- [x] `diagnose_sku`: ratio de posicionamiento, bandas de salud, P(quiebre), textos explicativos
- [x] `catalog_health_report()`: diagnóstico masivo con métricas financieras
- [x] UI: subsección "Inventario" en detalle de SKU + vista "Health" con radar multidimensional

### Fase 5 — Motor de recomendación de compra
- [ ] Generación de órdenes de compra sugeridas (ROP/s-S/s-Q + MOQ)
- [ ] Input: diagnóstico de salud + forecast (`yhat_hi80`) + lead time + MOQ
- [ ] Output: tabla de órdenes recomendadas por SKU con fecha y cantidad

### Fase 6 — Pipeline y producción
- [ ] Orquestación end-to-end
- [ ] Monitoreo de concept drift
- [ ] Reconciliación jerárquica
- [ ] Dashboards de monitoreo

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.9+ |
| Modelos clásicos | StatsForecast (Nixtla) |
| Modelos ML | MLForecast + LightGBM |
| Modelos DL (futuro) | NeuralForecast (Nixtla) |
| Detección de outliers | pyod, STL manual |
| Datos | pandas, numpy |
| Visualización | Streamlit + plotly |
| API | FastAPI + uvicorn |
| Tests | pytest (170 tests) |

## Referencias

- Syntetos, A.A. y Boylan, J.E. (2005). *On the categorization of demand patterns*. Journal of the Operational Research Society.
- Hyndman, R.J. y Koehler, A.B. (2006). *Another look at measures of forecast accuracy*. International Journal of Forecasting.
- Hyndman, R.J. y Athanasopoulos, G. (2021). *Forecasting: Principles and Practice*, 3ra edición.
- Croston, J.D. (1972). *Forecasting and Stock Control for Intermittent Demands*.
- Silver, E.A., Pyke, D.F. y Thomas, D.J. (2017). *Inventory and Production Management in Supply Chains*, 4ta edición.
