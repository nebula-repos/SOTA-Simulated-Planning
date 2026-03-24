# Framework de Backtest (Expanding Window)

**Librería**: `StatsForecast` — método `cross_validation()`
**Fase**: 2.4

---

## ¿Qué es?

El backtest con ventana expansiva (expanding window) simula el proceso real de forecasting: se entrena el modelo con todo el histórico disponible hasta un punto de corte `t`, se genera el pronóstico para h períodos hacia adelante, se avanza el corte, y se repite.

```
Corte 1:  [─────────── train ───────────] [── test h=3 ──]
Corte 2:  [──────────────── train ──────────] [── test h=3 ──]
Corte 3:  [───────────────────── train ────────] [── test h=3 ──]
                                                ↑ fin del histórico disponible
```

A diferencia del backtest con ventana fija (rolling), la ventana expansiva usa toda la información disponible hasta cada punto de corte — igual que lo que haría el modelo en producción.

---

## Parámetros de diseño

| Parámetro | Valor recomendado | Justificación |
|---|---|---|
| `h` (horizonte) | 3 meses (mensual) / 12 semanas (semanal) | Horizonte típico de planificación de compras |
| `n_windows` | Mínimo 3, idealmente 6 | Con < 3 ventanas el MASE es estadísticamente inestable |
| `step_size` | 1 (avanzar de a 1 periodo) | Máxima información de evaluación; costoso pero correcto |
| Punto de corte mínimo | 24 periodos de historia antes del primer corte | El modelo necesita suficiente historia para ajustar parámetros |

---

## Implementación con StatsForecast

```python
from statsforecast import StatsForecast
from statsforecast.models import AutoETS, CrostonSBA, SeasonalNaive

sf = StatsForecast(
    models=[AutoETS(season_length=12), CrostonSBA(), SeasonalNaive(season_length=12)],
    freq="MS",           # MS = month start (mensual)
    n_jobs=-1,           # paralelismo completo
)

# cross_validation devuelve un DataFrame con columnas:
# [unique_id, ds, cutoff, y, AutoETS, CrostonSBA, SeasonalNaive]
cv_df = sf.cross_validation(
    df=nixtla_df,        # formato Nixtla: columnas [unique_id, ds, y]
    h=3,
    n_windows=6,
    step_size=1,
)
```

El formato Nixtla requiere que la serie de demanda tenga columnas `unique_id` (sku), `ds` (datetime del periodo) y `y` (demanda). El pipeline debe convertir el output de `prepare_demand_series()` a este formato.

---

## Consideraciones importantes

### Series cortas

Si una serie tiene < `24 + h * n_windows` periodos, no hay suficiente historia para ejecutar el backtest completo. Opciones:
- Reducir `n_windows` a 2-3
- Omitir del backtest y asignar el modelo por regla (sin evaluación empírica)
- Usar la media histórica como único candidato

### SKUs intermittent en el backtest

El MAPE es indefinido cuando `y=0` (denominador cero). Por eso MASE es la métrica obligatoria para demanda intermittente — está bien definida para todos los casos.

### Tiempo de cómputo

StatsForecast con `n_jobs=-1` procesa 800 SKUs con AutoETS+SBA en ~30-60 segundos (granularidad mensual, 3 años de historia). Es viable en producción recurrente.

### Overfitting del selector

Si se usan los mismos datos del backtest para seleccionar el modelo Y para evaluar la selección, hay data leakage. La evaluación final del selector debe hacerse sobre una ventana de test completamente fuera del backtest.

---

## Output del backtest

```python
# Calcular métricas por modelo y SKU
from planning_core.forecasting.metrics import compute_mase, compute_wape

metrics = cv_df.groupby("unique_id").apply(lambda g: {
    "mase_ets": compute_mase(g["y"], g["AutoETS"], g["SeasonalNaive"]),
    "mase_sba": compute_mase(g["y"], g["CrostonSBA"], g["SeasonalNaive"]),
    "wape_ets": compute_wape(g["y"], g["AutoETS"]),
})
```

---

## Selección del modelo ganador

```python
# Para cada SKU, seleccionar el modelo con menor MASE
best_model = metrics.idxmin(axis=1)   # columna del modelo con menor MASE
```

El nombre del modelo ganador se almacena junto al forecast generado, para auditoría y monitoreo de concept drift.
