# Guía de Parametrización del Módulo de Forecasting

## Contexto del dataset actual

| Campo | Valor |
|---|---|
| SKUs | 800 |
| Período | 2022-01-01 → 2024-12-31 (36 meses) |
| Locaciones | 5 sucursales + 1 CD Santiago |
| Perfil | Industrial |
| Granularidad oficial | Mensual (`"M"`) |
| Scope de clasificación | `network_aggregate` (agregado de red) |

---

## 1. Parámetro `n_windows`

### Qué es

Es el número de **folds del backtest expanding-window** que se usan para evaluar cada modelo candidato antes de elegir el ganador del horse-race.

Cada fold:
1. Entrena con todo el historial hasta el punto de corte
2. Predice `h` períodos hacia adelante
3. Compara predicción vs. demanda real y calcula MASE

El MASE final de cada modelo es el **promedio sobre todos los folds**. El ganador es el de menor MASE promedio.

### Diagrama (h=3, n_windows=3, mensual)

```
                 ← entrenamiento →    ← eval →
Fold 1:  [ene-22 ........... jun-24]  [jul-ago-sep-24]
Fold 2:  [ene-22 ............ jul-24]  [ago-sep-oct-24]
Fold 3:  [ene-22 ............. ago-24]  [sep-oct-nov-24]
```

### Restricción de datos mínimos

```
min_observaciones = season_length + h × n_windows
```

| Config | Mensual (season=12) | Meses mín. | ¿Viable con 36 meses? |
|---|---|---|---|
| h=3, n_windows=3 | 12 + 9 | **21** | ✅ Sí |
| h=6, n_windows=3 | 12 + 18 | **30** | ✅ Sí (necesita 2.5 años) |
| h=3, n_windows=5 | 12 + 15 | **27** | ✅ Sí |
| h=6, n_windows=5 | 12 + 30 | **42** | ❌ No (necesita 3.5 años) |
| h=12, n_windows=3 | 12 + 36 | **48** | ❌ No (necesita 4 años) |

Si la serie es más corta que el mínimo, todos los modelos del horse-race reciben `status="series_too_short"` y el sistema degrada automáticamente a `SeasonalNaive` o `HistoricAverage` (status=`"fallback"`).

### Efecto en el resultado

| | Pocos windows (n=2-3) | Muchos windows (n=5-6) |
|---|---|---|
| **Estabilidad del MASE** | Baja — puede ser ruido de un solo fold | Alta — promedio más confiable |
| **Selección del modelo** | Puede elegir un ganador por azar | Ganador más consistente |
| **Datos requeridos** | Menos | Más |
| **Velocidad** | Rápida | Más lenta (lineal en n_windows) |
| **Riesgo** | El backtest más antiguo usa datos muy viejos que pueden no ser representativos | — |

### Recomendaciones concretas para este dataset (36 meses)

| Caso de uso | h | n_windows | Justificación |
|---|---|---|---|
| **Reposición estándar** | 3 | 3 | Horizonte de 1 trimestre; mín. requerido = 21 meses, bien cubierto |
| **Presupuesto anual** | 6 | 3 | Mín. = 30 meses; SKUs con <2.5 años de historial van a fallback automático |
| **SKU prioritario (A)** | 6 | 4 | Mín. = 36 meses — exactamente al límite; solo sirve para SKUs con historial completo desde 2022 |
| **Exploración/notebook** | 6 | 3 | Configuración actual del notebook — correcta |

> **Regla práctica**: con 36 meses de historial, `n_windows=3` es el máximo seguro para `h=6`. No aumentar a `n_windows=4+` con `h=6` porque excluiría muchos SKUs del horse-race.

---

## 2. Parámetro `granularity`

### Qué es

Define la resolución temporal de la serie de demanda que se alimenta a los modelos. Controla:
- La longitud estacional (`season_length`): ciclo que los modelos intentan capturar
- La frecuencia de actualización del forecast
- El volumen mínimo de datos necesario

| Granularidad | `season_length` | Frecuencia pandas | Ciclo capturado |
|---|---|---|---|
| `"M"` | 12 | `MS` (inicio de mes) | Anual (12 meses) |
| `"W"` | 52 | `W-MON` (lunes) | Anual (52 semanas) |
| `"D"` | 7 | `D` | Semanal (7 días) |

> **Nota**: la granularidad diaria solo captura el ciclo *semanal* (season=7), no el anual. Para ciclo anual diario se necesitaría season=365, lo cual es computacionalmente inviable con los modelos actuales.

### Granularidad actual: mensual (`"M"`) — correcta

La granularidad oficial se define en el manifest (`classification.default_granularity = "M"`) y se usa en toda la clasificación y el forecast cuando no se especifica otra. Es la elección correcta para este dataset por las siguientes razones:

1. **36 meses = 3 ciclos anuales completos** — suficiente para que AutoETS y AutoARIMA estimen estacionalidad mensual con fiabilidad
2. **Perfil industrial** — ciclos de reposición típicamente mensuales o bimensuales, no semanales
3. **Agregación de red** — la clasificación es `network_aggregate` (suma de todas las sucursales); el ruido diario/semanal se suaviza naturalmente
4. **Datos mínimos** — mensual requiere menos observaciones que semanal para el mismo `n_windows` y `h`

### ¿Cuándo considerar granularidad semanal?

Sería beneficiosa solo para SKUs que cumplan **todos** estos criterios:

- ABC = **A** (alta rotación, representa >70% del revenue)
- XYZ = **X** (demanda estable, coeficiente de variación bajo)
- Ciclo de reposición del proveedor < 4 semanas (PO frecuentes)
- Historial ≥ 2 años completos (≥ 104 semanas para 2 ciclos anuales)

Con el dataset actual (800 SKUs, perfil industrial), esta combinación probablemente aplique a una minoría de SKUs. Implementarlo requeriría además re-clasificar esos SKUs en granularidad semanal, lo que cambia `season_length` en toda la cadena.

### ¿Y granularidad diaria?

No recomendada para este dataset:
- `season_length=7` solo captura el patrón día-de-semana, no estacionalidad anual
- El ruido diario en productos industriales suele ser alto
- 36 meses = ~1080 días: suficiente en volumen, pero los modelos estadísticos son más lentos

Podría valer para análisis de patrones de día-de-semana en sucursales con alta venta diaria, pero no para forecast de reposición.

---

## 3. Flujo de selección de modelos: ¿uno o varios?

### Respuesta corta

El horse-race evalúa **múltiples modelos en paralelo**, pero solo **1 modelo ganador** genera el forecast final que se usa en la simulación y en la UI.

### Flujo completo

```
classify_single_sku(SKU)
        │
        ▼
  sb_class? ──────────────────────────────────────────────────────────┐
  inactive                                                            │
        │ → status="no_forecast"  (sin modelo, sin forecast)          │
        │                                                             │
  intermittent / lumpy                                                │
        │ → candidatos: [CrostonSBA, ADIDA]                          │
        │                                                             │
  smooth / erratic                                                    │
        │ → candidatos: [AutoETS, AutoARIMA, SeasonalNaive]           │
        │   + MSTL si is_seasonal=True                                │
        │   + LightGBM si n_obs >= 3×season_length (36 obs mensual)   │
        │                                                             │
        ▼                                                             │
  run_backtest(todos los candidatos)                                  │
  → MASE por modelo, promediado sobre n_windows folds                │
        │                                                             │
        ▼                                                             │
  _pick_winner() → modelo con menor MASE válido                      │
        │                                                             │
        ▼                                                             │
  fit_predict_{ganador}(historial completo)                           │
  → forecast [ds, yhat, yhat_lo80, yhat_hi80] para h períodos        │
        │                                                             │
        └─────────────────────────────────────────────────────────────┘
        ▼
  result = {
    "model": "AutoARIMA",          ← el ganador
    "mase": 0.68,                  ← MASE del ganador en backtest
    "forecast": DataFrame(h rows), ← solo del modelo ganador
    "backtest": {                  ← métricas de TODOS los candidatos
        "AutoETS":      {mase, wape, bias, ...},
        "AutoARIMA":    {mase, wape, bias, ...},
        "SeasonalNaive":{mase, wape, bias, ...},
        "LightGBM":     {mase, wape, bias, ...},
    }
  }
```

### Qué ve la UI vs. qué está disponible

| Elemento | UI (tab Forecast) | API / Notebook |
|---|---|---|
| Modelo usado | ✅ Nombre del ganador | ✅ `result["model"]` |
| MASE del ganador | ✅ Mostrado | ✅ `result["mase"]` |
| Forecast (yhat + IC 80%) | ✅ Gráfico | ✅ `result["forecast"]` |
| Métricas de todos los candidatos | ❌ No mostrado | ✅ `result["backtest"]` → `backtest_summary()` |
| Ranking completo del horse-race | ❌ No mostrado | ✅ Celda 4 del notebook |

### ¿Es correcto que solo se use 1 modelo?

Sí. El objetivo del horse-race es seleccionar el mejor modelo **por SKU**, no hacer ensemble. Las razones:

1. **Interpretabilidad**: el planificador puede entender "este SKU usa AutoARIMA" — un ensemble de 4 modelos es opaco
2. **Simulación determinista**: la simulación de inventario requiere un único vector de demanda forward; un ensemble requeriría definir pesos y complica la propagación de incertidumbre
3. **Los intervalos de confianza ya capturan incertidumbre**: `yhat_lo80` / `yhat_hi80` son el rango de escenarios, no necesariamente el promedio de múltiples modelos

### Implicancias para la simulación

El forecast que entra a la simulación de inventario es:
- `yhat` → demanda esperada (escenario base)
- `yhat_lo80` → demanda baja (escenario conservador / riesgo stockout si se subestima)
- `yhat_hi80` → demanda alta (escenario agresivo / riesgo sobrestock si se subestima)

Los tres valores provienen del **mismo modelo ganador**, no de modelos distintos. Esto es consistente y correcto para el simulador.

---

## 4. Configuración recomendada para producción (dataset actual)

```python
result = service.sku_forecast(
    sku        = "SKU-XXXXX",
    granularity = "M",     # mensual — granularidad oficial del dataset
    h          = 6,        # horizonte de 6 meses (1 semestre)
    n_windows  = 3,        # máximo seguro con 36 meses de historial
)
```

Para un análisis más estable de SKUs con historial completo (36 meses):

```python
result = service.sku_forecast(
    sku        = "SKU-XXXXX",
    granularity = "M",
    h          = 3,        # horizonte trimestral
    n_windows  = 4,        # más folds: mín. = 12 + 12 = 24 obs — OK para SKUs desde ene-22
)
```

> Si un SKU tiene menos historial del requerido, el sistema degrada automáticamente a `status="fallback"` con `SeasonalNaive` o `HistoricAverage`. No falla — genera forecast de todas formas.
