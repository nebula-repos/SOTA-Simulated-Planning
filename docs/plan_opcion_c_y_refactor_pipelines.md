# Plan: Opción C + Refactor pipelines — Estado y próximos pasos

Última actualización: `2026-04-07`

---

## Qué se implementó

### Sprint 1 — Correcciones quirúrgicas

| Item | Módulo | Estado |
|---|---|---|
| S1.1 — D38: `fill_rate_min` en backtest | `forecasting/backtest.py` | ✅ |
| S1.2 — Escritura atómica en `ForecastStore.save()` | `forecasting/evaluation/forecast_store.py` | ✅ |
| S1.3 — Señal forecast en SKU individual | `pipelines/purchase.py` → `run_sku_purchase_recommendation` | ✅ |

**S1.1**: `_aggregate_window_metrics()` ahora calcula `fill_rate_min` (mínimo entre ventanas de backtest) además del promedio. Expone el peor escenario observado — relevante para decisiones de inventario.

**S1.2**: `ForecastStore.save()` usa write-then-rename (`os.replace`) para garantizar que un proceso interrumpido no corrompe el artefacto previo. Sin cambio de interfaz pública.

**S1.3**: `run_sku_purchase_recommendation` ahora carga `ForecastStore` antes de calcular SS/ROP, igual que `run_catalog_health_report`. Elimina la asimetría entre la ruta de catálogo y la ruta de SKU individual.

---

### Sprint 2 — ClassificationStore

| Item | Módulo | Estado |
|---|---|---|
| S2.1 — `ClassificationStore` | `planning_core/classification/store.py` | ✅ |
| S2.2 — `pipelines/classification.py` | `run_catalog_classification`, `catalog_classification_status` | ✅ |
| S2.3 — `apps/batch_classification.py` | CLI `--granularity` / `--status` | ✅ |
| S2.4 — `services.py` | `classify_catalog(_skip_store)`, `run_catalog_classification`, `catalog_classification_status` | ✅ |
| S2.5 — Tests | `tests/test_classification_store.py` (14 tests) | ✅ |

`ClassificationStore` es el análogo de `ForecastStore` para clasificaciones:
- Persiste en `output/derived/classification_catalog_{granularity}.parquet` + `_meta.json`
- Escritura atómica, modelo de frescura idéntico (`DEFAULT_MAX_AGE_DAYS = {M:35, W:9, D:2}`)
- `classify_catalog()` intenta el store primero; solo recalcula si está ausente o stale
- Resuelve D14 (recálculo en cada request) para la API y la UI

---

### Reorganización de `planning_core/`

| Antes | Después |
|---|---|
| `planning_core/classification.py` | `planning_core/classification/core.py` |
| `planning_core/classification_store.py` | `planning_core/classification/store.py` |
| — | `planning_core/classification/__init__.py` (re-exporta todo) |

Los callers existentes (`from planning_core.classification import X`) **no requirieron ningún cambio** — el `__init__.py` reexporta la interfaz pública completa.

`preprocessing.py` y `validation.py` se mantienen planos: son módulos pequeños con un único importador (`services.py`). No justifican un package propio todavía.

---

### Sprint 3 — `demand_signal_source` en recomendaciones de compra

| Item | Módulo | Estado |
|---|---|---|
| `ss_method` en health report rows | `pipelines/inventory.py` | ✅ |
| `demand_signal_source` en `PurchaseRecommendation` | `purchase/recommendation.py` | ✅ |
| `_demand_signal_from_ss_method()` helper | `purchase/recommendation.py` | ✅ |
| Propagación en `generate_purchase_plan` | `purchase/recommendation.py` | ✅ |
| Propagación en `run_sku_purchase_recommendation` | `pipelines/purchase.py` | ✅ |

Cada `PurchaseRecommendation` ahora expone `demand_signal_source: str` (`"forecast"` | `"historical"`). Derivado desde el sufijo `_forecast` / `_historical` que `compute_sku_safety_stock` escribe en `ss_method`. Trazabilidad end-to-end sin romper ninguna interfaz existente.

---

### Sprint 4 — UI: badges de frescura + botones de materialización

| Item | Módulo | Estado |
|---|---|---|
| `_get_forecast_status` (TTL 60s) | `apps/viz/app.py` | ✅ |
| `_get_classification_status` (TTL 60s) | `apps/viz/app.py` | ✅ |
| Badge + botón "Ejecutar Forecast" en Compras | `render_compras_tab` | ✅ |
| Badge + botón "Materializar Clasificación" en Clasificación | `render_classification_tab` | ✅ |

Los badges leen solo el meta JSON (no abren el parquet). TTL=60s para reflejar cambios rápido sin saturar I/O. Los botones invalidan los caches relevantes y hacen `st.rerun()`.

---

## Estado actual de la arquitectura

```
planning_core/
├── classification/          ← NUEVO package
│   ├── __init__.py          ← re-exporta core + store (back-compat total)
│   ├── core.py              ← engine: ADI-CV², ABC-XYZ, lifecycle, quality
│   └── store.py             ← ClassificationStore (persiste en output/derived/)
├── forecasting/
│   └── evaluation/
│       ├── forecast_store.py    ← ForecastStore (persiste en output/derived/)
│       └── catalog_runner.py    ← captura yhat stats + save_to_derived
├── inventory/
│   └── safety_stock.py      ← acepta forecast_mean_daily / forecast_sigma_daily
├── pipelines/               ← orquestación extraída de services.py
│   ├── classification.py    ← run_catalog_classification, catalog_classification_status
│   ├── forecast.py          ← run_sku_forecast, run_catalog_forecast
│   ├── inventory.py         ← run_catalog_health_report (consume ForecastStore)
│   └── purchase.py          ← run_purchase_plan, run_sku_purchase_recommendation
├── purchase/
│   └── recommendation.py    ← PurchaseRecommendation con demand_signal_source
└── services.py              ← 1.354 líneas (pendiente refactor completo a thin facade)

apps/
├── batch_forecast.py        ← CLI: materializa ForecastStore
├── batch_classification.py  ← CLI: materializa ClassificationStore
├── api/main.py
└── viz/app.py               ← badges de frescura + botones en Compras y Clasificación
```

---

## Suite de tests actual

| Archivo | Tests | Cobertura |
|---|---|---|
| `test_api.py` | 54 | Todos los endpoints REST |
| `test_backtest.py` | — | Backtest expanding-window |
| `test_classification_store.py` | 14 | Round-trip, atomic write, staleness |
| `test_forecast_store.py` | 13 | Round-trip, atomic write, staleness, build_store_entries |
| `test_diagnostics.py` | — | InventoryDiagnosis, stockout_prob |
| `test_purchase.py` | 39 | Recommendation, aggregate_by_supplier, summary |
| Resto | — | inventory, services, system_log, etc. |
| **Total** | **350** | — |

---

## Qué queda — por prioridad

### P1 — Alta (completado ✅)

#### Refactor de `services.py` → thin facade — COMPLETADO

`services.py`: 1.354 → **1.103 líneas** (-250 líneas de lógica de negocio extraída).

Sprints ejecutados:
- **S5**: `sku_safety_stock()` → `pipelines/inventory.run_sku_safety_stock()` con señal ForecastStore
- **S6**: Censoring helpers (`_compute_censoring_info`, `_augment_profile_with_censoring`, etc.) + `classify_single_sku()` + recalc path de `classify_catalog()` → `pipelines/classification.py`
- **S7**: `classification_summary()` → `pipelines/classification.run_classification_summary()`

Los 4 helpers `_log_forecast_*` permanecen en services (son la interfaz de logging que el pipeline necesita vía `service._log_*`). Los métodos `sku_demand_series`, `sku_outlier_series`, `sku_acf`, `sku_clean_series` son wrappers legítimos de repository — no tienen lógica de negocio propia.

---

#### D08 — `validation.py` incompleto

El módulo actual solo chequea duplicados, negativos y transfers sin receipt_date. Faltan:
- FK checks (sku en transactions debe existir en product_catalog)
- Receipts before order (receipt_date < po.created_at)
- Over-receipt (recepción > PO)
- Reconciliación de inventario
- Validación de locations contra manifest
- Clasificación de severidad (crítico / medio / bajo)

---

### P2 — Media

#### D09 — Cobertura 0 en módulos críticos

| Módulo | Riesgo |
|---|---|
| `classification/core.py` | Alto — ADI-CV², ABC-XYZ, estacionalidad, lifecycle |
| `preprocessing.py` | Medio — outliers, censura |
| `validation.py` | Alto — alineado con D08 |

Acción concreta: `test_classification.py` con al menos `classify_sku`, `compute_abc_segmentation`, `select_granularity`, `detect_outliers`.

---

#### D33 — Dashboard agregado de calidad de forecast en UI

La UI muestra forecast por SKU pero no existe vista agregada de calidad del catálogo. Hoy no se puede ver desde la UI: % SKUs con MASE > 1, sesgo por segmento ABC/SB, distribución de fill_rate_min.

**Dependencia**: requiere que `ForecastStore` esté materializado. El badge en Compras ya empuja al usuario en esa dirección.

Acción: nueva sub-vista en el tab de Forecast con distribución de métricas por segmento, usando `ForecastStore` como fuente (no recalcular backtest).

---

#### Caché en la API para `/catalog/classification`

`classify_catalog()` en la API recalcula en cada request aunque el `ClassificationStore` esté fresco. La UI ya usa el store via `@st.cache_data`. La API debería hacer lo mismo: intentar el store antes de recalcular.

Acción: en `apps/api/main.py`, el endpoint `/catalog/classification` puede llamar directamente a `service.classify_catalog()` que ya tiene la lógica de store-first incorporada. Verificar que el endpoint no pase `_skip_store=True`.

---

### P3 — Baja

#### D21 — Notebook del param sweep

`docs/forecasting_param_sweep_results.md` documenta resultados pero no hay notebook reproducible. Útil para experimentación futura pero no bloquea nada.

#### `demand_signal_source` en la UI de Compras

El campo existe en `PurchaseRecommendation.to_dict()` pero no se muestra en la tabla de la UI. Añadir una columna o indicador visual (ícono forecast/histórico) en `render_compras_tab`.

---

## Diagrama de flujo actual (Opción C)

```
[batch_forecast.py]          [batch_classification.py]
        |                              |
  run_catalog_forecast         run_catalog_classification
        |                              |
  ForecastStore.save()         ClassificationStore.save()
        |                              |
  output/derived/                output/derived/
  forecast_catalog_M.parquet    classification_catalog_M.parquet
        |                              |
        └──────────────┬───────────────┘
                       ▼
             catalog_health_report()
             (consume ambos stores si están frescos)
                       |
                       ▼
             PurchaseRecommendation
             con demand_signal_source = "forecast" | "historical"
```
