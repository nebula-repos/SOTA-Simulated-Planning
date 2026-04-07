# Registro de Deuda Técnica

## Objetivo

Backlog de deuda técnica, bugs no declarados y oportunidades de mejora detectadas en inspecciones del repo.
Solo contiene ítems **vigentes**. Lo resuelto debe eliminarse del registro.

Última actualización: `2026-04-06` (Sprint 1-4: ClassificationStore, demand_signal_source, UI badges, reorganización planning_core)

---

## Resumen ejecutivo

| Frente | Items vigentes | Prioridad más alta |
|---|---|---|
| Arquitectura / refactor | services.py thin facade pendiente | Alta |
| Validación de datos | D08 | Alta |
| Testing / cobertura | D09 (parcial — API cubierta) | Media |
| Arquitectura / performance | D14 (API), D15 (resuelto) | Media |
| Forecasting / observabilidad | D21, D33 | Media / Baja |

Resueltos en esta sesión: **D38** (fill_rate_min), **D15** (ClassificationStore completa la capa de artefactos), escritura atómica en ForecastStore, `demand_signal_source` en PurchaseRecommendation.

---

## Inventario vigente

| ID | Prio | Tipo | Resumen |
|---|---|---|---|
| — | Alta | Arquitectura | `services.py` aún tiene 1.354 líneas — refactor a thin facade pendiente |
| D08 | Alta | Validación | `validation.py` sigue muy por debajo del framework documentado |
| D09 | Media | Testing | Cobertura 0 en `classification/core.py`, `preprocessing.py`, `validation.py` |
| D14 | Media | Performance | `classify_catalog()` en la API no verifica el store primero |
| D21 | Baja | Analítica | Falta notebook reproducible del sweep de parametrización |
| D33 | Media | Forecasting | Falta dashboard agregado de calidad de forecast en UI |

---

## Detalle por ítem

### D08. `validation.py` sigue muy por debajo del framework documentado

**Tipo**: validación
**Prioridad**: alta

El módulo actual sólo chequea duplicados, negativos y transfers abiertos sin `receipt_date`.

Evidencia:

- `planning_core/validation.py` cubre sólo checks básicos
- `docs/data_health_checks.md` describe un framework mucho más amplio

Faltan todavía:

- FK checks (product_catalog → transactions, inventory_snapshot, purchase_order_lines, etc.)
- receipts-before-order (receipt_date < po.created_at)
- over-receipt (recepción mayor al PO)
- reconciliación de inventario (on_hand = prev + receipts − ventas − transfers)
- validación de `location` (solo locations conocidas del manifest)
- clasificación de severidad (crítico / alto / medio / bajo)

**Acción**: implementar una v1 de integridad relacional y temporal antes de seguir ampliando serving o analítica.

---

### D09. Cobertura 0 en módulos críticos

**Tipo**: testing
**Prioridad**: alta

Suite verde con 222 tests. Pero la cobertura es muy desigual por capa:

| Módulo | Tests directos | Riesgo |
|---|---|---|
| `planning_core/classification.py` | ❌ ninguno | Alto — lógica ADI-CV², ABC-XYZ, estacionalidad |
| `planning_core/preprocessing.py` | ❌ ninguno | Medio — outliers y censura |
| `planning_core/validation.py` | ❌ ninguno | Alto — alineado con D08 |
| `apps/api/main.py` | ✅ 54 tests en `test_api.py` | Cubierto |
| `planning_core/forecasting/models/mstl.py` | ❌ ninguno | Medio |
| `planning_core/forecasting/models/lgbm.py` | ❌ ninguno | Medio |
| `test_services.py` | Solo 4 tests | Bajo (pero insuficiente para PlanningService) |

**Acción**:

1. `test_classification.py` — cubrir `classify_sku`, segmentación ABC-XYZ, `select_granularity`, `detect_outliers`
2. `test_api.py` — cubrir todos los endpoints con `TestClient` de FastAPI; verificar códigos de respuesta y esquema JSON
3. `test_validation.py` — cubrir las reglas que sí existen en `basic_health_report`

---

### D14. `classify_catalog()` en la API no verifica el store primero

**Tipo**: performance
**Prioridad**: media

`classify_catalog()` en `services.py` ya tiene la lógica de store-first incorporada (devuelve el `ClassificationStore` si está fresco). Pero el endpoint `/catalog/classification` en `apps/api/main.py` debe verificar que no llame con `_skip_store=True`.

La UI mitiga esto con `@st.cache_data(ttl=600)`. Para la API, la solución es verificar que el endpoint use el método correcto.

**Acción**: confirmar que el endpoint de clasificación en la API llama a `service.classify_catalog()` sin `_skip_store=True`.

---

### D15. Sin capa formal para artefactos derivados persistidos — RESUELTO ✅

**Resuelto** (2026-04-06): tanto `ForecastStore` como `ClassificationStore` implementan el contrato completo de persistencia:
- Parquet + meta JSON por granularidad en `output/derived/`
- Escritura atómica (write-then-rename)
- Modelo de frescura con `DEFAULT_MAX_AGE_DAYS`
- CLIs `apps/batch_forecast.py` y `apps/batch_classification.py`
- Badges de estado en la UI con TTL=60s
- `catalog_health_report` consume `ForecastStore` automáticamente
- `classify_catalog()` consume `ClassificationStore` automáticamente

---

### D21. Falta notebook reproducible del sweep

**Tipo**: analítica
**Prioridad**: baja

`docs/forecasting_param_sweep_results.md` documenta resultados, pero no existe `notebooks/03_param_sweep_analysis.ipynb`.

**Acción**: crear el notebook con tablas y visualizaciones reproducibles.

---

### D33. Falta dashboard agregado de calidad de forecast en UI

**Tipo**: forecasting / UX
**Prioridad**: media

La UI muestra KPIs por SKU y el horse-race individual, pero no existe una vista agregada de calidad de forecast del catálogo.

Hoy no se puede responder fácilmente desde la UI:

- qué porcentaje de SKUs tiene MASE > 1
- dónde aparece sesgo sistemático
- cómo se distribuye el error por segmento ABC o SB

**Acción**: agregar una vista agregada de calidad con distribución de métricas, KPIs por segmento y sesgo.

---

### D38. `fill_rate_min` en backtest — RESUELTO ✅

**Resuelto** (2026-04-06): `_aggregate_window_metrics` ahora calcula `fill_rate_min` (mínimo entre ventanas) además del promedio. Expone el peor escenario observado para decisiones de inventario.

---

## Preguntas de diseño abiertas

### Q1. Artefactos derivados (D15)

¿Los derivados deben ser:

- vistas on-demand recalculadas
- archivos en `output/derived/`
- un store liviano centralizado

### Q2. Observabilidad de forecasting (D33)

Fill Rate ya está implementado en `metrics.py` y propagado al backtest. ¿La calidad agregada debe mostrarse principalmente:

- en notebooks y experimentos offline
- en una vista agregada de Streamlit
- en ambos

### Q3. Nivel de detalle de errores en la API

D35 resuelto: todos los endpoints tienen `try/except` con 404/422/503 diferenciados y `_sanitize` para NaN. ¿El mensaje de detalle en 500 debe ocultarse en producción (expone implementación interna)?
