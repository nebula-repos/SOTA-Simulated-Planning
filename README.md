# SOTA - Simulated Planning

Sistema de forecasting de demanda, clasificacion de series temporales y recomendacion de compra para catalogos masivos de productos.

## Proposito

Este repositorio implementa un pipeline completo de **demand planning** que abarca:

1. **Generacion de datos sinteticos** realistas para desarrollo y testing
2. **Clasificacion automatica** de patrones de demanda (Syntetos-Boylan ADI-CV2)
3. **Segmentacion ABC-XYZ** por valor y predictibilidad
4. **Forecasting** con seleccion automatica de modelos por tipo de demanda
5. **Recomendacion de compra** basada en pronosticos y politicas de reposicion

El objetivo es construir un modulo que clasifique automaticamente las series de demanda, seleccione y aplique el modelo mas adecuado para cada producto, sea escalable a miles de SKUs, y se ejecute de forma recurrente con monitoreo continuo de calidad.

## Estado actual

Fases 0-3 del pipeline de forecasting completadas. El repo es un sistema funcional end-to-end de demand planning: desde la generacion de datos hasta el forecast por SKU con evaluacion batch de todo el catalogo.

### Que existe hoy

| Componente | Descripcion | Estado |
|---|---|---|
| Generador de datos | Series diarias con 10 patrones de demanda, multi-perfil | Funcional |
| Modelo de datos (7 tablas) | Catalogo, transacciones, snapshots inventario, OC, recepciones, transferencias | Funcional |
| `planning_core` | Repository, servicios, agregaciones y health checks basicos | Funcional |
| Clasificacion de demanda | Syntetos-Boylan, ABC-XYZ, estacionalidad, tendencia, outliers, quality score, censura | Funcional |
| Preprocessing | Limpieza de outliers (IQR/Hampel) y deteccion de demanda censurada por stockout | Funcional |
| Modelos de forecast | AutoETS, AutoARIMA, MSTL, CrostonSBA, ADIDA, SeasonalNaive, LightGBM | Funcional |
| Backtest + selector | `run_backtest` expanding-window, horse-race por MASE adaptativo, `select_and_forecast` | Funcional |
| MASE adaptativo | Benchmark correcto por tipo de producto: lag-1 (smooth), lag-12 (estacional), mean (intermittent) | Funcional |
| Evaluacion de catalogo | `run_catalog_evaluation` — horse-race masivo en paralelo, checkpoints, resume | Funcional |
| Comparacion de runs | `run_store`, `aggregator`, `comparator` — persistencia y analisis multi-run | Funcional |
| `PlanningService.sku_forecast()` | Seleccion automatica de modelo + forecast por SKU | Funcional |
| UI | Exploracion operacional, clasificacion, forecast con IC 80% y grafico de backtest horse-race | Funcional |
| API | Endpoints REST para catalogo, clasificacion, timeseries y forecast por SKU | Funcional |
| Tests | 82 tests unitarios e integracion, 100% passing | Funcional |
| Experimentacion | `exp_02_catalog_eval.py` — evaluacion batch; `exp_03_param_sweep.py` — barrido h x n_windows | Funcional |
| Notebooks | `02_catalog_evaluation.ipynb` — analisis post-evaluacion batch | Funcional |

### Resultado del barrido de parametrizacion (2026-03-25)

Se evaluo un grid de 6 configuraciones (h ∈ {3,6}, n_windows ∈ {3..6}) sobre el catalogo completo de 800 SKUs.
**Config de produccion decidida: `h=3, n_windows=3`** — mejor MASE global (0.7475), menor fallback (4.6%), maxima cobertura.
Ver `docs/forecasting_param_sweep_results.md` para el analisis completo.

### Que viene a continuacion

- Motor de recomendacion de compra (Fase 4): politicas ROP/s-S/s-Q con forecast + lead time + MOQ
- Prophet / NeuralProphet para estacionalidad compleja con calendarios (Fase 3.4)
- Metricas operacionales para intermittent/lumpy: Fill Rate, Cycle Service Level (D18)
- Fortalecimiento de `validation.py` y cobertura de tests por capa (D08, D09)

## Estructura del repositorio

```
SOTA-Simulated-Planning/
├── apps/
│   ├── api/                     # API REST (FastAPI) para catalogo, clasificacion y forecast
│   ├── simulator/               # Generador del dataset canonico
│   └── viz/                     # UI exploratoria (Streamlit) — operacional + clasificacion + forecast
├── planning_core/
│   ├── classification.py        # Clasificador Syntetos-Boylan + ABC-XYZ
│   ├── preprocessing.py         # Outliers + demanda censurada
│   ├── services.py              # PlanningService — punto de entrada principal
│   ├── repository.py            # Carga del dataset canonico
│   ├── validation.py            # Health checks basicos
│   └── forecasting/
│       ├── metrics.py           # MASE adaptativo, WAPE, Bias, MAE, RMSE
│       ├── backtest.py          # run_backtest expanding-window
│       ├── selector.py          # select_and_forecast — horse-race completo
│       ├── utils.py             # FREQ_MAP, to_nixtla_df
│       ├── models/              # naive, ets, arima, mstl, sba, lgbm
│       └── evaluation/          # catalog_runner, run_store, aggregator, comparator
├── experiments/
│   ├── exp_02_catalog_eval.py   # Evaluacion batch del catalogo completo
│   └── exp_03_param_sweep.py    # Barrido de parametrizacion h x n_windows
├── notebooks/
│   └── 02_catalog_evaluation.ipynb  # Analisis post-evaluacion batch
├── tests/                       # 82 tests unitarios e integracion
├── output/                      # Datos generados (no versionado)
├── pyproject.toml               # Dependencias por capa via extras
└── docs/
    ├── forecasting_models_plan.md          # Estado del modulo de forecasting
    ├── forecasting_param_sweep_results.md  # Resultados del barrido de parametrizacion
    ├── forecasting_param_sweep_plan.md     # Diseno del experimento de sweep
    ├── forecasting_benchmark_selection.md  # MASE adaptativo por tipo de producto
    ├── forecasting_parametrizacion.md      # Guia de parametros h, n_windows, granularity
    ├── technical_debt_register.md          # Backlog de deuda tecnica priorizada
    ├── plan_backtest_chart_ui.md           # Plan del grafico de horse-race en la UI
    ├── business_logic_simulation.md
    ├── data_health_checks.md
    ├── output_er_model.md
    └── purchase_data_schema.md
```

## Generador de datos sinteticos

### Que genera

El generador produce **3 anos de datos diarios** (2022-2024) para un catalogo configurable de productos, simulando condiciones realistas de demanda y abastecimiento.

**Tablas de salida** (en `output/`):

| Archivo | Descripcion | Clave |
|---|---|---|
| `product_catalog.csv` | Maestro de productos (SKU, categoria, proveedor, precio, costo, MOQ) | `sku` |
| `transactions.csv` | Transacciones reales de salida por SKU y ubicacion | `date + sku + location` |
| `inventory_snapshot.csv` | Posicion diaria de inventario por SKU y ubicacion | `snapshot_date + sku + location` |
| `internal_transfers.csv` | Traslados internos entre nodo central y sucursales | `transfer_id` |
| `purchase_orders.csv` | Cabeceras de ordenes de compra | `po_id` |
| `purchase_order_lines.csv` | Detalle de productos por orden de compra | `po_line_id` |
| `purchase_receipts.csv` | Recepciones efectivas de mercaderia | `receipt_id` |

### Patrones de demanda simulados

El generador cubre los cuatro cuadrantes de la clasificacion Syntetos-Boylan mas patrones adicionales:

| Patron | ADI | CV | Descripcion |
|---|---|---|---|
| constant | < 1.32 | 0.05 - 0.20 | Demanda estable y predecible |
| smooth | < 1.32 | 0.15 - 0.35 | Regular con variacion moderada |
| erratic | < 1.32 | 0.50 - 1.30 | Frecuente pero con volumen variable |
| seasonal | < 1.32 | 0.30 - 0.65 | Con estacionalidad marcada |
| trend_up | < 1.32 | 0.10 - 0.35 | Tendencia creciente |
| trend_down | < 1.32 | 0.10 - 0.35 | Tendencia decreciente |
| intermittent | >= 1.32 | 0.05 - 0.35 | Esporadica, volumen similar |
| lumpy | >= 1.32 | 0.60 - 1.80 | Esporadica y volumen muy variable |
| new_product | variable | 0.30 - 0.80 | Rampa de lanzamiento |
| project_driven | variable | 0.50 - 1.20 | Demanda por proyecto |

### Perfiles disponibles

Se seleccionan cambiando `PROFILE` en `apps/simulator/config.py`:

**Industrial** (`"industrial"`) - Oleohidraulica:
- 800 productos, 5 ubicaciones (Santiago, Antofagasta, Copiapo, Concepcion, Lima)
- 12 categorias (bombas, motores, valvulas, cilindros, filtros, etc.)
- Precios: 5,000 - 8,000,000 CLP
- Lead times de importacion: 145 - 200 dias

**Retail** (`"retail"`) - Supermercado:
- 1,200 productos, 10 tiendas
- 8 categorias (bebidas, lacteos, snacks, limpieza, etc.)
- Precios: 300 - 15,000 CLP
- Lead times: 3 - 21 dias

### Como ejecutar el generador

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

La arquitectura modular del repo esta documentada en [docs/lightweight_monorepo_architecture.md](docs/lightweight_monorepo_architecture.md).

Los criterios de calidad y auditoria del dato estan documentados en [docs/data_health_checks.md](docs/data_health_checks.md).

La decision de modelado de moneda esta documentada en [docs/currency_modeling.md](docs/currency_modeling.md).

Instalacion sugerida por capa:

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

Ejecucion basica:

```bash
# Wrapper del simulador
python3 -m apps.simulator.generate_canonical_dataset

# Wrapper alternativo
python3 -m apps.simulator

# Wrapper alternativo
python3 -m apps.simulator.run

# Visualizadora
python3 -m streamlit run apps/viz/app.py

# API
python3 -m uvicorn apps.api.main:app --reload
```

### Caracteristicas del generador

- **Tendencia**: Crecimiento, declive y rampa de lanzamiento para productos nuevos
- **Estacionalidad**: Factores mensuales por categoria (mining_peak, summer_peak, etc.)
- **Dia de semana**: Factor de dia laboral configurable (5/7 para industrial, 7/7 para retail)
- **Ruido**: Lognormal para patrones erraticos/lumpy, normal para el resto
- **Intermitencia**: Mascara probabilistica de demanda cero por periodo
- **Spikes**: Picos de demanda por promociones o proyectos
- **Stockouts**: Quiebres emergentes cuando la demanda supera el stock disponible
- **Compras**: Ordenes generadas por politica de reposicion con recepciones parciales
- **Transferencias**: Reposicion desde nodo central hacia sucursales con lead times internos
- **Inventario**: Snapshot diario de stock disponible y stock en orden por SKU/ubicacion

## Modelo de datos

El modelo relacional sigue la convencion operativa:
- **Salidas** = `transactions.csv` (ventas/consumo realmente registrados)
- **Entradas** = `purchase_receipts.csv` (recepciones de compra)
- **Reabastecimiento interno** = `internal_transfers.csv`
- **Posicion** = `inventory_snapshot.csv` (estado diario de inventario)

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

El modelo canónico operacional **no** incluye metricas, promedios, ADI, CV, ABC-XYZ ni clasificaciones Syntetos-Boylan. Todo eso se considera data derivada para capas analiticas posteriores.

## Logica Del Modelo

Reglas particulares de esta simulacion:

- `transactions.csv` representa solo ventas o consumos realmente atendidos; no incluye demanda latente ni ventas perdidas.
- `inventory_snapshot.csv` se materializa para cada par `sku + location` activo durante todo el horizonte diario.
- un SKU del catalogo puede no aparecer en tablas operativas si su demanda total simulada es cero en todo el periodo.
- en perfil `industrial`, los fines de semana no generan ventas operativas por defecto.
- los quiebres de stock existen de forma implicita cuando `on_hand_qty` llega a cero; no se exportan como tabla separada.
- en perfil `industrial`, la compra se piensa como abastecimiento centralizado de importacion, con lead times promedio altos.
- la compra llega a un nodo central de abastecimiento y luego se redistribuye a sucursales via `internal_transfers`.
- en el perfil `industrial`, el nodo central actual (`CD Santiago`) es hibrido: recibe compra, abastece sucursales y tambien puede vender directo, por lo que puede aparecer en `transactions.csv`.

Regla actual de clasificacion:

- la clasificacion oficial de este repo, por ahora, se calcula a nivel `SKU` agregado de red
- eso significa que se suman las `transactions` de todas las locations activas del SKU
- si el nodo central tiene venta directa, esa demanda tambien entra en la serie agregada
- la clasificacion por sucursal queda fuera de este repo y se abordara en otro piloto

Implicancia:

- la agregacion de demanda para compra central y el abastecimiento por sucursal quedan ambos soportados por movimientos operacionales consistentes.


Ver [docs/output_er_model.md](docs/output_er_model.md) para el diagrama E/R completo.

## Roadmap tecnico

### Fase 0 - Generacion de datos (completada)
- [x] Generador de datos sinteticos multi-perfil
- [x] Modelo de datos relacional (7 tablas)
- [x] Documentacion de esquema E/R

### Fase 1 - Clasificacion y preprocesamiento ✅ Completa
- [x] Clasificador ADI-CV2 (Syntetos-Boylan) desde `transactions`
- [x] Segmentacion ABC-XYZ calculada
- [x] Test de estacionalidad por autocorrelacion
- [x] Test de tendencia por Mann-Kendall
- [x] Deteccion y tratamiento de outliers (`IQR`, `Hampel`)
- [x] Quality score con penalizacion por censura
- [x] Deteccion de demanda censurada (stockout detection via inventory snapshot)
- [x] Integracion de censura como flags en la clasificacion (`has_censored_demand`, `quality_score`)

### Fase 2 - Forecasting base ✅ Completa
- [x] `AutoETS` wrapper (StatsForecast)
- [x] `SeasonalNaive` / `HistoricAverage` baseline
- [x] `CrostonSBA` / `ADIDA` wrappers (demanda intermittent/lumpy)
- [x] Metricas de evaluacion: `MASE` adaptativo (lag-1/lag-12/mean segun tipo), `WAPE`, `Bias`, `MAE`, `RMSE`
- [x] Framework de backtest expanding-window (`backtest.py`) con `return_cv` para grafico UI
- [x] Seleccion automatica de modelos horse-race (`selector.py`)
- [x] Mapeo clasificacion SB → modelos candidatos + benchmark correcto por tipo
- [x] Integracion en `PlanningService.sku_forecast()`
- [x] Exposicion en API (`GET /sku/{sku}/forecast`) y UI (Forecast + Backtest horse-race tabs)

### Fase 2.5 - Evaluacion de catalogo ✅ Completa
- [x] `run_catalog_evaluation` — horse-race masivo en paralelo (`ProcessPoolExecutor`, fork context)
- [x] Checkpoints y resume para corridas largas
- [x] `run_store` — persistencia de runs en `output/eval_runs/` (parquet + metadata JSON)
- [x] `aggregator` — metricas globales y por segmento (sb_class, abc_class, xyz, is_seasonal)
- [x] `comparator` — comparacion multi-run wide, `find_winner_changes`
- [x] `exp_02_catalog_eval.py` — evaluacion batch del catalogo completo
- [x] `exp_03_param_sweep.py` — barrido de 6 configuraciones h × n_windows
- [x] Resultado del sweep documentado; config de produccion fijada: `h=3, n_windows=3`

### Fase 3 - Forecasting avanzado ✅ Parcialmente completa
- [x] AutoARIMA / SARIMA automatico (`models/arima.py`)
- [x] MSTL — descomposicion STL + AutoETS para series estacionales (`models/mstl.py`)
- [x] LightGBM con features temporales via MLForecast (`models/lgbm.py`, camino separado)
- [ ] Prophet / NeuralProphet para estacionalidad compleja con calendarios (Fase 3.4)

### Fase 4 - Recomendacion de compra
- [ ] Politicas de reposicion (ROP, s-S, s-Q)
- [ ] Motor de recomendacion con forecast + lead time + MOQ
- [ ] Integracion con datos de compras/recepciones

### Fase 5 - Pipeline y produccion
- [ ] Orquestacion end-to-end
- [ ] Monitoreo de concept drift
- [ ] Reconciliacion jerarquica
- [ ] Dashboards de monitoreo

## Stack tecnologico

| Componente | Tecnologia |
|---|---|
| Lenguaje | Python 3.9+ |
| Modelos clasicos | StatsForecast (Nixtla) |
| Modelos ML | MLForecast + LightGBM |
| Modelos DL (futuro) | NeuralForecast (Nixtla) |
| Deteccion de outliers | pyod, STL manual |
| Datos | pandas, numpy |
| Visualizacion | plotly, matplotlib |

## Referencias

- Syntetos, A.A. y Boylan, J.E. (2005). *On the categorization of demand patterns*. Journal of the Operational Research Society.
- Hyndman, R.J. y Koehler, A.B. (2006). *Another look at measures of forecast accuracy*. International Journal of Forecasting.
- Hyndman, R.J. y Athanasopoulos, G. (2021). *Forecasting: Principles and Practice*, 3ra edicion.
- Croston, J.D. (1972). *Forecasting and Stock Control for Intermittent Demands*.
