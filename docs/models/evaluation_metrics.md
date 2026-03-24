# Métricas de Evaluación de Forecast

**Módulo**: `planning_core/forecasting/metrics.py` (por implementar)
**Fase**: 2.5

---

## Métricas implementadas

### MASE — Mean Absolute Scaled Error ⭐ (métrica primaria)

```
MASE = mean(|y_t - F_t|) / mean(|y_t - y_{t-m}|)
```

El denominador es el MAE del Naive Estacional (predicción = valor m períodos atrás). Escala el error respecto a lo que lograría el modelo más simple posible.

**Por qué es la métrica primaria:**
- Está bien definida cuando `y=0` (a diferencia de MAPE)
- Es comparable entre SKUs con escalas de demanda radicalmente distintas (un SKU de 5 unidades/mes vs. uno de 5.000 unidades/mes)
- Un MASE < 1 garantiza que el modelo supera al naive — interpretación directa y sin ambigüedad
- Es la métrica recomendada por Hyndman & Koehler (2006) para evaluación a escala

**Limitación**: requiere calcular el Naive Estacional como referencia, lo que implica tener al menos `m+1` observaciones.

---

### WAPE — Weighted Absolute Percentage Error

```
WAPE = sum(|y_t - F_t|) / sum(|y_t|)
```

Similar al MAPE pero ponderado por el volumen real. Evita el problema de MAPE con denominadores cercanos a cero porque pondera por la suma total, no punto a punto.

**Cuándo usarlo**: métrica complementaria al MASE para comunicar el error en términos porcentuales a stakeholders de negocio. No usar para demanda intermittent con muchos ceros.

---

### Bias (sesgo sistemático)

```
Bias = mean(F_t - y_t)
```

Mide si el modelo sobreestima (Bias > 0) o subestima (Bias < 0) de forma sistemática. Un modelo con MAE bajo pero Bias alto es peligroso: genera stocks incorrectos de forma consistente.

**Umbral de alerta**: `|Bias| / mean(y) > 0.10` → el modelo tiene sesgo > 10% del nivel medio de demanda.

---

### MAE — Mean Absolute Error

```
MAE = mean(|y_t - F_t|)
```

Error en unidades originales. Útil para comunicar el error de forma concreta ("nos equivocamos en promedio 12 unidades por mes"), pero no comparable entre SKUs con distintas escalas.

---

### RMSE — Root Mean Squared Error

```
RMSE = sqrt(mean((y_t - F_t)²))
```

Penaliza errores grandes de forma cuadrática. Útil cuando los errores grandes son especialmente costosos (stockout de un repuesto crítico). Sensible a outliers en el período de evaluación.

---

## Tabla resumen

| Métrica | Maneja ceros | Comparable entre SKUs | Penaliza errores grandes | Uso recomendado |
|---|---|---|---|---|
| **MASE** | ✅ | ✅ | No | **Selección de modelos, monitoreo** |
| WAPE | ✅ | Parcial | No | Reporte a negocio |
| Bias | ✅ | No | No | Detección de sesgo sistemático |
| MAE | ✅ | No | No | Comunicación en unidades reales |
| RMSE | ✅ | No | ✅ | SKUs críticos donde el error grande es catastrófico |
| MAPE | ❌ | ✅ | No | **No usar con demanda intermittent** |

---

## Implementación

```python
import numpy as np
import pandas as pd

def compute_mase(y_true: pd.Series, y_pred: pd.Series, y_naive: pd.Series) -> float:
    """MASE respecto al Naive Estacional."""
    mae_model = np.mean(np.abs(y_true - y_pred))
    mae_naive = np.mean(np.abs(y_true - y_naive))
    if mae_naive == 0:
        return np.nan  # Serie constante: MASE indefinido
    return mae_model / mae_naive

def compute_wape(y_true: pd.Series, y_pred: pd.Series) -> float:
    """Weighted Absolute Percentage Error."""
    total_real = np.sum(np.abs(y_true))
    if total_real == 0:
        return np.nan
    return np.sum(np.abs(y_true - y_pred)) / total_real

def compute_bias(y_true: pd.Series, y_pred: pd.Series) -> float:
    """Sesgo medio (positivo = sobreestimación, negativo = subestimación)."""
    return float(np.mean(y_pred - y_true))
```

---

## Alertas de degradación (concept drift)

Para el monitoreo recurrente (Fase 5), comparar el MASE del último periodo de evaluación contra el MASE histórico del modelo:

```
alert_threshold = historical_mase * 1.5   # 50% de degradación
if current_mase > alert_threshold:
    trigger_reclassification(sku)
```

Cuando el MASE se degrada significativamente, puede indicar un cambio estructural en la demanda (nuevo cliente, cambio de canal, fin de ciclo de vida) — señal para re-ejecutar la clasificación y potencialmente cambiar el modelo.
