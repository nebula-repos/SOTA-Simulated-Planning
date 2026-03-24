# Plan de Modelos de Forecasting — Fase 2 y 3

Plan de implementación de modelos de forecast, ordenados por prioridad y complejidad.
Cada modelo tiene su propio `.md` con contexto de aplicación, limitaciones y reglas de selección.

---

## Secuencia de implementación

### Fase 2 — Modelos clásicos base (implementar primero)

| Orden | Modelo | Tipo de demanda | Doc |
|---|---|---|---|
| 2.1 | ETS automático | Smooth (sin/con estacionalidad, con/sin tendencia) | [ets_auto.md](models/ets_auto.md) |
| 2.2 | SBA (Syntetos-Boylan Approximation) | Intermittent / Lumpy | [sba_croston.md](models/sba_croston.md) |
| 2.3 | Naive estacional (baseline) | Todos (referencia) | [naive_seasonal.md](models/naive_seasonal.md) |
| 2.4 | Framework de backtest | Todos | [backtest_framework.md](models/backtest_framework.md) |
| 2.5 | Métricas de evaluación | Todos | [evaluation_metrics.md](models/evaluation_metrics.md) |

### Fase 3 — Modelos avanzados (después de validar Fase 2)

| Orden | Modelo | Tipo de demanda | Doc |
|---|---|---|---|
| 3.1 | ARIMA / SARIMA automático | Smooth con estructura AR | [arima_auto.md](models/arima_auto.md) |
| 3.2 | Prophet | Smooth con estacionalidad compleja, múltiples calendarios | [prophet.md](models/prophet.md) |
| 3.3 | XGBoost / LightGBM tabular | Erratic con variables exógenas | [gradient_boosting.md](models/gradient_boosting.md) |

---

## Reglas de selección automática (mapeo clasificación → candidatos)

Basado en la Sección 8.1 de la Guía Técnica:

| Clasificación | Candidatos Fase 2 | Candidatos Fase 3 |
|---|---|---|
| Smooth sin estacionalidad, sin tendencia | SES (vía ETS), Naive | ARIMA(0,0,q) |
| Smooth sin estacionalidad, con tendencia | Holt (vía ETS) | ARIMA con d=1 |
| Smooth con estacionalidad | Holt-Winters (vía ETS) | SARIMA, Prophet |
| Erratic (CV2 alto) | ETS auto, Naive estacional | ARIMA, Prophet, XGBoost |
| Intermittent | SBA, Naive | TSB |
| Lumpy | SBA | Willemain bootstrap |
| New product (<6 meses) | Naive, media cluster | Prophet con prior |
| Inactive | No forecast — monitoreo de reactivación | — |

---

## Stack tecnológico elegido

| Librería | Modelos | Justificación |
|---|---|---|
| **StatsForecast** (Nixtla) | ETS, SBA, Croston, ARIMA, Theta, Naive | Implementaciones C/Numba, procesa miles de series en segundos. API única para todos los modelos clásicos. |
| **MLForecast** (Nixtla) | XGBoost, LightGBM | Integra con StatsForecast, crea features temporales automáticamente (lags, rolling stats, calendarios). |
| **pandas + numpy** | Métricas, backtest | Sin dependencias adicionales. |

---

## Arquitectura del módulo de forecast (Fase 2)

```
planning_core/
├── classification.py     ← Fase 1 (completa)
├── preprocessing.py      ← Fase 2a/2b (completa)
├── forecasting/          ← Fase 2 (por implementar)
│   ├── __init__.py
│   ├── models/
│   │   ├── ets.py        ← ETS automático vía StatsForecast
│   │   ├── sba.py        ← SBA / Croston / TSB
│   │   └── naive.py      ← Naive estacional (baseline)
│   ├── backtest.py       ← Framework expanding window
│   ├── metrics.py        ← MAE, MASE, WAPE, Bias, RMSE
│   └── selector.py       ← Reglas clasificación → candidatos + horse-race
```

---

## Principios de diseño

1. **Misma interfaz para todos los modelos**: `fit(train_df) → model`, `predict(model, h) → forecast_df`. Permite intercambiarlos en el horse-race sin cambiar el pipeline.
2. **Separación train/eval sobre `demand_clean`**: los modelos siempre reciben la serie limpia (outliers tratados, periodos censurados excluidos).
3. **Backtest expanding window**: se entrena sobre todo el histórico hasta el punto de corte, se predice h períodos, se avanza el corte. Mínimo 3 ventanas de evaluación.
4. **MASE como métrica primaria**: funciona para todos los patrones de demanda incluyendo intermitente. Se calcula respecto al naive estacional de la misma granularidad.
5. **Modelo ganador por SKU**: se almacena el nombre del modelo seleccionado junto al forecast, permitiendo auditoría y detección de concept drift.
