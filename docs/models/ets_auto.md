# ETS Automático

**Librería**: `StatsForecast` (Nixtla) — clase `AutoETS`
**Fase**: 2.1

---

## ¿Qué es?

ETS (Error, Trend, Seasonality) es una familia de modelos de suavizado exponencial que cubre sistemáticamente todas las combinaciones posibles de:

- **Error**: Aditivo (A) o Multiplicativo (M)
- **Tendencia**: Ninguna (N), Aditiva (A), Aditiva amortiguada (Ad), Multiplicativa (M)
- **Estacionalidad**: Ninguna (N), Aditiva (A), Multiplicativa (M)

Esto produce hasta 30 modelos posibles (ETS(A,N,N) = SES, ETS(A,A,N) = Holt, ETS(A,A,A) = Holt-Winters aditivo, etc.). `AutoETS` los evalúa todos y selecciona el de menor AIC.

La selección por AIC no requiere validación externa — el criterio está integrado en el ajuste del modelo.

---

## ¿Para qué tipo de demanda?

| Clasificación | Subtipo | Modelo ETS esperado |
|---|---|---|
| Smooth | Sin estacionalidad, sin tendencia | ETS(A,N,N) — SES |
| Smooth | Con tendencia, sin estacionalidad | ETS(A,A,N) — Holt |
| Smooth | Con estacionalidad, sin tendencia | ETS(A,N,A) — Holt-Winters |
| Smooth | Con estacionalidad y tendencia | ETS(A,A,A) o ETS(M,A,M) |
| Erratic | Alta variabilidad de volumen | ETS multiplicativo en error |
| Intermittent / Lumpy | **No aplicar** — mejor SBA | — |

En la práctica, para nuestro catálogo industrial (~85% smooth), ETS cubre la gran mayoría de los SKUs activos.

---

## ¿Qué se necesita para que funcione bien?

- **Mínimo 2 ciclos estacionales** de historia si hay estacionalidad (≥ 24 meses para granularidad mensual).
- Serie **sin outliers sin tratar** — un pico extremo puede dominar el ajuste del alpha y sesgar todos los pronósticos siguientes.
- Periodos censurados **excluidos** del entrenamiento (o imputados) para no aprender del stockout.
- Valores **no negativos** — los modelos multiplicativos colapsan con ceros o negativos.

---

## Fallas conocidas y limitaciones

| Falla | Causa | Mitigación |
|---|---|---|
| Predicción negativa | ETS multiplicativo sobre serie con muchos ceros | Usar ETS aditivo, o truncar predicción a 0 |
| Overfitting a picos recientes | Alpha alto + outlier sin tratar cerca del fin del histórico | Ejecutar `treat_outliers()` antes de entrenar |
| No captura rupturas de nivel | Un cambio estructural en la media (nuevo cliente, pérdida de canal) no lo modela | Detectar con Mann-Kendall; si hay tendencia reciente → Holt |
| Estacionalidad incorrecta | Selecciona estacionalidad cuando no la hay si el período m es mal especificado | Fijar m según granularidad: m=12 mensual, m=52 semanal, m=1 si no hay estacionalidad |
| Lento en escala sin vectorización | `statsmodels` es lento; `StatsForecast.AutoETS` es 10-100x más rápido | Siempre usar `StatsForecast`, no `statsmodels.ExponentialSmoothing` |

---

## Parámetros clave

```python
from statsforecast.models import AutoETS

model = AutoETS(
    season_length=12,   # m: periodos por ciclo estacional (12=mensual, 52=semanal)
    model="ZZZ",        # Z=automático en cada componente. Alternativa: "AAA", "AAN", etc.
)
```

`season_length` debe inferirse de la granularidad:
- Mensual → `12`
- Semanal → `52`
- Diaria → `7` (si hay patrón día de semana) o `365`

---

## Output esperado

- Pronóstico puntual h períodos hacia adelante (`mean`)
- Intervalos de confianza al 80% y 95% (`lo-80`, `hi-80`, `lo-95`, `hi-95`)
- Parámetros ajustados: alpha, beta, gamma, phi y nombre del modelo seleccionado (ej. `"ETS(A,N,A)"`)

---

## Posición en el pipeline

```
clean_series + censored_mask
    → AutoETS.fit(train)
    → AutoETS.predict(h=H)
    → [backtest expanding window]
    → MASE vs. Naive estacional
    → Si MASE < threshold → modelo candidato ganador
```
