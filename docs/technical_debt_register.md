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
| Robustez / bugs | D35, D36, D37 | Alta / Media |
| Arquitectura / performance | D14, D15 | Media |
| Forecasting / observabilidad | D21, D33, D38 | Media / Baja |

---

## Inventario vigente

| ID | Prio | Tipo | Resumen |
|---|---|---|---|
| D08 | Alta | Validación | `validation.py` sigue muy por debajo del framework documentado |
| D09 | Alta | Testing | Cobertura 0 en `classification.py`, `preprocessing.py`, `validation.py` y `apps/api/` |
| D14 | Media | Performance | `classify_catalog()` sigue sin caché en la API |
| D15 | Media | Arquitectura | Sin capa formal para artefactos derivados persistidos |
| D21 | Baja | Analítica | Falta notebook reproducible del sweep de parametrización |
| D33 | Media | Forecasting | Falta dashboard agregado de calidad de forecast en UI |
| D34 | Alta | Consistencia | Docstrings de `backtest.py` y `selector.py` desactualizados respecto al nuevo contrato |
| D35 | Alta | Robustez | `apps/api/main.py` sin manejo de errores — excepciones del repositorio crashean todos los endpoints |
| D36 | Media | Bug | `_fit_predict_model` en `selector.py` silencia todas las excepciones sin log ni warning |
| D37 | Baja | Bug | `_apply_bias_correction` retorna referencia al original cuando no aplica corrección (riesgo de mutación) |
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
| `apps/api/main.py` | ❌ ninguno | Alto — endpoints sin contrato de respuesta probado |
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

### D34. Docstrings desactualizados en forecasting

**Tipo**: consistencia / mantenimiento
**Prioridad**: alta

El selector ya incorpora RMSSE tiebreak, filtro de bias, ensemble top-k, bias correction, Fill Rate y h dinámico. Los tests están alineados. Pero los docstrings y la documentación técnica describen el comportamiento anterior.

Ejemplos pendientes:

- `backtest.py:88` — docstring dice que retorna claves `mase, wmape, rmsse, bias, mae, rmse`; falta `fill_rate`
- `selector.py:36` — docstring en módulo y en `select_and_forecast` no documenta el flujo de ensemble ni bias correction
- `forecasting_models_plan.md` — no tiene sección del selector nuevo

**Acción**:

1. Actualizar docstrings de `run_backtest` y `select_and_forecast`
2. Agregar sección "Selector: flujo completo" en `forecasting_models_plan.md`

---

### D35. `apps/api/main.py` sin manejo de errores

**Tipo**: robustez
**Prioridad**: alta

Todos los endpoints llaman directamente a `service.*` sin `try/except`. Cualquier falla del repositorio (archivo CSV ausente, manifest corrupto, error de I/O) propaga una excepción no manejada y devuelve un 500 sin mensaje claro.

Casos de riesgo identificados:

| Endpoint | Escenario de fallo | Consecuencia |
|---|---|---|
| `GET /health` | CSVs ausentes → `FileNotFoundError` | 500 en health check — sistema parece caído |
| `GET /sku/{sku}/forecast` | LightGBM falla por memoria | 500 en lugar de fallback graceful |
| `GET /classification` | Catálogo vacío | 500 o respuesta vacía sin indicación |
| `GET /sku/{sku}/timeseries` | SKU no existe | silently retorna vacío, sin 404 |

Adicionalmente, el parámetro `location` en `/sku/{sku}/forecast` no se valida contra las ubicaciones conocidas del manifest; si se pasa un valor inválido, el forecast se genera silenciosamente sobre una serie vacía.

**Acción**:

1. Envolver todos los endpoints en `try/except` con respuestas HTTP estandarizadas (404 para SKU no encontrado, 422 para parámetros inválidos, 503 para fallos de repositorio)
2. Agregar validación de `location` antes de llamar `sku_forecast`
3. Añadir tests de API con `TestClient` (alineado con D09)

---

### D36. `_fit_predict_model` silencia todas las excepciones

**Tipo**: bug / observabilidad
**Prioridad**: media

`planning_core/forecasting/selector.py` — `_fit_predict_model()` atrapa `Exception` y retorna `None` sin emitir ningún log ni warning.

```python
except Exception:
    return None  # caller desconoce si falló o si el modelo es desconocido
```

Cuando el ensemble llama a `_fit_predict_model` para múltiples modelos y uno falla, ese modelo se descarta en silencio. En producción esto oculta bugs reales (ImportError de LightGBM, ValueError de series cortas en MSTL) y hace el diagnóstico difícil.

**Acción**: reemplazar `except Exception: return None` por `except Exception as exc: warnings.warn(...)` con el nombre del modelo y la excepción, igual al patrón ya usado para LightGBM en `select_and_forecast`.

---

### D37. `_apply_bias_correction` retorna referencia al original cuando no corrige

**Tipo**: bug (bajo impacto actual, riesgo futuro)
**Prioridad**: baja

`planning_core/forecasting/selector.py` — cuando `|bias| < 0.02`, la función retorna `forecast_df` directamente sin copiar:

```python
if math.isnan(bias) or abs(bias) < _BIAS_CORRECTION_MIN_ABS:
    return forecast_df  # referencia al original
```

Si el llamador modifica la variable retornada en el futuro, muta el objeto original inesperadamente. Hoy no causa un bug porque el llamador inmediato solo asigna la referencia, pero es una trampa al refactorizar.

**Acción**: cambiar a `return forecast_df.copy()` en esa rama, o documentar explícitamente que el contrato de retorno puede ser una referencia.

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

### Q3. Manejo de errores en la API (D35)

¿El nivel de detalle de los mensajes de error debe diferenciarse por entorno (dev vs. prod)?
Un 500 con stack trace es útil para debug pero puede exponer implementación interna en producción.
