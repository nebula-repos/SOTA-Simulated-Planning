# Inspection Report — Módulo de Forecasting

Fecha: 2026-03-24 (Round 1) · 2026-03-25 (Round 2)
Alcance: `planning_core/forecasting/` — todos los archivos · `apps/viz/app.py` · `planning_core/services.py`

Revisión exhaustiva de formulación, modelado, lógica de código y consistencia de interfaz.
20 hallazgos Round 1 (17 corregidos, 3 tradeoffs de diseño).
6 hallazgos adicionales Round 2 — todos corregidos.
16 tests nuevos agregados en Round 2 (total suite al cierre Round 2: 51 tests).

**Actualización 2026-03-25**: suite ampliada en Round 3 (benchmark adaptativo, backtest selector).
Total suite actual: **82 tests, 82 passing**.

---

## Resumen ejecutivo

### Round 1

| Severidad | Total | Corregidos | Pendiente/Info |
|---|---|---|---|
| HIGH | 3 | 3 | — |
| MEDIUM | 3 | 3 | — |
| LOW | 4 | 4 | — |
| INFO | 10 | 7 | 3 tradeoffs de diseño (INFO-03, INFO-04, INFO-08) |

### Round 2 (inspección del flujo usuario → UI → servicios)

| ID | Archivo(s) | Descripción | Estado |
|---|---|---|---|
| R2-01 | `utils.py` | `to_nixtla_df` no garantizaba orden cronológico | ✅ Corregido |
| R2-02 | `services.py` · `viz/app.py` | UI llamaba `sku_clean_series` dos veces — duplicaba I/O | ✅ Corregido |
| R2-03 | `viz/app.py` | Horse-race se re-ejecutaba en cada re-render de Streamlit | ✅ Corregido |
| R2-04 | `viz/app.py` | Código muerto: bloque `status == "series_too_short"` nunca alcanzable | ✅ Eliminado |
| R2-05 | `viz/app.py` | MASE NaN causaba crash en `f"{mase_val:.3f}"` sin guard | ✅ Corregido |
| R2-06 | `api/main.py` | `demand_series` (DataFrame interno) se serializaba en la respuesta JSON | ✅ Corregido |

---

## Hallazgos

### HIGH-01 — `backtest.py:141-145`: MASE calculado sin datos de entrenamiento

**Archivo**: `planning_core/forecasting/backtest.py`
**Estado**: ✅ Corregido

**Descripción**
El denominador del MASE es el MAE del naive estacional sobre los datos de *entrenamiento* (Hyndman & Koehler, 2006). El código anterior no pasaba `train_actual` a `compute_all_metrics`, por lo que `base` se fijaba al vector `actual` del test (solo `h` valores, p.ej. 6 meses). Resultado: el denominador era estadísticamente inestable y no comparable entre series.

**Antes**
```python
for cutoff_date, window_df in cv_df.groupby("cutoff"):
    actual = window_df["y"].values
    forecast = window_df[model_name].clip(lower=0).values
    m = compute_all_metrics(actual, forecast, season_length=season_length)
```

**Después**
```python
for cutoff_date, window_df in cv_df.groupby("cutoff"):
    train_mask = nixtla_df["ds"] <= cutoff_date
    train_y = nixtla_df.loc[train_mask, "y"].values
    actual = window_df["y"].values
    forecast = window_df[model_name].clip(lower=0).values
    m = compute_all_metrics(actual, forecast, season_length=season_length, train_actual=train_y)
```

---

### HIGH-02 — `metrics.py:83-88`: fallback MASE usa primera diferencia en lugar de naive estacional

**Archivo**: `planning_core/forecasting/metrics.py`
**Estado**: ✅ Corregido

**Descripción**
Cuando `len(base) <= season_length`, el código anterior caía en una rama que calculaba `np.diff(base)` (primera diferencia / random walk naive). Esto rompe la definición de MASE: el denominador debe ser siempre el *naive estacional*, no una métrica alternativa. Con datos insuficientes, la única opción correcta es devolver NaN.

**Antes**
```python
if len(base) <= season_length:
    naive_errors = np.abs(np.diff(base))
    if len(naive_errors) == 0:
        return float("nan")
    mae_naive = float(np.mean(naive_errors))
```

**Después**
```python
if len(base) <= season_length:
    # No hay suficientes datos para el denominador de naive estacional — MASE no calculable
    return float("nan")
```

**Impacto combinado con HIGH-01**: en la práctica, HIGH-01 es la causa raíz; con el fix de HIGH-01 el denominador siempre usará el set de entrenamiento (que tiene ≥ `season_length` obs por la validación del backtest). HIGH-02 es una capa defensiva que garantiza el contrato matemático incluso si se llama `compute_mase` directamente.

---

### HIGH-03 — `lgbm.py:284-289`: `run_backtest_lgbm` tampoco pasaba `train_actual`

**Archivo**: `planning_core/forecasting/models/lgbm.py`
**Estado**: ✅ Corregido

**Descripción**
Mismo problema que HIGH-01, pero en la ruta separada de LightGBM. El backtest de MLForecast también agrupa por cutoff y necesita extraer la ventana de entrenamiento de `nixtla_df`.

**Antes**
```python
for _, window_df in cv_df.groupby("cutoff"):
    actual = window_df["y"].values
    forecast = window_df[model_col].clip(lower=0).values
    m = compute_all_metrics(actual, forecast, season_length=season_length)
```

**Después**
```python
for cutoff_date, window_df in cv_df.groupby("cutoff"):
    train_mask = nixtla_df["ds"] <= cutoff_date
    train_y = nixtla_df.loc[train_mask, "y"].values
    actual = window_df["y"].values
    forecast = window_df[model_col].clip(lower=0).values
    m = compute_all_metrics(actual, forecast, season_length=season_length, train_actual=train_y)
```

---

### MEDIUM-01 — `selector.py:243-246`: ADIDA llama a `fit_predict_sba` que puede devolver CrostonSBA

**Archivo**: `planning_core/forecasting/selector.py`
**Estado**: ✅ Corregido

**Descripción**
Cuando el ganador del horse-race era `"ADIDA"`, el selector llamaba a `fit_predict_sba()`. Esta función contiene lógica interna que selecciona CrostonSBA cuando `n_nonzero >= 3`, ignorando el resultado del horse-race. La función correcta para ADIDA es `fit_predict_adida()`.

**Antes**
```python
elif best_model in ("CrostonSBA", "ADIDA"):
    forecast_result = fit_predict_sba(...)
```

**Después**
```python
elif best_model == "CrostonSBA":
    forecast_result = fit_predict_sba(...)
elif best_model == "ADIDA":
    forecast_result = fit_predict_adida(...)
```

---

### MEDIUM-02 — `lgbm.py:279-282`: `model_column_missing` retorna dict incompleto

**Archivo**: `planning_core/forecasting/models/lgbm.py`
**Estado**: ✅ Corregido

**Descripción**
El dict devuelto cuando la columna del modelo no existe en `cv_df` no incluía `wape`, `bias`, `mae`, `rmse`. Cualquier código que acceda a esos campos causaría `KeyError`.

**Antes**
```python
return {MODEL_NAME: {"status": "model_column_missing", "mase": float("nan"), "n_windows": 0, "h": h}}
```

**Después**
```python
return {MODEL_NAME: {
    "status": "model_column_missing",
    "mase": float("nan"), "wape": float("nan"), "bias": float("nan"),
    "mae": float("nan"), "rmse": float("nan"),
    "n_windows": 0, "h": h,
}}
```

---

### MEDIUM-03 — `mstl.py:97-98`: nivel por defecto `[80]` inconsistente con `ets.py` (`[80, 95]`)

**Archivo**: `planning_core/forecasting/models/mstl.py`
**Estado**: ✅ Corregido

**Descripción**
`fit_predict_ets` genera intervalos al 80 % y 95 %. `fit_predict_mstl` solo generaba al 80 %. La UI y la API solo exponen `yhat_lo80`/`yhat_hi80`, por lo que el IC 95 % está disponible en el raw output pero no expuesto — igual que ETS y ARIMA.

**Fix**: `level = [80, 95]` como default en `fit_predict_mstl`.

---

### LOW-01 — `selector.py:296-299`: cláusula `else` no actualiza `best_model`

**Archivo**: `planning_core/forecasting/selector.py`
**Estado**: ✅ Corregido

**Descripción**
La cláusula `else` final llama a `fit_predict_naive()` pero no asignaba el nombre del modelo retornado a `best_model`. El dict de retorno devolvía el nombre anterior (el que ganó el horse-race pero no tenía handler conocido), no el modelo que realmente generó el forecast.

**Después**
```python
else:
    forecast_result = fit_predict_naive(...)
    best_model = forecast_result["model"]
```

---

### LOW-02 — `naive.py`, `ets.py`, `sba.py`: cada uno tiene su propia `_get_freq()` local

**Archivos**: `models/naive.py`, `models/ets.py`, `models/sba.py`
**Estado**: ✅ Corregido

**Descripción**
Los tres módulos definían `_get_freq(granularity)` de forma local. Se eliminaron las tres funciones y se agregó `FREQ_MAP` al import de `utils`. `arima.py` y `mstl.py` ya usaban `FREQ_MAP` correctamente.

---

### LOW-03 — `backtest.py:137`: `model_column_missing` no incluye `wape`/`bias`/`mae`/`rmse`

**Archivo**: `planning_core/forecasting/backtest.py`
**Estado**: ✅ Corregido

**Descripción**
El dict de la rama `model_column_missing` en `run_backtest` solo incluía `status` y `mase`. Completado con `wape`, `bias`, `mae`, `rmse` y `n_windows`, `h` en `float("nan")` / 0.

---

### LOW-04 — `lgbm.py:61-65`: lag `season_length` duplicado para granularidad `D`

**Archivo**: `planning_core/forecasting/models/lgbm.py`
**Estado**: ✅ Corregido

**Descripción**
Para granularidad `"D"`, `get_season_length("D")` retorna 7. La función `_get_lags("D")` retornaba `[1, 7, 14, 7]` — el lag 7 duplicado. Corregido a `[1, 7, 14, 30]`: día anterior, semana, 2 semanas, mes.

---

### INFO-01 — `metrics.py`: docstring de `compute_mase` describe comportamiento anterior

**Archivo**: `planning_core/forecasting/metrics.py`
**Estado**: ✅ Corregido

**Descripción**
El docstring describía el comportamiento anterior (usar `actual` como proxy). Actualizado para reflejar que sin `train_actual` y con `len(base) <= season_length`, la función devuelve NaN.

---

### INFO-02 — `selector.py`: `get_model_candidates` acepta `sb_class=None` pero lo trata como `"smooth"`

**Archivo**: `planning_core/forecasting/selector.py`
**Estado**: ✅ Corregido

**Descripción**
La conversión silenciosa de `None` a `"smooth"` podía ocultar errores upstream. Se agrega `warnings.warn()` antes del fallback para que el caso sea visible sin interrumpir el flujo.

---

### INFO-03 — `selector.py`: `use_lgbm=True` por defecto puede triplicar el tiempo de backtest

**Archivo**: `planning_core/forecasting/selector.py`
**Estado**: ⚠️ Info / tradeoff de diseño

**Descripción**
LightGBM entrena 3 modelos (point + q10 + q90 para la inferencia final, más 1 para el backtest CV). Con `n_windows=3` y un dataset de 36 meses, puede ser 2-5x más lento que los modelos estadísticos. No es un bug pero es un parámetro que debería exponerse en la UI/API.

**Recomendación**: considerar `use_lgbm=False` como default para la ruta API (latencia interactiva) y reservar `True` para batch.

---

### INFO-04 — `backtest.py`: `step_size` fijo igual a `h` puede generar ventanas solapadas en datos diarios

**Archivo**: `planning_core/forecasting/backtest.py`
**Estado**: ⚠️ Info

**Descripción**
Con granularidad `"D"` y `h=7`, `step_size=7`. Para 3 ventanas se necesitan `7 + 7*3 = 28` días. Es correcto para el mínimo, pero en la práctica las series diarias pueden tener comportamiento autocorrelacionado entre ventanas contiguas. No afecta la corrección pero sí puede inflar la varianza del MASE.

---

### INFO-05 — `utils.py`: `FREQ_MAP["W"] = "W-MON"` difiere del `"W-MON"` de `sba.py` / `naive.py`

**Archivo**: `planning_core/forecasting/utils.py`, `models/naive.py`, `models/ets.py`
**Estado**: ✅ Resuelto por LOW-02

**Descripción**
`utils.py` exporta `FREQ_MAP = {"D": "D", "W": "W-MON", "M": "MS"}`. Los modelos que tienen su propia `_get_freq()` local usan los mismos valores. No hay divergencia ahora, pero si se actualiza `utils.py` los locales quedarán desincronizados.

---

### INFO-06 — `lgbm.py`: `_build_mlforecast` instancia tres MLForecast separados para point + CI

**Archivo**: `planning_core/forecasting/models/lgbm.py`
**Estado**: ✅ Corregido

**Descripción**
`fit_predict_lgbm` construía y entrenaba tres instancias independientes. Refactorizado: `_build_mlforecast` se reemplazó por `_build_mlforecast_point` (para backtest CV, solo modelo de punto) y `_build_mlforecast_full` (para inferencia, con LightGBM + lgbm_q10 + lgbm_q90). `fit_predict_lgbm` ahora hace una sola llamada a `.fit()`.

---

### INFO-07 — `sba.py`: `fit_predict_sba` puede devolver ADIDA aunque el ganador fue CrostonSBA

**Archivo**: `planning_core/forecasting/models/sba.py`
**Estado**: ✅ Parcialmente resuelto (MEDIUM-01 separó el dispatch)

**Descripción**
Antes del fix MEDIUM-01, si el horse-race elegía "CrostonSBA" pero la serie tenía < 3 obs no nulas, `fit_predict_sba` devolvía ADIDA. Ahora el dispatch está separado y el comportamiento de fallback interno de `fit_predict_sba` es correcto: CrostonSBA puede degradar a ADIDA si la serie es muy esporádica, lo cual es el comportamiento deseado.

---

### INFO-08 — `selector.py`: excepción `LightGBM` captura `ImportError` pero no propaga el aviso al usuario

**Archivo**: `planning_core/forecasting/selector.py`
**Estado**: ⚠️ Info

**Descripción**
El bloque `except (ValueError, ImportError)` del dispatch de LightGBM cae silenciosamente a `SeasonalNaive`. Si el motivo es `ImportError` (paquete no instalado), es útil que el `status` en el resultado refleje `"fallback_import_error"` para debugging.

---

### INFO-09 — `backtest.py`: `_MIN_OBS_BACKTEST = 24` definida pero no usada

**Archivo**: `planning_core/forecasting/backtest.py`
**Estado**: ✅ Corregido

**Descripción**
La constante `_MIN_OBS_BACKTEST = 24` era letra muerta. El mínimo real es `season_length + h * n_windows` calculado dinámicamente. Se eliminó del módulo.

---

### INFO-10 — `metrics.py`: `compute_bias` normaliza por `mean(actual)` — indefinido para series con media cero

**Archivo**: `planning_core/forecasting/metrics.py`
**Estado**: ✅ Manejado (devuelve NaN)

**Descripción**
Para series con `mean(actual) == 0` (demanda cero en el periodo de test), `compute_bias` devuelve `NaN`. Este caso es raro pero posible en series intermitentes con ventanas de test sin demanda. El NaN es la respuesta correcta aquí — no hay acción requerida, solo documentarlo.

---

---

## Hallazgos Round 2

### R2-01 — `utils.py`: `to_nixtla_df` no garantizaba orden cronológico

**Archivo**: `planning_core/forecasting/utils.py`
**Estado**: ✅ Corregido

**Descripción**
Si el DataFrame de entrada llegaba con fechas desordenadas (mezcla de fechas de transacciones, joins, etc.), `to_nixtla_df` lo pasaba tal cual a StatsForecast. Las librerías de series de tiempo asumen orden temporal estricto; datos desordenados pueden producir resultados silenciosamente incorrectos.

**Fix**
```python
return nixtla_df.dropna(subset=["y"]).sort_values("ds").reset_index(drop=True)
```

---

### R2-02 — `services.py` / `viz/app.py`: la UI llamaba `sku_clean_series` dos veces

**Archivos**: `planning_core/services.py`, `apps/viz/app.py`
**Estado**: ✅ Corregido

**Descripción**
`sku_forecast` en `services.py` ya computaba la serie limpia internamente para entrenar el modelo. La UI volvía a llamar `sku_clean_series` para obtener el histórico y dibujar el gráfico. Esto doblaba la carga de `transactions.csv` y el cómputo de outliers.

**Fix**: `sku_forecast` añade `demand_series` al dict de resultado; la UI lo lee directamente.

```python
# services.py
result["demand_series"] = model_input  # [period, demand_clean]
return result

# viz/app.py — antes
series_df = service.sku_clean_series(sku, ...)  # segunda carga

# viz/app.py — después
series_df = result.get("demand_series")  # sin I/O adicional
```

---

### R2-03 — `viz/app.py`: horse-race re-ejecutado en cada re-render de Streamlit

**Archivo**: `apps/viz/app.py`
**Estado**: ✅ Corregido

**Descripción**
La función `_render_sku_section_forecast` llamaba `service.sku_forecast(...)` directamente. Streamlit re-ejecuta el script completo ante cualquier cambio de widget (ej. cambiar de pestaña, mover un slider). El horse-race (backtest de 3-5 modelos × 3 ventanas) se corría en cada interacción aunque los parámetros del forecast no hubieran cambiado.

**Fix**: wrapper `@st.cache_data` con clave sobre `(sku, granularity, h, n_windows)`.

```python
@st.cache_data(show_spinner=False)
def _run_sku_forecast(_service: PlanningService, sku: str, granularity: str, h: int, n_windows: int) -> dict:
    return _service.sku_forecast(sku, granularity=granularity, h=h, n_windows=n_windows)
```

El prefijo `_` en `_service` le indica a Streamlit que no intente hashear el objeto servicio.

---

### R2-04 — `viz/app.py`: bloque `status == "series_too_short"` nunca alcanzable

**Archivo**: `apps/viz/app.py`
**Estado**: ✅ Eliminado

**Descripción**
La UI tenía un bloque `elif result["status"] == "series_too_short": st.warning(...)`. `select_and_forecast` nunca retorna ese status — cuando la serie es muy corta, degradan a `"fallback"` con `SeasonalNaive/HistoricAverage`. El status `"series_too_short"` solo existe dentro de `run_backtest` (dict interno, no expuesto al caller). El bloque era dead code que podía inducir a confusión sobre el contrato de la API.

**Fix**: eliminado. Se agregó guard explícito para `status == "no_data"`.

---

### R2-05 — `viz/app.py`: MASE `NaN` causaba crash al formatear con `:.3f`

**Archivo**: `apps/viz/app.py`
**Estado**: ✅ Corregido

**Descripción**
Cuando MASE no era calculable (serie demasiado corta para denominador estacional), `compute_mase` devuelve `float("nan")`. La UI formateaba directamente con `f"{mase_val:.3f}"` — Python lanza `ValueError` si `mase_val` es `None`, y formatea NaN como la cadena `"nan"` (legible pero técnicamente incorrecto para el usuario final).

**Fix**
```python
import math
mase_str = f"{mase_val:.3f}" if (mase_val is not None and not math.isnan(mase_val)) else "N/A"
```

---

### R2-06 — `api/main.py`: `demand_series` (DataFrame interno) se exponía en la respuesta JSON

**Archivo**: `apps/api/main.py`
**Estado**: ✅ Corregido

**Descripción**
Con el fix R2-02, `sku_forecast` retorna `demand_series` como `pd.DataFrame` en el dict. La API serializaba el dict completo con `jsonable_encoder`. Un DataFrame no es serializable directamente por FastAPI — habría causado un `TypeError` en runtime, o (si el framework lo convierte) habría expuesto datos internos a los consumidores de la API.

**Fix**: eliminar la clave antes de serializar.

```python
output = dict(result)
output.pop("demand_series", None)  # campo interno — no exponer en API
return JSONResponse(content=jsonable_encoder(output))
```

---

## Tests nuevos (Round 2)

| Archivo | Test | Qué verifica |
|---|---|---|
| `test_metrics.py` | `test_returns_nan_when_no_train_and_actual_too_short` | HIGH-02 boundary: `len(actual) <= season_length` sin `train_actual` → NaN |
| `test_metrics.py` | `test_returns_nan_when_train_too_short` | HIGH-02: `len(train_actual) <= season_length` → NaN |
| `test_models.py` | `test_unsorted_input_is_sorted_by_ds` | R2-01: input desordenado sale ordenado por `ds` |
| `test_services.py` | `test_sku_forecast_includes_demand_series` | R2-02: resultado incluye `demand_series` como DataFrame |
| `test_services.py` | `test_sku_forecast_no_data_for_unknown_sku` | SKU inexistente → `status="no_data"` sin crash |
| `test_backtest_selector.py` | `TestRunBacktest` (4 tests) | MASE finito, claves completas, serie corta → `series_too_short`, `n_windows` correcto |
| `test_backtest_selector.py` | `TestSelectAndForecast` (7 tests) | Status ok/fallback, no_forecast, Croston/ADIDA, yhat≥0, backtest dict, season_length/granularity, warning sb_class=None |

Suite total: **51 tests, 51 passing**.

---

## Tradeoffs de diseño abiertos (sin acción inmediata requerida)

| ID | Archivo | Descripción |
|---|---|---|
| INFO-03 | `selector.py` | `use_lgbm=True` por defecto puede ser lento en API interactiva — considerar `False` como default para la ruta HTTP |
| INFO-04 | `backtest.py` | `step_size = h` para datos diarios puede generar ventanas con autocorrelación — solo relevante si se usa granularidad `"D"` |
| INFO-08 | `selector.py` | `ImportError` de LightGBM cae silenciosamente a `SeasonalNaive` — considerar `"fallback_import_error"` en el status si se necesita observabilidad |
