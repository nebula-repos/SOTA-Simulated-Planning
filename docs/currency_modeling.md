# Modelado de Moneda

## Objetivo

Documentar como se modela la moneda en este proyecto hoy y como deberia evolucionar hacia un contexto multiempresa.

## Estado actual

En el dataset simulado actual, la moneda se configura a nivel general del dataset.

Eso significa:

- no se define por producto
- no se define por proveedor
- no vive como tabla del canonico
- se exporta como metadata del dataset

Hoy esa metadata se publica en `output/dataset_manifest.json`.

Ademas:

- `purchase_orders` incluye `currency` a nivel documental
- la UI toma la moneda desde el manifest

## Por que hoy esta bien asi

En la simulacion actual:

- todo el dataset opera en una sola moneda
- los precios y costos fueron generados bajo un mismo contexto monetario
- no hay necesidad de conversion entre monedas
- el objetivo es mantener el canonico simple

Por eso, modelar moneda por producto o por proveedor agregaria complejidad sin aportar valor real al caso actual.

## Que no conviene hacer

### No modelar moneda por producto

En general, un SKU no "tiene" una moneda propia.

Lo normal es que:

- el producto tenga costo y precio
- la moneda la defina el contexto comercial o documental

Poner moneda por producto suele generar ruido y malos joins.

### No asumir moneda por proveedor dentro del canonico operacional

Un proveedor puede transar en una o mas monedas, pero eso no significa que cada fila operativa del canonico deba depender de una tabla monetaria compleja desde el inicio.

Si se necesita modelar eso, debe hacerse de forma explicita y documental.

## Recomendacion de modelado

### Nivel 1. Dataset o tenant

Debe existir una moneda base del dataset o de la empresa.

Ejemplo:

- `currency = CLP`

Esto sirve para:

- UI
- reporting basico
- validaciones
- interpretacion consistente de montos

### Nivel 2. Documento de compra

Si el negocio empieza a operar con multiples monedas, la moneda correcta para modelar primero es la del documento.

En este proyecto, eso significa:

- `purchase_orders.currency`

Porque la orden de compra si es un documento comercial que puede emitirse en una moneda especifica.

## Evolucion recomendada para SaaS

Si el producto evoluciona a multiempresa, el modelo recomendado es:

### Configuracion por tenant

`tenants`

- `tenant_id`
- `tenant_name`
- `base_currency`

### Moneda documental

`purchase_orders`

- `currency`

### Opcional: maestro de proveedores

Si mas adelante hace falta modelar acuerdos o monedas habituales por proveedor, eso deberia vivir en una entidad maestra aparte, no en el canonico operacional minimo.

Ejemplo:

`suppliers`

- `supplier_id`
- `supplier_name`
- `default_currency`

Pero eso deberia agregarse solo cuando exista una necesidad real.

## Regla practica

La regla general recomendada es:

- moneda general por dataset o tenant
- moneda documental por orden o documento
- no moneda por producto

## Implicancia para este repo

Hoy el repo usa una moneda unica por dataset y esa decision es correcta para la simulacion actual.

Si el dia de manana se incorporan:

- multiples empresas
- proveedores internacionales con monedas distintas
- conversiones FX

entonces el siguiente paso correcto no es mover la moneda al SKU, sino fortalecer:

- metadata del tenant
- moneda por documento
- eventualmente tablas maestras de proveedores y FX
