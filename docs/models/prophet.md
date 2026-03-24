# Prophet

**Librería**: `prophet` (Meta) o `neuralprophet`
**Fase**: 3.2

---

## ¿Qué es?

Prophet es un modelo de descomposición aditiva (y opcionalmente multiplicativa) diseñado por Meta para series temporales de negocio con patrones complejos. Modela la serie como:

```
y(t) = trend(t) + seasonality(t) + holidays(t) + noise(t)
```

- **Trend**: crecimiento lineal o logístico (con saturación) con changepoints automáticos
- **Seasonality**: componentes de Fourier para múltiples períodos simultáneos (anual, semanal, diario)
- **Holidays**: efectos de eventos especiales como fechas ingresadas manualmente

Es especialmente útil cuando la estacionalidad tiene forma irregular o hay múltiples calendarios superpuestos (vacaciones, feriados, estaciones).

---

## ¿Para qué tipo de demanda?

| Clasificación | Cuándo usar Prophet |
|---|---|
| Smooth con estacionalidad compleja | Cuando Holt-Winters no captura bien la forma de la estacionalidad (ej: pico en semana 3 del mes, no en todo el mes) |
| Erratic con variables de calendario | Cuando hay feriados, temporadas o eventos conocidos que explican los picos |
| Tendencia con changepoints | Cuando la tasa de crecimiento cambió en algún punto histórico identificable |
| SKUs ABC=A con revisión manual | Justifica el costo computacional adicional |
| Intermittent / Lumpy | **No aplicar** — Prophet no maneja ceros estructurales |

En el contexto industrial de este proyecto: candidato para SKUs AX y AY con estacionalidad confirmada y ABC=A (los más críticos).

---

## ¿Qué se necesita para que funcione bien?

- **Mínimo 2 ciclos estacionales** de historia (24 meses para mensual).
- **Fechas en formato datetime** — Prophet requiere columna `ds` con fechas reales.
- Series **sin valores negativos** — la descomposición puede generar negativos en el componente estacional; truncar a 0.
- **Outliers tratados** — los changepoints pueden confundirse con outliers extremos.
- Si hay **feriados o eventos especiales** relevantes para la demanda, agregarlos como `holidays` df aumenta significativamente la precisión.

---

## Fallas conocidas y limitaciones

| Falla | Causa | Mitigación |
|---|---|---|
| No captura autocorrelación de corto plazo | Prophet modela componentes globales, no dependencias AR locales | Para series con ACF significativa en lags 1-2, ARIMA o ETS son mejores |
| Sobreajuste de changepoints | Con `n_changepoints` alto, puede ajustar ruido como tendencia | Usar `changepoint_prior_scale=0.05` (default conservador) |
| Predicciones negativas | Componente estacional puede ser negativo en períodos de baja demanda | Truncar forecast a max(0, prediction) |
| Lento con muchos SKUs | Prophet ajusta un modelo complejo por serie — ~2-5 segundos por SKU | Aplicar solo a subconjunto ABC=A/B con estacionalidad confirmada; no para todo el catálogo |
| Estacionalidad incorrecta en datos industriales | Prophet asume datos diarios por defecto; para datos mensuales hay que configurar manualmente | Desactivar `weekly_seasonality`, `daily_seasonality`; activar solo `yearly_seasonality` |

---

## Parámetros clave

```python
from prophet import Prophet

model = Prophet(
    seasonality_mode="multiplicative",  # mejor cuando la estacionalidad crece con el nivel
    yearly_seasonality=True,
    weekly_seasonality=False,           # desactivar para datos mensuales
    daily_seasonality=False,
    changepoint_prior_scale=0.05,       # regularización de changepoints (0.05 = conservador)
    seasonality_prior_scale=10.0,       # regularización de la estacionalidad
    holidays=holidays_df,               # opcional: DataFrame con columnas [ds, holiday]
)

# Requiere formato específico: columnas [ds, y]
train_df = demand_df.rename(columns={"period": "ds", "demand_clean": "y"})
model.fit(train_df)

future = model.make_future_dataframe(periods=h, freq="MS")
forecast = model.predict(future)
```

---

## Decisión de uso en este proyecto

Prophet se justifica para SKUs que cumplan **todos** estos criterios:
1. `abc_class == "A"` — alto impacto económico que justifica el costo computacional
2. `is_seasonal == True` — hay patrón estacional confirmado
3. `lifecycle in ["mature", "growing"]` — historia suficiente y demanda activa
4. MASE de ETS en backtest > 0.85 — ETS no está capturando bien el patrón

Para el resto del catálogo, AutoETS o SBA son suficientes y mucho más rápidos.
