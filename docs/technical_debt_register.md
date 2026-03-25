# Registro de Deuda Tecnica y Documental

## Objetivo

Dejar explicitada la deuda tecnica y documental detectada durante la inspeccion del repo para usarla como backlog de trabajo.

Este documento cubre:

- inconsistencias entre documentacion y codigo
- gaps entre arquitectura declarada e implementacion real
- riesgos de modelado y semantica
- gaps de testing, validacion e integracion
- decisiones de diseno pendientes antes de corregir ciertas piezas

## Estado actual del repo evaluado

Fecha de analisis: `2026-03-24`

Contexto observado:

- generador de dataset canonico operativo y consistente
- capa `planning_core` funcional para lectura, agregacion y clasificacion
- UI y API livianas conectadas al canonico
- modulo de forecasting parcialmente implementado pero no integrado
- tests existentes centrados en metricas y wrappers de forecast

## Resumen ejecutivo

La deuda no esta concentrada en una sola capa. Hoy hay cuatro frentes principales:

1. deuda documental: el repo documenta una realidad anterior en varias partes
2. deuda semantica/modelado: hay decisiones importantes que hoy estan implicitas o inferidas
3. deuda de producto/integracion: forecasting existe como libreria interna, pero no como capacidad end-to-end
4. deuda de confiabilidad: validaciones y tests cubren una porcion menor de la superficie real del sistema

## Inventario priorizado

| ID | Prioridad | Tipo | Resumen | Estado |
|---|---|---|---|---|
| D01 | Alta | Documental | `README.md` no refleja el estado real del repo | **Resuelto** |
| D02 | Alta | Documental | Documentacion de forecasting y metricas quedo desfasada respecto al codigo | **Resuelto** |
| D03 | Alta | Semantica | El nodo central y las locations no estan modelados explicitamente como entidad/configuracion | **Resuelto** |
| D04 | Alta | Semantica | La clasificacion masiva depende de una granularidad global que altera fuertemente los resultados | **Resuelto en codigo** (pendiente re-ejecutar output/) |
| D05 | Alta | Semantica | La clasificacion usa demanda atendida y no incorpora censura por stockout en el pipeline principal | **Resuelto** |
| D06 | Alta | Integracion | El modulo de forecasting existe pero no esta integrado a `planning_core`, API ni UI | **Resuelto** — `sku_forecast()` + endpoint API + seccion UI |
| D07 | Alta | Integracion | Faltan `backtest.py` y `selector.py`, aunque son piezas centrales del roadmap declarado | **Resuelto** — ambos implementados |
| D08 | Alta | Validacion | `planning_core/validation.py` cubre solo chequeos basicos y esta muy por debajo de `docs/data_health_checks.md` | Pendiente |
| D09 | Alta | Testing | No hay tests para simulador, repository, services, clasificacion, preprocessing, API o UI | Pendiente (parcial: test_services.py existe) |
| D10 | Media | Semantica | Inconsistencia de frecuencias semanales (`W` vs `W-MON`) entre capas | **Resuelto** — `GRANULARITY_PLANNING_KEYS` centraliza convencion en app.py |
| D11 | Media | Semantica | `central_location()` se infiere heuristica y no desde un contrato explicito | **Resuelto** |
| D12 | Media | Configuracion | Hay parametros del simulador definidos pero no usados | **Resuelto** |
| D13 | Media | Modelo | El esquema permite OCs multi-linea, pero el generador siempre produce 1 linea por OC | **Cerrado** (simplificacion deliberada) |
| D14 | Media | Performance | `classify_catalog()` recomputa sobre todo el catalogo y en API no hay cache | Pendiente |
| D15 | Media | Producto | No existe capa formal para resultados derivados persistidos | Pendiente |
| D16 | Baja | Operacion | No hay estructura de `notebooks/`, `experiments/` o `scripts/` para trabajo reproducible de analitica | **Resuelto** |

## Detalle por item

### D01. `README.md` no refleja el estado real del repo

**Tipo**: documental  
**Prioridad**: alta

**Evidencia**

- [README.md](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/README.md) declara que el proyecto esta en `Fase 0 (Data Generation)`.
- El repo ya contiene implementacion de clasificacion en [planning_core/classification.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/classification.py), exposicion en [planning_core/services.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/services.py), endpoints en [apps/api/main.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/apps/api/main.py) y UI en [apps/viz/app.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/apps/viz/app.py).
- El README tambien afirma que el nodo central no genera ventas operativas, pero el simulador y otros docs ya modelan `CD Santiago` como nodo hibrido.

**Impacto**

- induce decisiones equivocadas sobre el alcance real del repo
- hace parecer inexistentes capacidades que si estan implementadas
- vuelve ambiguo el punto de partida para nuevas tareas

**Accion sugerida**

- reescribir `README.md` para separar claramente:
  - implementado hoy
  - parcialmente implementado
  - planificado

**Decision pendiente**

- definir si el README debe describir el repo como:
  - laboratorio de planning con varias capas ya operativas
  - o simulador central con modulos experimentales alrededor

### D02. Documentacion de forecasting y metricas desfasada

**Tipo**: documental
**Prioridad**: alta
**Estado**: ✅ Resuelto — 2026-03-24

`docs/forecasting_models_plan.md` y `docs/models/evaluation_metrics.md` actualizados para reflejar:
- lo que ya esta implementado (modelos, metricas, backtest, selector, integracion en services)
- lo que queda pendiente (API endpoint, UI de forecast, Fase 3.1-3.3)

### D03. Nodo central y universo de locations no estan modelados explicitamente

**Tipo**: semantica/modelado
**Prioridad**: alta
**Estado**: ✅ Resuelto — 2026-03-24

El manifest persistido en `output/dataset_manifest.json` contiene la clave `location_model` con:

```json
"location_model": {
  "all_locations": ["Santiago", "Antofagasta", "Copiapó", "Concepción", "Lima", "CD Santiago"],
  "branch_locations": ["Santiago", "Antofagasta", "Copiapó", "Concepción", "Lima"],
  "central_location": "CD Santiago",
  "central_supply_mode": true,
  "central_node_sales_mode": true
}
```

`PlanningService.location_model()` lee este contrato explicito. La heuristica en `central_location()` queda como fallback defensivo pero no se invoca con el manifest actual.

### D04. La clasificacion masiva depende de granularidad global y distorsiona resultados

**Tipo**: semantica/analitica  
**Prioridad**: alta

**Evidencia**

- [planning_core/classification.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/classification.py) usa una granularidad global en `classify_all_skus()` si no se fuerza una.
- Sobre el dataset real:
  - `M`: `smooth 682`, `intermittent 69`, `lumpy 22`
  - `W`: `smooth 589`, `intermittent 154`, `lumpy 30`
  - `D`: `intermittent 676`, `lumpy 91`, `smooth 16`

**Impacto**

- la clasificacion cambia de forma estructural segun granularidad
- el resultado actual puede suavizar artificialmente patrones intermitentes
- cualquier mapeo futuro clasificacion -> forecast queda condicionado por una decision metodologica no resuelta

**Accion sugerida**

- decidir una semantica oficial para clasificacion:
  - por SKU agregado de red
  - por SKU-location
  - ambas
- decidir si la granularidad debe ser:
  - global
  - por SKU
  - fija por caso de uso

**Decision actual**

- para este repo/piloto, la clasificacion oficial queda fijada como `SKU` agregado de red
- la clasificacion por sucursal queda fuera de alcance y se abordara en otro piloto

**Decision pendiente**

- resuelta: la granularidad oficial por defecto queda mensual (`M`)

**Estado**

- implementado en codigo para la clasificacion oficial del repo
- pendiente re-ejecutar sobre `output/` regenerado y revisar la salida final en UI/API con el dataset persistido

### D05. El pipeline principal clasifica demanda atendida sin incorporar censura

**Tipo**: semantica/forecasting
**Prioridad**: alta
**Estado**: ✅ Resuelto — 2026-03-24

Decision tomada e implementada:
- clasificacion opera sobre demanda observada (sin reconstruccion)
- `mark_censored_demand()` y `censored_summary()` calculan censura por separado
- `classify_catalog()` y `classify_single_sku()` augmentan el resultado con flags de censura via `_augment_catalog_classification_with_censoring()`
- `quality_score` penaliza periodos censurados
- `sku_censored_mask()` expuesto en servicios y UI
- reconstruccion de demanda queda para fase posterior de forecasting

### D06. El modulo de forecasting existe pero no esta integrado al flujo principal

**Tipo**: integracion/producto
**Prioridad**: alta
**Estado**: ✅ Resuelto — 2026-03-24

Integración completa en tres capas:

1. `PlanningService.sku_forecast()` en `planning_core/services.py` — horse-race + forecast final
2. `GET /sku/{sku}/forecast` en `apps/api/main.py` — parámetros `granularity`, `h`, `n_windows`, `location`
3. Seccion **Forecast** en detalle de SKU (`apps/viz/app.py`) — controles interactivos, gráfico histórico + forecast con IC 80%, tabla de forecast, resumen del horse-race

### D07. Faltan `backtest.py` y `selector.py`

**Tipo**: integracion/arquitectura
**Prioridad**: alta
**Estado**: ✅ Resuelto — 2026-03-24

- `planning_core/forecasting/backtest.py`: `run_backtest()` expanding-window con minimo de 3 ventanas, `backtest_summary()`
- `planning_core/forecasting/selector.py`: `select_and_forecast()` — candidatos por clasificacion SB, horse-race por MASE, forecast final con modelo ganador

### D08. `validation.py` esta muy por debajo del framework documentado

**Tipo**: validacion  
**Prioridad**: alta

**Evidencia**

- [docs/data_health_checks.md](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/docs/data_health_checks.md) describe un framework amplio.
- [planning_core/validation.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/validation.py) solo chequea:
  - duplicados
  - negativos
  - un conteo de transfers abiertos sin `receipt_date`

**Impacto**

- la documentacion promete un nivel de auditoria que el codigo no entrega
- futuros cambios podrian romper integridad relacional o semantica sin alarmas

**Accion sugerida**

- definir una v1 realista del health check
- implementar al menos:
  - FK checks
  - receipts before order
  - over-receipt
  - reconciliacion de inventario
  - locations validas

### D09. Falta cobertura de tests fuera del forecasting

**Tipo**: testing  
**Prioridad**: alta

**Evidencia**

- Tests existentes:
  - [tests/test_models.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/tests/test_models.py)
  - [tests/test_metrics.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/tests/test_metrics.py)
- No hay tests para:
  - simulador
  - repository
  - services
  - classification
  - preprocessing
  - validation
  - API

**Impacto**

- mucha logica de negocio queda sin red de seguridad
- cualquier refactor en simulacion o clasificacion es riesgoso

**Accion sugerida**

- agregar suites por capa, empezando por:
  - `planning_core/classification.py`
  - `planning_core/services.py`
  - `planning_core/validation.py`

### D10. Inconsistencia de frecuencia semanal entre capas

**Tipo**: semantica
**Prioridad**: media
**Estado**: ✅ Resuelto — 2026-03-24

Se creo `GRANULARITY_PLANNING_KEYS` en `apps/viz/app.py` como constante top-level que mapea nombres de display a claves de la API de `planning_core` (`"W"`). Se eliminó el dict inline duplicado que existia en la funcion de censura. Se agrego comentario explicando la diferencia:
- `GRANULARITY_FREQUENCIES`: pandas resample strings directos (`"W-MON"`)
- `GRANULARITY_PLANNING_KEYS`: claves internas de planning_core (`"W"` → `"W-MON"` via `FREQ_MAP`)

### D11. `central_location()` usa inferencia heuristica

**Tipo**: semantica/servicios
**Prioridad**: media
**Estado**: ✅ Resuelto por D03 — 2026-03-24

`central_location()` lee primero `location_model.central_location` del manifest. Como el manifest ya tiene este campo explicito, la heuristica (inferencia desde `purchase_orders` o `internal_transfers`) nunca se invoca en condiciones normales. El codigo de fallback permanece como salvaguarda defensiva.

### D12. Parametros del simulador definidos pero no usados

**Tipo**: configuracion
**Prioridad**: media
**Estado**: ✅ Resuelto — 2026-03-24

Eliminados de `apps/simulator/config.py`:
- `STOCKOUT_RECORD_PROBABILITY = 0.3` (constante top-level huerfana)
- `"stockouts_per_abc"` en `_INDUSTRIAL` y `_RETAIL`
- `"stockout_duration"` en `_INDUSTRIAL` y `_RETAIL`
- `STOCKOUTS_PER_ABC` y `STOCKOUT_DURATION` (exports de esas claves)

Los stockouts en el simulador son emergentes (el stock llega a cero por demanda), no programados. Esta decision queda documentada como deliberada. 35/35 tests pasan tras la limpieza.

### D13. El modelo permite OCs multi-linea pero el generador no las produce

**Tipo**: modelo/simulacion
**Prioridad**: media
**Estado**: ✅ Cerrado como simplificacion deliberada — 2026-03-24

El generador produce 1 SKU por OC (`po_line_id = f"{po_id}-L01"`) de forma intencional. El modelo E/R soporta multi-linea para compatibilidad futura con datos reales, pero el simulador no requiere esa complejidad para los casos de uso actuales (forecasting + clasificacion por SKU). Las OCs multi-linea quedan para una fase de integracion con datos reales de ERP.

### D14. `classify_catalog()` tiene costo relevante y la API no cachea

**Tipo**: performance  
**Prioridad**: media

**Evidencia**

- La clasificacion masiva sobre el dataset actual tarda varios segundos.
- La UI usa cache (`@st.cache_data`), pero la API no.

**Impacto**

- endpoints de clasificacion pueden degradarse a medida que crece el catalogo

**Accion sugerida**

- introducir cache o materializacion para resultados derivados

### D15. Falta una capa formal para artefactos derivados persistidos

**Tipo**: arquitectura/producto  
**Prioridad**: media

**Evidencia**

- El repo distingue bien entre canonico y derivado a nivel conceptual.
- Pero no existe una capa persistida o contrato formal para:
  - clasificaciones
  - quality reports
  - forecasts
  - resultados de backtest

**Impacto**

- todo se recalcula en memoria
- no hay trazabilidad de corridas analiticas

**Accion sugerida**

- definir si los derivados viven como:
  - vistas on-demand
  - archivos en `output/derived/`
  - o una capa semantica separada

### D16. Falta estructura reproducible de trabajo analitico experimental

**Tipo**: operacion/repositorio  
**Prioridad**: baja

**Evidencia**

- no hay `notebooks/`, `experiments/` ni `scripts/` del dominio

**Impacto**

- las pruebas exploratorias futuras tienden a quedar dispersas

**Accion sugerida**

- si el repo va a seguir como laboratorio, conviene definir una convención minima

## Orden recomendado de resolucion

### Bloque 1. Alinear realidad del repo

1. ✅ `D01` — resuelto 2026-03-24
2. ✅ `D02` — resuelto 2026-03-24
3. ✅ `D03` — resuelto 2026-03-24

### Bloque 2. Fijar semantica antes de crecer

4. ✅ `D04` — resuelto 2026-03-24
5. ✅ `D05` — resuelto 2026-03-24
6. ✅ `D10` — resuelto 2026-03-24
7. ✅ `D11` — resuelto por D03, 2026-03-24

### Bloque 3. Cerrar huecos estructurales

8. ~~`D08`~~ — pendiente (validation.py)
9. ~~`D09`~~ — pendiente (tests por capa)
10. ✅ `D12` — resuelto 2026-03-24
11. ✅ `D13` — cerrado como simplificacion deliberada 2026-03-24

### Bloque 4. Habilitar forecast productizable

12. ✅ `D06` — resuelto (parcial: falta API + UI) 2026-03-24
13. ✅ `D07` — resuelto 2026-03-24
14. ~~`D14`~~ — pendiente (cache API)
15. ~~`D15`~~ — pendiente (artefactos derivados)

### Bloque 5. Mejoras operativas

16. ✅ `D16` — resuelto 2026-03-24 (creados `notebooks/` y `experiments/`)

## Preguntas de diseño que conviene responder antes de corregir deuda fuerte

### Q1. Semantica de clasificacion

Respondida para este repo:

- la clasificacion oficial queda por `SKU` agregado de red
- `SKU + location` se deja para otro piloto

### Q2. Granularidad oficial

La clasificacion por defecto debe usar:

- una granularidad fija global
- granularidad automatica por SKU
- o granularidad fija por caso de uso

### Q3. Nodo central

Recomendacion pragmatica:

- resolver primero con metadata explicita en el manifest
- dejar tabla maestra de locations para una etapa posterior donde ya exista una capa semantica mas formal

### Q4. Integracion de forecasting

La primera integracion de forecast debe llegar primero a:

- `planning_core` solamente
- `planning_core + API`
- o `planning_core + API + UI`

### Q5. Nivel de health check deseado

La v1 de validaciones debe ser:

- minima pero ejecutable en cada corrida
- o mas completa aunque sea mas lenta

## Criterio para ir resolviendo uno por uno

Para cada item conviene mantener este formato:

1. fijar la decision de diseno si aplica
2. corregir documentacion o codigo
3. agregar test o validacion de regresion
4. dejar explicitado el cambio en este backlog o en el doc correspondiente
