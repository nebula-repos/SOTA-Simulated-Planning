# Resultados: Barrido de Parametrización del Horse-Race

**Experimento:** `exp_03_param_sweep.py`
**Fecha:** 2026-03-25
**Dataset:** 800 SKUs, 36 meses (2022-01-01 → 2024-12-31), granularidad mensual

---

## Grid evaluado

| Config    | h | n_windows | min_obs requerido | fallback_rate |
|-----------|---|-----------|-------------------|---------------|
| h3_w3     | 3 | 3         | 21 meses          | 4.6%          |
| h3_w4     | 3 | 4         | 24 meses          | 5.4%          |
| h3_w5     | 3 | 5         | 27 meses          | 6.3%          |
| h3_w6     | 3 | 6         | 30 meses          | 7.0%          |
| h6_w3     | 6 | 3         | 30 meses          | 7.0%          |
| h6_w4     | 6 | 4         | 36 meses          | **17.5%**     |

---

## Resultados globales

| Config | MASE mediana | MASE media | MASE p75 | MASE p90 |
|--------|-------------|------------|----------|----------|
| h3_w3  | **0.7475**  | 0.8058     | 0.9254   | 1.1865   |
| h3_w4  | 0.7711      | 0.8214     | 0.9481   | 1.1817   |
| h3_w5  | 0.7699      | 0.8157     | 0.9489   | 1.1765   |
| h3_w6  | 0.7839      | 0.8228     | 0.9568   | **1.1490** |
| h6_w3  | 0.7854      | 0.8341     | 0.9751   | 1.1787   |
| h6_w4  | 0.8098      | 0.8578     | 0.9831   | 1.1655   |

---

## MASE mediana por segmento SB × config

| sb_class     | h3_w3  | h3_w4  | h3_w5  | h3_w6  | h6_w3  | h6_w4  |
|--------------|--------|--------|--------|--------|--------|--------|
| smooth       | 0.7288 | 0.7511 | 0.7523 | 0.7639 | 0.7663 | 0.8042 |
| erratic      | 0.7864 | 0.8542 | 0.8943 | 0.9051 | 0.9174 | 1.0203 |
| lumpy        | 0.7978 | 0.7791 | 0.8224 | 0.8689 | 0.8783 | 1.1284 |
| intermittent | 1.0288 | 1.0336 | 1.0686 | 1.0425 | 1.0723 | 1.0417 |

## MASE mediana por clase ABC × config

| abc_class | h3_w3  | h3_w4  | h3_w5  | h3_w6      | h6_w3  | h6_w4  |
|-----------|--------|--------|--------|------------|--------|--------|
| A         | 0.7678 | 0.7904 | 0.7717 | **0.7542** | 0.7612 | 0.8388 |
| B         | 0.6925 | 0.7242 | 0.7299 | 0.7590     | 0.7642 | 0.8163 |
| C         | 0.7633 | 0.7906 | 0.7771 | 0.8106     | 0.7954 | 0.7997 |

## Fallback por segmento SB × config

| sb_class     | h3_w3 | h3_w4 | h3_w5 | h3_w6 | h6_w3 | h6_w4    |
|--------------|-------|-------|-------|-------|-------|----------|
| smooth       | 2.9%  | 2.9%  | 3.1%  | 3.1%  | 3.1%  | 8.4%     |
| erratic      | 0%    | 0%    | 0%    | 0%    | 0%    | 30%      |
| lumpy        | 13.6% | 18.2% | 22.7% | 36.4% | 36.4% | **95.5%** |
| intermittent | 20.3% | 27.5% | 34.8% | 39.1% | 39.1% | **85.5%** |

## Estabilidad del modelo ganador vs. baseline (h3_w3)

| Comparación      | SKUs que cambian de ganador |
|------------------|-----------------------------|
| h3_w3 → h3_w4   | 150 / 783  (19.2%)          |
| h3_w3 → h3_w5   | 201 / 783  (25.7%)          |
| h3_w3 → h3_w6   | 240 / 783  (30.7%)          |
| h3_w3 → h6_w3   | 331 / 783  (42.3%)          |
| h3_w3 → h6_w4   | 401 / 783  (51.2%)          |

---

## Interpretación y conclusiones

### 1. Más ventanas no mejoran el MASE global

La hipótesis de que más folds de backtest producen una evaluación más estable
y mejor selección de modelo **no se confirma** con este dataset.

El MASE mediana sube al aumentar `n_windows` (0.7475 → 0.7839 para h=3).
La causa probable: ventanas adicionales incluyen cortes más antiguos del historial
(2022-2023) que son menos representativos del comportamiento reciente. El backtest
penaliza a modelos que se ajustan bien al período reciente.

**Excepción**: el p90 sí mejora con más ventanas (1.1865 → 1.1490 para h3_w6).
Más ventanas benefician a los peores SKUs reduciendo la varianza del MASE,
aunque el SKU típico no gana.

### 2. Intermittent y lumpy no son sensibles a los parámetros — son sensibles al fallback

Para intermittent, el MASE > 1 en **todas** las configuraciones. Esto no es un
problema de parametrización: es un resultado estructural. El benchmark "mean"
(desviación respecto a la media histórica) es difícil de vencer para series con
muchos ceros — CrostonSBA y ADIDA apenas compiten con él.

Lo que sí varía con los parámetros es la **cobertura**: a h6_w4, el 85% de los
SKUs intermittentes y el 95% de los lumpy caen a fallback (HistoricAverage), lo
que destruye cualquier valor del horse-race para esos segmentos.

**Conclusión**: para intermittent/lumpy, la parametrización correcta es la más
conservadora posible (h3_w3). El problema de MASE > 1 se resuelve con mejores
métricas (WAPE, Fill Rate), no con más ventanas.

### 3. Erratic es el segmento más frágil

El MASE de erratic pasa de 0.786 (h3_w3) a 1.020 (h6_w4) — cruza el umbral de
ineficacia del modelo respecto al naive. Su alta variabilidad se compone a lo largo
de más ventanas y un horizonte mayor, haciendo el problema de forecast
progresivamente más difícil.

Para erratic, h=3 es obligatorio. Usar h=6 con erratic equivale a pronosticar ruido.

### 4. SKUs clase A son los únicos que se benefician de más ventanas

h3_w6 produce MASE 0.754 para clase A vs. 0.768 con h3_w3 (diferencia = 0.014).
Los SKUs de alta rotación generalmente tienen historial completo y demanda más
estable, por lo que pueden aprovechar 6 ventanas sin degradación por datos viejos.
La ganancia es pequeña pero consistente.

### 5. La selección de modelo es inestable para ~20-50% del catálogo

El 19% de los SKUs cambia de modelo ganador simplemente añadiendo una ventana.
Esto revela que para una fracción significativa del catálogo, los modelos compiten
con márgenes de MASE muy pequeños: cualquier cambio en el período de evaluación
invierte el resultado del horse-race.

No es un error — es una propiedad del dataset. Implica que en producción el
modelo "ganador" para muchos SKUs no es el único correcto, sino el que ganó la
carrera en ese corte temporal específico.

---

## Configuración recomendada para producción

### Configuración única (default actual, recomendada)

```python
CONFIG = EvalConfig(
    granularity = "M",
    h           = 3,
    n_windows   = 3,
)
```

**Justificación:**
- Mejor MASE global (0.7475) y mejor en 3 de 4 segmentos SB
- Fallback rate más bajo (4.6%) — mayor cobertura del catálogo
- Más rápida de ejecutar (142s vs 200s para h3_w6)
- Única config que mantiene cobertura aceptable para intermittent y lumpy

### Configuración diferenciada por segmento (posible mejora futura)

Si se implementa parametrización por tipo de producto:

| Segmento               | h recomendado | n_windows recomendado | Justificación                              |
|------------------------|---------------|-----------------------|--------------------------------------------|
| smooth + ABC-A         | 3             | 5-6                   | Historial completo, mejora la cola (p90)   |
| smooth + ABC-B/C       | 3             | 3                     | Ganancia marginal no justifica el costo    |
| erratic                | 3             | 3                     | Muy sensible al horizonte, no aumentar     |
| intermittent           | 3             | 3                     | Fallback explota con más ventanas          |
| lumpy                  | 3             | 3                     | Fallback explota con más ventanas          |

La ganancia máxima de esta diferenciación es ~0.014 MASE para ABC-A.
Dado que solo impacta ~96 SKUs, el beneficio agregado es bajo.

---

## Deuda técnica y features posibles

### TD-01 — Parametrización diferenciada por segmento (baja prioridad)

**Estado:** no implementado
**Descripción:** Pasar parámetros distintos de `(h, n_windows)` según el `sb_class`
y `abc_class` del SKU, dentro del mismo run de catálogo.
**Impacto esperado:** Reducción de ~0.014 MASE en ABC-A (~96 SKUs).
Reducción de fallback en intermittent/lumpy al forzar h3_w3 para esos segmentos.
**Costo de implementación:** Medio — requiere que `catalog_runner.py` acepte una
función o dict de configuración por SKU en lugar de un `EvalConfig` fijo.
**Criterio para activar:** Solo vale si el dataset crece a >5 años de historial,
donde la diferencia entre n_windows=3 y n_windows=6 se amplía.

### TD-02 — Métricas alternativas para intermittent/lumpy (media prioridad)

**Estado:** no implementado
**Descripción:** MASE > 1 para intermittent/lumpy no significa que el modelo sea
peor que no tener forecast — significa que es peor que la media histórica.
Para estos segmentos, las métricas operacionales correctas son:
- **WAPE** (ya calculado, ya disponible en `backtest`)
- **Fill Rate / CSL** (Cycle Service Level) — no implementado
- **Stockout rate** — no implementado

**Impacto:** Mejor evaluación de CrostonSBA/ADIDA para decisiones de reposición.
**Costo de implementación:** Medio — requiere datos de inventario en el backtest
(nivel de servicio no se puede calcular solo con demanda y forecast).

### TD-03 — Detección de "empate técnico" en el horse-race (baja prioridad)

**Estado:** no implementado
**Descripción:** El 19-51% de cambios de ganador entre configs sugiere que muchos
SKUs tienen modelos que compiten con diferencias de MASE < 0.05.
Un empate técnico debería preferir el modelo más simple (SeasonalNaive > AutoETS >
AutoARIMA) en lugar de elegir el que ganó por mínima diferencia.
**Ejemplo:** Si AutoARIMA gana con MASE=0.731 y SeasonalNaive tiene MASE=0.735,
la diferencia (0.004) está dentro del ruido de estimación — elegir SeasonalNaive
es más robusto y más interpretable.
**Implementación:** En `_pick_winner()` de `selector.py`, añadir un parámetro
`tie_threshold` (default 0.02): si el ganador supera al segundo por menos de
ese umbral, preferir el modelo más simple.

### TD-04 — Sweep automático por horizonte operacional (media prioridad)

**Estado:** no implementado
**Descripción:** El experimento actual evalúa MASE como métrica única de
selección. Pero en la práctica, el horizonte de forecast debería alinearse con
el lead time del proveedor por categoría o SKU, no ser fijo para todo el catálogo.
Un SKU con lead time de 1 mes no necesita h=6.
**Implementación futura:** Incorporar `lead_time_days` del proveedor (disponible
en `purchase_orders`) para derivar `h` automáticamente por SKU.

### TD-05 — Notebook de análisis del sweep (baja prioridad)

**Estado:** no implementado
**Descripción:** Crear `notebooks/03_param_sweep_analysis.ipynb` con los
gráficos del sweep:
- Bar chart agrupado: MASE por sb_class × config
- Heatmap: fallback_rate por sb_class × config
- Box plot: distribución MASE por config (no solo mediana)
- Sankey o matriz de cambios: modelo A → modelo B entre configs

El análisis de texto existe en este documento; el notebook complementaría
con visualizaciones interactivas.

---

## Decisión tomada

**Config de producción: `h=3, n_windows=3` para todo el catálogo.**

No se implementa parametrización diferenciada por segmento en esta fase.
La ganancia esperada (~0.014 MASE en ABC-A) no justifica la complejidad añadida
con el dataset actual (36 meses). Reevaluar si el historial crece a 48+ meses
o si se identifican SKUs cuya mala performance de forecast tiene impacto
operacional directo medible (stockouts frecuentes en ABC-A).
