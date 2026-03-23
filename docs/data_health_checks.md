# Data Health Checks del Modelo Canonico

## Objetivo

Definir un framework completo de controles para validar la salud del dato operacional.

El objetivo no es solo detectar errores tecnicos, sino tambien:

- inconsistencias operacionales
- incongruencias de negocio
- drift o degradacion de calidad
- fallas de mapeo desde ERP/WMS/OMS
- configuraciones invalidas por empresa

Este documento aplica al modelo canonico actual:

- `product_catalog`
- `transactions`
- `inventory_snapshot`
- `internal_transfers`
- `purchase_orders`
- `purchase_order_lines`
- `purchase_receipts`

Y esta pensado para evolucionar desde datos simulados hacia datos reales multiempresa.

## Principios

### 1. El health check no reemplaza el canonico

El canonico sigue siendo solo dato operacional observable.

Los health checks viven en una capa aparte y producen:

- hallazgos
- alertas
- scores
- auditorias

No deben contaminar las tablas canonicas.

### 2. No todo hallazgo es un error

Hay que distinguir entre:

- dato imposible
- dato improbable
- dato incompleto
- dato semanticamente sospechoso

Si no se separan estas categorias, el sistema termina generando ruido y nadie lo confia.

### 3. Los checks deben ser configurables por tenant

No todas las empresas operan igual.

Por eso el motor de checks debe ser comun, pero las reglas deben poder cambiar por empresa:

- nodos que venden o no venden
- compra centralizada o descentralizada
- uso o no de transferencias internas
- calendarios de operacion
- lead times esperados
- tolerancias por categoria o familia

### 4. Debe existir trazabilidad

Cada finding debe responder:

- que regla fallo
- en que tabla
- sobre que llave
- desde cuando ocurre
- con que severidad
- cual es el impacto de negocio

## Niveles de severidad

### `critical`

Rupturas que invalidan el dataset o vuelven imposible la reconciliacion.

Ejemplos:

- claves primarias duplicadas
- foreign keys rotas
- stock negativo donde no se permite
- recepciones mayores al ordenado
- fechas imposibles
- reconciliacion de inventario rota

### `high`

Problemas que no rompen toda la carga, pero hacen que el dato no sea confiable para planning.

Ejemplos:

- huecos temporales relevantes
- locations inexistentes o inactivas
- lead times fuera de rango de forma sistematica
- ventas en nodos que no deberian vender
- compras en nodos que no deberian comprar

### `medium`

Anomalias importantes que deben revisarse, pero pueden ser reales.

Ejemplos:

- spikes extremos de demanda
- saltos grandes de precio o costo
- cambios bruscos en mix por sucursal
- caidas abruptas de cobertura

### `low`

Observaciones o posibles mejoras.

Ejemplos:

- categorias con baja cobertura
- SKUs inactivos por periodos largos
- suppliers casi no usados

## Capas de chequeo

## 1. Ingestion y completitud fisica

Primera barrera. Responde: llego lo que debia llegar.

Checks:

- existencia del archivo o tabla
- fecha/hora de carga
- tamano minimo y maximo esperado
- numero de filas esperado
- hash o version de archivo cuando aplique
- columnas esperadas
- columnas nuevas o faltantes
- tipo de dato por columna
- encoding y parseo correcto

Hallazgos tipicos:

- tabla vacia
- columna renombrada sin aviso
- cambio de tipo `date -> string`
- archivo truncado

## 2. Integridad estructural

Responde: la forma del dato sigue siendo valida.

Checks:

- unicidad de primary keys o llaves naturales
- unicidad de combinaciones que deberian ser unicas
- nulls en campos obligatorios
- dominios validos
- enums permitidos
- precision numerica valida
- fechas parseables
- timestamps con timezone esperada cuando aplique

Ejemplos por tabla:

### `product_catalog`

- `sku` unico
- `unit_price >= 0`
- `unit_cost >= 0`
- `moq >= 1`
- categoria y proveedor no nulos si son obligatorios

### `transactions`

- `date + sku + location` no duplicado si ese es el grano definido
- `quantity > 0`
- `unit_price >= 0`
- `total_amount = quantity * unit_price` o dentro de tolerancia

### `inventory_snapshot`

- `snapshot_date + sku + location` unico
- `on_hand_qty >= 0`
- `on_order_qty >= 0`

### `internal_transfers`

- `transfer_id` unico
- `transfer_qty > 0`
- `source_location != destination_location`

### `purchase_orders`

- `po_id` unico
- `order_date` valida
- `expected_receipt_date >= order_date`

### `purchase_order_lines`

- `po_line_id` unico
- `ordered_qty > 0`
- `unit_cost >= 0`

### `purchase_receipts`

- `receipt_id` unico
- `received_qty > 0`
- `receipt_date >= order_date`

## 3. Integridad relacional

Responde: las tablas siguen conectadas correctamente.

Checks:

- SKUs de transacciones existen en catalogo
- SKUs de snapshots existen en catalogo
- transferencias usan SKUs existentes
- `purchase_order_lines.po_id` existe en `purchase_orders`
- `purchase_receipts.po_id` existe en `purchase_orders`
- `purchase_receipts.po_line_id` existe en `purchase_order_lines`
- locations referenciadas existen en el universo permitido del tenant

Casos criticos:

- venta de SKU inexistente
- recepcion sin OC
- receipt ligado a linea equivocada

## 4. Integridad semantica

Responde: el dato no solo existe, sino que significa lo que dice significar.

Este punto es clave en un SaaS porque muchas fallas no son tecnicas sino de mapeo.

Checks:

- `transactions` representa salidas reales y no forecast, reserva o demanda estimada
- `purchase_receipts` representa recepcion fisica real y no ASN, ETA o recepcion planificada
- `inventory_snapshot` representa posicion observada y no proyeccion
- `internal_transfers` representa movimiento interno real y no solicitud
- el grano temporal de cada tabla es el esperado
- no se mezclan eventos confirmados con eventos planeados en una misma tabla

Señales de mapeo incorrecto:

- ventas con cantidad negativa usadas como devoluciones sin regla explicita
- transacciones con montos pero sin cantidades
- receipts masivos en la misma fecha iguales a la OC original porque se cargo el pedido y no la recepcion
- snapshots reconstruidos con logica distinta entre locations

## 5. Cobertura temporal

Responde: el dataset cubre el horizonte que dice cubrir.

Checks:

- fecha minima y maxima por tabla
- continuidad del calendario
- huecos no explicados por calendario operativo
- snapshots diarios completos donde aplica
- cobertura consistente por `sku/location`
- cobertura consistente por tenant, categoria y location
- no existencia de datos fuera del horizonte permitido

Checks especificos:

- `inventory_snapshot` debe tener continuidad diaria para cada `sku/location` activo
- `transactions` puede no tener todos los dias, pero los huecos deben ser compatibles con calendario y demanda
- `purchase_orders` y `purchase_receipts` no necesitan continuidad diaria, pero si orden temporal consistente

Hallazgos relevantes:

- una location desaparece una semana y luego vuelve
- snapshots faltantes para un SKU activo
- actividad futura por error de timezone o parseo

## 6. Reconciliacion operacional

Es la capa mas importante para planning.

Responde: el inventario se explica por eventos operacionales reales.

### Reconciliacion de `on_hand`

Para cada `sku/location/date`:

`on_hand(t) = on_hand(t-1) + entradas - salidas`

Donde las entradas y salidas dependen del nodo:

- entradas de compra
- recepciones de transferencia
- ventas o consumos
- despachos de transferencia

En el modelo actual:

`delta_on_hand = purchase_receipts + transfer_in - transfer_out - sales`

Checks:

- mismatch absoluto por fila
- mismatch agregado por SKU
- mismatch agregado por location
- mismatch agregado por dia
- porcentaje de filas conciliadas

### Reconciliacion de `on_order`

El stock en orden debe explicarse por aperturas y cierres de supply.

En el modelo actual:

- ordenes de compra abren `on_order`
- recepciones de compra lo cierran

Si a futuro existieran otros estados o supply modes, la formula se ajusta.

Checks:

- `delta_on_order = ordered - received`
- no permitir `on_order < 0`
- no permitir recepciones contra lineas ya cerradas

### Reconciliacion de documentos

Checks:

- suma de lineas coincide con cabecera cuando exista total de cabecera
- receipts no superan ordered_qty por linea
- estado de OC consistente con lo recibido
- transferencias abiertas vs recibidas consistentes con sus fechas

## 7. Reglas de negocio por nodo

Responde: cada location se comporta segun su rol operacional.

Esto es especialmente importante para multiempresa.

Reglas posibles:

- un nodo puede vender o no vender
- un nodo puede comprar o no comprar
- un nodo puede recibir compra o no
- un nodo puede transferir salida o no
- un nodo puede transferir entrada o no
- un nodo puede tener inventario o no

Ejemplos:

- una tienda no deberia emitir compra si el modelo es centralizado
- un CD puede recibir compra y transferir salida
- un nodo hibrido puede recibir compra, transferir y vender

Checks:

- ventas en nodos no habilitados
- compras en nodos no habilitados
- receipts en nodos no habilitados para recepcion
- transferencias con origen o destino no validos

## 8. Calidad de maestros

Responde: los catalogos y dimensiones siguen siendo confiables.

Checks:

- SKUs duplicados con atributos distintos
- mismo SKU con multiples categorias sin regla
- proveedores inconsistentes para un mismo SKU
- cambios extremos de costo o precio maestro
- UOM inconsistente
- MOQ nulo o absurdo
- lead time parametrico faltante o invalido cuando aplica

Checks adicionales utiles:

- porcentaje de SKUs sin categoria
- porcentaje de SKUs sin supplier
- productos activos sin costo
- productos activos sin precio

## 9. Cobertura de negocio

Responde: el dataset representa realmente la operacion y no solo una fraccion accidental.

Checks:

- SKUs del catalogo sin actividad operacional
- SKUs con ventas pero sin snapshots
- SKUs con snapshots pero sin catalogo
- locations configuradas pero nunca usadas
- suppliers sin compras durante ventanas largas
- categorias sin movimiento
- nodos sin actividad por periodos sospechosos

Esto no siempre es un error, pero si un excelente detector de:

- cargas incompletas
- filtros mal aplicados
- joins defectuosos
- problemas de mapeo por empresa

## 10. Plausibilidad de negocio

Responde: aun cuando el dato sea valido, sigue siendo creible.

Checks:

- lead times negativos o demasiado bajos
- lead times excesivos para la industria
- recepciones excesivamente fragmentadas
- compras demasiado frecuentes para el modelo de abastecimiento
- saltos extremos de precio o costo
- OCs repetidas del mismo SKU con spacing improbable
- transferencias masivas incompatibles con la capacidad operativa
- inventario inmovil por periodos excesivos
- inventario siempre en cero para SKUs supuestamente activos

Este bloque es importante porque muchas incongruencias no rompen integridad, pero si rompen realismo.

## 11. Anomalias estadisticas

Responde: hay comportamientos raros que merecen revision.

No todos estos checks deben bloquear la carga.

Checks sugeridos:

- outliers de venta por SKU/location
- cambios bruscos de mix entre sucursales
- picos raros de transferencias
- dias con recepciones anormalmente altas
- caidas abruptas de fill rate
- cambio de tendencia repentino por categoria
- aumento inesperado de nulos
- drift de distribucion en cantidades o precios

Metodos posibles:

- z-score robusto
- IQR
- Hampel
- STL + residuos
- thresholds por percentiles historicos

## 12. Controles especificos por tabla

## `product_catalog`

Minimos:

- PK unica por `sku`
- costo y precio validos
- MOQ valido
- categoria y proveedor con cobertura razonable

Controles avanzados:

- relacion `unit_cost <= unit_price` salvo excepcion declarada
- misma familia de SKU no cambia de categoria sin versionado

## `transactions`

Minimos:

- cantidades positivas
- sin duplicados al grano definido
- location valida
- SKU valido

Controles avanzados:

- ventas en fines de semana compatibles con el calendario del tenant
- precio fuera de banda historica
- montos no conciliados con precio por cantidad

## `inventory_snapshot`

Minimos:

- continuidad diaria
- sin negativos
- sin duplicados

Controles avanzados:

- quiebres prolongados
- inventario inmovil prolongado
- saltos sin evento explicativo

## `internal_transfers`

Minimos:

- origen y destino distintos
- cantidad positiva
- lead time interno no negativo

Controles avanzados:

- transferencias demasiado frecuentes para un SKU
- transferencias entre nodos no permitidos
- transferencia recibida antes de la fecha de despacho

## `purchase_orders`

Minimos:

- `po_id` unico
- fechas validas
- location de destino permitida

Controles avanzados:

- spacing entre OCs para modelos importados
- exceso de OCs abiertas
- frecuencia incoherente con politica de revision

## `purchase_order_lines`

Minimos:

- `ordered_qty > 0`
- `unit_cost >= 0`
- referencia valida a OC y SKU

Controles avanzados:

- misma linea duplicada
- costo fuera de banda para el SKU

## `purchase_receipts`

Minimos:

- `received_qty > 0`
- `receipt_date >= order_date`
- no superar ordered_qty acumulado

Controles avanzados:

- recepciones parcializadas excesivamente
- recepciones demasiado cercanas entre si para lead times de importacion
- receipts en nodo invalido

## 13. Health checks para modelo multiempresa

En un SaaS, el mayor error es asumir una sola logica operativa.

La solucion no es crear checks distintos por empresa, sino separar:

- motor comun de validacion
- configuracion por tenant

La configuracion por tenant deberia incluir al menos:

- calendario operativo
- timezone
- moneda
- locations activas
- capacidades por nodo
- supply mode por familia, SKU o red
- lead times esperados
- tolerancias de inventario
- reglas de negativos
- grano esperado por tabla

Ejemplos:

### Tenant A

- compra centralizada
- CD hibrido
- transferencias internas obligatorias

### Tenant B

- compra descentralizada por sucursal
- no usa transferencias internas

### Tenant C

- CD no vende
- ventas solo en tiendas

El motor de checks es el mismo.
Lo que cambia es la configuracion de reglas.

## 14. Salida esperada del sistema de health checks

Un sistema serio no deberia devolver solo `pass/fail`.

Debe devolver al menos tres niveles de salida:

### 1. Resumen ejecutivo

- estado general
- total de findings
- findings por severidad
- score general
- tablas afectadas

### 2. Findings detallados

Campos sugeridos:

- `check_id`
- `severity`
- `table_name`
- `entity_key`
- `location`
- `sku`
- `date`
- `metric_name`
- `observed_value`
- `expected_value`
- `tolerance`
- `description`
- `business_impact`
- `suggested_action`

### 3. Historico de salud

Para monitorear tendencia:

- findings por dia
- findings por tenant
- findings por tabla
- score historico
- comparacion vs corridas anteriores

## 15. Frecuencia recomendada

### En simulacion o desarrollo

- correr en cada regeneracion del dataset
- bloquear artefactos inconsistentes

### En productivo

- controles estructurales en cada ingestion
- reconciliaciones al cierre de cada carga
- anomalias estadisticas en batch diario
- score historico semanal y mensual

## 16. Orden de implementacion recomendado

### Fase 1. Basicos obligatorios

- ingestion
- esquema
- PK/FK
- nulls
- dominios

### Fase 2. Reconciliacion operacional

- `on_hand`
- `on_order`
- receipts vs ordered
- consistencia de estados documentales

### Fase 3. Cobertura y semantica

- cobertura temporal
- cobertura de negocio
- capacidades por nodo
- validacion semantica de tablas

### Fase 4. Anomalias y monitoreo

- drift
- outliers
- score historico
- dashboards de salud

## 17. Set minimo que siempre deberia bloquear una carga

Aunque el sistema completo sea amplio, hay un nucleo que deberia ser no negociable.

Bloquear si ocurre cualquiera de estos:

- archivos o tablas faltantes
- columnas criticas faltantes
- PK duplicadas
- FK rotas
- cantidades negativas invalidas
- fechas imposibles
- receipts mayores a ordered
- inventario no conciliado
- `on_order` no conciliado
- ventas en nodos invalidos
- compras en nodos invalidos

## 18. Conclusion

Un buen sistema de data health para planning no debe limitarse a validar tipos y duplicados.

Tiene que cubrir cuatro capas al mismo tiempo:

- integridad tecnica
- integridad relacional
- consistencia operacional
- plausibilidad de negocio

Si se hace bien, esta capa permite:

- confiar en el canonico
- detectar errores de integracion temprano
- adaptar el producto a varias empresas sin reescribir la logica
- sostener forecast, replenishment y analitica sobre una base realmente util
