# Registro de Deuda Técnica

## Objetivo

Backlog de deuda técnica, bugs no declarados y oportunidades de mejora detectadas en inspecciones del repo.
Solo contiene ítems **vigentes**. Lo resuelto debe eliminarse del registro.

Última actualización: `2026-04-04` (Opción C + Refactor services.py en pipelines)

---

## Resumen ejecutivo

| Frente | Items vigentes | Prioridad más alta |
|---|---|---|
| Validación de datos | D08 | Alta |
| Testing / cobertura | D09 (parcial — API cubierta) | Media |
| Arquitectura / performance | D14, D15 (parcial) | Media |
| Forecasting / observabilidad | D21, D33, D38 | Media / Baja |

---

## Inventario vigente

| ID | Prio | Tipo | Resumen |
|---|---|---|---|
| D08 | Alta | Validación | `validation.py` sigue muy por debajo del framework documentado |
| D09 | Media | Testing | Cobertura 0 en `classification.py`, `preprocessing.py`, `validation.py` (API ya cubierta) |
| D14 | Media | Performance | `classify_catalog()` sigue sin caché en la API |
| D15 | Media | Arquitectura | Sin capa formal para artefactos derivados persistidos |
| D21 | Baja | Analítica | Falta notebook reproducible del sweep de parametrización |
| D33 | Media | Forecasting | Falta dashboard agregado de calidad de forecast en UI |
| D38 | Media | Calidad | `fill_rate` en backtest se promedia entre ventanas; para planning debería reportarse también el mínimo |

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

### D14. `classify_catalog()` sigue sin caché en la API

**Tipo**: performance
**Prioridad**: media

La UI mitiga esto con `@st.cache_data`, pero la API recalcula la clasificación completa en cada request.

**Acción**: agregar caché simple en memoria o materialización liviana con TTL.

---

### D15. Sin capa formal para artefactos derivados persistidos (parcialmente resuelto)

**Tipo**: arquitectura
**Prioridad**: media

**Resuelto parcialmente** (2026-04-04): `ForecastStore` en
`planning_core/forecasting/evaluation/forecast_store.py` define el contrato
de persistencia para forecasts. Los artefactos viven en `output/derived/`
con metadata JSON + parquet por granularidad. CLI `apps/batch_forecast.py`
para materializarlos. `catalog_health_report` los consume automáticamente.

Pendiente:
- Clasificaciones: aún se recalculan on-demand sin persistencia (D14).
- Health reports: sin store propio.
- Diagnósticos por SKU: sin store propio.

**Acción residual**: extender el patrón `ForecastStore` a clasificaciones
si el costo de recalcular en la API se vuelve inaceptable (D14).

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

### D38. `fill_rate` en backtest se promedia entre ventanas

**Tipo**: calidad de métricas
**Prioridad**: media

`planning_core/forecasting/backtest.py` — `_aggregate_window_metrics` promedia `fill_rate` entre todas las ventanas del backtest, igual que MASE y WMAPE. Pero para decisiones de inventario el promedio puede ser engañoso: un modelo que subestima en el 50% de los períodos de una ventana es muy diferente de uno que subestima solo esporádicamente.

Ejemplo:
- Ventana 1: fill_rate = 1.0 (cero subestimaciones)
- Ventana 2: fill_rate = 0.0 (subestima siempre)
- Promedio = 0.5 → parece aceptable, pero el modelo es inestable

**Acción**: en `_aggregate_window_metrics`, agregar `fill_rate_min` (mínimo entre ventanas) junto al promedio, para exponer el peor escenario observado.

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
