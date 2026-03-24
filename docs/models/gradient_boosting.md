# Gradient Boosting (XGBoost / LightGBM)

**Librería**: `MLForecast` (Nixtla) con `LightGBM` o `XGBoost`
**Fase**: 3.3

---

## ¿Qué es?

Los modelos de gradient boosting reformulan el problema de series temporales como **regresión tabular**: en lugar de modelar la dependencia temporal explícitamente (como ARIMA o ETS), crean features a partir del histórico y otros datos contextuales, y entrenan un regresor estándar.

Las features más comunes son:
- **Lags de la serie**: `y_{t-1}, y_{t-2}, ..., y_{t-k}` (los valores pasados como features)
- **Rolling statistics**: media, desviación estándar, min, max en ventanas de 3, 6, 12 períodos
- **Features de calendario**: mes, trimestre, día de semana, semana del año, indicadores de feriados
- **Features cíclicas**: seno/coseno del mes para capturar ciclicidad sin breaks en diciembre-enero
- **Features exógenas**: precio, categoría, proveedor, región, indicadores de campaña

`MLForecast` integra la creación de estas features y el entrenamiento multi-SKU con una API similar a StatsForecast, permitiendo entrenar un modelo global sobre todos los SKUs simultáneamente.

---

## ¿Para qué tipo de demanda?

| Clasificación | Cuándo usar Gradient Boosting |
|---|---|
| Erratic con variables exógenas | Cuando los picos de demanda se correlacionan con precio, promociones o estación |
| Smooth ABC=A con muchos features disponibles | Cuando hay datos contextuales ricos que los modelos estadísticos no aprovechan |
| Cross-learning de catálogo | Cuando el catálogo tiene cientos de SKUs similares — el modelo global aprende patrones compartidos |
| Intermittent / Lumpy | **No directamente** — los ceros dominan el entrenamiento; usar con transformación o bajo umbral de demanda |

---

## Ventajas sobre modelos estadísticos

1. **Variables exógenas nativas**: no hay adaptación especial — simplemente son más columnas en la tabla de features.
2. **No-linealidades**: captura interacciones complejas (ej: el efecto del precio es diferente en verano vs. invierno).
3. **Escala**: un solo modelo LightGBM puede pronosticar miles de SKUs — no hay un modelo por serie.
4. **Feature importance**: se puede explicar qué features impulsan el forecast de cada SKU, útil para validación.
5. **Rápido en inferencia**: una vez entrenado, genera forecasts en milisegundos.

---

## Limitaciones y fallas conocidas

| Falla | Causa | Mitigación |
|---|---|---|
| No extrapola tendencias | Boosting es un interpolador — si la demanda crece más allá del rango de entrenamiento, el modelo la trunca | Incluir features de tendencia (t, t², log(t)); usar para horizonte corto (h ≤ 3 meses) |
| Requiere muchos datos | Con < 100 observaciones por SKU el modelo se sobreajusta | Usar modelos estadísticos para SKUs con historia corta |
| Features de lags introducen data leakage | Si `y_{t-1}` se calcula con datos del futuro en el backtest | `MLForecast` maneja esto automáticamente con su pipeline de cross-validation |
| Interpretabilidad baja para negocio | Un árbol de boosting es menos intuitivo que "ETS con estacionalidad" | Usar SHAP values para explicar predicciones individuales |
| Necesita reentrenamiento frecuente | Los patrones del catálogo cambian; el modelo global puede desactualizarse | Re-entrenar mensualmente con ventana deslizante de 2-3 años |

---

## Parámetros clave

```python
from mlforecast import MLForecast
from mlforecast.target_transforms import Differences
from lightgbm import LGBMRegressor
import numpy as np

mlf = MLForecast(
    models=[LGBMRegressor(
        n_estimators=100,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=10,   # regularización clave para series cortas
    )],
    freq="MS",
    lags=[1, 2, 3, 6, 12],                    # lags de la serie
    lag_transforms={
        1: [(rolling_mean, 3), (rolling_mean, 6)],  # rolling features
        12: [(rolling_mean, 12)],
    },
    date_features=["month", "quarter"],         # features de calendario
    target_transforms=[Differences([12])],      # diferenciación estacional opcional
    num_threads=4,
)

# Entrenamiento multi-SKU en una sola llamada
mlf.fit(nixtla_df)                             # df con [unique_id, ds, y, ...features exógenas]
forecasts = mlf.predict(h=3)
```

---

## Posición en el pipeline de selección

Gradient Boosting es el último candidato que se evalúa en el horse-race, y solo para subconjuntos específicos:

```
Todos los SKUs activos:
    → Naive Estacional (baseline)
    → AutoETS           (smooth, erratic)
    → CrostonSBA        (intermittent, lumpy)

SKUs ABC=A/B con variables exógenas disponibles:
    → + LightGBM global model

Modelo ganador = menor MASE en backtest expanding window
```

No tiene sentido incluir LightGBM para un SKU CX sin variables exógenas — el costo computacional no se justifica y probablemente ETS gana.
