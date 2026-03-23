# README - Agregacion de Series en la UI

## Objetivo

Este documento explica como la UI agrega series temporales cuando el usuario cambia:

- granularidad (`Diaria`, `Semanal`, `Mensual`)
- temporalidad (`Completo`, `Ultimos 30 dias`, `Ultimos 90 dias`, `Ultimos 180 dias`, `Ultimos 365 dias`, `YTD`)
- lineas a comparar (sucursales individuales, nodo central, `Agregado sucursales` y `Agregado total red`)

La implementacion actual vive en [apps/viz/app.py](../apps/viz/app.py).

## Regla general

La UI no crea nuevas metricas de negocio. Solo reexpresa datos operacionales ya presentes en el modelo canonico.

Eso implica dos familias de series:

- **Flujos**
  - `sales_qty`
  - `purchase_receipt_qty`
  - `transfer_in_qty`
  - `transfer_out_qty`

- **Estados**
  - `on_hand_qty`
  - `on_order_qty`

La diferencia entre ambas es clave para agregar bien.

## Como se agrega cada tipo de serie

### 1. Flujos: suma del periodo

Cuando la granularidad cambia a semanal o mensual, los flujos se agregan por **suma**.

Ejemplos:

- ventas semanales = suma de ventas diarias de la semana
- recepciones mensuales = suma de recepciones diarias del mes
- transferencias recibidas semanales = suma de transferencias recibidas del periodo

Esto esta implementado en `aggregate_timeseries()` usando:

- `sales_qty -> sum`
- `sales_amount -> sum`
- `purchase_receipt_qty -> sum`
- `transfer_in_qty -> sum`
- `transfer_out_qty -> sum`

### 2. Estados: ultimo valor del periodo

Cuando la granularidad cambia a semanal o mensual, el stock no se promedia. Se toma el **ultimo valor observado del periodo**.

Ejemplos:

- `on_hand_qty` semanal = stock al cierre de la semana
- `on_order_qty` mensual = stock en orden al cierre del mes

Esto esta implementado en `aggregate_timeseries()` usando:

- `on_hand_qty -> last`
- `on_order_qty -> last`

## Por que esta bien asi

### Para flujos

La suma es la agregacion operacional correcta porque los flujos representan eventos ocurridos dentro del periodo:

- ventas registradas
- recepciones registradas
- transferencias registradas

Si se usara promedio para estas series, se perderia el volumen total realmente movido en el periodo.

### Para stock

El stock es una **posicion**, no un flujo.

Por eso:

- sumar stock diario en una semana no tiene sentido contable ni operacional
- promediar stock puede ser util para analitica puntual, pero no representa la posicion real de cierre
- usar el ultimo valor del periodo preserva la lectura operativa esperada en planning y ERP

En otras palabras:

- para flujos interesa "cuanto paso en el periodo"
- para stock interesa "como termino el periodo"

## Por que no se usa promedio por defecto

El promedio podria servir para otras preguntas, por ejemplo:

- stock promedio del mes
- ocupacion promedio
- cobertura promedio

Pero esas ya son metricas derivadas o analiticas, no la lectura operacional basica que hoy busca la UI.

Como este repo prioriza un modelo canonico operacional simple:

- la UI por defecto usa suma para flujos
- la UI por defecto usa cierre de periodo para estados

Eso evita mezclar lectura transaccional con metricas derivadas sin dejarlo explicito.

## Orden del calculo en la UI

La UI aplica la logica en este orden:

1. Cargar la serie diaria operacional por `sku + location`.
2. Reagregar la serie a la granularidad elegida.
3. Filtrar la ventana temporal elegida.
4. Renombrar la serie con el nombre de la sucursal.
5. Hacer merge entre sucursales por fecha.
6. Calcular `Agregado sucursales` como suma de todas las sucursales activas del SKU para esa fecha.
7. Calcular `Agregado total red` como suma de todas las locations activas del SKU para esa fecha, incluyendo nodo central cuando exista.

Esto ocurre principalmente en:

- `aggregate_timeseries()`
- `apply_temporality_filter()`
- `build_location_comparison_frame()`

## Que significa `Agregado sucursales`

`Agregado sucursales` es la suma horizontal de las sucursales operativas del SKU para cada fecha del grafico.

Importante:

- excluye el nodo central
- representa la suma de sucursales, no la red completa
- su objetivo es comparar demanda o stock sucursal vs sucursal y contra su agregado

## Que significa `Agregado total red`

`Agregado total red` es la suma horizontal de todas las locations activas del SKU para cada fecha del grafico.

Importante:

- incluye sucursales y nodo central
- representa la red completa visible en el modelo
- sirve para comparar el total de red contra cada nodo individual

## Temporalidad

La temporalidad se aplica sobre la serie ya agregada.

Ejemplos:

- `Ultimos 90 dias` con granularidad semanal muestra las semanas cuya fecha agregada cae dentro de esa ventana
- `YTD` muestra desde el 1 de enero del ano del ultimo dato disponible

Esto es suficiente para una visualizacion exploratoria. Si en el futuro se requiere otra semantica, se puede cambiar a:

- filtrar primero a nivel diario
- reagregar despues

Hoy no se hace porque la diferencia principal aparece solo en bordes de ventana y no cambia la lectura operacional central.

## Frecuencias usadas

La UI usa las frecuencias de `pandas`:

- `Diaria -> None`
- `Semanal -> W`
- `Mensual -> MS`

Interpretacion:

- `W` genera cortes semanales segun la convencion de pandas
- `MS` usa inicio de mes como etiqueta del bucket mensual

## Limitaciones conocidas

- `Agregado sucursales` suma todas las sucursales activas del SKU, no solo las seleccionadas por el usuario.
- No hay selector de tipo de agregacion alternativo (`sum`, `mean`, `last`) porque la vista actual privilegia semantica operacional fija.

## Conclusión

La logica actual es correcta para esta UI porque respeta la naturaleza de los datos:

- evento -> suma
- estado -> ultimo valor

Eso mantiene consistencia con una lectura ERP-like y evita promedios que podrian inducir interpretaciones equivocadas del stock.
