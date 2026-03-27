# Registro de Deuda Técnica

## Objetivo

Backlog de deuda técnica, bugs no declarados y oportunidades de mejora detectadas en inspecciones del repo.
Solo contiene items **vigentes** — lo resuelto se elimina. Cada item tiene prioridad, tipo y acción concreta.

Última actualización: `2026-03-27`

---

## Resumen ejecutivo

| Frente | Items vigentes | Prioridad más alta |
|---|---|---|
| Testing / cobertura | D09, D26, D27 | Alta |
| Performance / N+1 | D23, D24 | Alta |
| Validación de datos | D08 | Alta |
| **Calidad de forecast** | **D31, D32, D33** | **Alta** |
| Forecasting post-exp | D18, D19, D20, D21 | Media–Baja |
| Arquitectura / deuda estructural | D14, D15, D22 | Media |
| Calidad de código | D25, D28, D29, D30 | Baja–Media |

---

## Inventario vigente

| ID | Prio | Tipo | Resumen |
|---|---|---|---|
| D08 | Alta | Validación | `validation.py` muy por debajo del framework documentado |
| D09 | Alta | Testing | Sin tests para simulador, repository, services, classification, validation, API |
| D14 | Media | Performance | `classify_catalog()` sin caché en API; recomputa sobre todo el catálogo |
| D15 | Media | Arquitectura | Sin capa formal para artefactos derivados persistidos |
| D18 | Media | Forecasting | MASE > 1 en intermittent/lumpy — sin métricas operacionales (Fill Rate, CSL) |
| D19 | Baja | Forecasting | Empate técnico en horse-race no detectado — ganador puede cambiar con MASE < 0.02 |
| D20 | Baja | Forecasting | `h` fijo para todo el catálogo — no derivado del lead time por SKU |
| D21 | Baja | Analítica | Notebook de análisis del sweep de parametrización no existe |
| D22 | Media | Packaging | `services.py` importa `forecasting.selector` a nivel módulo — dependencia runtime de statsforecast |
| D23 | Alta | Performance | N+1 scan de transactions en `catalog_health_report` — carga completa por cada SKU |
| D24 | Alta | Performance | N+1 scan en `classify_all_skus` — filtro O(n) por cada SKU en el loop |
| D25 | Baja | Código | `sku_catalog_row` computado pero no usado en `catalog_health_report` |
| D26 | Alta | Testing | Sin tests para `diagnostics.py` / `InventoryDiagnosis` / `diagnose_sku` |
| D27 | Alta | Testing | Sin tests para `catalog_health_report` (el método más complejo de Fase 4) |
| D28 | Media | Código | Valores sentinel `9999.0` / `999.0` para `inf` en diagnostics sin constantes nombradas |
| D29 | Baja | UI | `use_container_width=True` deprecado en Streamlit (deadline 2025-12-31) — 30 instancias en app.py |
| D30 | Baja | UI | TTL de caché `_get_catalog_health` en 300 s — demasiado corto para un cálculo de ~74 s |
| D31 | Alta | Forecasting | Selección de modelo usa solo MASE — WMAPE, RMSSE y Bias computados pero ignorados |
| D32 | Alta | Forecasting | Calidad de forecast insatisfactoria — margen de mejora amplio sin explorar (tuning, ensemble, post-procesado) |
| D33 | Media | Forecasting | Métricas de error del catálogo no expuestas en UI — no hay dashboard de calidad agregada |

---

## Detalle por item

### D08. `validation.py` muy por debajo del framework documentado

**Tipo**: validación
**Prioridad**: alta

`docs/data_health_checks.md` describe un framework amplio de auditoría. El módulo actual solo chequea duplicados, negativos y un conteo de transfers sin `receipt_date`.

Faltan: FK checks, receipts-before-order, over-receipt, reconciliación de inventario, locations válidas.

**Acción**: implementar al menos una v1 ejecutable con los checks más críticos de integridad relacional.

---

### D09. Sin tests para simulador, repository, services, classification, validation, API

**Tipo**: testing
**Prioridad**: alta

Tests existentes cubren solo forecasting (modelos, métricas, backtest) e inventory params/safety_stock.

Sin cobertura para:
- `apps/simulator/generate_canonical_dataset.py`
- `planning_core/repository.py`
- `planning_core/services.py` (métodos: `classify_catalog`, `sku_timeseries`, `catalog_health_report`)
- `planning_core/classification.py` (`classify_all_skus`, `classify_sku`)
- `planning_core/validation.py`
- `apps/api/main.py`

**Acción**: agregar suites por capa, priorizar `classification.py` y `services.py`.

---

### D14. `classify_catalog()` no está cacheado en la API

**Tipo**: performance
**Prioridad**: media

La clasificación masiva sobre el dataset actual tarda varios segundos. La UI lo mitiga con `@st.cache_data`, pero la API (`GET /catalog/classify`) recalcula en cada request.

**Acción**: agregar caché en memoria (functools.lru_cache o similar) o materializar el resultado en disco con TTL.

---

### D15. Sin capa formal para artefactos derivados persistidos

**Tipo**: arquitectura
**Prioridad**: media

No existe contrato formal para persistir: clasificaciones, quality reports, forecasts, backtests, diagnósticos de salud.
Todo se recalcula en memoria — sin trazabilidad de corridas analíticas.

**Acción**: decidir si los derivados viven como: vistas on-demand, archivos en `output/derived/`, o capa semántica separada.

---

### D18. Métricas operacionales ausentes para demanda intermitente/lumpy

**Tipo**: forecasting
**Prioridad**: media

MASE > 1 es esperado para SKUs intermitentes y lumpy. Las métricas relevantes para estos segmentos son Fill Rate y CSL alcanzado — ninguna está implementada.

**Acción**: implementar métricas operacionales en `backtest.py` o módulo separado.

---

### D19. Empate técnico en horse-race no detectado

**Tipo**: forecasting
**Prioridad**: baja

El selector elige el ganador por MASE mínimo sin umbral de indiferencia. Diferencias < 0.02 no son estadísticamente significativas pero cambian el modelo seleccionado.

**Acción**: agregar lógica de "empate" — si la diferencia entre primer y segundo lugar es < ε, preferir el modelo más simple (Naive o ETS sobre Prophet/GB).

---

### D20. Horizonte `h` fijo para todo el catálogo

**Tipo**: forecasting
**Prioridad**: baja

`h=3` es un global fijo. No se deriva del lead time del proveedor por SKU. Un SKU con lead time de 90 días necesita un horizonte de ~3 meses; uno con 21 días solo 1.

**Acción**: derivar `h` desde `InventoryParams.lead_time_days` por SKU al momento de seleccionar el modelo.

---

### D21. Notebook de análisis del sweep de parametrización no existe

**Tipo**: analítica
**Prioridad**: baja

`docs/forecasting_param_sweep_results.md` documenta resultados pero no hay notebook reproducible (`notebooks/03_param_sweep_analysis.ipynb`).

**Acción**: crear el notebook para hacer el análisis visual reproducible.

---

### D22. `services.py` importa `forecasting.selector` a nivel módulo

**Tipo**: packaging
**Prioridad**: media

```python
# planning_core/services.py — top-level import
from planning_core.forecasting.selector import select_and_forecast
```

Esto hace que `statsforecast` sea una dependencia **runtime** del core aunque `pyproject.toml` la declare como extra `[forecast]` opcional. Cualquier import de `PlanningService` arrastra toda la cadena de dependencias de forecasting.

**Acción**: mover el import dentro de la función `sku_forecast()` (lazy import) o crear un módulo bridge.

---

### D23. N+1 scan de transactions en `catalog_health_report`

**Tipo**: performance
**Prioridad**: alta
**Archivo**: `planning_core/services.py`, línea 942

```python
# Dentro del loop por SKU (660+ iteraciones):
demand_series = self.sku_demand_series(sku, granularity=granularity)
```

`sku_demand_series` llama a `repository.load_table("transactions")` (cacheado) y luego filtra con `transactions[transactions["sku"] == sku]` — un O(n) scan completo por cada SKU. Con 660 SKUs y ~2M filas de transactions: 660 scans completos secuenciales.

**Acción**: pre-agrupar `transactions` por SKU antes del loop usando `groupby`, igual que hace `classify_catalog` con `tx_groups` (línea 734). Pasar el grupo pre-filtrado directamente a `prepare_demand_series`.

---

### D24. N+1 scan en `classify_all_skus`

**Tipo**: performance
**Prioridad**: alta
**Archivo**: `planning_core/classification.py`, línea 1079–1092

```python
for sku in all_skus:
    if sku in skus_with_tx:
        sku_tx = transactions[transactions["sku"] == sku]  # O(n) por iteración
        profile = classify_sku(sku_tx, ...)
```

Mismo patrón que D23. La tabla de transactions se filtra con un boolean mask por cada SKU, lo cual escala O(SKUs × rows).

**Acción**: usar `transactions.groupby("sku")` antes del loop y acceder con `.get_group(sku)`. El `tx_groups = dict(groupby)` tarda una vez O(n) y los lookups son O(1).

---

### D25. `sku_catalog_row` computado pero no usado

**Tipo**: código / dead code
**Prioridad**: baja
**Archivo**: `planning_core/services.py`, línea 938

```python
sku_catalog_row = catalog.loc[catalog["sku"] == sku]  # ← nunca usado
params = get_sku_params(sku, abc_class, supplier, self.repository, manifest)
```

La variable se calcula (O(n) scan) pero no se consume. Debería eliminarse.

**Acción**: eliminar la línea.

---

### D26. Sin tests para `diagnostics.py` / `diagnose_sku`

**Tipo**: testing
**Prioridad**: alta

El módulo de Fase 4 más crítico para la UI de Health no tiene ningún test:

Casos sin cobertura:
- `diagnose_sku` con demanda cero (coverage_net → inf, ratio → inf, dead stock)
- `diagnose_sku` con todas las bandas de salud (quiebre, substock, equilibrio, sobrestock_leve, sobrestock_critico)
- `_stockout_probability` con `sigma_ddlt = 0` (demanda determinística)
- `_classify_ratio` con `is_dead_stock=True`
- `DEAD_STOCK_DAYS_THRESHOLD` boundary (89 días vs 90 días)

**Acción**: crear `tests/test_diagnostics.py` con suites para `diagnose_sku` y helpers.

---

### D27. Sin tests para `catalog_health_report`

**Tipo**: testing
**Prioridad**: alta
**Archivo**: `planning_core/services.py`

`catalog_health_report` es el método más complejo agregado en Fase 4 y no tiene ningún test. Cubre: clasificación, lookup de stock, dead stock, SS, diagnóstico, columnas financieras.

Casos sin cobertura:
- Retorna DataFrame con columnas esperadas (`excess_capital`, `stockout_capital`, etc.)
- SKU sin transacciones → days_since = 9999 → dead stock
- SKU sin snapshot de inventario → on_hand=0, on_order=0
- DataFrame no vacío cuando hay al menos un SKU activo

**Acción**: crear suite en `tests/test_services.py` o archivo dedicado.

---

### D28. Valores sentinel `9999.0` / `999.0` para infinito en diagnostics

**Tipo**: código / semántica
**Prioridad**: media
**Archivo**: `planning_core/inventory/diagnostics.py`, líneas 349, 373, 389

```python
ratio_for_band = positioning_ratio if not math.isinf(positioning_ratio) else 999.0
coverage_net_days=coverage_net_days if not math.isinf(coverage_net_days) else 9999.0,
```

Los valores `9999.0` y `999.0` aparecen literales sin constante nombrada. Si el umbral cambia o se introduce serialización, estos "inf disfrazados" pueden aparecer en reportes como valores de stock reales.

**Acción**: definir constantes: `_INF_COVERAGE_SENTINEL = 9999.0` y `_INF_RATIO_SENTINEL = 999.0` en la cabecera del módulo.

---

### D29. `use_container_width=True` deprecado en Streamlit

**Tipo**: UI / deuda API
**Prioridad**: baja
**Archivo**: `apps/viz/app.py`, ~30 instancias

El parámetro fue deprecado con deadline 2025-12-31. En Streamlit 1.50 (instalado) sigue en la firma del método, pero puede removerse en versiones futuras.

**Acción**: eliminar el parámetro en todos los `st.plotly_chart()`, `st.dataframe()`, y `st.button()` donde aparece — es el comportamiento default actual.

---

### D30. TTL de caché `_get_catalog_health` demasiado corto

**Tipo**: UI / performance
**Prioridad**: baja
**Archivo**: `apps/viz/app.py`

```python
@st.cache_data(show_spinner=False, ttl=300)
def _get_catalog_health(_service):
```

El cálculo tarda ~74 segundos (incluido el pytest). Con TTL=300 s (5 min) en una sesión activa, el sistema regenerará frecuentemente y bloqueará la UI.

**Acción**: subir TTL a 1800 s (30 min) o hacerlo configurable. El botón "↺ Recargar" ya existe para recarga manual forzada.

---

### D31. Selección de modelo usa solo MASE — métricas secundarias ignoradas

**Tipo**: forecasting / selección de modelos
**Prioridad**: alta
**Archivo**: `planning_core/forecasting/selector.py` — `_pick_winner()`

El horse-race elige el modelo ganador comparando únicamente MASE. WMAPE, RMSSE y Bias ya están implementados en `metrics.py` y calculados en `backtest_summary`, pero no influyen en la decisión.

**Problemas concretos**:
- **MASE no detecta sesgo sistemático**: un modelo puede ganar en MASE y aun así sobre-predecir consistentemente → sobrestock acumulado. `Bias` debería actuar como penalización o filtro.
- **RMSSE penaliza errores grandes más que MASE**: para SKUs lumpy/erratic con spikes, RMSSE discrimina mejor entre modelos que "aciertan la magnitud" vs los que simplemente minimizan el promedio.
- **WMAPE pondera por volumen**: para ABC-A, los errores en periodos de alta demanda importan más que en periodos bajos — MASE no lo captura.
- **Empate técnico sin desempate formal** (ver D19): si dos modelos difieren en MASE < 0.02, hoy gana el que sale primero en el DataFrame, no el más simple ni el de menor bias.

**Acciones sugeridas**:
1. Agregar **filtro de bias**: si el ganador por MASE tiene `|Bias| > umbral` (e.g. 0.15), preferir el siguiente candidato con menor sesgo.
2. Agregar **desempate por RMSSE**: cuando `delta MASE < 0.02`, desempatar con RMSSE — más estable para series con varianza alta.
3. Evaluar un **score combinado ponderado** por segmento: `score = α×MASE + β×RMSSE + γ×|Bias|` con pesos distintos para smooth, erratic e intermittent/lumpy. Requiere experimentación con el catálogo real antes de activar.
4. Exponer en el resumen del horse-race (UI) el Bias y RMSSE del ganador para transparencia.

**Decisión pendiente**: elegir entre (a) score combinado o (b) filtros/desempates secuenciales. La opción (b) es más interpretable y menos frágil para un primer paso.

---

### D32. Calidad de forecast insatisfactoria — margen de mejora amplio sin explorar

**Tipo**: forecasting / calidad de resultados
**Prioridad**: alta

Los resultados actuales del horse-race (MASE mediana global 0.7475, `h=3, n_windows=3`) son funcionales pero no convincentes. Hay múltiples ejes de mejora sin tocar:

**1. Calibración de modelos (sin tuning)**

Ningún modelo está tuneado — todos usan configuraciones por defecto:
- `AutoETS` y `AutoARIMA` buscan el mejor modelo automáticamente pero dentro de un espacio no ajustado al dominio industrial (oleohidráulica con demanda muy irregular).
- `LightGBM` usa lags fijos sin exploración de ventanas ni features de dominio.
- Los umbrales de candidatura (`n_obs >= 36` para LightGBM mensual) son conservadores pero no validados empíricamente.

**2. Features de dominio ausentes en LightGBM**

El modelo ML solo usa lags y features de calendario genéricas. No incorpora:
- Lead time del proveedor como feature
- Clase ABC/XYZ como feature categórica
- Precio unitario / valor del SKU
- Estacionalidad detectada por autocorrelación (flag booleano ya calculado en clasificación)

**3. Sin ensemble ni combinación de forecasts**

La literatura (Hyndman & Athanasopoulos, M4 Competition) muestra que combinar los top-k modelos del horse-race supera sistemáticamente al ganador individual. Actualmente se descarta todo salvo el ganador.

**4. Sin post-procesado**

- No hay corrección de bias post-predicción (ajuste por sesgo histórico del ganador).
- No hay calibración de intervalos de confianza — los IC 80% no se validan empíricamente contra cobertura real.
- No hay suavizado de forecast para series muy ruidosas.

**5. Horizonte fijo desconectado del lead time (ver D20)**

`h=3` para todos los SKUs es correcto para lead times de ~90 días, pero insuficiente para proveedores con LT=180 días y excesivo para LT=21 días. Un horizonte mal calibrado degrada el backtest y el modelo elegido.

**6. Granularidad mensual forzada**

La granularidad mensual reduce dramáticamente el número de observaciones disponibles para el backtest (3 años = 36 puntos). Algunos SKUs con series suaves podrían beneficiarse de granularidad semanal, que duplica el tamaño de muestra efectivo.

**Acciones sugeridas (por prioridad de impacto/esfuerzo)**:

| Acción | Impacto esperado | Esfuerzo |
|---|---|---|
| Corrección de bias post-predicción | Alto para SKUs con sesgo sistemático | Bajo |
| Ensemble simple (promedio top-3) | Alto — demostrado en literatura | Medio |
| Features de dominio en LightGBM | Alto para SKUs erratic/lumpy | Medio |
| Horizonte `h` por SKU (D20) | Alto para catálogos con LT muy variable | Medio |
| Granularidad adaptativa por SKU | Medio — complejidad de pipeline | Alto |
| Tuning de hiperparámetros | Medio — depende del SKU | Alto |

**Criterio de éxito**: reducir MASE mediana global por debajo de 0.65, o demostrar mejora estadísticamente significativa en al menos 2 segmentos ABC.

---

### D33. Métricas de error del catálogo no expuestas en UI

**Tipo**: forecasting / experiencia de usuario
**Prioridad**: media
**Archivo**: `apps/viz/app.py`

WMAPE, RMSSE y Bias están calculados en `backtest_summary` y disponibles en `PlanningService.sku_forecast(return_cv=True)`, pero la UI solo muestra:
- El gráfico del horse-race por SKU individual
- El modelo ganador y su MASE

No existe ninguna vista de **calidad agregada del catálogo** que permita responder:
- ¿Cuántos SKUs tienen MASE > 1? ¿Por qué segmento?
- ¿Hay sesgo sistemático en algún proveedor o categoría?
- ¿Cuál es la distribución de WMAPE por clase ABC?
- ¿Los IC 80% tienen cobertura empírica real del ~80%?

**Acciones sugeridas**:
1. Agregar vista "Calidad de forecast" en la UI con:
   - Distribución de MASE, WMAPE, Bias por segmento (ABC, SB class, is_seasonal)
   - KPI strip: % SKUs con MASE < 1, mediana WMAPE, % con |Bias| < 0.1
   - Scatter MASE vs Bias coloreado por ABC para detectar clusters problemáticos
2. Exponer estas métricas en la subsección "Forecast" del detalle de SKU (hoy solo muestra MASE del ganador).

---

## Preguntas de diseño abiertas

### Q1. Artefactos derivados (D15)

¿Los derivados (clasificaciones, forecasts, diagnósticos) deben ser:
- vistas on-demand recalculadas (hoy)
- archivos en `output/derived/` con versión y timestamp
- base de datos ligera (DuckDB/SQLite)

### Q2. Horizonte de forecast por SKU (D20)

¿El horizonte `h` debe ser:
- global fijo (hoy: `h=3`)
- derivado del `lead_time_days` del proveedor por SKU
- configurable por segmento ABC

### Q4. Estrategia de mejora del forecast (D31, D32)

¿El siguiente paso prioritario debe ser:
- (a) **Corrección de bias + desempate por RMSSE** en `_pick_winner` — cambio quirúrgico, bajo riesgo, impacto inmediato
- (b) **Ensemble de top-k modelos** — mayor complejidad de pipeline, pero evidencia empírica sólida de mejora
- (c) **Features de dominio en LightGBM** — requiere ingeniería de features y re-experimentación
- (d) **Horizonte `h` por SKU** — dependiente de D20, pero integra bien con el módulo de inventario ya existente

La opción (a) es el camino de menor fricción y debería hacerse primero independientemente de las otras.

### Q3. Nivel de health check deseado (D08)

¿La v1 de validaciones debe ser:
- mínima ejecutable en cada corrida del simulador
- más completa aunque más lenta (on-demand)

---

## Criterio para resolver items

1. Fijar la decisión de diseño si aplica
2. Corregir documentación o código
3. Agregar test o validación de regresión
4. Marcar como resuelto con fecha y eliminar en la próxima inspección
