# Plan: Barrido de Parametrización del Horse-Race

## Objetivo

Determinar empíricamente qué combinación de `(h, n_windows)` produce
los mejores MASE por tipo de producto (segmento SB, clase ABC, estacionalidad),
y si la elección de parámetros afecta la selección del modelo ganador.

---

## Contexto

El `exp_02_catalog_eval.py` corre una sola configuración fija:

```
h=3, n_windows=3  →  "baseline"
```

Pero no sabemos:
- ¿Un horizonte más largo (h=6) penaliza más a ciertos tipos de producto?
- ¿Más ventanas de backtest (n_windows=4-6) mejoran la selección del modelo o solo reducen la cobertura?
- ¿Los SKUs smooth no estacionales y los intermitentes responden distinto a estos parámetros?

---

## Restricción de datos mínimos

```
min_obs = season_length + h × n_windows = 12 + h × n_windows
Historial disponible: 36 meses
```

| h  | n_windows | min_obs | Viable | SKUs en riesgo de fallback                 |
|----|-----------|---------|--------|--------------------------------------------|
| 3  | 3         | 21      | ✅     | Mínimo — pocos SKUs afectados              |
| 3  | 4         | 24      | ✅     | SKUs con <2 años                           |
| 3  | 5         | 27      | ✅     | SKUs con <2.25 años                        |
| 3  | 6         | 30      | ✅     | SKUs con <2.5 años                         |
| 6  | 3         | 30      | ✅     | SKUs con <2.5 años                         |
| 6  | 4         | 36      | ✅⚠️   | Solo SKUs con historial completo desde ene-22 |
| 6  | 5         | 42      | ❌     | Excluye ~40% del catálogo                  |
| 12 | 3         | 48      | ❌     | Excluye todo el catálogo (36 < 48)         |

---

## Grid de experimentos

| run_name      | h | n_windows | Descripción                           |
|---------------|---|-----------|---------------------------------------|
| sweep_h3_w3   | 3 | 3         | Baseline trimestral (igual a exp_02)  |
| sweep_h3_w4   | 3 | 4         | +1 ventana, más estable               |
| sweep_h3_w5   | 3 | 5         | Máxima estabilidad con h=3            |
| sweep_h3_w6   | 3 | 6         | Agresivo — requiere 30 obs            |
| sweep_h6_w3   | 6 | 3         | Semestral, 3 ventanas                 |
| sweep_h6_w4   | 6 | 4         | Semestral, 4 ventanas (límite exacto) |

---

## Análisis de salida (exp_03_param_sweep.py)

### 1. Tabla global de comparación

Por cada config: `mase_median`, `mase_p75`, `mase_p90`, `fallback_rate`, `elapsed_s`.

Permite ver:
- ¿Aumentar n_windows mejora el MASE global o solo lo estabiliza?
- ¿Cuántos SKUs pierden coverage al aumentar h?

### 2. Pivot MASE por sb_class × config

```
sb_class      | h3_w3 | h3_w4 | h3_w5 | h3_w6 | h6_w3 | h6_w4
--------------|-------|-------|-------|-------|-------|------
smooth        | 0.85  | 0.83  | 0.82  | 0.81  | 0.91  | 0.92
erratic       | 1.12  | 1.10  | 1.09  | ...   | ...   | ...
intermittent  | 0.74  | 0.75  | ...   | ...   | ...   | ...
lumpy         | 0.91  | ...   | ...   | ...   | ...   | ...
```

Permite ver: ¿los SKUs smooth mejoran con más ventanas? ¿los intermitentes no les importa?

### 3. Pivot MASE por is_seasonal × config

Confirmar si el benchmark adaptativo (lag1 vs seasonal) interactúa con los parámetros.

### 4. Pivot MASE por abc_class × config

¿Los SKUs A (alta rotación) responden diferente a los C (baja rotación)?

### 5. Cambios de modelo ganador

Para cada config vs. baseline (h3_w3): cuántos SKUs cambiaron de modelo ganador.
Un alto % de cambios indica inestabilidad en la selección.

---

## Implementación: `experiments/exp_03_param_sweep.py`

**Flujo:**
1. Definir `PARAM_GRID` con las 6 configs viables
2. Para cada config: check si ya existe un run con ese `run_name` → skip si `SKIP_EXISTING=True`
3. Correr `run_catalog_evaluation()` con `n_jobs=N_JOBS` para cada config nueva
4. Guardar cada run en `output/eval_runs/` con `run_store.save_run()`
5. Cargar todos los run_ids y ejecutar el análisis comparativo con `comparator`

**Parámetros configurables en el script:**
- `SKIP_EXISTING` — no re-corre configs ya guardadas
- `N_JOBS` — paralelismo por config
- `BASE_DIR` — directorio de outputs
- `USE_LGBM` — incluir LightGBM (desactivado por defecto: 3x más lento)

---

## Inferencias esperadas

| Pregunta | Indicador |
|----------|-----------|
| ¿Más ventanas mejoran el MASE? | mase_median cae al aumentar n_windows |
| ¿h=6 penaliza más a ciertos segmentos? | Δ MASE entre h3_w3 y h6_w3 mayor en smooth que en intermittent |
| ¿Parámetros afectan selección de modelo? | % cambios de ganador > 20% entre configs |
| ¿Cuál es la config óptima para producción? | Menor mase_median con fallback_rate aceptable (<15%) |
