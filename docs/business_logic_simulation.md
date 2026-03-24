# Logica de Negocio de la Simulacion

## Objetivo

Este documento resume la logica de negocio hoy inyectada en la simulacion.

No describe solo el esquema de datos, sino tambien las reglas operativas que explican por que los datos se ven como se ven.

## 1. Modelo operacional base

La simulacion genera un canonico operacional con estas tablas:

- `product_catalog.csv`
- `transactions.csv`
- `inventory_snapshot.csv`
- `internal_transfers.csv`
- `purchase_orders.csv`
- `purchase_order_lines.csv`
- `purchase_receipts.csv`

La semantica es:

- `transactions` = salidas reales atendidas
- `purchase_receipts` = recepciones reales de compra
- `internal_transfers` = traslados internos reales
- `inventory_snapshot` = posicion diaria de inventario

No se exportan:

- demanda latente
- ventas perdidas
- metricas derivadas
- clasificaciones analiticas

## 2. Compra centralizada

En el perfil `industrial`, la compra se modela como importacion centralizada.

Regla:

- las OCs se emiten al nodo central
- las recepciones de compra llegan al nodo central
- las sucursales se reabastecen desde el nodo central por `internal_transfers`

Eso implica dos lead times distintos:

- proveedor -> nodo central
- nodo central -> sucursal

## 3. Nodo central hibrido

El nodo central actual es `CD Santiago`.

Ya no se modela como nodo puramente logistico.

Hoy se comporta como nodo hibrido:

- compra a proveedor
- recibe compra
- despacha a sucursales
- tambien puede vender directo

Por eso:

- `CD Santiago` puede aparecer en `transactions.csv`
- `CD Santiago` puede aparecer en `inventory_snapshot.csv`
- `CD Santiago` sigue siendo origen de `internal_transfers.csv`

## 4. Demanda y ventas

La demanda simulada existe internamente, pero el canonico exporta solo venta realmente atendida.

Regla:

- si hay stock, se registra `transaction`
- si no hay stock suficiente, no se inventa una venta

Entonces:

- el modelo mantiene consistencia ERP-like
- los quiebres quedan implicitos en stock bajo o en cero

## 5. Stockouts

Los stockouts existen como fenomeno operativo, pero no como tabla.

Se infieren por:

- `on_hand_qty = 0`
- ausencia de venta atendida frente a demanda simulada interna

Esto se hizo asi para no contaminar el canonico con datos no observables desde una operacion real.

## 6. Reposicion a sucursales

Cada sucursal mantiene demanda propia.

La reposicion interna se decide en base a:

- demanda historica reciente
- lead time interno
- `review_days`
- `safety_days`
- stock actual
- stock en orden

Cuando la sucursal cae bajo su punto de reorden:

- solicita stock al nodo central
- el nodo central despacha si tiene disponibilidad

## 7. Compra a proveedor

La compra central usa politica de reposicion por SKU basada en:

- demanda agregada esperada
- lead time proveedor
- `review_days`
- `safety_days`
- stock actual del nodo central
- stock en orden del nodo central
- MOQ

### Ajuste importante ya incorporado

Antes, la compra central se evaluaba todos los dias.

Eso producia un efecto poco realista para importaciones:

- multiples OCs del mismo SKU muy seguidas
- recepciones demasiado cercanas entre si

Eso ya fue corregido.

Ahora la compra central se revisa de forma periodica real:

- clase `A`: cada 14 dias
- clase `B`: cada 21 dias
- clase `C`: cada 30 dias

Resultado:

- menos OCs artificialmente pegadas
- recepciones mas espaciadas
- comportamiento mas creible para importacion

## 8. Lead times

### Proveedor

Los proveedores industriales tienen lead times largos de importacion.

El valor esperado depende del proveedor.
La recepcion real se samplea con variabilidad alrededor de ese valor.

### Transferencia interna

Cada sucursal tiene su propio lead time desde `CD Santiago`.

Tambien se samplea con variabilidad.

## 9. Parcialidades

Las compras pueden recibirse parcializadas.

Eso permite:

- OCs `received`
- OCs `partially_received`
- OCs `open`

Las transferencias internas, en cambio, se modelan de forma mas simple:

- traslado despachado
- recibido o abierto

## 10. Cobertura de locations

No todos los SKUs aparecen en todas las locations.

La simulacion asigna locations por SKU segun clase ABC.

Ademas:

- el nodo central puede tener demanda propia
- un SKU puede quedar activo solo en algunas sucursales
- un SKU puede incluso quedar activo solo en el nodo central

## 11. Agregados usados en UI

En la UI hoy existen dos agregados distintos:

- `Agregado sucursales`
- `Agregado total red`

Semantica:

- `Agregado sucursales` = suma de sucursales, excluyendo nodo central
- `Agregado total red` = suma de todas las locations activas del SKU, incluyendo nodo central

Esto es intencional para separar:

- red comercial distribuida
- red completa incluyendo pulmón central

## 12. Semantica actual de clasificacion

La clasificacion oficial de demanda de este repo, por ahora, se calcula a nivel `SKU` agregado de red.

Eso significa:

- se suman las `transactions` de todas las locations activas del SKU
- si `CD Santiago` tiene venta directa, esa demanda tambien entra en la serie clasificada
- no se calcula clasificacion oficial por sucursal en este repo

Razon:

- el caso actual prioriza compra centralizada y lectura de demanda agregada de red
- la logica por sucursal se abordara en otro piloto separado

Implicancia:

- la clasificacion actual sirve para decisiones agregadas de planning
- no debe interpretarse como clasificacion local de cada sucursal
- la granularidad oficial por defecto para esta clasificacion es mensual
- si existe censura por stockout, la clasificacion sigue calculandose sobre demanda observada y la censura se expone como metadata/flags de calidad, no como correccion automatica de demanda

## 13. Reglas de agregacion temporal en la UI

Cuando la UI cambia granularidad:

- flujos se agregan por `sum`
- stocks se agregan por `last`

No se usa promedio por defecto.

Razon:

- ventas, recepciones y transferencias son flujos
- stock es una posicion de cierre

## 14. Consistencia exigida

La simulacion actual se considera valida si mantiene:

- `delta on_hand = purchase_receipts + transfer_in - transfer_out - sales`
- reconciliacion de `on_order`
- sin cantidades negativas indebidas
- sin referencias rotas entre tablas
- sin recepciones antes de orden
- sin sobre-recepciones

## 15. Conclusión

La logica actual representa un caso industrial bastante especifico:

- compra importada centralizada
- redistribucion interna
- nodo central hibrido
- demanda por sucursal
- canonico ERP-like sin metricas derivadas

Ese conjunto de reglas es deliberado y explica la forma actual de los datos simulados.
