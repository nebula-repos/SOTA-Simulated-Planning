# E/R del Modelo de Datos de `output/`

## Alcance

Este E/R considera las tablas operativas actuales generadas en `output/`:

- `product_catalog.csv`
- `transactions.csv`
- `inventory_snapshot.csv`
- `internal_transfers.csv`
- `purchase_orders.csv`
- `purchase_order_lines.csv`
- `purchase_receipts.csv`

Las metricas, promedios y clasificaciones derivadas no forman parte del modelo operacional canónico.

## Logica Particular de Esta Simulacion

- `transactions.csv` modela solo salidas realmente atendidas.
- `inventory_snapshot.csv` cubre diariamente cada par `sku + location` activo.
- un SKU del catalogo puede no quedar materializado en tablas operativas si no tuvo demanda total en el horizonte.
- los quiebres de stock no se exportan como tabla; quedan implicitos en snapshots con `on_hand_qty = 0` y en ausencia de venta atendida.
- en el perfil `industrial`, la logica de compras busca aproximar abastecimiento centralizado de importacion, por eso los proveedores tienen lead times promedio altos.
- la compra llega a un nodo central y luego se redistribuye a sucursales via `internal_transfers`.
- el nodo central puede operar como nodo hibrido: abastece a sucursales y tambien puede vender directo, por lo que puede aparecer en `transactions.csv`.

Consecuencia de diseño:

- el modelo sirve para forecasting, reposicion y conciliacion operacional basica.
- la demanda sigue observandose por sucursal y tambien puede existir en nodo central cuando ese nodo es hibrido.
- el abastecimiento sigue modelandose centralizado.

## Diagrama E/R

```mermaid
erDiagram
    PRODUCT_CATALOG {
        string sku PK
        string name
        string category
        string subcategory
        string brand
        string supplier
        decimal base_price
        decimal cost
        int moq
        int warranty_months
    }

    TRANSACTIONS {
        date date
        string sku FK
        string location
        int quantity
        decimal unit_price
        decimal total_amount
    }

    INVENTORY_SNAPSHOT {
        date snapshot_date
        string sku FK
        string location
        int on_hand_qty
        int on_order_qty
    }

    INTERNAL_TRANSFERS {
        string transfer_id PK
        string sku FK
        string source_location
        string destination_location
        date ship_date
        date expected_receipt_date
        date receipt_date
        int transfer_qty
        string transfer_status
    }

    PURCHASE_ORDERS {
        string po_id PK
        string supplier
        string destination_location
        date order_date
        date expected_receipt_date
        string order_status
        string currency
        int payment_terms_days
    }

    PURCHASE_ORDER_LINES {
        string po_line_id PK
        string po_id FK
        string sku FK
        int ordered_qty
        decimal unit_cost
        decimal line_amount
        int moq_applied
    }

    PURCHASE_RECEIPTS {
        string receipt_id PK
        string po_id FK
        string po_line_id FK
        string sku FK
        string supplier
        string location
        date receipt_date
        int received_qty
        decimal unit_cost
        decimal total_cost
        string receipt_status
    }

    PRODUCT_CATALOG ||--o{ TRANSACTIONS : "sku"
    PRODUCT_CATALOG ||--o{ INVENTORY_SNAPSHOT : "sku"
    PRODUCT_CATALOG ||--o{ INTERNAL_TRANSFERS : "sku"
    PRODUCT_CATALOG ||--o{ PURCHASE_ORDER_LINES : "sku"
    PRODUCT_CATALOG ||--o{ PURCHASE_RECEIPTS : "sku"
    PURCHASE_ORDERS ||--o{ PURCHASE_ORDER_LINES : "po_id"
    PURCHASE_ORDERS ||--o{ PURCHASE_RECEIPTS : "po_id"
    PURCHASE_ORDER_LINES ||--o{ PURCHASE_RECEIPTS : "po_line_id"
```

## Lectura del Modelo

### 1. `product_catalog.csv`

Maestro de productos.

Clave primaria:

- `sku`

Rol en el modelo:

- define el producto base
- define proveedor principal
- define costo base y MOQ
- sirve como referencia para ventas y compras

Columnas:

- `sku`
- `name`
- `category`
- `subcategory`
- `brand`
- `supplier`
- `base_price`
- `cost`
- `moq`
- `warranty_months`

### 2. `transactions.csv`

Movimientos de salida realmente registrados.
En este proyecto representan venta o consumo atendido por sucursal.

Clave natural sugerida:

- `date + sku + location`

Relaciones:

- `sku -> product_catalog.sku`

Columnas:

- `date`
- `sku`
- `location`
- `quantity`
- `unit_price`
- `total_amount`

Semantica:

- `transactions` = `salidas` reales

### 3. `inventory_snapshot.csv`

Posicion diaria de inventario por producto y ubicacion.

Clave natural sugerida:

- `snapshot_date + sku + location`

Relaciones:

- `sku -> product_catalog.sku`

Columnas:

- `snapshot_date`
- `sku`
- `location`
- `on_hand_qty`
- `on_order_qty`

Semantica:

- representa el estado operativo diario del inventario
- no es una metrica analitica, sino un saldo operacional persistido

### 4. `internal_transfers.csv`

Traslados internos desde nodo central hacia sucursales.

Clave primaria:

- `transfer_id`

Relaciones:

- `sku -> product_catalog.sku`

Columnas:

- `transfer_id`
- `sku`
- `source_location`
- `destination_location`
- `ship_date`
- `expected_receipt_date`
- `receipt_date`
- `transfer_qty`
- `transfer_status`

Semantica:

- representa reposicion interna entre nodos del inventario
- desacopla lead time proveedor de lead time interno

### 5. `purchase_orders.csv`

Cabecera de orden de compra.
Una OC pertenece a un proveedor y a una sucursal destino.

Clave primaria:

- `po_id`

Relaciones:

- se relaciona con `purchase_order_lines` por `po_id`
- se relaciona con `purchase_receipts` por `po_id`

Columnas:

- `po_id`
- `supplier`
- `destination_location`
- `order_date`
- `expected_receipt_date`
- `order_status`
- `currency`
- `payment_terms_days`

### 6. `purchase_order_lines.csv`

Detalle de cada OC.

Clave primaria:

- `po_line_id`

Claves foraneas:

- `po_id -> purchase_orders.po_id`
- `sku -> product_catalog.sku`

Columnas:

- `po_id`
- `po_line_id`
- `sku`
- `ordered_qty`
- `unit_cost`
- `line_amount`
- `moq_applied`

Nota:

- hoy el generador crea 1 linea por OC, pero el modelo permite varias lineas por OC

### 7. `purchase_receipts.csv`

Entradas efectivas de inventario por recepcion.

Clave primaria:

- `receipt_id`

Claves foraneas:

- `po_id -> purchase_orders.po_id`
- `po_line_id -> purchase_order_lines.po_line_id`
- `sku -> product_catalog.sku`

Columnas:

- `receipt_id`
- `po_id`
- `po_line_id`
- `sku`
- `supplier`
- `location`
- `receipt_date`
- `received_qty`
- `unit_cost`
- `total_cost`
- `receipt_status`

Semantica:

- `purchase_receipts` = `entradas`

## Cardinalidades

- un `producto` puede tener muchas `transactions`
- un `producto` puede tener muchos `inventory_snapshot`
- un `producto` puede tener muchas `internal_transfers`
- un `producto` puede aparecer en muchas `purchase_order_lines`
- una `purchase_order` puede tener muchas `purchase_order_lines`
- una `purchase_order_line` puede tener una o varias `purchase_receipts`
- una `purchase_order` puede generar una o varias `purchase_receipts`

## Vista de Negocio

Agrupacion recomendada:

- `transactions.csv` = salidas
- `purchase_receipts.csv` = entradas
- `internal_transfers.csv` = reabastecimiento interno
- `inventory_snapshot.csv` = posicion de inventario

Con eso, el flujo operacional queda:

1. el catalogo define producto, proveedor, costo y MOQ
2. las `transactions` representan consumo o venta
3. `purchase_receipts` representan ingreso al nodo central
4. `internal_transfers` mueven inventario desde el nodo central a sucursales
5. `inventory_snapshot` persiste la posicion diaria por nodo
6. las `purchase_orders` representan decision de abastecimiento al proveedor
7. las `purchase_order_lines` detallan que SKU se compra

## Relacion Logica de Llaves

```text
product_catalog.sku
    -> transactions.sku
    -> inventory_snapshot.sku
    -> internal_transfers.sku
    -> purchase_order_lines.sku
    -> purchase_receipts.sku

purchase_orders.po_id
    -> purchase_order_lines.po_id
    -> purchase_receipts.po_id

purchase_order_lines.po_line_id
    -> purchase_receipts.po_line_id
```

## Nota sobre `product_metrics.csv`

`product_metrics.csv` no forma parte del E/R operacional actual porque:

- no es tabla transaccional ni maestra
- no participa en relaciones de negocio
- el generador actual ya no la vuelve a producir como salida principal

Si quisieras mantenerla, deberia tratarse como tabla derivada o analitica, no como entidad core del modelo.
