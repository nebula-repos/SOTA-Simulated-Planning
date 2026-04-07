# Referencia: Gestión de Inventario Orientada a Decisiones

## Tabla de contenidos
1. [Fundamentos de stock e inventario](#1-fundamentos-de-stock-e-inventario)
2. [Diagnóstico: sobrestock, substock y equilibrio](#2-diagnóstico-sobrestock-substock-y-equilibrio)
3. [Métricas e indicadores de salud](#3-métricas-e-indicadores-de-salud)
4. [Safety stock e incertidumbre](#4-safety-stock-e-incertidumbre)
5. [Punto de reorden y políticas de reposición](#5-punto-de-reorden-y-políticas-de-reposición)
6. [Enfoque probabilístico para decisiones de inventario](#6-enfoque-probabilístico-para-decisiones-de-inventario)
7. [Costos y trade-offs económicos](#7-costos-y-trade-offs-económicos)
8. [Segmentación para decisiones operacionales](#8-segmentación-para-decisiones-operacionales)
9. [Complicaciones en escenarios reales](#9-complicaciones-en-escenarios-reales)
10. [Modelos formales de optimización](#10-modelos-formales-de-optimización)

---

## 1. Fundamentos de stock e inventario

### Qué significa gestionar inventario

Gestionar inventario es decidir, para cada SKU en cada ubicación y momento, cuánto stock mantener, cuándo reabastecer y cuánto pedir. El inventario equilibra dos riesgos contrapuestos: quiebre (ventas perdidas, daño reputacional) y sobrestock (capital inmovilizado, riesgo de obsolescencia).

El inventario actúa como buffer entre la incertidumbre de la demanda y la rigidez del abastecimiento. La gestión opera en la intersección de tres dominios: **operaciones** (disponibilidad, lead times, capacidades), **finanzas** (capital de trabajo, flujo de caja, rentabilidad) y **comercial** (nivel de servicio, satisfacción del cliente).

### Funciones del stock

1. **Desacoplar oferta y demanda**: servir al cliente mientras el proveedor produce o transporta
2. **Absorber variabilidad**: la demanda fluctuante y los lead times variables necesitan un colchón
3. **Explotar economías de escala**: comprar en lotes mayores reduce costos unitarios
4. **Anticipar demanda conocida**: estacionalidades, promociones, lanzamientos

### Tipos de inventario

| Tipo | Definición | Lógica de control |
|------|-----------|-------------------|
| Operativo (cíclico) | Stock que cubre demanda esperada entre reposiciones | Dimensionado por forecast, frecuencia de pedido y tamaño de lote |
| Safety stock | Buffer adicional para absorber variabilidad | Calculado por incertidumbre y CSL objetivo |
| En tránsito | Ordenado y en camino, no disponible aún | Visibilizar para evitar pedidos duplicados |
| Anticipación | Acumulado previamente a pico conocido | Planificado desde forecast estacional |
| Obsoleto | Descontinuados, vencidos, tecnológicamente superados | Identificar, separar, liquidar o destruir |
| Bloqueado | Físicamente presente pero no disponible (cuarentena, calidad) | Monitorear y resolver activamente |
| Exceso | Stock que excede necesidad en horizonte razonable | ¿Sobrecompra, caída de demanda o error de forecast? |

### SKU bien balanceado vs. mal posicionado

**Bien balanceado**: cobertura dentro de rango razonable, SS correctamente dimensionado para la variabilidad real, sin inventario obsoleto o bloqueado significativo, rotación coherente con el ciclo de vida.

**Sobrestock**: capital inmovilizado, riesgo de obsolescencia, costo de almacenamiento innecesario.

**Substock**: riesgo de quiebre, ventas perdidas, pedidos en backorder, daño reputacional.

### Dimensiones de disponibilidad

| Concepto | Qué mide | Cómo se expresa |
|----------|---------|-----------------|
| Disponibilidad | Si el producto está físicamente disponible para vender | Binario por SKU o % de SKUs disponibles |
| Cobertura (days on hand) | Cuántos días de demanda futura cubre el stock actual | Días: stock disponible / demanda diaria esperada |
| Nivel de servicio (CSL) | Probabilidad de no tener quiebre durante un ciclo de reposición | Porcentaje (ej. 95%, 98%) |
| Capital inmovilizado | Valor monetario total del inventario | Unidades monetarias, vinculado a WACC |

La tensión central: mejorar el CSL requiere más stock → aumenta capital inmovilizado. Pasar de 95% a 99% puede duplicar el safety stock. El costo marginal de cada punto porcentual adicional de servicio crece exponencialmente.

---

## 2. Diagnóstico: sobrestock, substock y equilibrio

### Marco conceptual

**Stock efectivo** = Stock Disponible − Stock Comprometido + Stock en Tránsito

**Necesidad** = Demanda durante Lead Time + Safety Stock

donde la DDLT (Demand During Lead Time) es la suma del forecast diario sobre los días de lead time (más el período de revisión si aplica).

### Variables del diagnóstico

| Variable | Rol | Fuente típica |
|----------|-----|--------------|
| Stock disponible (on hand) | Cuántas unidades hay físicamente | ERP / WMS |
| Stock comprometido | Reservado para pedidos confirmados | ERP / OMS |
| Stock en tránsito | Ordenado y en camino | PO tracking |
| Forecast esperado | Demanda proyectada por período | Modelo de forecast |
| DDLT | Forecast acumulado durante lead time | Forecast × lead time |
| σ_d | Desviación estándar de la demanda | Historial de demanda |
| Lead time (LT) | Tiempo desde pedido hasta llegada | Historial de proveedor |
| σ_LT | Desviación estándar del lead time real | Historial de recepciones |
| Período de revisión (R) | Frecuencia con que se revisa para reordenar | Política de compras |
| CSL objetivo | Porcentaje de ciclos sin quiebre deseados | Decisión de negocio |

### Lógica de clasificación: umbrales y bandas

**Cobertura neta** (días) = Stock Efectivo / Demanda Diaria Esperada

**Cobertura objetivo** = Lead Time + Período de Revisión + Cobertura de Seguridad (días equivalentes de SS)

**Ratio de posicionamiento** = Cobertura Neta / Cobertura Objetivo

| Rango del Ratio | Clasificación | Acción típica |
|-----------------|--------------|--------------|
| < 0.3 | Quiebre inminente | Compra urgente / expedite |
| 0.3 – 0.7 | Substock | Acelerar pedido pendiente o generar orden |
| 0.7 – 1.3 | Equilibrio | Monitorear; seguir política normal |
| 1.3 – 2.0 | Sobrestock leve | Evaluar reducción en próxima compra |
| > 2.0 | Sobrestock crítico | Detener compras; evaluar liquidación |

Los umbrales son configurables — dependen de la industria y tolerancia al riesgo. Productos perecibles usan bandas más estrechas (0.8-1.2 para equilibrio). Repuestos críticos usan bandas más holgadas (0.5-2.0).

**Importante**: el diagnóstico debe ser contextual. Un ratio de 1.0 puede estar substockeado si hay pico estacional en 3 semanas. Un ratio de 1.5 puede estar bien si la vida útil del producto es 50 días.

### Diagnóstico probabilístico

Alternativa a las bandas fijas: calcular directamente la **probabilidad de quiebre**:

P(Quiebre) = P(Demanda durante LT + R > Stock Efectivo)

Si la DDLT sigue una Normal(μ, σ²), esta probabilidad se calcula directamente. Si P(Quiebre) > (1 − CSL objetivo), el SKU está substockeado. Este enfoque es continuo (no binario) y conecta directamente con el nivel de servicio — permite priorizar: un SKU con P=40% es más urgente que uno con P=10%, incluso si ambos están clasificados como "substock" según las bandas.

---

## 3. Métricas e indicadores de salud

### Indicadores de cobertura y rotación

**Cobertura de Stock (Days on Hand)**:
```
Cobertura = Stock Disponible / Demanda Diaria Promedio
```
Se recomienda calcular usando el **forecast futuro** (no la venta histórica) para que refleje la situación prospectiva.

**Rotación de Inventario (Inventory Turnover)**:
```
Rotación = Costo de Ventas Anual / Inventario Promedio (a costo)
```
Una rotación de 12 significa que el inventario se renueva mensualmente. Mayor rotación = menor capital inmovilizado relativo. Rotación excesivamente alta puede indicar substock.

### Indicadores de nivel de servicio

**CSL** = Número de ciclos sin quiebre / Total de ciclos. Un CSL de 95% significa que en 95 de cada 100 ciclos el inventario fue suficiente.

**Fill Rate** = Unidades entregadas desde stock / Unidades demandadas. Generalmente mayor que CSL porque un quiebre no implica perder toda la demanda del ciclo. Un CSL de 95% tipicamente corresponde a fill rate > 98%.

**Stockout Rate**: porcentaje de tiempo (o SKUs) en condición de quiebre.

**Backorder Rate**: proporción de demanda que no se satisface inmediatamente y queda como pedido pendiente.

### Indicadores financieros

**GMROI** = Margen Bruto Anual / Inventario Promedio (a costo). Mide cuántos pesos de margen genera cada peso invertido en inventario. GMROI de 3.0 = por cada peso en stock se generan tres de margen bruto anual. Es el indicador preferido para evaluar la rentabilidad del inventario.

**Carrying Cost Rate** = (Costo financiero + Almacén + Seguros + Merma + Obsolescencia) / Valor del Inventario. Tipicamente entre 15% y 35% anual.

**Capital Invertido en Stock**: valor monetario total valorizado al costo de adquisición. Descomponer por categoría (operativo, seguridad, exceso, obsoleto) para identificar ineficiencias.

### Indicadores de salud operacional

**Slow movers**: SKUs con rotación significativamente menor al promedio de su categoría (< 2 vueltas al año).

**Dead stock**: SKUs sin movimiento en 6–12 meses. Capital inmovilizado que no genera retorno → gestionar activamente (liquidación, transferencia, donación).

**Inventory Accuracy**: SKUs donde stock físico = stock en sistema / total de SKUs contados. Target > 95%. Sin precisión, todas las decisiones basadas en datos de stock son poco confiables.

### Interpretación conjunta

- **Alta cobertura + baja rotación + bajo GMROI**: sobrestock de un producto poco rentable → prioridad de reducción
- **Baja cobertura + alto GMROI + alto stockout rate**: substock de un producto rentable → oportunidad de mejora
- **Alta rotación + alto fill rate + GMROI razonable**: SKU bien gestionado → mantener política actual
- **Dead stock + inventario obsoleto creciente**: problema de planificación o ciclo de vida → requiere intervención

---

## 4. Safety stock e incertidumbre

### Concepto y propósito

El safety stock (SS) es inventario mantenido por encima de la demanda esperada para proteger contra la variabilidad. No cubre la demanda promedio — cubre la desviación respecto al promedio. Su propósito es reducir la probabilidad de quiebre durante el período de exposición (lead time + período de revisión).

### Fórmula clásica: solo demanda variable

```
SS = z × σ_d × √LT
```
Donde **z** es el factor de servicio (cuantil normal estándar): CSL 90% → z=1.28, CSL 95% → z=1.65, CSL 98% → z=2.05, CSL 99% → z=2.33, CSL 99.5% → z=2.58.

### Fórmula extendida: demanda Y lead time variables

```
SS = z × √(LT × σ_d² + d̄² × σ_LT²)
```
Captura dos fuentes de incertidumbre: variabilidad de demanda acumulada durante LT promedio + efecto de la variabilidad del lead time sobre la demanda esperada. La variabilidad del lead time suele ser la fuente dominante en cadenas con proveedores poco confiables.

### Para revisión periódica

```
SS = z × σ_d × √(LT + R)
```
El período de revisión R aumenta el período de exposición. Revisiones más frecuentes (R menor) permiten menos safety stock — trade-off entre costo operacional y costo de inventario adicional.

### Implementación en planning_core

| Clase ABC | Método | Fórmula usada |
|-----------|--------|---------------|
| A | extended | SS = z × √(LT·σ_d² + d̄²·σ_LT²) |
| B | extended | SS = z × σ_d × √(LT+R) |
| C | simple_pct_lt | SS = pct × d̄ × LT (sin z, configurable) |

CSL defaults: A=98.5%, B=94.5%, C=88.5%.

### Supuestos y limitaciones de las fórmulas clásicas

Las fórmulas asumen: distribución normal de la demanda, independencia entre períodos, desviación estándar estacionaria, demanda continua.

En la práctica estas condiciones no se cumplen para: demanda intermitente/lumpy (mejor: Poisson, Gamma o Croston), series con estacionalidad o tendencia, lead times de importación con distribuciones asimétricas.

Para estos casos → enfoques probabilísticos directos, simulación, o ajuste empírico del factor z.

### Enfoques alternativos

- **SS basado en percentiles del forecast**: si el modelo genera distribución predictiva (yhat_lo80, yhat_hi80), el SS = percentil deseado de DDLT − mediana de DDLT. Elimina el supuesto de normalidad.
- **SS por simulación**: simular demanda y lead time N veces, calcular cuánto stock se habría necesitado en cada escenario, elegir el percentil correspondiente al CSL deseado.
- **SS basado en forecast error (MAD/MAPE)**: σ ≈ 1.25 × MAD del forecast. Usa el error histórico como proxy de incertidumbre futura.

---

## 5. Punto de reorden y políticas de reposición

### Punto de reorden (ROP)

```
ROP = DDLT + SS = (d̄ × LT) + SS
```
Cuando el stock efectivo cae por debajo del ROP, se dispara una orden. El ROP garantiza que, con la probabilidad definida por el CSL, el stock será suficiente para cubrir la demanda hasta que llegue la reposición.

### Política (s, Q): revisión continua, cantidad fija

Cuando el inventario cae a **s** (el ROP), se ordena una cantidad fija **Q**. La cantidad óptima es el EOQ:

```
EOQ = Q* = √(2 × D × K / h)
```
Donde **D** es demanda anual, **K** es costo fijo por orden y **h** es costo de mantener una unidad por año. Trade-off del EOQ: lotes grandes reducen frecuencia de pedidos (menor costo de ordenar) pero aumentan inventario promedio (mayor costo de mantener). Eficiente para demanda relativamente estable.

### Política (s, S): Min-Max

Cuando el inventario cae a **s** (mínimo), se ordena hasta alcanzar **S** (máximo). La cantidad ordenada varía según cuánto haya caído. Útil cuando la demanda puede caer en lotes grandes (ej. clientes B2B) y el stock puede pasar de estar por encima de s a muy por debajo en un solo evento.

```
Cantidad a ordenar = S − Stock Efectivo
```

### Política (R, S): revisión periódica, order-up-to

Cada **R** períodos se revisa el inventario y se ordena hasta alcanzar el nivel **S**:

```
S = d̄ × (LT + R) + SS
Cantidad a ordenar = S − Stock Efectivo
```

Es la política más común en la práctica porque se alinea con procesos reales de compra (revisiones los lunes, órdenes consolidadas semanalmente). R más largo reduce carga administrativa pero aumenta el SS necesario (mayor período de exposición).

### Política (R, s, S): híbrida

Combina revisión periódica con mínimo: cada R períodos se revisa, pero solo se ordena si el stock está por debajo de s. Si se ordena, se lleva hasta S. Reduce el número de órdenes cuando la demanda es baja en algunos ciclos.

### Comparación de políticas

| Política | Cuándo revisar | Cuánto ordenar | Mejor para |
|----------|---------------|----------------|-----------|
| (s, Q) | Continuo (cuando stock ≤ s) | Cantidad fija Q (EOQ) | SKUs de alta rotación, demanda estable, bajo costo de monitoreo |
| (s, S) Min-Max | Continuo (cuando stock ≤ s) | Variable: hasta S | Demanda lumpy, clientes B2B, tamaños de pedido irregulares |
| (R, S) Order-up-to | Cada R períodos | Variable: hasta S | La mayoría de operaciones reales, compras consolidadas |
| (R, s, S) Híbrida | Cada R períodos (si stock < s) | Variable: hasta S | Demanda intermitente con revisión periódica |

### Push vs. Pull

**Pull**: reabastecimiento disparado por consumo real. Reactivo, adapta a demanda real, pero puede ser lento si el lead time es largo.

**Push**: reabastecimiento determinado centralmente basado en forecast. Proactivo, permite anticipar. Puede generar sobrestock si el forecast es impreciso.

En la práctica: push para planificación de mediano plazo (pre-posicionar stock antes de temporada alta) + pull para ejecución diaria (reabastecer según consumo).

---

## 6. Enfoque probabilístico para decisiones de inventario

### Del forecast puntual al forecast probabilístico

Un forecast puntual dice "se esperan 100 unidades". Un forecast probabilístico dice "con 50% de probabilidad se venderán menos de 100, con 90% menos de 130, con 95% menos de 145". La diferencia es crítica para inventario: la decisión de cuánto stock mantener no depende de la demanda esperada sino de la demanda que se quiere cubrir con cierta confianza.

### Uso de percentiles para dimensionar stock

Si la distribución acumulada de la DDLT está disponible:
```
Stock Requerido = Percentil α de la distribución de DDLT
```

Para DDLT ~ Normal(μ=500, σ=80):
- CSL 90%: Stock = 500 + 1.28 × 80 = 602 unidades
- CSL 95%: Stock = 500 + 1.65 × 80 = 632 unidades
- CSL 99%: Stock = 500 + 2.33 × 80 = 686 unidades

La diferencia entre 90% y 99% es 84 unidades adicionales. El costo marginal de cada punto porcentual adicional crece exponencialmente.

### Escenarios: conservador, base, agresivo

- **Escenario base (P50)**: stock para cubrir la mediana. Aceptable cuando el costo de quiebre es bajo.
- **Escenario conservador (P80-P95)**: para productos críticos, alto margen o alto costo reputacional.
- **Escenario agresivo (P20-P40)**: stock mínimo. Para productos de bajo margen, fácilmente sustituibles, o con riesgo de obsolescencia.

El escenario se elige según la segmentación: A-críticos → conservador; C-sustituibles → agresivo.

### Simulación Monte Carlo

Cuando la distribución analítica de la DDLT es compleja (lead time variable, demanda no-normal, restricciones de lote):
1. Generar N escenarios (ej. 10,000) de demanda durante lead time
2. Para cada escenario, calcular si el stock actual habría sido suficiente
3. El porcentaje de escenarios sin quiebre es el CSL simulado
4. Ajustar el nivel de stock hasta alcanzar el CSL deseado

Especialmente valiosa para demanda intermitente y para situaciones con restricciones operacionales (MOQ, pack size).

---

## 7. Costos y trade-offs económicos

### Taxonomía de costos de inventario

| Costo | Descripción | Comportamiento |
|-------|------------|---------------|
| Financiero del capital | WACC aplicado al valor del inventario | Proporcional al stock. Típicamente 8-15% anual |
| Almacenamiento | Arriendo, utilities, personal de bodega, equipamiento | Parcialmente fijo (espacio) + variable (manejo) |
| Merma y deterioro | Pérdida física por daño, robo, vencimiento | Variable. Alto en perecibles y frágiles |
| Obsolescencia | Depreciación por cambio tecnológico, moda o ciclo de vida | Crítico en electrónica, moda, alimentos frescos |
| De quiebre (stockout) | Venta perdida + penalidad contractual + costo reputacional | Difícil de medir. Puede incluir pérdida de cliente |
| De urgencia | Flete aéreo, overtime, compras spot a precio premium | Activado por quiebres. Generalmente 2-10× costo normal |
| De ordenar | Costo administrativo + logístico de generar y recibir un pedido | Fijo por orden |
| De oportunidad | Espacio o capital usado en stock de bajo valor | Implícito; capturado con GMROI |

### El trade-off fundamental: quiebre vs. exceso

El **critical ratio** (o critical fractile) formaliza la decisión óptima:

```
CR = co / (co + cu)
```

Donde **co** (costo de overage/exceso) es la pérdida por cada unidad que sobra y **cu** (costo de underage/quiebre) es la pérdida por cada unidad de demanda no satisfecha. El CR define el percentil óptimo de la distribución de demanda que se debe cubrir.

- Si co >> cu (alto costo de quiebre): CR → 1 → cubrir percentiles altos (P95, P99). Ejemplo: repuestos críticos en minería.
- Si cu >> co (alto costo de exceso): CR → 0 → ser conservador con el stock. Ejemplo: moda rápida con alto riesgo de liquidación.
- Si co ≈ cu: CR ≈ 0.5 → cubrir la mediana.

### Cuándo tolerar más inventario

- El margen es alto y el costo de quiebre incluye pérdida de clientes
- El lead time es largo e incierto (reposición lenta y costosa)
- El producto es crítico para la operación (parada de línea, incumplimiento contractual)
- La obsolescencia es baja (producto estable, larga vida útil)
- El costo de almacenamiento es bajo relativo al valor del producto

### Cuándo aceptar más riesgo de quiebre

- El margen es bajo y el costo de exceso (liquidación a pérdida) es alto
- Hay sustitutos disponibles que absorben la demanda insatisfecha
- El producto es perecible o tiene alta obsolescencia
- El lead time es corto y confiable (se puede reponer rápidamente)
- El costo de la urgencia (expedite) es menor que el costo de mantener inventario permanente

---

## 8. Segmentación para decisiones operacionales

### Por qué segmentar

Tratar todos los SKUs igual desperdicia recursos en productos poco importantes y puede descuidar los críticos. La segmentación permite asignar niveles de servicio, métodos de cálculo y frecuencias de revisión diferenciados.

### Dimensiones de segmentación

1. **ABC por valor**: Pareto 80/15/5 del valor anual de ventas (o unidades)
2. **XYZ por variabilidad**: X (CV bajo, alta predictibilidad), Y (media), Z (alta variabilidad)
3. **Patrón de demanda** (ADI-CV²): smooth/erratic/intermittent/lumpy
4. **Ciclo de vida**: nuevo, crecimiento, maduro, declive, inactivo

### Cómo la segmentación cambia los parámetros

| Dimensión | Impacto en parámetros |
|-----------|----------------------|
| Clase A | CSL alto (98.5%), SS extendido, revisión frecuente, alerta prioritaria |
| Clase C | CSL menor (88.5%), SS simple (% del lead time), revisión periódica |
| XYZ: X | Demanda predecible → SS mínimo requerido para el CSL objetivo |
| XYZ: Z | Alta variabilidad → SS significativo para el mismo CSL |
| Intermittent | Fórmula de SS incorrecta con distribución normal → usar simulación o bootstrap |
| Ciclo: declive | Reducir SS gradualmente; evitar nueva compra si hay riesgo de obsolescencia |

---

## 9. Complicaciones en escenarios reales

### Múltiples ubicaciones

El inventario centralizado vs. descentralizado implica trade-offs. El inventario central tiene el beneficio de la "raíz cuadrada" (pooling): σ_total < Σσ_i. El inventario descentralizado tiene tiempos de respuesta menores. En planning_core, el modelo soporta `scope = "network_aggregate"` o por ubicación específica.

### MOQ, pack sizes y restricciones de lote

El MOQ (Minimum Order Quantity) puede forzar compras que generan exceso temporal. En planning_core: `recommended_qty = ajustar_a(MOQ, pack_size)`. El trade-off: la cantidad recomendada puede ser menor al MOQ → comprar MOQ y aceptar sobrestock temporal, o diferir la compra.

### Lead times variables y proveedores poco confiables

Un lead time promedio de 30 días con σ_LT de 10 días puede requerir más SS que un lead time de 60 días con σ_LT de 2 días. La variabilidad del lead time suele ser la fuente dominante de incertidumbre. En planning_core: `sigma_lt_days` se calcula desde el historial de purchase receipts.

### Productos perecibles y vencimientos

El "inventario obsoleto" en planning_core (dead_stock) es un proxy, pero no maneja explícitamente fechas de vencimiento. La rotación FIFO es crítica para perecibles. Los umbrales del positioning_ratio deben ajustarse con la vida útil del producto.

### Catálogos masivos

Para miles de SKUs, la revisión manual es imposible. El sistema analítico debe generar alertas automáticas priorizadas. En planning_core: el `urgency_score` (0-100) permite ordenar el plan de compras.

---

## 10. Modelos formales de optimización

### Modelo Newsvendor

Para decisiones de una sola compra con demanda incierta (ej. moda, temporada). Optimiza la cantidad a ordenar minimizando el costo esperado de exceso + quiebre. La solución óptima es cubrir el percentil CR de la distribución de demanda.

### Modelo Base-Stock

Para reposición continua: mantener el inventario de posición (on-hand + on-order - backorders) igual a un nivel base S. Cada vez que se realiza una venta, se coloca un pedido de una unidad. Óptimo bajo ciertos supuestos de costos lineales.

### Modelos multi-echelon

Cuando hay múltiples eslabones (central, regional, tienda): el inventario en cada eslabón interactúa. METRIC y sus variantes optimizan el inventario total considerando estas interacciones.

### Optimización con restricción de servicio

Minimizar costo total de inventario (carrying + ordering) sujeto a un nivel de servicio mínimo por clase ABC. Equivalente a asignar el presupuesto de inventario donde genera más impacto en servicio.

### Lo que los modelos formales no resuelven

Los modelos asumen que conocemos los costos (costo de quiebre, cost de urgencia, WACC), que los datos son confiables, y que los patrones son estacionarios. En la práctica: el costo de quiebre es difícil de medir, los datos tienen errores, y los patrones cambian. Los modelos son guías, no oráculos. El criterio del analista es irreemplazable.
