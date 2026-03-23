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

El proyecto se encuentra en **Fase 0 (Data Generation)**: cuenta con un generador de datos sinteticos completo que produce series temporales con patrones de demanda diversos, junto con un modelo de datos relacional para transacciones y compras.

### Que existe hoy

| Componente | Descripcion | Estado |
|---|---|---|
| Generador de transacciones | Series diarias con 10 patrones de demanda | Funcional |
| Configuracion multi-perfil | Industrial (oleohidraulica) y Retail (supermercado) | Funcional |
| Modelo de datos (5 tablas) | Catalogo, transacciones, OC, lineas OC, recepciones | Funcional |
| Documentacion de esquema | E/R y especificacion de compras | Documentado |

### Que viene a continuacion

- Clasificacion de demanda desde datos crudos (ADI-CV2, estacionalidad, tendencia)
- Deteccion de outliers y quality gate
- Modelos de forecast (ETS, SBA, ARIMA, Prophet, XGBoost)
- Seleccion automatica de modelos via backtest
- Motor de recomendacion de compra

## Estructura del repositorio

```
SOTA-Simulated-Planning/
├── config.py                    # Configuracion global (perfiles, umbrales, parametros)
├── generate_transactions.py     # Generador de datos sinteticos
├── requirements.txt             # Dependencias Python
├── output/                      # Datos generados (no versionado)
└── docs/
    ├── output_er_model.md       # Modelo E/R de las tablas de salida
    └── purchase_data_schema.md  # Especificacion del esquema de compras
```

## Generador de datos sinteticos

### Que genera

El generador produce **3 anos de datos diarios** (2022-2024) para un catalogo configurable de productos, simulando condiciones realistas de demanda y abastecimiento.

**Tablas de salida** (en `output/`):

| Archivo | Descripcion | Clave |
|---|---|---|
| `product_catalog.csv` | Maestro de productos (SKU, categoria, proveedor, precio, MOQ) | `sku` |
| `transactions.csv` | Transacciones diarias de demanda por SKU y ubicacion | `date + sku + location` |
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

Se seleccionan cambiando `PROFILE` en `config.py`:

**Industrial** (`"industrial"`) - Oleohidraulica:
- 800 productos, 5 ubicaciones (Santiago, Antofagasta, Copiapo, Concepcion, Lima)
- 12 categorias (bombas, motores, valvulas, cilindros, filtros, etc.)
- Precios: 5,000 - 8,000,000 CLP
- Lead times: 7 - 120 dias

**Retail** (`"retail"`) - Supermercado:
- 1,200 productos, 10 tiendas
- 8 categorias (bebidas, lacteos, snacks, limpieza, etc.)
- Precios: 300 - 15,000 CLP
- Lead times: 3 - 21 dias

### Como ejecutar el generador

```bash
# Instalar dependencias
pip install -r requirements.txt

# Seleccionar perfil en config.py (linea 11)
# PROFILE = "industrial"  o  PROFILE = "retail"

# Ejecutar
python generate_transactions.py
```

Los archivos CSV se generan en el directorio `output/`.

### Caracteristicas del generador

- **Tendencia**: Crecimiento, declive y rampa de lanzamiento para productos nuevos
- **Estacionalidad**: Factores mensuales por categoria (mining_peak, summer_peak, etc.)
- **Dia de semana**: Factor de dia laboral configurable (5/7 para industrial, 7/7 para retail)
- **Ruido**: Lognormal para patrones erraticos/lumpy, normal para el resto
- **Intermitencia**: Mascara probabilistica de demanda cero por periodo
- **Spikes**: Picos de demanda por promociones o proyectos
- **Stockouts**: Periodos de quiebre de stock por clase ABC
- **Compras**: Ordenes generadas por politica de reposicion con recepciones parciales

## Modelo de datos

El modelo relacional sigue la convencion operativa:
- **Salidas** = `transactions.csv` (demanda/ventas)
- **Entradas** = `purchase_receipts.csv` (recepciones de compra)

```
product_catalog.sku
    -> transactions.sku
    -> purchase_order_lines.sku
    -> purchase_receipts.sku

purchase_orders.po_id
    -> purchase_order_lines.po_id
    -> purchase_receipts.po_id

purchase_order_lines.po_line_id
    -> purchase_receipts.po_line_id
```

Ver [docs/output_er_model.md](docs/output_er_model.md) para el diagrama E/R completo.

## Roadmap tecnico

### Fase 0 - Generacion de datos (completada)
- [x] Generador de datos sinteticos multi-perfil
- [x] Modelo de datos relacional (5 tablas)
- [x] Documentacion de esquema E/R

### Fase 1 - Clasificacion y preprocesamiento
- [ ] Clasificador ADI-CV2 (Syntetos-Boylan) desde datos crudos
- [ ] Segmentacion ABC-XYZ calculada
- [ ] Tests de estacionalidad (STL, autocorrelacion)
- [ ] Tests de tendencia (Mann-Kendall)
- [ ] Deteccion de outliers (IQR, STL + residuos, Hampel)
- [ ] Quality gate de datos

### Fase 2 - Forecasting base
- [ ] ETS automatico (StatsForecast) para demanda smooth/erratic
- [ ] SBA / Croston / TSB para demanda intermitente/lumpy
- [ ] Framework de backtest (expanding window)
- [ ] Metricas de evaluacion (MAE, RMSE, MASE, WAPE, Bias)

### Fase 3 - Forecasting avanzado
- [ ] ARIMA / SARIMA automatico
- [ ] Prophet para series con estacionalidad compleja
- [ ] XGBoost / LightGBM con features temporales
- [ ] Seleccion automatica de modelos (horse-race)
- [ ] Mapeo clasificacion -> modelos candidatos

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
| Lenguaje | Python 3.10+ |
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
