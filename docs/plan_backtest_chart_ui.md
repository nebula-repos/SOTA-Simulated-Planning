# Plan: Gráfico de Backtest Horse-Race en la UI

## Objetivo

Agregar un gráfico junto al forecast que muestre, para cada modelo que compitió en el horse-race, sus predicciones sobre los períodos de backtest versus la demanda real. El usuario puede ver visualmente por qué un modelo ganó sobre los otros.

## Diseño del gráfico

```
[Demanda histórica (azul sólido)] ─────────────────────────── | [Forecast (rojo)]
                                                               ^
                                            ventanas de backtest
                                 │window 1│  │window 2│  │window 3│

Trazas por modelo (en las ventanas):
  AutoARIMA  ── ── ──  (verde, ganador, más grueso)
  AutoETS    ·· ·· ··  (naranja)
  SeasonalNaive __ __ (gris)
  Actual     ●──●──●   (negro, línea real en el período de backtest)
```

Layout en la UI: **dos tabs** bajo los KPIs:
- Tab "Forecast" → gráfico actual (histórico + proyección futura)
- Tab "Backtest horse-race" → gráfico nuevo con predicciones por modelo

---

## Cambios necesarios (5 pasos)

---

### Paso 1 — `backtest.py`: exponer `cv_df` opcionalmente

**Archivo:** `planning_core/forecasting/backtest.py`
**Función:** `run_backtest()`

Agregar parámetro `return_cv: bool = False`. Cuando es `True`, incluir el DataFrame completo de cross-validation en el resultado.

```python
def run_backtest(
    ...,
    return_cv: bool = False,     # nuevo
) -> dict[str, dict]:
    ...
    cv_df = sf.cross_validation(...)      # ya existe
    ...
    results: dict[str, dict] = { ... }    # ya existe

    if return_cv:
        results["__cv_df__"] = cv_df      # clave especial con el df completo

    return results
```

**`cv_df` estructura** (columnas de StatsForecast):
```
unique_id | ds        | cutoff     | y    | AutoETS | AutoARIMA | SeasonalNaive | ...
SKU-001   | 2024-10-01| 2024-09-01 | 142  | 138.2   | 145.1     | 130.0         | ...
SKU-001   | 2024-11-01| 2024-09-01 | 160  | 155.1   | 162.3     | 148.0         | ...
```

---

### Paso 2 — `selector.py`: pasar `return_cv` hacia abajo

**Archivo:** `planning_core/forecasting/selector.py`
**Función:** `select_and_forecast()`

```python
def select_and_forecast(
    ...,
    return_cv: bool = False,    # nuevo
) -> dict:
    ...
    backtest_results = run_backtest(..., return_cv=return_cv)

    cv_df = backtest_results.pop("__cv_df__", None)   # extraer sin contaminar métricas

    ...
    result = { "backtest": backtest_results, ... }

    if cv_df is not None:
        result["cv_df"] = cv_df

    return result
```

---

### Paso 3 — `services.py`: pasar `return_cv` a `sku_forecast()`

**Archivo:** `planning_core/services.py`
**Función:** `PlanningService.sku_forecast()`

```python
def sku_forecast(
    self,
    sku: str,
    ...,
    return_cv: bool = False,    # nuevo
) -> dict:
    ...
    return select_and_forecast(..., return_cv=return_cv)
```

---

### Paso 4 — `app.py`: nueva función `build_backtest_figure()`

**Archivo:** `apps/viz/app.py`

Nueva función constructora del gráfico (misma convención que `build_line_figure`, etc.):

```python
def build_backtest_figure(
    cv_df: pd.DataFrame,
    hist_df: pd.DataFrame,       # demanda histórica completa (ds, y)
    winner_model: str,
    backtest_metrics: dict,      # {model: {mase: float, ...}}
    sku: str,
) -> go.Figure:
    """
    Gráfico del horse-race de backtest:
    - Línea histórica completa (antes del primer cutoff)
    - Línea negra de demanda real en cada ventana de backtest
    - Una línea coloreada por modelo (dashed) en cada ventana
    - El ganador resaltado con mayor grosor
    - Líneas verticales grises en cada cutoff
    - Leyenda con MASE de cada modelo
    """
    COLORS = {
        "AutoETS": "#e67e22",
        "AutoARIMA": "#27ae60",
        "SeasonalNaive": "#8e44ad",
        "MSTL": "#2980b9",
        "CrostonSBA": "#c0392b",
        "ADIDA": "#16a085",
    }

    fig = go.Figure()
    cutoffs = sorted(cv_df["cutoff"].unique())
    first_cutoff = cutoffs[0]

    # 1. Histórico hasta el primer cutoff
    pre = hist_df[hist_df["ds"] <= first_cutoff]
    fig.add_trace(go.Scatter(x=pre["ds"], y=pre["y"],
                             name="Histórico", line=dict(color="#2980b9", width=2)))

    # 2. Demanda real en período de backtest (una traza por ventana, misma leyenda)
    for i, cutoff in enumerate(cutoffs):
        window = cv_df[cv_df["cutoff"] == cutoff]
        fig.add_trace(go.Scatter(
            x=window["ds"], y=window["y"],
            name="Real (backtest)" if i == 0 else None,
            showlegend=(i == 0),
            line=dict(color="black", width=2),
            mode="lines+markers",
        ))
        # Línea vertical de corte
        fig.add_vline(x=cutoff, line_dash="dot", line_color="gray", opacity=0.4)

    # 3. Predicciones por modelo
    model_cols = [c for c in cv_df.columns if c not in ("unique_id", "ds", "cutoff", "y")]
    for model in model_cols:
        mase = backtest_metrics.get(model, {}).get("mase", float("nan"))
        label = f"{model}  MASE={mase:.3f}" if not math.isnan(mase) else model
        is_winner = (model == winner_model)
        color = COLORS.get(model, "#999999")

        for i, cutoff in enumerate(cutoffs):
            window = cv_df[cv_df["cutoff"] == cutoff]
            fig.add_trace(go.Scatter(
                x=window["ds"], y=window[model].clip(lower=0),
                name=label if i == 0 else None,
                showlegend=(i == 0),
                line=dict(color=color,
                          width=3 if is_winner else 1.5,
                          dash="solid" if is_winner else "dash"),
                opacity=1.0 if is_winner else 0.65,
            ))

    fig.update_layout(
        title=f"{sku} — Backtest horse-race  (ganador: {winner_model})",
        xaxis_title="Período",
        yaxis_title="Demanda",
        template="plotly_white",
        height=420,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=20, r=20, t=80, b=20),
    )
    return fig
```

---

### Paso 5 — `app.py`: integrar en `_render_sku_section_forecast()`

Reemplazar la llamada de forecast y el render del gráfico actual:

```python
# Llamada con return_cv=True
result = _run_sku_forecast(service, selected_sku, granularity, int(h), int(n_windows),
                           return_cv=True)    # nuevo parámetro

...

# Tabs para los dos gráficos
tab_fc, tab_bt = st.tabs(["📈 Forecast", "🔬 Backtest horse-race"])

with tab_fc:
    # Gráfico actual (sin cambios)
    st.plotly_chart(fig, use_container_width=True)
    # Tabla de forecast (sin cambios)
    ...

with tab_bt:
    cv_df = result.get("cv_df")
    if cv_df is not None and not cv_df.empty:
        bt_fig = build_backtest_figure(
            cv_df=cv_df,
            hist_df=hist,                    # ya construido para el forecast chart
            winner_model=result.get("model"),
            backtest_metrics=result.get("backtest", {}),
            sku=selected_sku,
        )
        st.plotly_chart(bt_fig, use_container_width=True)
        # Tabla de métricas por modelo (ya existe debajo del forecast)
        ...
    else:
        st.info("No hay datos de backtest disponibles (serie muy corta o fallback).")
```

---

### Cambio en el cache de `_run_sku_forecast`

La función está decorada con `@st.cache_data`. Al agregar `return_cv`, el cache incluirá el `cv_df`. Es más pesado pero correcto — el cache key ya incluye todos los parámetros.

```python
@st.cache_data
def _run_sku_forecast(_service, sku, granularity, h, n_windows,
                      return_cv: bool = False):   # nuevo
    return _service.sku_forecast(sku, ..., return_cv=return_cv)
```

---

## Archivos a modificar (en orden)

| # | Archivo | Cambio |
|---|---------|--------|
| 1 | `planning_core/forecasting/backtest.py` | Agregar `return_cv` a `run_backtest()` |
| 2 | `planning_core/forecasting/selector.py` | Pasar `return_cv`, extraer `cv_df` del resultado |
| 3 | `planning_core/services.py` | Pasar `return_cv` a `sku_forecast()` |
| 4 | `apps/viz/app.py` | Agregar `build_backtest_figure()` + tabs + `return_cv` en cache |
| 5 | `tests/test_backtest_selector.py` | Test: `run_backtest(return_cv=True)` retorna `__cv_df__` con columnas correctas |

---

## Consideraciones

- **Performance:** `cv_df` se genera en el horse-race de todas formas — no hay costo adicional de cómputo, solo de memoria/serialización en el cache de Streamlit.
- **Catálogo masivo:** `catalog_runner.py` llama `select_and_forecast` con `return_cv=False` (default) — sin impacto en la evaluación masiva.
- **LightGBM fallido:** Si un modelo tiene `status=error` en backtest (como LightGBM actualmente), su columna no estará en `cv_df` → `build_backtest_figure` simplemente no lo incluye.
- **Fallback (HistoricAverage):** Si `status=fallback`, no hay `cv_df`. El tab de backtest muestra el mensaje informativo.
