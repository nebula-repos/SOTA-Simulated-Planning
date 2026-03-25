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

El repo ya no esta solo en `Fase 0`.

Hoy combina:

- un generador de dataset canonico operacional
- una capa reusable `planning_core`
- clasificacion de demanda operativa
- preprocesamiento basico para forecasting
- wrappers iniciales de modelos de forecast
- una UI exploratoria y una API liviana

La parte mas madura sigue siendo la simulacion y el modelo canonico. La clasificacion, el preprocessing y el pipeline de forecasting (backtest + selector + integracion en `planning_core`) estan operativos. Lo que sigue es la exposicion del forecast en API/UI y el motor de recomendacion de compra.

### Que existe hoy

| Componente | Descripcion | Estado |
|---|---|---|
| Generador de transacciones | Series diarias con 10 patrones de demanda | Funcional |
| Configuracion multi-perfil | Industrial (oleohidraulica) y Retail (supermercado) | Funcional |
| Modelo de datos (7 tablas) | Catalogo, transacciones, snapshots inventario, transferencias internas, OC, lineas OC, recepciones | Funcional |
| `planning_core` | Repository, servicios, agregaciones y health checks basicos | Funcional |
| Clasificacion de demanda | Syntetos-Boylan, ABC-XYZ, estacionalidad, tendencia, outliers, quality score | Funcional |
| Preprocessing | Limpieza de outliers y deteccion de demanda censurada | Funcional |
| Forecasting base | Wrappers `SeasonalNaive`, `HistoricAverage`, `AutoETS`, `CrostonSBA`, `ADIDA`, metricas | Funcional |
| Backtest + selector | `run_backtest` expanding-window, horse-race por MASE, `select_and_forecast` | Funcional |
| Integracion forecast | `PlanningService.sku_forecast()` — seleccion automatica + pronostico por SKU | Funcional |
| UI + API | Exploracion operacional, clasificacion y forecast por SKU | Funcional |
| Documentacion de esquema | E/R y especificacion de compras | Documentado |

### Que viene a continuacion

- Motor de recomendacion de compra (Fase 4): politicas ROP/s-S/s-Q con forecast + lead time + MOQ
- Motor de recomendacion de compra (Fase 4): politicas ROP/s-S/s-Q con forecast + lead time + MOQ
- Prophet / NeuralProphet para estacionalidad compleja con calendarios (Fase 3.4)
- Fortalecimiento de validaciones (`validation.py`) y cobertura de tests por capa

## Estructura del repositorio

```
SOTA-Simulated-Planning/
├── apps/
│   ├── api/                     # API liviana para explorar el canonico
│   ├── simulator/               # Configuracion y generador del dataset canonico
│   └── viz/                     # Visualizadora basica con Streamlit
├── planning_core/               # Capa reusable de consultas y validaciones
├── pyproject.toml               # Dependencias por capa via extras
├── requirements.txt             # Dependencias base del simulador/core
├── output/                      # Datos generados (no versionado)
└── docs/
    ├── business_logic_simulation.md
    ├── currency_modeling.md
    ├── data_health_checks.md
    ├── lightweight_monorepo_architecture.md
    ├── output_er_model.md       # Modelo E/R de las tablas de salida
    └── purchase_data_schema.md  # Especificacion del esquema de compras
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

### Fase 1 - Clasificacion y preprocesamiento
- [x] Clasificador ADI-CV2 (Syntetos-Boylan) desde `transactions`
- [x] Segmentacion ABC-XYZ calculada
- [x] Test de estacionalidad por autocorrelacion
- [x] Test de tendencia por Mann-Kendall
- [x] Deteccion y tratamiento de outliers (`IQR`, `Hampel`)
- [x] Quality score basico por serie
- [x] Deteccion de demanda censurada
- [ ] Integracion de censura en la clasificacion principal
- [ ] Tests y validaciones mas profundas de la capa

### Fase 2 - Forecasting base
- [x] `AutoETS` wrapper (StatsForecast)
- [x] `SeasonalNaive` / `HistoricAverage` baseline
- [x] `CrostonSBA` / `ADIDA` wrappers
- [x] Metricas de evaluacion (`MAE`, `RMSE`, `MASE`, `WAPE`, `Bias`)
- [x] Framework de backtest expanding-window (`backtest.py`)
- [x] Seleccion automatica de modelos horse-race (`selector.py`)
- [x] Mapeo clasificacion → modelos candidatos
- [x] Integracion del forecast a `planning_core` (`PlanningService.sku_forecast()`)
- [x] Exposicion en API (`GET /sku/{sku}/forecast`) y UI (seccion Forecast en detalle de SKU)

### Fase 3 - Forecasting avanzado
- [x] AutoARIMA / SARIMA automatico (`models/arima.py`)
- [x] MSTL — descomposicion STL + AutoETS para series estacionales (`models/mstl.py`)
- [x] LightGBM con features temporales via MLForecast (`models/lgbm.py`, camino separado)
- [ ] Prophet / NeuralProphet para estacionalidad compleja con calendarios

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
