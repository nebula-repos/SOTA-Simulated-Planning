# Esquema Minimo de Datos de Compras e Inventario

## Objetivo

Definir el conjunto minimo de tablas para modelar abastecimiento, compras e inventario,
calzando con los archivos actuales:

- `output/product_catalog.csv`
- `output/transactions.csv`
- `output/inventory_snapshot.csv`
- `output/internal_transfers.csv`

La data actual representa `salidas` reales de inventario por `sku` y `location`.
Para completar el lado de supply, el minimo util es modelar:

1. ordenes de compra
2. lineas de orden de compra
3. recepciones de compra
4. snapshot de inventario
5. transferencias internas

Con estas 3 tablas ya se puede calcular:

- lead time esperado vs real
- compras abiertas
- fill rate de proveedor
- backlog de recepcion
- entradas de inventario por sucursal
- costo de abastecimiento
- posicion diaria de stock para reposicion
- lead time interno centro -> sucursal

## Convencion Operativa

Agrupacion sugerida para el proyecto:

- `salidas`: ventas, consumo, despacho
- `entradas`: recepciones de compra
- `traslados`: transferencias internas
- `posicion`: snapshot diario de inventario

En el estado actual:

- `output/transactions.csv` = `salidas`

Con el esquema de abajo:

- `purchase_receipts.csv` = `entradas`
- `internal_transfers.csv` = `traslados`
- `inventory_snapshot.csv` = `posicion`

## Logica Particular de Este Modelo

- el modelo canónico actual sí incluye transferencias internas entre nodo central y sucursales.
- `internal_transfers.csv` es el cambio mínimo que permite representar compra centralizada sin perder consistencia operativa por sucursal.
- la compra industrial se piensa como importacion a un nodo central y posterior redistribucion a sucursales.
- esta estructura mantiene consistencia entre `transactions`, `purchase_receipts`, `internal_transfers` e `inventory_snapshot`.

## 1. inventory_snapshot.csv

Snapshot diario de inventario por SKU y ubicacion.
Cada fila representa el saldo operacional al cierre del dia.

Columnas:

- `snapshot_date`: fecha del snapshot
- `sku`: FK a `product_catalog.sku`
- `location`: sucursal o bodega
- `on_hand_qty`: stock fisicamente disponible
- `on_order_qty`: stock ya ordenado y aun no recibido

Notas:

- esta tabla no es analitica; persiste estado operacional
- permite separar baja venta de quiebre de stock
- habilita recomendacion de compra usando posicion observable

Ejemplo:

```csv
snapshot_date,sku,location,on_hand_qty,on_order_qty
2024-03-15,SKU-00001,Santiago,8,10
```

## 2. internal_transfers.csv

Traslado interno entre nodo central y sucursal.
Cada fila representa un embarque interno ya despachado desde origen.

Columnas:

- `transfer_id`: identificador unico del traslado
- `sku`: FK a `product_catalog.sku`
- `source_location`: nodo origen
- `destination_location`: nodo destino
- `ship_date`: fecha de despacho interno
- `expected_receipt_date`: fecha esperada de recepcion
- `receipt_date`: fecha efectiva de recepcion; puede quedar vacia si el traslado sigue abierto
- `transfer_qty`: cantidad trasladada
- `transfer_status`: `open`, `received`

Notas:

- desacopla lead time proveedor de lead time interno
- permite que la compra sea centralizada y la demanda siga analizandose por sucursal
- al no modelar orden de transferencia separada, esta tabla representa el movimiento interno despachado

Ejemplo:

```csv
transfer_id,sku,source_location,destination_location,ship_date,expected_receipt_date,receipt_date,transfer_qty,transfer_status
TR-20240315-000123,SKU-00001,CD Santiago,Antofagasta,2024-03-15,2024-03-19,2024-03-20,8,received
```

## 3. purchase_orders.csv

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
- en `industrial`, los lead times deben interpretarse como importacion de largo plazo

Ejemplo:

```csv
po_id,supplier,destination_location,order_date,expected_receipt_date,order_status,currency,payment_terms_days
PO-20240315-000123,Maestranza Integral,Santiago,2024-03-15,2024-06-13,received,CLP,30
```

## 4. purchase_order_lines.csv

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

## 5. purchase_receipts.csv

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
- `inventory_snapshot.sku -> product_catalog.sku`
- `internal_transfers.sku -> product_catalog.sku`
- `purchase_orders.supplier -> product_catalog.supplier`
- `purchase_orders.destination_location -> inventory_snapshot.location`
- `purchase_receipts.location -> inventory_snapshot.location`
- `internal_transfers.source_location -> inventory_snapshot.location`
- `internal_transfers.destination_location -> inventory_snapshot.location`
- `inventory_snapshot.location` contiene nodos operativos y nodo central
- `purchase_receipts.sku -> product_catalog.sku`

Consistencias esperadas:

- un `sku` tiene un proveedor principal en `product_catalog`
- una OC no debe mezclar proveedores
- la recepcion debe ocurrir en la misma `destination_location` de la OC
- `receipt_date >= order_date`
- `sum(received_qty por po_line_id) <= ordered_qty`, salvo sobre-recepcion explicita
- un traslado interno no debe crear ni destruir stock; solo moverlo entre nodos

## Reglas Minimas de Generacion

Si luego generamos data sintetica de compras, sugiero estas reglas:

- una compra se gatilla por quiebre esperado o politica de reposicion
- `expected_receipt_date = order_date + supplier_lead_time_days`
- `receipt_date` se mueve alrededor de la esperada con atraso o adelanto controlado
- `ordered_qty` depende de cobertura objetivo, MOQ y tamaño de lote
- `unit_cost` se mueve alrededor de `product_catalog.cost`
- no todos los pedidos llegan completos; debe existir una fraccion de parciales
- la compra industrial se recibe en nodo central
- el reabastecimiento a sucursal se mueve con `internal_transfers`
- cada sucursal usa su propio lead time interno para reposicion
- `inventory_snapshot` debe persistir `on_hand_qty` y `on_order_qty` por dia

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

- `transactions.quantity` -> `movement_direction = out`, `movement_type = sale`
- `purchase_receipts.csv` -> `movement_direction = in`, `movement_type = purchase_receipt`
- `internal_transfers.csv` -> `movement_type = transfer`

## Esquema Minimo Recomendado

Si hubiera que quedarse con lo estrictamente minimo para el siguiente modulo:

1. `inventory_snapshot.csv`
2. `internal_transfers.csv`
3. `purchase_orders.csv`
4. `purchase_order_lines.csv`
5. `purchase_receipts.csv`

Ese es el punto minimo en que compras, abastecimiento e inventario quedan modelados con suficiente fidelidad para forecast, reposicion y analitica operacional.
