# Métricas de Evaluación de Forecast

**Módulo**: `planning_core/forecasting/metrics.py` (implementado)
**Fase**: 2.5

**Estado actual**:

- implementadas en código: `compute_mase`, `compute_wape`, `compute_bias`, `compute_mae`, `compute_rmse`, `compute_all_metrics`
- cubiertas por tests en `tests/test_metrics.py`
- todavía no integradas a un `backtest.py` o `selector.py` porque esos módulos siguen pendientes

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

### Bias relativo (sesgo sistemático)

```
Bias = mean(F_t - y_t) / mean(y_t)
```

Mide si el modelo sobreestima (Bias > 0) o subestima (Bias < 0) de forma sistemática en relación al nivel medio real de la serie. Un modelo con MAE bajo pero Bias alto es peligroso: genera stocks incorrectos de forma consistente.

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

La implementación real vive en `planning_core/forecasting/metrics.py` y usa firmas orientadas a forecast operativo:

```python
compute_mase(actual, forecast, season_length=12, train_actual=None)
compute_wape(actual, forecast)
compute_bias(actual, forecast)
compute_mae(actual, forecast)
compute_rmse(actual, forecast)
compute_all_metrics(actual, forecast, season_length=12, train_actual=None)
```

Notas de implementación:

- `compute_mase()` usa `train_actual` para escalar contra naive estacional cuando está disponible
- `compute_bias()` retorna sesgo relativo, no sesgo absoluto
- cuando el denominador de la métrica es cero, la función retorna `NaN`

---

## Alertas de degradación (concept drift)

Para el monitoreo recurrente (Fase 5), comparar el MASE del último periodo de evaluación contra el MASE histórico del modelo:

```
alert_threshold = historical_mase * 1.5   # 50% de degradación
if current_mase > alert_threshold:
    trigger_reclassification(sku)
```

Cuando el MASE se degrada significativamente, puede indicar un cambio estructural en la demanda (nuevo cliente, cambio de canal, fin de ciclo de vida) — señal para re-ejecutar la clasificación y potencialmente cambiar el modelo.
