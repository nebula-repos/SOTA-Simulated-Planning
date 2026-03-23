# Modelo Canonico para SaaS Multiempresa

## Objetivo

Explicar como modelar un sistema de planning que:

- usa un modelo canonico comun
- recibe empresas con operacion distinta
- mantiene consistencia operacional
- puede escalar a un SaaS multi-tenant

Este documento usa como ejemplo el caso actual del repo:

- compra centralizada
- transferencias internas
- nodo central hibrido que abastece y tambien vende

## La idea clave

No conviene crear un modelo totalmente distinto por empresa.

Tampoco conviene forzar a todas las empresas a operar exactamente igual.

La estrategia correcta para un SaaS suele ser:

1. definir un **canonico comun**
2. definir **capacidades operacionales** que algunas empresas usan y otras no
3. mapear cada empresa al canonico
4. activar solo las capacidades que aplican

En otras palabras:

- mismo lenguaje de datos
- distinta configuracion operativa

## Que es el canonico

El canonico es el contrato comun minimo para representar el negocio.

En este proyecto, el canonico operacional base es:

- `product_catalog`
- `transactions`
- `inventory_snapshot`
- `internal_transfers`
- `purchase_orders`
- `purchase_order_lines`
- `purchase_receipts`

Ese canonico no dice que todas las empresas hagan exactamente lo mismo.
Solo dice como se representa lo que efectivamente ocurre.

## Que cambia entre empresas

Lo que cambia no deberia ser la forma de la tabla principal, sino:

- que nodos existen
- que roles cumplen esos nodos
- si hay compra centralizada o directa
- si hay transferencias internas
- si un centro de distribucion vende o no
- si hay cross-docking
- si hay manufactura o solo reventa
- que lead times aplican
- que politicas de reabastecimiento se usan

Eso es comportamiento, no estructura base.

## Como modelarlo bien

### 1. Mantener un canonico comun

Ejemplo:

- `transactions` siempre significa salida real atendida
- `purchase_receipts` siempre significa recepcion real de compra
- `internal_transfers` siempre significa traslado interno real
- `inventory_snapshot` siempre significa posicion diaria observada

Eso no cambia por tenant.

### 2. Modelar nodos, no solo sucursales

El error comun es pensar solo en "sucursal".
Para un SaaS mas general, conviene pensar en **nodos operativos**.

Tipos posibles:

- tienda
- sucursal
- centro de distribucion
- bodega regional
- planta
- dark store
- hub logistico

Y ademas cada nodo puede tener uno o mas roles:

- venta
- almacenamiento
- compra
- transferencia
- fulfillment

## Caso actual: nodo hibrido

En el modelo actual:

- `CD Santiago` compra a proveedor
- recibe las compras
- despacha transferencias a sucursales
- y ademas puede vender directo

Eso lo convierte en nodo **hibrido**.

La conclusion importante es:

- el rol de un nodo no debe inferirse solo por su nombre
- debe inferirse por su comportamiento o por una configuracion explicita

## Patron recomendado para SaaS

Si esto se lleva a un producto multiempresa, una forma robusta es agregar una capa de configuracion por tenant:

### `tenants`

- `tenant_id`
- `tenant_name`
- `timezone`
- `currency`
- `status`

### `locations`

- `tenant_id`
- `location_id`
- `location_name`
- `location_type`
- `country`
- `city`
- `active_flag`

### `location_capabilities`

- `tenant_id`
- `location_id`
- `can_sell`
- `can_purchase`
- `can_receive_purchase`
- `can_transfer_in`
- `can_transfer_out`
- `can_hold_inventory`

### `supply_policies`

- `tenant_id`
- `sku` o familia
- `supply_mode`
- `source_location`
- `supplier`
- `lead_time_policy`
- `review_policy`

Con eso, el canonico no cambia, pero el comportamiento si.

## Ejemplo de empresas distintas sobre el mismo canonico

### Empresa A: compra centralizada con CD hibrido

- compra en CD
- CD vende
- CD abastece sucursales
- usa `internal_transfers`

### Empresa B: compra directa por sucursal

- cada sucursal compra a proveedor
- no usa transferencias internas
- `purchase_receipts.location = sucursal`

### Empresa C: retail con CD no vendedor

- compra en CD
- CD solo almacena
- ventas solo en tiendas
- `transactions` nunca aparece en el CD

### Empresa D: modelo mixto

- algunos SKUs se compran centralizados
- otros se compran directos
- algunas tiendas reciben transferencias
- otras compran directas

Todo eso puede vivir sobre el mismo canonico si la capa de configuracion es buena.

## Lo que no conviene hacer

### 1. No crear tablas distintas por empresa

Mala idea:

- `transactions_empresa_a`
- `transactions_empresa_b`

Eso destruye escalabilidad y complica todo el producto.

### 2. No codificar reglas de negocio dentro del nombre de la location

Mala idea:

- asumir que si una location parte con `CD` entonces nunca vende

El caso actual demuestra por que:

- `CD Santiago` si puede vender

### 3. No mezclar canonico con metricas derivadas

Mala idea:

- guardar forecast, cobertura, ADI, ABC o fill rate dentro de las tablas operativas base

Eso debe vivir en una capa analitica separada.

## Capas recomendadas en un SaaS

### Capa 1. Ingestion

Mapea ERP/WMS/POS del cliente al canonico.

### Capa 2. Canonico operacional

Guarda:

- productos
- ventas reales
- inventario
- compras
- recepciones
- transferencias

### Capa 3. Semantica / reglas

Guarda:

- roles de nodos
- politicas de abastecimiento
- lead times
- jerarquias
- configuracion tenant-specific

### Capa 4. Analitica

Calcula:

- forecast
- cobertura
- fill rate
- stockouts inferidos
- alertas
- recomendaciones

### Capa 5. UI / APIs

Consume las capas anteriores sin redefinir la semantica base.

## Como programarlo para que escale

### 1. Todo con `tenant_id`

Toda tabla multiempresa deberia poder particionarse por `tenant_id`.

### 2. Contratos canonicos estables

Los nombres y significados de tablas canonicas deben cambiar poco.

### 3. Reglas parametrizadas

No hardcodear:

- "el CD no vende"
- "todas las empresas tienen transferencias"
- "todas las compras llegan a sucursal"

Eso debe venir de configuracion.

### 4. Separar comportamiento de almacenamiento

El almacenamiento de eventos debe ser estable.
El comportamiento del tenant debe ser configurable.

### 5. Hacer visible el rol del nodo

En UI, APIs y analitica conviene distinguir:

- `Agregado sucursales`
- `Agregado total red`
- `Nodo central`

porque no siempre representan lo mismo.

## Aplicado al caso actual

La evolucion correcta del modelo fue:

1. compra centralizada
2. transferencias internas
3. nodo central hibrido con venta directa

Eso no obligo a romper el canonico.

Lo que cambio fue la operacion representada.

Esa es exactamente la propiedad que interesa conservar en un SaaS:

- mismo contrato
- distinta configuracion
- distinta topologia operacional

## Conclusión

La forma general de escalar este problema a multiples empresas es:

- canonico comun
- nodos operativos con roles
- capacidades configurables por tenant
- metricas y forecast fuera del canonico

El caso de `CD Santiago` muestra por que esto importa:

- un nodo central puede ser solo logístico
- o puede ser hibrido
- y el modelo debe soportar ambos sin rediseñarse por completo
