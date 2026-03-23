# Esquema Minimo de Datos de Compras

## Objetivo

Definir el conjunto minimo de tablas para modelar abastecimiento y compras,
calzando con los archivos actuales:

- `output/product_catalog.csv`
- `output/transactions.csv`

La data actual representa `salidas` de inventario por `sku` y `location`.
Para completar el lado de supply, el minimo util es modelar:

1. ordenes de compra
2. lineas de orden de compra
3. recepciones de compra

Con estas 3 tablas ya se puede calcular:

- lead time esperado vs real
- compras abiertas
- fill rate de proveedor
- backlog de recepcion
- entradas de inventario por sucursal
- costo de abastecimiento

## Convencion Operativa

Agrupacion sugerida para el proyecto:

- `salidas`: ventas, consumo, despacho
- `entradas`: recepciones de compra

En el estado actual:

- `output/transactions.csv` = `salidas`

Con el esquema de abajo:

- `purchase_receipts.csv` = `entradas`

## 1. purchase_orders.csv

Cabecera de la orden de compra.
Una OC pertenece a un solo proveedor y a una sola sucursal destino.

Columnas:

- `po_id`: identificador unico de la OC. Ejemplo: `PO-20240315-000123`
- `supplier`: nombre del proveedor. Debe calzar con `product_catalog.supplier`
- `destination_location`: sucursal o bodega destino. Debe calzar con `transactions.location`
- `order_date`: fecha de emision de la OC
- `expected_receipt_date`: fecha esperada de recepcion
- `order_status`: `open`, `partially_received`, `received`, `cancelled`
- `currency`: moneda, por ejemplo `CLP`
- `payment_terms_days`: plazo de pago pactado

Notas:

- `expected_receipt_date` debe derivarse del promedio del proveedor, no del producto
- una OC puede tener varias lineas
- una OC puede recibirse en una o varias recepciones

Ejemplo:

```csv
po_id,supplier,destination_location,order_date,expected_receipt_date,order_status,currency,payment_terms_days
PO-20240315-000123,Maestranza Integral,Santiago,2024-03-15,2024-06-13,received,CLP,30
```

## 2. purchase_order_lines.csv

Detalle de productos pedidos en cada OC.

Columnas:

- `po_id`: FK a `purchase_orders.po_id`
- `po_line_id`: identificador unico de linea. Ejemplo: `PO-20240315-000123-L01`
- `sku`: FK a `product_catalog.sku`
- `ordered_qty`: cantidad ordenada
- `unit_cost`: costo unitario pactado de compra
- `line_amount`: `ordered_qty * unit_cost`
- `moq_applied`: MOQ aplicado al momento de comprar

Notas:

- `sku` debe pertenecer al mismo `supplier` definido en la cabecera de la OC
- `ordered_qty` debiera ser multiplo de `moq_applied` en la mayoria de los casos
- `unit_cost` puede tomar como base `product_catalog.cost`, con ruido o variacion temporal

Ejemplo:

```csv
po_id,po_line_id,sku,ordered_qty,unit_cost,line_amount,moq_applied
PO-20240315-000123,PO-20240315-000123-L01,SKU-00001,10,319405,3194050,5
```

## 3. purchase_receipts.csv

Recepciones efectivas de mercaderia.
Cada fila representa una recepcion de una linea de OC.

Columnas:

- `receipt_id`: identificador unico de recepcion. Ejemplo: `GR-20240614-000455`
- `po_id`: FK a `purchase_orders.po_id`
- `po_line_id`: FK a `purchase_order_lines.po_line_id`
- `sku`: FK a `product_catalog.sku`
- `supplier`: redundancia util para analisis y trazabilidad
- `location`: sucursal o bodega que recibe
- `receipt_date`: fecha efectiva de recepcion
- `received_qty`: cantidad efectivamente recibida
- `unit_cost`: costo unitario recibido
- `total_cost`: `received_qty * unit_cost`
- `receipt_status`: `received`, `partial`, `rejected`

Notas:

- esta tabla representa `entradas`
- permite recepciones parciales sin perder trazabilidad
- el lead time real se calcula como `receipt_date - order_date`

Ejemplo:

```csv
receipt_id,po_id,po_line_id,sku,supplier,location,receipt_date,received_qty,unit_cost,total_cost,receipt_status
GR-20240614-000455,PO-20240315-000123,PO-20240315-000123-L01,SKU-00001,Maestranza Integral,Santiago,2024-06-14,10,319405,3194050,received
```

## Relaciones con la Data Actual

Llaves y reglas de cruce:

- `purchase_order_lines.sku -> product_catalog.sku`
- `purchase_orders.supplier -> product_catalog.supplier`
- `purchase_orders.destination_location -> transactions.location`
- `purchase_receipts.location -> transactions.location`
- `purchase_receipts.sku -> product_catalog.sku`

Consistencias esperadas:

- un `sku` tiene un proveedor principal en `product_catalog`
- una OC no debe mezclar proveedores
- la recepcion debe ocurrir en la misma `destination_location` de la OC
- `receipt_date >= order_date`
- `sum(received_qty por po_line_id) <= ordered_qty`, salvo sobre-recepcion explicita

## Reglas Minimas de Generacion

Si luego generamos data sintetica de compras, sugiero estas reglas:

- una compra se gatilla por quiebre esperado o politica de reposicion
- `expected_receipt_date = order_date + supplier_avg_lead_time_days`
- `receipt_date` se mueve alrededor de la esperada con atraso o adelanto controlado
- `ordered_qty` depende de cobertura objetivo, MOQ y tamaño de lote
- `unit_cost` se mueve alrededor de `product_catalog.cost`
- no todos los pedidos llegan completos; debe existir una fraccion de parciales

## Vista Unificada Recomendada

Si mas adelante quieres un modelo comun de movimientos, la recomendacion es una vista o tabla normalizada:

- `inventory_movements.csv`

Columnas minimas:

- `movement_id`
- `date`
- `sku`
- `location`
- `movement_direction`: `in` o `out`
- `movement_type`: `sale`, `purchase_receipt`, `transfer_in`, `transfer_out`, `adjustment`
- `quantity`
- `unit_cost_or_price`
- `total_amount`
- `reference_id`

Mapeo inicial:

- `transactions.csv` -> `movement_direction = out`, `movement_type = sale`
- `purchase_receipts.csv` -> `movement_direction = in`, `movement_type = purchase_receipt`

## Esquema Minimo Recomendado

Si hubiera que quedarse con lo estrictamente minimo para el siguiente modulo:

1. `purchase_orders.csv`
2. `purchase_order_lines.csv`
3. `purchase_receipts.csv`

Ese es el punto minimo en que compras y abastecimiento quedan modelados con suficiente fidelidad para forecast, reposicion y analitica operacional.
