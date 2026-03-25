# Selección de Benchmark para Evaluación de Forecast

## El problema: un solo benchmark no sirve para todos los tipos de producto

El MASE (Mean Absolute Scaled Error) es la métrica principal de evaluación:

```
MASE = MAE_modelo / MAE_benchmark
```

El valor del benchmark (denominador) determina el estándar contra el cual se mide el modelo. **Un benchmark inadecuado distorsiona la comparación entre SKUs**.

### Estado actual del sistema

El sistema usa **Seasonal Naïve (lag-12)** como benchmark universal:

```
ŷ_t = y_{t-12}   →   denominador = mean(|y_t - y_{t-12}|) sobre el train
```

Esto es correcto para SKUs **estacionales**, pero problemático para el resto.

---

## Análisis por tipo de producto

### 1. Smooth estacional (`is_seasonal=True`)

- **Benchmark correcto:** Seasonal Naïve (lag-12) ✅ — ya implementado
- **Justificación:** La estacionalidad es el patrón dominante y explotable. Si el modelo no supera "repito el año pasado", no aporta nada.
- **Impacto en el catálogo:** 96 SKUs (12% del catálogo)

### 2. Smooth no estacional (`is_seasonal=False`, `sb_class=smooth`)

- **Benchmark actual:** Seasonal Naïve lag-12 ⚠️ — **inflado**
- **Benchmark correcto:** Naïve lag-1 (random walk: `ŷ_t = y_{t-1}`)
- **Por qué:** Para una serie sin estacionalidad, `y_{t-12}` se diferencia de `y_t` por 12 meses de ruido acumulado. El denominador es grande → MASE artificialmente bajo → el modelo *parece* mejor de lo que es.
- **Efecto numérico con n=36, lag=12:** El denominador lag-12 es ~1.4x más grande que lag-1 para series sin estacionalidad. Un MASE reportado de 0.72 podría ser realmente 1.0 contra el benchmark correcto.
- **Impacto en el catálogo:** 586 SKUs (73% del catálogo) — **el más relevante a corregir**

### 3. Intermittent (`sb_class=intermittent`)

- **Benchmark actual:** Seasonal Naïve lag-12 ⚠️ — **no aplica**
- **Benchmark correcto:** Media histórica
- **Por qué:** Para demanda intermitente (muchos ceros), el lag-12 frecuentemente devuelve 0, haciendo el denominador ≈ 0 o muy pequeño → MASE explota o es inestable.
- **Alternativa:** Usar RAE (Relative Absolute Error) contra media histórica, o directamente WAPE que es más estable con ceros.
- **Impacto en el catálogo:** 69 SKUs (8.6%)

### 4. Lumpy (`sb_class=lumpy`)

- **Benchmark actual:** Seasonal Naïve lag-12 ⚠️ — **no aplica**
- **Benchmark correcto:** Media histórica (igual que intermittent)
- **Por qué:** Combinación de alta intermitencia + alta variabilidad en los picos. El lag-12 es un predictor pésimo porque los picos raramente se repiten en el mismo mes.
- **Métrica complementaria:** CSL (Cycle Service Level) es más relevante que MASE para lumpy — lo que importa es si hubo stockout, no el error absoluto.
- **Impacto en el catálogo:** 22 SKUs (2.75%)

### 5. Erratic (`sb_class=erratic`)

- **Benchmark actual:** Seasonal Naïve lag-12 ⚠️ — **subóptimo**
- **Benchmark correcto:** Naïve lag-1 o media histórica
- **Por qué:** Alta variabilidad pero demanda continua. Sin estacionalidad, lag-12 es un benchmark demasiado fácil de vencer.
- **Impacto en el catálogo:** 10 SKUs (1.25%)

### 6. Inactive (`sb_class=inactive`)

- **Benchmark:** No aplica — no se genera forecast
- **Impacto en el catálogo:** 17 SKUs (2.1%) — actualmente retornan `status=error`

---

## Tabla resumen

| Clase S/B | is_seasonal | N SKUs | Benchmark actual | Benchmark correcto | Impacto del error |
|-----------|-------------|--------|-----------------|-------------------|-------------------|
| smooth | True | 96 | lag-12 ✅ | lag-12 | — |
| smooth | False | 586 | lag-12 ⚠️ | lag-1 | MASE subestimado ~30-40% |
| intermittent | — | 69 | lag-12 ⚠️ | media histórica | MASE inestable |
| lumpy | — | 22 | lag-12 ⚠️ | media histórica | MASE inestable |
| erratic | — | 10 | lag-12 ⚠️ | lag-1 | MASE subestimado |
| inactive | — | 17 | N/A | N/A | — |

---

## Implementación recomendada

### Cambio en `compute_mase()` — denominador adaptativo

En `planning_core/forecasting/metrics.py`, agregar parámetro `naive_type`:

```python
def compute_mase(
    actual: np.ndarray,
    forecast: np.ndarray,
    season_length: int = 12,
    train_actual: np.ndarray | None = None,
    naive_type: str = "seasonal",   # "seasonal" | "lag1" | "mean"
) -> float:
    ...
    if naive_type == "seasonal":
        lag = season_length  # lag-12 para estacionales
    elif naive_type == "lag1":
        lag = 1              # random walk para no estacionales
    elif naive_type == "mean":
        base = np.abs(train_actual - train_actual.mean())  # vs media histórica
```

### Cambio en `run_backtest()` y `select_and_forecast()`

Pasar el `naive_type` correcto según el perfil del SKU:

```python
# En selector.py, _evaluate_sku, catalog_runner.py
if profile.get("is_seasonal"):
    naive_type = "seasonal"
elif sb_class in ("intermittent", "lumpy"):
    naive_type = "mean"
else:
    naive_type = "lag1"
```

### Impacto esperado en las métricas del catálogo

Con el benchmark correcto, los MASE de smooth no estacional subirán (~30-40%), reflejando la dificultad real del problema. Los MASE de smooth estacional no cambian. Los de intermittent/lumpy se estabilizarán.

**El resultado agregado del catálogo será más honesto**, aunque los números absolutos sean peores.

---

## Métricas complementarias por tipo

Más allá de MASE, estas métricas son más informativas según el tipo:

| Clase | Métrica principal | Métricas complementarias |
|-------|-----------------|--------------------------|
| smooth estacional | MASE lag-12 | WAPE, Bias |
| smooth no estacional | MASE lag-1 | WAPE, Bias |
| intermittent | WAPE | Fill Rate, CSL |
| lumpy | WAPE | CSL, Stockout rate |
| erratic | MASE lag-1 | WAPE |

---

## Prioridad de implementación

1. **Alta:** Corregir denominador para smooth no estacional (586 SKUs, impacto ~30% en MASE reportado)
2. **Media:** Estabilizar métrica para intermittent/lumpy (91 SKUs)
3. **Baja:** Renombrar `status=error` a `status=inactive` para SKUs sin transacciones
