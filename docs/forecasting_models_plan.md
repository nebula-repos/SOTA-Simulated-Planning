# Plan de Modelos de Forecasting — Fase 2 y 3

Plan de implementación de modelos de forecast, ordenados por prioridad y complejidad.

## Estado actual del repo

### Implementado ✅

| Archivo | Contenido |
|---|---|
| `forecasting/utils.py` | `to_nixtla_df`, `_normalize_forecast`, `FREQ_MAP`, `SEASON_LENGTH` |
| `forecasting/metrics.py` | `compute_mase/wape/bias/mae/rmse`, `compute_all_metrics` |
| `forecasting/models/naive.py` | `fit_predict_naive` — SeasonalNaive + HistoricAverage fallback |
| `forecasting/models/ets.py` | `fit_predict_ets` — AutoETS con intervalos 80/95 |
| `forecasting/models/sba.py` | `fit_predict_sba` + `fit_predict_adida` — CrostonSBA + ADIDA |
| `forecasting/models/arima.py` | `fit_predict_arima`, `get_arima_model` — AutoARIMA con intervalos 80/95 |
| `forecasting/models/mstl.py` | `fit_predict_mstl`, `get_mstl_model` — MSTL + AutoETS tendencia |
| `forecasting/models/lgbm.py` | `fit_predict_lgbm`, `run_backtest_lgbm` — LightGBM con MLForecast |
| `forecasting/backtest.py` | `run_backtest` expanding-window, `backtest_summary` |
| `forecasting/selector.py` | `select_and_forecast`, `get_model_candidates` — horse-race completo |
| `planning_core/services.py` | `PlanningService.sku_forecast()` integrado |
| `apps/api/main.py` | `GET /sku/{sku}/forecast` endpoint |
| `apps/viz/app.py` | Sección "Forecast" en detalle de SKU |
| `tests/test_metrics.py` + `tests/test_models.py` | 35 tests, 100% pasando |

### Pendiente ❌

- Tests para `backtest.py`, `selector.py`, los nuevos wrappers Fase 3
- Fase 3.2: Prophet o equivalente complejo (NeuralProphet, AutoTheta)
- Fase 4: motor de recomendación de compra

---

## Secuencia de implementación

### Fase 2 — Modelos clásicos base ✅ Completa

| Orden | Modelo | Tipo de demanda | Estado |
|---|---|---|---|
| 2.1 | AutoETS | Smooth (sin/con estacionalidad, con/sin tendencia) | ✅ |
| 2.2 | CrostonSBA / ADIDA | Intermittent / Lumpy | ✅ |
| 2.3 | SeasonalNaive (baseline) | Todos | ✅ |
| 2.4 | Framework de backtest expanding-window | Todos | ✅ |
| 2.5 | Métricas MASE / WAPE / Bias / MAE / RMSE | Todos | ✅ |
| 2.6 | `select_and_forecast` — horse-race por MASE | Todos | ✅ |
| 2.7 | Integración en `PlanningService.sku_forecast()` | — | ✅ |
| 2.8 | API `GET /sku/{sku}/forecast` + UI sección Forecast | — | ✅ |

### Fase 3 — Modelos avanzados ✅ Parcialmente completa

| Orden | Modelo | Tipo de demanda | Estado |
|---|---|---|---|
| 3.1 | AutoARIMA (SARIMA automático) | Smooth/erratic con estructura AR/tendencia | ✅ |
| 3.2 | MSTL (descomposición STL + AutoETS) | Smooth con estacionalidad fuerte | ✅ |
| 3.3 | LightGBM tabular (MLForecast) | Smooth/erratic con suficientes datos | ✅ (camino separado) |
| 3.4 | Prophet / NeuralProphet | Estacionalidad compleja, calendarios | ❌ |

---

## Reglas de selección automática (horse-race por clasificación)

| Clasificación SB | `is_seasonal` | Candidatos |
|---|---|---|
| smooth | True | AutoETS, AutoARIMA, MSTL, SeasonalNaive, LightGBM* |
| smooth | False | AutoETS, AutoARIMA, SeasonalNaive, LightGBM* |
| erratic | — | AutoETS, AutoARIMA, SeasonalNaive, LightGBM* |
| intermittent | — | CrostonSBA, ADIDA |
| lumpy | — | CrostonSBA, ADIDA |
| inactive | — | Sin forecast |

\* LightGBM solo cuando `n_obs >= 3 * season_length` (camino separado via `run_backtest_lgbm`).

---

## Arquitectura del módulo (estado actual)

```
planning_core/
├── classification.py      ← Fase 1 (completa)
├── preprocessing.py       ← Fase 2a/2b (completa)
├── forecasting/
│   ├── __init__.py
│   ├── utils.py           ← FREQ_MAP, SEASON_LENGTH, to_nixtla_df, _normalize_forecast
│   ├── metrics.py         ← compute_mase/wape/bias/mae/rmse
│   ├── backtest.py        ← run_backtest (expanding window), backtest_summary
│   ├── selector.py        ← select_and_forecast, get_model_candidates
│   └── models/
│       ├── naive.py       ← SeasonalNaive + HistoricAverage (Fase 2)
│       ├── ets.py         ← AutoETS (Fase 2)
│       ├── sba.py         ← CrostonSBA + ADIDA (Fase 2)
│       ├── arima.py       ← AutoARIMA (Fase 3.1) ← nuevo
│       ├── mstl.py        ← MSTL + AutoETS tendencia (Fase 3.2) ← nuevo
│       └── lgbm.py        ← LightGBM via MLForecast (Fase 3.3) ← nuevo
└── services.py            ← sku_forecast() integrado
```

---

## Principios de diseño

1. **Misma interfaz para todos los modelos estadísticos**: `fit_predict_X(demand_df, granularity, h, unique_id, target_col, level) → dict`. Permite intercambiarlos en el horse-race.
2. **LightGBM usa camino separado**: `run_backtest_lgbm()` retorna el mismo formato que `run_backtest()` y se fusiona al dict antes de elegir el ganador.
3. **Backtest expanding window**: se entrena sobre todo el histórico hasta el punto de corte, se predice `h` períodos, se avanza el corte. Mínimo: `season_length + h * n_windows` observaciones.
4. **MASE como métrica primaria**: scale-free, funciona para todos los patrones. Se escala respecto al SeasonalNaive de la misma granularidad.
5. **Fallback chain**: si el modelo ganador falla al generar el forecast final, se degrada a `SeasonalNaive` o `HistoricAverage`. El status indica `"fallback"`.
6. **is_seasonal drive MSTL**: MSTL solo entra al horse-race cuando la clasificación detectó estacionalidad significativa. Evita sobreajuste en series planas.

---

## Stack tecnológico

| Librería | Modelos | Justificación |
|---|---|---|
| **StatsForecast** (Nixtla) | AutoETS, AutoARIMA, MSTL, CrostonSBA, ADIDA, SeasonalNaive | Implementaciones C/Numba. Una sola API y cross_validation para todos. |
| **MLForecast** (Nixtla) | LightGBM tabular | Crea features temporales automáticamente (lags, calendario). |
| **lightgbm** | — | Motor de gradient boosting con soporte de regresión cuantílica. |
| **pandas + numpy** | Métricas, backtest | Sin dependencias adicionales. |
