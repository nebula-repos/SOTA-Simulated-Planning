# Naive Estacional (Baseline de referencia)

**Librería**: `StatsForecast` (Nixtla) — clases `SeasonalNaive`, `HistoricAverage`
**Fase**: 2.3

---

## ¿Qué es?

El Naive Estacional es el modelo más simple posible para una serie temporal con estacionalidad: el pronóstico para el próximo periodo equivalente es simplemente el valor observado en el mismo periodo del año anterior.

```
F(t+h) = y(t + h - m)    donde m = longitud del ciclo estacional
```

Para granularidad mensual con m=12: el pronóstico de enero 2025 = demanda de enero 2024.

Para series sin estacionalidad, se usa el `HistoricAverage` (media de toda la historia) o el `Naive` simple (último valor observado).

**No es un modelo que se "elige" para producción** — es el **benchmark obligatorio** contra el cual se mide el valor agregado de cualquier modelo más sofisticado. Si un modelo no supera el Naive Estacional en MASE, no hay justificación para usarlo.

---

## ¿Por qué es el baseline correcto?

El MASE (Mean Absolute Scaled Error) se define precisamente respecto al Naive Estacional:

```
MASE = MAE_modelo / MAE_naive_estacional
```

- `MASE < 1.0` → el modelo supera al naive (hay valor agregado real)
- `MASE = 1.0` → el modelo es equivalente al naive
- `MASE > 1.0` → el modelo es peor que el naive → no usarlo en producción

Forzar el cálculo del Naive Estacional antes de cualquier modelo sofisticado previene el antipatrón de implementar modelos complejos que en realidad no mejoran nada.

---

## ¿Para qué tipo de demanda?

El Naive Estacional se calcula **siempre**, para todos los SKUs activos, como referencia. No es un candidato final salvo en casos extremos:

- SKUs con historia muy corta (< 2 ciclos) donde ningún modelo puede aprender estructura.
- SKUs new product donde la única referencia es la tendencia de temporada del cluster.

---

## Fallas conocidas y limitaciones

| Falla | Causa | Mitigación |
|---|---|---|
| Muy sensible a outliers en el histórico | Un pico anómalo en el periodo de referencia se propaga al forecast | Usar `demand_clean` (outliers tratados) como input |
| No captura tendencia | Si la demanda crece o decrece, el naive del año anterior siempre estará sesgado | Para SKUs con tendencia, el MASE del Naive será alto → justifica Holt-Winters |
| m incorrecto produce forecasts sin sentido | Si m=12 pero la estacionalidad real es m=4 (trimestral) | Inferir m desde `seasonality.seasonal_lag` del clasificador |
| No funciona sin al menos m+1 observaciones | No hay "mismo periodo del año anterior" | Usar HistoricAverage como fallback |

---

## Parámetros clave

```python
from statsforecast.models import SeasonalNaive, HistoricAverage, Naive

# Naive estacional: forecast = valor m periodos atrás
model_seasonal_naive = SeasonalNaive(season_length=12)

# Media histórica: forecast = promedio de toda la historia
model_avg = HistoricAverage()

# Naive simple: forecast = último valor observado
model_naive = Naive()
```

---

## Uso en el pipeline

```python
# El Naive se entrena SIEMPRE junto a los modelos candidatos
models = [SeasonalNaive(season_length=m), AutoETS(season_length=m)]
# El MASE de cada modelo se calcula respecto al Naive dentro del backtest
```
