# Módulo de System Log

## Objetivo

Documentar el estado actual del system log de `planning_core`, cómo se usa hoy, qué eventos emite y qué trabajo queda pendiente para una siguiente iteración.

Este módulo busca resolver tres necesidades:

- trazabilidad de ejecución
- auditoría de decisiones de planning
- observabilidad legible en terminal y persistible en disco

## Qué es y qué no es

### Qué es

Es un **business event log** estructurado.

Registra operaciones relevantes del dominio con contexto suficiente para responder preguntas como:

- qué proceso corrió
- cuándo corrió
- sobre qué SKU o catálogo
- con qué parámetros
- cuánto demoró
- qué resultado produjo

### Qué no es

No es un reemplazo directo del `logging` tradicional de Python para debugging de bajo nivel ni para infraestructura.

No registra:

- DataFrames completos
- series temporales completas
- `cv_df` del backtest
- payloads arbitrariamente grandes
- secretos o tokens

## Ubicación actual en el código

### Módulo central

- `planning_core/system_log.py`

### Integración en capas de entrada

- `planning_core/services.py`
- `planning_core/forecasting/evaluation/catalog_runner.py`
- `apps/api/main.py`
- `apps/viz/app.py`

La implementación evita introducir side effects dentro de módulos puros como:

- `planning_core/classification.py`
- `planning_core/forecasting/selector.py`
- `planning_core/forecasting/backtest.py`

La regla actual es:

- la lógica pura sigue pura
- la instrumentación ocurre en la capa orquestadora

## Arquitectura actual

## Componentes

### `EventLogger`

Punto de entrada principal del módulo.

Responsabilidades:

- emitir eventos estructurados
- manejar sinks
- correlacionar eventos por `execution_id`
- operar en modo `fail-open`

### `JsonlSink`

Persistencia append-only en disco.

Ubicación actual:

```text
output/system_logs/YYYY-MM-DD/events_<source>_<pid>.jsonl
```

Ejemplos de `source`:

- `api`
- `viz`
- `batch_eval`
- `service`

### `ConsoleSink`

Renderiza eventos en formato humano, corto y legible en terminal.

Diseñado para:

- lectura operacional
- debugging rápido
- seguimiento en tiempo real al correr UI, API o batch

### `EventSpan`

Context manager que emite automáticamente:

- `<evento>.started`
- `<evento>.completed`
- `<evento>.failed`

Además propaga contexto con `contextvars`, de modo que una cadena como:

`purchase_plan -> catalog_health_report -> classify_catalog`

queda ligada al mismo `execution_id`.

## Contrato actual del evento

Cada línea del JSONL contiene un evento con estructura similar a:

```json
{
  "schema_version": 1,
  "ts_utc": "2026-03-31T19:23:01.452Z",
  "level": "INFO",
  "event_name": "forecast.sku.completed",
  "status": "ok",
  "module": "forecasting",
  "source": "viz",
  "execution_id": "exec_1234567890ab",
  "operation_id": "op_abcdef123456",
  "parent_operation_id": "op_9876543210ab",
  "event_id": "evt_a1b2c3d4e5f6",
  "entity_type": "sku",
  "entity_id": "SKU-042",
  "duration_ms": 3412,
  "params": {},
  "metrics": {},
  "result": {},
  "error": null
}
```

## Políticas de seguridad actuales

Hoy el módulo aplica estas reglas:

- `fail-open`: si escribir el log falla, el flujo principal no se interrumpe
- conteo interno de eventos descartados (`dropped_events_count`)
- warning rate-limited por sink fallido
- redacción básica por clave sensible: `token`, `secret`, `password`, `authorization`, `api_key`
- truncado de strings largos
- conversión segura de `NaN` / `Inf` a `null`
- límite de tamaño para colecciones serializadas

## Salida a terminal

## Estado actual

### UI

La UI Streamlit hoy crea el servicio con consola activada por defecto.

Archivo:

- `apps/viz/app.py`

### API

La API FastAPI hoy crea el servicio con consola activada por defecto.

Archivo:

- `apps/api/main.py`

### Batch

El runner batch permite activar consola con parámetro y, si crea su propio logger, la activa por defecto cuando `verbose=True`.

Archivo:

- `planning_core/forecasting/evaluation/catalog_runner.py`

## Cómo activarla o controlarla

### Por parámetro

```python
service = PlanningService(
    repo,
    enable_console_log=True,
    console_use_color=False,
)
```

```python
result = run_catalog_evaluation(
    service,
    config,
    enable_console_log=True,
    console_use_color=False,
)
```

### Por variable de entorno

```bash
export SOTA_SYSTEM_LOG_CONSOLE=1
export SOTA_SYSTEM_LOG_COLOR=0
```

## Importante sobre Streamlit

En la UI los logs aparecen cuando realmente corre lógica del core.

No siempre aparecerán nuevas líneas en cada rerender visual porque varias rutas están cacheadas con `@st.cache_data` y `@st.cache_resource`.

En particular:

- `classify_catalog()` está cacheado en la UI
- `sku_forecast()` está cacheado en la UI
- `catalog_health_report()` está cacheado en la UI

Eso significa:

- si el cálculo se reutiliza desde cache, no hay ejecución nueva
- si cambian SKU o parámetros y el core vuelve a correr, sí aparecerán logs

## Catálogo de eventos actual

## Clasificación

- `classification.catalog.started`
- `classification.catalog.completed`
- `classification.catalog.failed`
- `classification.sku.started`
- `classification.sku.completed`
- `classification.sku.failed`

## Forecast por SKU

### Evento principal

- `forecast.sku.started`
- `forecast.sku.completed`
- `forecast.sku.failed`

### Subeventos de decisión

- `forecast.sku.profile.completed`
- `forecast.sku.horizon.completed`
- `forecast.sku.series.completed`
- `forecast.sku.selection.completed`

Estos subeventos agregan observabilidad sin romper pureza del selector.

Hoy permiten auditar:

- perfil del SKU antes del modelado
- resolución del horizonte `h`
- tamaño de la serie y outliers detectados
- ranking resumido de modelos candidatos

## Forecast batch

- `forecast.batch.started`
- `forecast.batch.completed`
- `forecast.batch.failed`
- `forecast.batch.resume.completed`
- `forecast.batch.sku.completed`

`forecast.batch.sku.completed` se emite desde el proceso padre, no desde workers, para evitar problemas de concurrencia en multiproceso.

## Inventario

- `inventory.safety_stock.started`
- `inventory.safety_stock.completed`
- `inventory.safety_stock.failed`
- `inventory.health_report.started`
- `inventory.health_report.completed`
- `inventory.health_report.failed`

## Compra

- `purchase.plan.started`
- `purchase.plan.completed`
- `purchase.plan.failed`
- `purchase.plan_by_supplier.started`
- `purchase.plan_by_supplier.completed`
- `purchase.plan_by_supplier.failed`
- `purchase.plan_summary.started`
- `purchase.plan_summary.completed`
- `purchase.plan_summary.failed`
- `purchase.recommendation.started`
- `purchase.recommendation.completed`
- `purchase.recommendation.failed`

## Nivel de granularidad actual

Hoy la cobertura es mixta:

### Ya existe log por SKU

- clasificación individual
- forecast individual
- safety stock individual
- recomendación de compra individual
- evaluación batch de forecast por SKU

### Ya existe log agregado

- clasificación de catálogo
- health report de catálogo
- plan de compra agregado
- plan de compra por proveedor
- summary ejecutivo del plan

### Aún no existe log por SKU dentro de procesos masivos

No se emite todavía, dentro de corridas de catálogo:

- `classification.catalog` con un evento por SKU
- `catalog_health_report` con un evento por SKU
- `purchase_plan` con un evento por SKU

Esto fue una decisión deliberada para evitar explosión de volumen.

## Cómo consultar los logs hoy

### En vivo en terminal

Al levantar UI o API, los eventos salen en la terminal del proceso.

### Desde archivos

```bash
tail -f output/system_logs/$(date +%F)/*.jsonl
```

### Desde Python

```python
df = service.event_logger.tail(100)
df = service.event_logger.query(event_name="forecast.sku.selection.completed")
```

## Estado actual resumido

El módulo hoy ya está en un estado usable para operación local y debugging funcional:

- persistencia JSONL
- salida humana en consola
- correlación por ejecución
- sanitización básica
- observabilidad de clasificación, forecast, inventario y compra
- subeventos de decisión de forecast
- cobertura de tests dedicada

## Pendiente para después

Los siguientes ítems no son blockers para el uso actual, pero sí son trabajo natural de la siguiente fase.

### 1. Eventos item-level opcionales para procesos de catálogo

Agregar un flag tipo:

- `log_item_events=True`

para que corridas masivas como:

- `classify_catalog()`
- `catalog_health_report()`
- `purchase_plan()`

puedan emitir eventos por SKU bajo demanda.

### 2. Vista de logs en la UI

Agregar una pestaña o panel para:

- ver últimos eventos
- filtrar por `sku`
- filtrar por `status`
- filtrar por `source`
- filtrar por `execution_id`

### 3. Query API más rica

Hoy `tail()` y `query()` existen, pero siguen siendo básicas.

Pendiente:

- filtro por rango temporal explícito
- paginación
- vista agregada por tipo de evento

### 4. Materialización de resúmenes

Persistir resúmenes diarios o por ejecución para facilitar:

- dashboards
- auditoría histórica
- métricas agregadas por proceso

### 5. Política más formal de retención / rotación

Hoy los logs quedan particionados por día y proceso, pero no existe todavía:

- limpieza automática
- límite de tamaño
- compresión

### 6. Más subeventos de negocio

En forecast se enriqueció bastante la trazabilidad.

Siguientes candidatos naturales:

- decisión de health por SKU dentro de `catalog_health_report`
- decisión de compra por SKU dentro de `purchase_plan`
- razones de exclusión o no-acción

### 7. Contrato de eventos versionado y documentado como API interna

Aunque hoy ya existe `schema_version=1`, todavía falta formalizar:

- catálogo estable de eventos
- contrato de payload por evento
- política de deprecación

## Conclusión

El system log ya dejó de ser una idea y pasó a ser una capacidad operativa real del repositorio.

Su estado actual es suficientemente sólido para:

- inspección local
- auditoría de decisiones
- seguimiento de procesos en terminal
- consumo posterior desde archivos JSONL

La siguiente etapa ya no es “hacer que exista”, sino ampliar granularidad, consulta y visualización.
