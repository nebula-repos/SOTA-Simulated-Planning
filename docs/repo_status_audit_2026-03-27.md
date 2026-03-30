# Auditoría de Estado del Repo

Fecha: `2026-03-27`

## Objetivo

Separar tres cosas que hoy están mezcladas en la documentación:

1. Deuda técnica realmente vigente
2. Deuda ya resuelta en código pero aún reportada como pendiente
3. Roadmap recomendado para ordenar el siguiente tramo de trabajo

---

## Resumen ejecutivo

El repositorio sí está funcional en las fases principales del pipeline: simulación, clasificación, forecasting, safety stock, diagnóstico por SKU y health report de catálogo. La mayor debilidad actual no es falta masiva de implementación, sino desalineación entre código, tests y documentación.

En particular:

- El registro de deuda técnica contiene varios ítems marcados como vigentes que ya fueron resueltos en código.
- La documentación de estado declara "`170 tests`, `100% passing`", pero la suite actual no está verde.
- La API y el README no están perfectamente alineados: el README declara cobertura de inventario por SKU, pero la API pública actual expone principalmente catálogo, clasificación y forecast.

---

## 1. Deuda real vigente

### Alta prioridad

#### A1. Estado del repo mal documentado

La documentación de estado no representa la realidad actual del código.

Evidencia:

- [README.md](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/README.md#L41) afirma `170 tests unitarios e integración, 100% passing`
- [docs/forecasting_models_plan.md](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/docs/forecasting_models_plan.md#L44) repite `170 tests ... 100% passing`
- La suite actual falla en [tests/test_backtest_selector.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/tests/test_backtest_selector.py#L154)

Observación local:

- `python3 -m pytest -x -q` falla en `test_intermittent_series_uses_croston_or_adida`
- El test espera `CrostonSBA` o `ADIDA`, pero el selector ahora puede devolver `Ensemble`

Impacto:

- La documentación pierde credibilidad
- Cuesta distinguir regresiones reales de cambios funcionales no reflejados en tests/docs

Acción recomendada:

- Corregir el test o redefinir la regla de negocio del selector
- Actualizar README y plan de forecasting con el estado real de la suite

#### A2. Validación de datos sigue siendo débil

La deuda D08 sigue vigente.

Evidencia:

- [planning_core/validation.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/validation.py#L4) sólo valida duplicados, negativos y transfers abiertos sin `receipt_date`
- [docs/technical_debt_register.md](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/docs/technical_debt_register.md#L55) documenta correctamente el gap

Faltan todavía:

- FK checks
- conciliación entre tablas
- reglas temporales tipo receipt-before-order
- locations válidas
- controles de consistencia más cercanos a `docs/data_health_checks.md`

Acción recomendada:

- Implementar una v1 de integridad relacional antes de seguir agregando features

#### A3. Cobertura de tests incompleta por capa

La deuda D09 no está bien descrita, pero el problema de fondo sigue vigente.

Evidencia:

- Sí existen tests para services y diagnostics en [tests/test_services.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/tests/test_services.py#L80) y [tests/test_diagnostics.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/tests/test_diagnostics.py#L333)
- No encontré tests de API con `TestClient`
- No encontré tests directos de `planning_core/classification.py`
- No encontré tests de `planning_core/validation.py`

Impacto:

- El statement "sin tests para services/classification/..." es falso en parte
- Pero la cobertura sigue siendo dispareja y deja zonas críticas sin protección

Acción recomendada:

- Reescribir D09 con un inventario real de cobertura por capa
- Priorizar tests de API, validation y clasificación pura

### Media prioridad

#### M1. `services.py` sigue acoplado al stack de forecasting en import-time

La deuda D22 sigue vigente.

Evidencia:

- [planning_core/services.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/services.py#L16) importa `planning_core.forecasting.selector` a nivel módulo

Problema:

- `PlanningService` arrastra dependencias pesadas aunque no se use forecasting
- La nota `lazy-loaded per call` del comentario no es cierta

Acción recomendada:

- Mover el import dentro de `sku_forecast()`
- O introducir un bridge explícito para la capa forecasting

#### M2. La API no refleja completamente lo que promete el README

Hay una brecha entre la declaración de producto y la superficie pública real.

Evidencia:

- [README.md](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/README.md#L40) afirma endpoints para forecast e inventario por SKU
- [apps/api/main.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/apps/api/main.py#L161) expone hasta `/sku/{sku}/forecast`
- El archivo termina en 201 líneas y no incluye endpoints públicos para `sku_inventory_params`, `sku_safety_stock` o `catalog_health_report`

Acción recomendada:

- Elegir una de dos opciones:
- ampliar la API para inventario/health
- o recortar el claim del README

#### M3. `classify_catalog()` no está cacheado en la API

La deuda D14 sigue vigente.

Evidencia:

- [apps/api/main.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/apps/api/main.py#L77) llama directo a `service.classify_catalog(...)`
- La UI sí cachea clasificación en [apps/viz/app.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/apps/viz/app.py#L1302)

Acción recomendada:

- agregar caché simple en memoria o materialización liviana

#### M4. Falta capa formal de artefactos derivados

La deuda D15 sigue vigente.

Evidencia:

- [docs/technical_debt_register.md](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/docs/technical_debt_register.md#L98) sigue describiendo correctamente el problema
- Existen stores de evaluación de forecasting, pero no un contrato coherente para clasificaciones, diagnósticos, health reports o artefactos de serving

Acción recomendada:

- Definir si los derivados viven en `output/derived/`, en un run store unificado o como vistas on-demand

### Baja prioridad

#### B1. Métricas operacionales para intermittent/lumpy siguen faltando

La deuda D18 sigue vigente.

Evidencia:

- [docs/technical_debt_register.md](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/docs/technical_debt_register.md#L110)

Comentario:

- El forecast sí mejoró respecto a lo documentado, pero Fill Rate y CSL alcanzado siguen sin aparecer como parte del backtest operativo

#### B2. Falta notebook reproducible del sweep

La deuda D21 sigue vigente.

Evidencia:

- [docs/technical_debt_register.md](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/docs/technical_debt_register.md#L143)
- `notebooks/03_param_sweep_analysis.ipynb` no existe

#### B3. Métricas agregadas de calidad de forecast no están realmente expuestas como dashboard de catálogo

La deuda D33 parece seguir vigente en espíritu.

Evidencia:

- La UI sí muestra KPIs por SKU como MASE y WAPE en [apps/viz/app.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/apps/viz/app.py#L2066)
- No encontré un dashboard agregado de calidad del catálogo completo en la UI

---

## 2. Deuda resuelta pero mal documentada

Los siguientes ítems aparecen todavía como vigentes en el registro, pero ya no representan el estado real del código.

### R1. D20 ya está resuelta

Evidencia:

- [planning_core/services.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/services.py#L29) define `_h_from_lead_time`
- [planning_core/services.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/services.py#L625) deriva `h` desde `lead_time_days` cuando no se entrega explícitamente

Conclusión:

- El registro de deuda sigue diciendo "`h` fijo para todo el catálogo", pero eso ya no es cierto

### R2. D23 ya está resuelta

Evidencia:

- [planning_core/services.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/services.py#L909) preagrupa `transactions`
- [planning_core/services.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/services.py#L973) usa `tx_by_sku.get(...)`

Conclusión:

- El N+1 principal de `catalog_health_report` ya fue atacado

### R3. D24 ya está resuelta

Evidencia:

- [planning_core/classification.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/classification.py#L1074) usa `transactions.groupby("sku")` antes del loop

### R4. D26 y D27 ya están resueltas

Evidencia:

- [tests/test_diagnostics.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/tests/test_diagnostics.py#L1) declara cobertura explícita de diagnostics y `catalog_health_report`
- [tests/test_diagnostics.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/tests/test_diagnostics.py#L333) contiene la suite de `catalog_health_report`

### R5. D28 ya está resuelta

Evidencia:

- [planning_core/inventory/diagnostics.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/inventory/diagnostics.py#L60) define constantes nombradas para sentinels

### R6. D29 ya no corresponde al estado actual

Evidencia:

- No encontré ocurrencias de `use_container_width=True` en `apps/viz/app.py`

### R7. D30 ya está resuelta

Evidencia:

- [apps/viz/app.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/apps/viz/app.py#L2788) usa `ttl=1800`, no `300`

### R8. D31 está obsoleta como descripción

Evidencia:

- [planning_core/forecasting/selector.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/forecasting/selector.py#L29) ya incorpora RMSSE tiebreak y filtro de sesgo
- [planning_core/forecasting/selector.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/forecasting/selector.py#L368) implementa selección multi-criterio

Conclusión:

- La selección ya no usa "solo MASE", aunque MASE siga siendo la métrica primaria

### R9. D32 está parcialmente obsoleta

Evidencia:

- [planning_core/forecasting/selector.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/forecasting/selector.py#L34) ya implementa ensemble top-k
- [planning_core/forecasting/selector.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/forecasting/selector.py#L40) ya aplica corrección de sesgo

Conclusión:

- El problema "no se ha explorado ensemble / post-procesado" ya no es cierto
- Lo que sigue pendiente es validar empíricamente el efecto y estabilizar tests/reglas

### R10. D25 parece ya resuelta

Evidencia:

- No encontré `sku_catalog_row` en [planning_core/services.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/planning_core/services.py)

---

## 3. Roadmap recomendado por prioridad

### Prioridad 1. Recuperar consistencia del repo

Objetivo:

- que código, tests y documentación vuelvan a contar la misma historia

Acciones:

- Resolver la falla actual de tests en [tests/test_backtest_selector.py](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/tests/test_backtest_selector.py#L154)
- Actualizar [README.md](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/README.md) y [docs/forecasting_models_plan.md](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/docs/forecasting_models_plan.md) con:
- número real de tests o wording no frágil
- estado real de la suite
- comportamiento actual del selector
- Limpiar [docs/technical_debt_register.md](/Users/mtombolini-vr/Desktop/SOTA%20/SOTA-Simulated-Planning/docs/technical_debt_register.md) eliminando deuda ya resuelta

### Prioridad 2. Cerrar la deuda estructural de plataforma

Objetivo:

- bajar acoplamiento y mejorar estabilidad operativa

Acciones:

- Resolver D22 con import lazy real
- Resolver D14 con caché en API
- Definir D15: estrategia formal para artefactos derivados

### Prioridad 3. Cerrar la brecha entre producto declarado y superficie expuesta

Objetivo:

- que la API pública sea coherente con la propuesta del repo

Acciones:

- o agregar endpoints de inventario y health
- o ajustar el README para no prometer una API más amplia que la implementada

### Prioridad 4. Endurecer calidad de datos y cobertura

Objetivo:

- reducir riesgo silencioso antes de agregar más features

Acciones:

- ampliar `validation.py`
- agregar tests de API
- agregar tests de clasificación pura
- agregar tests dedicados de validación

### Prioridad 5. Forecasting y analítica de segunda capa

Objetivo:

- mejorar observabilidad y calidad operacional, no sólo exactitud estadística

Acciones:

- implementar Fill Rate / CSL alcanzado para intermittent y lumpy
- exponer métricas agregadas de calidad en UI
- crear el notebook reproducible del sweep

---

## 4. Qué considero implementado hoy

Con base en código y tests, considero implementado y utilizable:

- simulación del dataset canónico
- clasificación SB + ABC/XYZ + censura
- forecast automático por SKU
- evaluación batch y comparación de runs
- safety stock y parámetros de inventario por SKU
- diagnóstico de salud por SKU
- `catalog_health_report`
- UI Streamlit funcional para exploración y demo

Considero no implementado o no cerrado:

- motor de recomendación de compra Fase 5
- Prophet / NeuralProphet
- capa sólida de validación avanzada
- dashboard agregado de calidad de forecast
- API pública completa de inventario/health

---

## 5. Recomendación de gestión

No seguir agregando funcionalidad nueva antes de cerrar tres cosas:

1. suite de tests en verde
2. debt register depurado
3. README/API/roadmap alineados con el estado real

Ese trabajo no es cosmético. Hoy reduce confusión, baja costo de mantenimiento y evita seguir tomando decisiones sobre documentación que ya no representa al sistema.
