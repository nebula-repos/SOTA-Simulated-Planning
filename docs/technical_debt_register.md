# Registro de Deuda Técnica

## Objetivo

Backlog de deuda técnica, bugs no declarados y oportunidades de mejora detectadas en inspecciones del repo.
Solo contiene ítems **vigentes**. Lo resuelto debe eliminarse del registro.

Última actualización: `2026-03-30`

---

## Resumen ejecutivo

| Frente | Items vigentes | Prioridad más alta |
|---|---|---|
| Estado del repo / documentación | D34 | Alta |
| Validación de datos | D08 | Alta |
| Testing / cobertura | D09 | Alta |
| Arquitectura / performance | D14, D15 | Media |
| Forecasting / observabilidad | D21, D33 | Media |

---

## Inventario vigente

| ID | Prio | Tipo | Resumen |
|---|---|---|---|
| D08 | Alta | Validación | `validation.py` sigue muy por debajo del framework documentado |
| D09 | Alta | Testing | Cobertura parcial: faltan tests de API, validation y clasificación pura |
| D14 | Media | Performance | `classify_catalog()` sigue sin caché en la API |
| D15 | Media | Arquitectura | Sin capa formal para artefactos derivados persistidos |
| D21 | Baja | Analítica | Falta notebook reproducible del sweep de parametrización |
| D33 | Media | Forecasting | Falta dashboard agregado de calidad de forecast en UI |
| D34 | Alta | Consistencia | Código, tests y documentación no están alineados |

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

- FK checks
- receipts-before-order
- over-receipt
- reconciliación de inventario
- validación de `location`

**Acción**: implementar una v1 de integridad relacional y temporal antes de seguir ampliando serving o analítica.

---

### D09. Cobertura parcial: faltan tests de API, validation y clasificación pura

**Tipo**: testing  
**Prioridad**: alta

La descripción anterior del registro era demasiado amplia. Hoy sí existen tests de:

- forecasting
- inventory
- `PlanningService`
- diagnostics
- `catalog_health_report`

Pero siguen faltando o son débiles:

- tests de API con `TestClient`
- tests dedicados de `planning_core/validation.py`
- tests directos de `planning_core/classification.py`

**Acción**: reequilibrar cobertura por capa y dejar explícito qué está cubierto y qué no.

---

### D14. `classify_catalog()` sigue sin caché en la API

**Tipo**: performance  
**Prioridad**: media

La UI mitiga esto con `@st.cache_data`, pero la API recalcula la clasificación completa en cada request.

**Acción**: agregar caché simple en memoria o materialización liviana con TTL.

---

### D15. Sin capa formal para artefactos derivados persistidos

**Tipo**: arquitectura  
**Prioridad**: media

No existe contrato formal para persistir y versionar:

- clasificaciones
- health reports
- diagnósticos por SKU
- forecasts servidos

El módulo de evaluación de forecasting sí tiene su propio store, pero no hay una estrategia transversal para derivados analíticos.

**Acción**: decidir si los derivados viven como vistas on-demand, en `output/derived/`, o en una capa separada.

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

### D34. Código, tests y documentación no están alineados

**Tipo**: consistencia / mantenimiento
**Prioridad**: alta

El selector ya incorpora RMSSE tiebreak, filtro de bias, ensemble top-k, bias correction, Fill Rate y h dinámico por SKU. Los tests de la suite fueron actualizados para aceptar el nuevo comportamiento. Sin embargo, la documentación técnica (docstrings en `backtest.py`, `forecasting_models_plan.md`) sigue describiendo el selector antiguo.

Ejemplos pendientes:

- `backtest.py` docstring dice "retorna claves `mase`, `wmape`, `rmsse`, `bias`, `mae`, `rmse`" — falta `fill_rate`
- `forecasting_models_plan.md` no documenta el flujo de ensemble ni bias correction
- README sigue con un conteo de tests hardcodeado que se desactualiza con cada PR

**Acción**:

1. Mantener README con número aproximado ("220+") en vez de count exacto
2. Actualizar docstrings de backtest/selector cuando se estabilice el contrato de retorno
3. Agregar sección de "Selector: flujo completo" en `forecasting_models_plan.md`

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
