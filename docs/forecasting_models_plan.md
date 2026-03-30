# Módulo de Forecasting — Estado actual y roadmap

**Última actualización:** 2026-03-27

---

## Estado implementado

### Capa de modelos (`planning_core/forecasting/models/`)

| Archivo | Modelo(s) | Tipo de demanda | Estado |
|---|---|---|---|
| `naive.py` | SeasonalNaive + HistoricAverage fallback | Todos (baseline) | ✅ |
| `ets.py` | AutoETS con intervalos 80% | Smooth / erratic | ✅ |
| `sba.py` | CrostonSBA + ADIDA | Intermittent / lumpy | ✅ |
| `arima.py` | AutoARIMA con intervalos 80% | Smooth / erratic con estructura AR | ✅ |
| `mstl.py` | MSTL + AutoETS tendencia | Smooth estacional fuerte | ✅ |
| `lgbm.py` | LightGBM via MLForecast (lags, calendario) | Smooth / erratic con ≥36 obs | ✅ |

### Infraestructura de evaluacion

| Archivo | Contenido | Estado |
|---|---|---|
| `metrics.py` | MASE adaptativo (lag-1/lag-12/mean), WMAPE, RMSSE, WAPE, Bias, MAE, RMSE | ✅ |
| `backtest.py` | `run_backtest` expanding-window, `backtest_summary`, `return_cv` | ✅ |
| `selector.py` | `select_and_forecast`, `get_model_candidates`, `_get_naive_type` | ✅ |
| `utils.py` | `FREQ_MAP`, `SEASON_LENGTH`, `to_nixtla_df`, `_normalize_forecast` | ✅ |
| `evaluation/catalog_runner.py` | `run_catalog_evaluation` — paralelo + checkpoint + resume | ✅ |
| `evaluation/run_store.py` | Persistencia de runs (parquet + metadata JSON) | ✅ |
| `evaluation/aggregator.py` | Metricas globales y por segmento, distribucion percentil | ✅ |
| `evaluation/comparator.py` | Comparacion multi-run, pivot por segmento, `find_winner_changes` | ✅ |
| `evaluation/_types.py` | `EvalConfig`, `CatalogEvalResult` | ✅ |

### Integracion en el stack

| Capa | Metodo / endpoint | Estado |
|---|---|---|
| `planning_core/services.py` | `PlanningService.sku_forecast(return_cv=False)` | ✅ |
| `apps/api/main.py` | `GET /sku/{sku}/forecast` | ✅ |
| `apps/viz/app.py` | Tab "Forecast" + Tab "Backtest horse-race" | ✅ |

### Tests

El repo tiene **200+ tests** unitarios e integración. La cobertura del módulo es amplia, pero al `2026-03-27` la suite no está completamente verde: existe al menos una falla conocida en `test_backtest_selector.py` asociada al comportamiento actual del selector con `Ensemble`.

| Suite | Cobertura |
|---|---|
| `test_metrics.py` | MASE (seasonal/lag1/mean/edge cases), WMAPE, RMSSE, WAPE, Bias, MAE, RMSE |
| `test_models.py` | naive, ets, sba, to_nixtla_df |
| `test_backtest_selector.py` | run_backtest, select_and_forecast (smoke tests por sb_class; requiere alineación con `Ensemble`) |
| `test_services.py` | sku_forecast, clasificacion, censura, safety_stock |
| `test_evaluation.py` | EvalConfig, CatalogEvalResult, aggregator, run_store, comparator |
| `test_inventory.py` | params, service_level, safety_stock (compute_demand_stats, compute_safety_stock, compute_rop, compute_sku_safety_stock, PlanningService.sku_safety_stock) |
| `test_diagnostics.py` | `diagnose_sku`, bandas de salud, sentinels y `catalog_health_report` |

---

## Reglas de candidatura del horse-race

| Clasificacion SB | `is_seasonal` | Candidatos | Benchmark MASE |
|---|---|---|---|
| smooth | True | AutoETS, AutoARIMA, MSTL, SeasonalNaive, LightGBM* | lag-12 (seasonal) |
| smooth | False | AutoETS, AutoARIMA, SeasonalNaive, LightGBM* | lag-1 (random walk) |
| erratic | — | AutoETS, AutoARIMA, SeasonalNaive, LightGBM* | lag-1 |
| intermittent | — | CrostonSBA, ADIDA | mean historico |
| lumpy | — | CrostonSBA, ADIDA | mean historico |
| inactive | — | Sin forecast | — |

\* LightGBM solo cuando `n_obs >= 3 × season_length` (36 obs para mensual).

Nota:

- Para `intermittent` y `lumpy`, los candidatos base siguen siendo `CrostonSBA` y `ADIDA`.
- El resultado final puede etiquetarse como `Ensemble` si ambos quedan suficientemente cerca según el criterio del selector.

---

## MASE adaptativo por tipo de producto

El denominador del MASE varia segun el perfil del SKU para evitar benchmarks artificialmente faciles o inestables. Implementado en `metrics.py` → `backtest.py` → `selector.py`.

| Tipo | Benchmark | Razon |
|---|---|---|
| Smooth / erratic estacional | Seasonal Naive lag-12 | La estacionalidad es el patron dominante |
| Smooth / erratic no estacional | Naive lag-1 (random walk) | Benchmark mas exigente para series planas |
| Intermittent / lumpy | Media historica | lag-1 y lag-12 inestables con muchos ceros |

Ver `docs/forecasting_benchmark_selection.md` para el analisis completo.

---

## Parametros de produccion (fijados por experimento)

**Config decidida para evaluación batch:** `h=3, n_windows=3`, granularidad mensual (`M`).

Resultado del barrido `exp_03_param_sweep.py` sobre 6 configuraciones × 800 SKUs:
- Mejor MASE mediana global (0.7475)
- Menor fallback rate (4.6%)
- Optimo para todos los segmentos excepto ABC-A, donde h3_w6 gana marginalmente (diferencia: 0.014)

Ver `docs/forecasting_param_sweep_results.md` para tablas completas y justificacion.

Nota operativa:

- En evaluación masiva se usa `h=3` como configuración experimental fija.
- En serving, `PlanningService.sku_forecast()` ya deriva `h` por SKU desde `lead_time_days` cuando no se entrega explícitamente.

---

## Arquitectura del modulo

```
planning_core/
├── classification.py          ← Fase 1 (completa)
├── preprocessing.py           ← Fase 1 (completa)
├── services.py                ← sku_forecast() + sku_safety_stock() + catalog_health_report()
├── inventory/                 ← Fase 4 (completa)
│   ├── params.py              ← InventoryParams, lead times reales por proveedor, σ_LT
│   ├── service_level.py       ← CSL por ABC, factor z, ServiceLevelConfig
│   ├── safety_stock.py        ← SS (extended/standard/simple_pct_lt), ROP, SafetyStockResult
│   └── diagnostics.py         ← diagnose_sku, InventoryDiagnosis, bandas de salud, P(quiebre)
└── forecasting/
    ├── __init__.py
    ├── utils.py               ← FREQ_MAP, SEASON_LENGTH, to_nixtla_df
    ├── metrics.py             ← MASE adaptativo (naive_type), WAPE, Bias, MAE, RMSE
    ├── backtest.py            ← run_backtest (expanding window, return_cv), backtest_summary
    ├── selector.py            ← select_and_forecast, get_model_candidates, _get_naive_type
    ├── models/
    │   ├── naive.py           ← SeasonalNaive + HistoricAverage
    │   ├── ets.py             ← AutoETS
    │   ├── sba.py             ← CrostonSBA + ADIDA
    │   ├── arima.py           ← AutoARIMA
    │   ├── mstl.py            ← MSTL + AutoETS tendencia
    │   └── lgbm.py            ← LightGBM via MLForecast (camino separado)
    └── evaluation/
        ├── __init__.py
        ├── _types.py          ← EvalConfig, CatalogEvalResult
        ├── catalog_runner.py  ← run_catalog_evaluation (paralelo, checkpoint, resume)
        ├── run_store.py       ← save_run, load_run, list_runs, delete_run
        ├── aggregator.py      ← metricas globales, por segmento, distribucion
        └── comparator.py      ← compare_runs, compare_runs_by_segment, find_winner_changes
```

---

## Pendiente (roadmap)

### Fase 3.4 — Prophet / NeuralProphet

Para series con estacionalidad compleja, calendarios (feriados, cierres), o patrones
múltiples que MSTL no captura bien. Baja prioridad mientras LightGBM cubra esos casos.

### Fase 5 — Motor de recomendación de compra

La Fase 4 (inventario, SS, diagnóstico de salud) está completa. La Fase 5 se enfoca en generar órdenes de compra accionables:

- Políticas de reposición: ROP, (s, S), (s, Q)
- Input: `InventoryDiagnosis.suggested_order_qty` + forecast (`yhat_hi80`) + MOQ + condiciones de pago
- Output: tabla de órdenes recomendadas por SKU con fecha sugerida, cantidad y proveedor

### Deuda técnica del módulo (ver `technical_debt_register.md`)

| ID | Resumen | Prioridad |
|----|---------|-----------|
| D18 | Métricas operacionales para intermittent/lumpy (Fill Rate, CSL alcanzado) | Media |
| D21 | Notebook de visualización del sweep (`03_param_sweep_analysis.ipynb`) | Baja |
| D22 | `services.py` importa `forecasting.selector` a nivel módulo — dependencia runtime de statsforecast | Media |
| D33 | Falta dashboard agregado de calidad de forecast en UI | Media |
| D34 | Código, tests y documentación del selector no están completamente alineados | Alta |

> Nota: los antiguos ítems sobre desempate por RMSSE, sesgo y horizonte `h` fijo quedaron resueltos en código. El trabajo pendiente ahora es estabilizar tests, documentación y observabilidad sobre ese comportamiento actualizado.

---

## Principios de diseno

1. **Misma interfaz para todos los wrappers estadisticos**: `fit_predict_X(demand_df, granularity, h, unique_id, target_col) → dict`. Intercambiables en el horse-race.
2. **LightGBM usa camino separado**: `run_backtest_lgbm()` retorna el mismo formato que `run_backtest()` y se fusiona antes de elegir el ganador.
3. **Backtest expanding-window**: minimo `season_length + h × n_windows` observaciones. Series mas cortas devuelven `status="series_too_short"` y el sistema degrada a fallback.
4. **MASE adaptativo como metrica primaria**: denominador correcto segun el tipo de demanda (lag-1, lag-12 o media historica). El selector agrega filtros y desempates por RMSSE y Bias cuando corresponde.
5. **Fallback chain**: ganador falla → SeasonalNaive → HistoricAverage. Siempre se genera un forecast; `status` indica la degradacion.
6. **SKUs inactivos (sin transacciones)**: `select_and_forecast` devuelve `status="no_forecast"`. `catalog_runner` los registra como `no_forecast`, no como `error`.
7. **`return_cv=True`**: expone el DataFrame completo de cross-validation para el grafico de horse-race en la UI, sin costo adicional de computo.
8. **Ensemble y bias correction**: cuando hay varios candidatos cercanos, el selector puede devolver `Ensemble` y luego aplicar corrección de sesgo al forecast final.

---

## Stack tecnologico

| Libreria | Modelos | Justificacion |
|---|---|---|
| **StatsForecast** (Nixtla) | AutoETS, AutoARIMA, MSTL, CrostonSBA, ADIDA, SeasonalNaive | Implementaciones Numba/C. Una sola API y `cross_validation` para todos. |
| **MLForecast** (Nixtla) | LightGBM tabular | Genera features de lags y calendario automaticamente. |
| **lightgbm** | — | Gradient boosting con soporte de regresion cuantilica (IC 80%). |
| **pandas + numpy** | Metricas, backtest | Sin dependencias adicionales. |
