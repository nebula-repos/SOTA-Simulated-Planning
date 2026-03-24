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

| ID | Prioridad | Tipo | Resumen |
|---|---|---|---|
| D01 | Alta | Documental | `README.md` no refleja el estado real del repo |
| D02 | Alta | Documental | Documentacion de forecasting y metricas quedo desfasada respecto al codigo |
| D03 | Alta | Semantica | El nodo central y las locations no estan modelados explicitamente como entidad/configuracion |
| D04 | Alta | Semantica | La clasificacion masiva depende de una granularidad global que altera fuertemente los resultados |
| D05 | Alta | Semantica | La clasificacion usa demanda atendida y no incorpora censura por stockout en el pipeline principal |
| D06 | Alta | Integracion | El modulo de forecasting existe pero no esta integrado a `planning_core`, API ni UI |
| D07 | Alta | Integracion | Faltan `backtest.py` y `selector.py`, aunque son piezas centrales del roadmap declarado |
| D08 | Alta | Validacion | `planning_core/validation.py` cubre solo chequeos basicos y esta muy por debajo de `docs/data_health_checks.md` |
| D09 | Alta | Testing | No hay tests para simulador, repository, services, clasificacion, preprocessing, API o UI |
| D10 | Media | Semantica | Inconsistencia de frecuencias semanales (`W` vs `W-MON`) entre capas |
| D11 | Media | Semantica | `central_location()` se infiere heuristica y no desde un contrato explicito |
| D12 | Media | Configuracion | Hay parametros del simulador definidos pero no usados |
| D13 | Media | Modelo | El esquema permite OCs multi-linea, pero el generador siempre produce 1 linea por OC |
| D14 | Media | Performance | `classify_catalog()` recomputa sobre todo el catalogo y en API no hay cache |
| D15 | Media | Producto | No existe capa formal para resultados derivados persistidos |
| D16 | Baja | Operacion | No hay estructura de `notebooks/`, `experiments/` o `scripts/` para trabajo reproducible de analitica |

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

**Evidencia**

- [docs/forecasting_models_plan.md](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/docs/forecasting_models_plan.md) sigue marcando `planning_core/forecasting/` como "`por implementar`".
- [docs/models/evaluation_metrics.md](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/docs/models/evaluation_metrics.md) marca `planning_core/forecasting/metrics.py` como "`por implementar`".
- En codigo ya existen:
  - [planning_core/forecasting/metrics.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/forecasting/metrics.py)
  - [planning_core/forecasting/models/naive.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/forecasting/models/naive.py)
  - [planning_core/forecasting/models/ets.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/forecasting/models/ets.py)
  - [planning_core/forecasting/models/sba.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/forecasting/models/sba.py)

**Impacto**

- falsa percepcion de ausencia total de forecasting
- dificulta priorizar lo que realmente falta: integracion, backtest y selector

**Accion sugerida**

- actualizar los docs de forecasting para marcar:
  - implementado
  - parcialmente implementado
  - pendiente

### D03. Nodo central y universo de locations no estan modelados explicitamente

**Tipo**: semantica/modelado  
**Prioridad**: alta

**Evidencia**

- El dataset real tiene 6 locations efectivas, incluyendo `CD Santiago`.
- El manifest en [output/dataset_manifest.json](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/output/dataset_manifest.json) lista solo las 5 sucursales del perfil y deja al central fuera de `locations`.
- La semantica del nodo central esta repartida entre:
  - [apps/simulator/config.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/apps/simulator/config.py)
  - [docs/business_logic_simulation.md](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/docs/business_logic_simulation.md)
  - inferencia en [planning_core/services.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/services.py)

**Impacto**

- el contrato de locations es ambiguo
- UI/API deben inferir comportamiento del nodo central indirectamente
- complica cualquier futura generalizacion a multiempresa o red multiroles

**Accion sugerida**

- definir una representacion explicita de locations/nodos en metadata o tabla maestra
- distinguir:
  - location list operativa completa
  - central location
  - capabilities por nodo

**Decision actual**

- para este repo se resuelve primero con metadata explicita en `dataset_manifest.json`
- una tabla maestra de locations queda para una etapa posterior

**Estado**

- implementado en codigo/generador
- pendiente regenerar `output/` para persistir el manifest nuevo en el dataset actual
- pendiente revisar visualmente y validar que el manifest regenerado refleje correctamente `all_locations`, `branch_locations`, `central_location` y `classification`

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

**Evidencia**

- [planning_core/preprocessing.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/preprocessing.py) implementa deteccion de demanda censurada.
- [planning_core/services.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/services.py) expone `sku_censored_mask()`.
- Pero `classify_catalog()` y `classify_single_sku()` no usan esa informacion.

**Impacto**

- series con stockout pueden quedar mal clasificadas
- cualquier futuro forecast entrenado sobre `transactions` puede subestimar demanda estructuralmente

**Accion sugerida**

- definir si la clasificacion debe operar sobre:
  - demanda observada
  - demanda observada con flags de censura
  - o una serie ajustada

**Decision pendiente**

- resuelta para esta etapa:
  - clasificar sobre demanda observada
  - calcular censura por separado
  - exponer flags/resumen de censura
  - penalizar `quality_score`
  - dejar cualquier reconstruccion de demanda para una fase posterior de forecasting

**Estado**

- implementado en codigo, UI y servicios
- pendiente regenerar dataset y hacer validacion final sobre el `output/` persistido
- pendiente revisar visualmente en la UI que los marcadores de `sin venta por stockout` se comporten como se espera

### D06. El modulo de forecasting existe pero no esta integrado al flujo principal

**Tipo**: integracion/producto  
**Prioridad**: alta

**Evidencia**

- Existen wrappers funcionales en [planning_core/forecasting/models/](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/forecasting/models).
- No hay uso de esos modelos en:
  - [planning_core/services.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/services.py)
  - [apps/api/main.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/apps/api/main.py)
  - [apps/viz/app.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/apps/viz/app.py)

**Impacto**

- forecasting existe solo como libreria interna + tests
- no hay capacidad visible end-to-end

**Accion sugerida**

- definir una primera interfaz oficial de forecast en `planning_core`
- luego exponerla en API/UI

### D07. Faltan `backtest.py` y `selector.py`

**Tipo**: integracion/arquitectura  
**Prioridad**: alta

**Evidencia**

- [docs/forecasting_models_plan.md](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/docs/forecasting_models_plan.md) los define como piezas centrales.
- El arbol real de [planning_core/forecasting/](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/forecasting) no contiene esos modulos.

**Impacto**

- no existe seleccion empirica de modelos
- tampoco hay criterio sistematico para elegir el forecast productivo por SKU

**Accion sugerida**

- implementar primero `backtest.py`
- despues `selector.py`

**Dependencia**

- D04 y D05 deberian resolverse o al menos quedar fijados conceptualmente antes

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

**Evidencia**

- [planning_core/classification.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/classification.py) usa `W-MON`.
- [apps/viz/app.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/apps/viz/app.py) agrega semanal con `W`.

**Impacto**

- mismas series pueden verse etiquetadas o agrupadas distinto segun capa
- complica comparaciones entre UI y analitica

**Accion sugerida**

- normalizar una sola convencion semanal en todo el repo

### D11. `central_location()` usa inferencia heuristica

**Tipo**: semantica/servicios  
**Prioridad**: media

**Evidencia**

- [planning_core/services.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/planning_core/services.py) infiere el nodo central desde `purchase_orders.destination_location` o `internal_transfers.source_location`.

**Impacto**

- funciona para el dataset actual, pero es fragil si aparecen modelos mixtos
- mezcla deduccion de negocio con lectura operativa

**Accion sugerida**

- leer el central desde manifest o metadata explicita

### D12. Parametros del simulador definidos pero no usados

**Tipo**: configuracion  
**Prioridad**: media

**Evidencia**

- En [apps/simulator/config.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/apps/simulator/config.py) existen:
  - `STOCKOUT_RECORD_PROBABILITY`
  - `stockouts_per_abc`
  - `stockout_duration`
- No aparecen en la logica efectiva de [apps/simulator/generate_canonical_dataset.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/apps/simulator/generate_canonical_dataset.py).

**Impacto**

- configuracion engañosa
- sugiere capacidades de simulacion que no estan activas

**Accion sugerida**

- o implementarlos
- o eliminarlos/documentarlos como descartados

### D13. El modelo permite OCs multi-linea pero el generador no las produce

**Tipo**: modelo/simulacion  
**Prioridad**: media

**Evidencia**

- [docs/output_er_model.md](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/docs/output_er_model.md) explicita que el modelo permite varias lineas por OC.
- [apps/simulator/generate_canonical_dataset.py](/Users/mtombolini-vr/Desktop/SOTA /SOTA-Simulated-Planning/apps/simulator/generate_canonical_dataset.py) genera `po_line_id = f"{po_id}-L01"` y 1 SKU por OC.

**Impacto**

- el esquema relacional es mas rico que los escenarios realmente cubiertos por el simulador
- no se ejercitan casos multi-linea en servicios ni validaciones

**Accion sugerida**

- decidir si se mantiene como simplificacion deliberada o se enriquece el generador

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

1. `D01`
2. `D02`
3. `D03`

### Bloque 2. Fijar semantica antes de crecer

4. `D04`
5. `D05`
6. `D10`
7. `D11`

### Bloque 3. Cerrar huecos estructurales

8. `D08`
9. `D09`
10. `D12`
11. `D13`

### Bloque 4. Habilitar forecast productizable

12. `D06`
13. `D07`
14. `D14`
15. `D15`

### Bloque 5. Mejoras operativas

16. `D16`

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
