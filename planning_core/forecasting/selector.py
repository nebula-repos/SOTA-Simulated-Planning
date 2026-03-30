"""Selector automatico de modelo de forecast por SKU.

Dado el perfil de clasificacion de un SKU, determina los modelos candidatos
y ejecuta un horse-race empirico via backtest expanding-window para elegir
el mejor modelo.

Reglas de candidatura
---------------------

Fase 2 (modelos estadisticos base):

| Clasificacion SB | Seasonal | Candidatos SF                            |
|------------------|----------|------------------------------------------|
| smooth           | True     | AutoETS, AutoARIMA, MSTL, SeasonalNaive  |
| smooth           | False    | AutoETS, AutoARIMA, SeasonalNaive        |
| erratic          | —        | AutoETS, AutoARIMA, SeasonalNaive        |
| intermittent     | —        | CrostonSBA, ADIDA                        |
| lumpy            | —        | CrostonSBA, ADIDA                        |
| inactive         | —        | sin forecast                             |

Fase 3 (modelos ML — camino separado):

LightGBM se evalua via ``run_backtest_lgbm()`` y sus resultados se fusionan
al backtest antes de elegir el ganador. Solo aplica a series smooth/erratic
con suficientes datos (>= 3 * season_length).

Seleccion del ganador (``_pick_winner``)
-----------------------------------------
1. Filtrar candidatos validos (status="ok", MASE no NaN).
2. Ordenar por MASE ascendente.
3. RMSSE tiebreak: si |MASE_1 - MASE_2| < 0.02, preferir menor RMSSE.
4. Filtro de sesgo: si el ganador tiene |Bias| > 0.20 y existe un modelo con
   MASE dentro del 20% y menor |Bias|, preferir ese modelo.

Ensemble top-k (``_apply_ensemble``)
--------------------------------------
Si existen >= 2 modelos con MASE <= ganador * 1.25, se promedian sus forecasts
hasta k=3. El resultado se etiqueta como "Ensemble".

Correccion de sesgo (``_apply_bias_correction``)
--------------------------------------------------
Al forecast final se aplica yhat_corrected = yhat / (1 + bias), acotado a ±30%.
Solo aplica si |bias| >= 0.02. Siempre retorna una copia nueva del DataFrame.

Retorno de ``select_and_forecast``
------------------------------------
dict con claves: ``status``, ``model``, ``mase``, ``bias``, ``backtest``,
``forecast``, ``season_length``, ``granularity``, ``h``.
``model`` puede ser ``"Ensemble"`` si se activó el promedio de modelos.

Uso tipico
----------
>>> result = select_and_forecast(profile, demand_df, granularity="M", h=3)
>>> result["model"]      # "AutoARIMA" o "Ensemble"
>>> result["mase"]       # 0.68
>>> result["forecast"]   # pd.DataFrame con [ds, yhat, yhat_lo80, yhat_hi80]
"""

from __future__ import annotations

import math
import warnings
from typing import Any

import numpy as np
import pandas as pd
from statsforecast.models import ADIDA, AutoARIMA, AutoETS, CrostonSBA, HistoricAverage, MSTL, SeasonalNaive

from planning_core.forecasting.backtest import run_backtest
from planning_core.forecasting.models.arima import fit_predict_arima
from planning_core.forecasting.models.ets import fit_predict_ets
from planning_core.forecasting.models.lgbm import run_backtest_lgbm, fit_predict_lgbm
from planning_core.forecasting.models.mstl import fit_predict_mstl, get_mstl_model
from planning_core.forecasting.models.naive import fit_predict_naive
from planning_core.forecasting.models.sba import fit_predict_adida, fit_predict_sba
from planning_core.forecasting.utils import get_season_length

# ---------------------------------------------------------------------------
# Constantes de clasificacion
# ---------------------------------------------------------------------------

_INTERMITTENT_CLASSES = {"intermittent", "lumpy"}
_NO_FORECAST_CLASSES = {"inactive"}

# Minimo de observaciones para incluir LightGBM en el horse-race
_MIN_OBS_LGBM_FACTOR = 3

# Seleccion del ganador — umbrales
_MASE_TIE_THRESHOLD: float = 0.02          # diferencia maxima de MASE para aplicar tiebreak RMSSE
_BIAS_HIGH_THRESHOLD: float = 0.20         # |Bias| por encima del cual se considera sesgado
_BIAS_PREFERENCE_MASE_SLACK: float = 0.20  # MASE slack para preferir modelo menos sesgado

# Ensemble
_ENSEMBLE_MASE_THRESHOLD: float = 1.25     # modelos con MASE <= ganador * este factor entran al ensemble
_ENSEMBLE_MAX_K: int = 3                   # numero maximo de modelos en el ensemble

# Correccion de sesgo
_BIAS_CORRECTION_MIN_ABS: float = 0.02     # no corregir si |bias| < este valor
_BIAS_CORRECTION_MAX_ADJ: float = 0.30     # cap de ajuste en ±30%


# ---------------------------------------------------------------------------
# Logica de candidatos por clasificacion
# ---------------------------------------------------------------------------

def get_model_candidates(
    sb_class: str,
    season_length: int,
    is_seasonal: bool = False,
) -> tuple[list[Any], list[str]]:
    """Retorna los modelos candidatos para el horse-race dado el tipo de demanda.

    Parameters
    ----------
    sb_class : str
        Clasificacion Syntetos-Boylan.
    season_length : int
        Longitud estacional para configurar los modelos.
    is_seasonal : bool
        Si True y la serie es smooth, agrega MSTL al horse-race.

    Returns
    -------
    tuple[list, list[str]]
        ``(model_instances, model_names)`` — instancias para StatsForecast
        y sus nombres correspondientes.
    """
    sb = sb_class.lower() if sb_class else "smooth"

    if sb in _INTERMITTENT_CLASSES:
        return (
            [CrostonSBA(), ADIDA()],
            ["CrostonSBA", "ADIDA"],
        )

    # smooth, erratic o desconocido — candidatos ETS + ARIMA
    instances = [
        AutoETS(season_length=season_length),
        AutoARIMA(season_length=season_length),
        SeasonalNaive(season_length=season_length),
    ]
    names = ["AutoETS", "AutoARIMA", "SeasonalNaive"]

    # MSTL solo para series con estacionalidad detectada
    if is_seasonal:
        instances.insert(2, MSTL(season_length=season_length, trend_forecaster=AutoETS(model="ZZN")))
        names.insert(2, "MSTL")

    return instances, names


# ---------------------------------------------------------------------------
# Selector principal
# ---------------------------------------------------------------------------

def select_and_forecast(
    profile: dict,
    demand_df: pd.DataFrame,
    granularity: str = "M",
    h: int = 3,
    n_windows: int = 3,
    unique_id: str | None = None,
    target_col: str = "demand",
    use_lgbm: bool = True,
    return_cv: bool = False,
) -> dict:
    """Selecciona el mejor modelo para un SKU y genera el forecast.

    Flujo:
    1. Lee la clasificacion SB del perfil.
    2. Si el SKU es ``inactive``, retorna ``{"status": "no_forecast"}``.
    3. Determina los modelos candidatos estadisticos por clasificacion.
    4. Corre backtest expanding-window sobre todos los candidatos estadisticos.
    5. Opcionalmente, corre backtest LightGBM y fusiona al resultado.
    6. Elige el modelo ganador (MASE + RMSSE tiebreak + filtro de sesgo).
    7. Genera forecast final: ensemble top-k si hay multiples candidatos cercanos,
       forecast individual en caso contrario.
    8. Aplica correccion de sesgo al forecast final.

    Parameters
    ----------
    profile : dict
        Perfil de clasificacion del SKU (``classify_sku()`` o ``classify_single_sku()``).
        Debe contener ``"sb_class"``, ``"sku"`` y opcionalmente ``"is_seasonal"``.
    demand_df : pd.DataFrame
        Serie de demanda limpia ``[period, demand]``.
    granularity : str
        ``"D"``, ``"W"`` o ``"M"``.
    h : int
        Horizonte de pronostico en periodos.
    n_windows : int
        Ventanas del backtest.
    unique_id : str, optional
        Identificador de la serie. Si None, usa ``profile["sku"]``.
    target_col : str
        Columna objetivo en ``demand_df``.
    use_lgbm : bool
        Si True, incluye LightGBM en el horse-race cuando hay datos suficientes.
        Default True.
    return_cv : bool
        Si True, incluye el DataFrame de cross-validation en el resultado.

    Returns
    -------
    dict con claves:
        - ``status`` : ``"ok"``, ``"no_forecast"``, ``"fallback"``, ``"error"``
        - ``model`` : nombre del modelo ganador (o ``"Ensemble"`` si aplica)
        - ``mase`` : MASE promedio del ganador en backtest (float, puede ser NaN)
        - ``bias`` : Bias promedio del ganador en backtest (float, puede ser NaN)
        - ``backtest`` : dict completo con métricas de todos los candidatos
        - ``forecast`` : pd.DataFrame ``[ds, yhat, yhat_lo80, yhat_hi80]``
          con bias correction ya aplicado (si |bias| >= 0.02)
        - ``season_length`` : int
        - ``granularity`` : str
        - ``h`` : int
    """
    raw_sb = profile.get("sb_class")
    if not raw_sb:
        warnings.warn(
            "sb_class es None o vacío en el perfil — usando 'smooth' como fallback. "
            "Verificar que classify_sku/classify_single_sku retornen sb_class correctamente.",
            stacklevel=2,
        )
    sb_class = (raw_sb or "smooth").lower()
    is_seasonal = bool(profile.get("is_seasonal", False))
    uid = unique_id or profile.get("sku", "SKU")
    season_length = get_season_length(granularity)

    # 1. SKUs inactivos no se pronostican
    if sb_class in _NO_FORECAST_CLASSES:
        return {
            "status": "no_forecast",
            "reason": "inactive SKU",
            "model": None,
            "mase": float("nan"),
            "bias": float("nan"),
            "forecast": pd.DataFrame(),
            "backtest": {},
            "season_length": season_length,
            "granularity": granularity,
            "h": h,
        }

    # 2. Candidatos estadisticos segun clasificacion
    model_instances, model_names = get_model_candidates(sb_class, season_length, is_seasonal)
    naive_type = _get_naive_type(sb_class, is_seasonal)

    # 3. Horse-race estadistico via StatsForecast
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        backtest_results = run_backtest(
            demand_df=demand_df,
            model_instances=model_instances,
            model_names=model_names,
            granularity=granularity,
            h=h,
            n_windows=n_windows,
            unique_id=uid,
            target_col=target_col,
            naive_type=naive_type,
            return_cv=return_cv,
        )

    cv_df = backtest_results.pop("__cv_df__", None)

    # 4. Horse-race LightGBM (camino separado, solo para series no intermitentes)
    if use_lgbm and sb_class not in _INTERMITTENT_CLASSES:
        n_obs = len(demand_df)
        if n_obs >= _MIN_OBS_LGBM_FACTOR * season_length:
            try:
                lgbm_results = run_backtest_lgbm(
                    demand_df=demand_df,
                    granularity=granularity,
                    h=h,
                    n_windows=n_windows,
                    unique_id=uid,
                    target_col=target_col,
                    naive_type=naive_type,
                )
                backtest_results.update(lgbm_results)
            except Exception as _lgbm_exc:
                warnings.warn(
                    f"LightGBM backtest falló para {uid!r}: {_lgbm_exc}. "
                    "Continuando con candidatos estadísticos.",
                    stacklevel=2,
                )

    # 5. Elegir ganador con criterios multi-metrica
    best_model, best_mase, best_bias = _pick_winner(backtest_results)

    # 6. Intentar ensemble top-k si hay multiples modelos cercanos
    status = "ok"
    try:
        if best_model is None:
            # Fallback: serie muy corta para backtest
            status = "fallback"
            forecast_result = fit_predict_naive(
                demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
            )
            best_model = forecast_result["model"]
            forecast_df = forecast_result["forecast"]

        else:
            # Intentar ensemble si hay >= 2 candidatos dentro del umbral
            ensemble_df = _apply_ensemble(
                backtest_results, best_mase, demand_df, granularity, h, uid, target_col
            )

            if ensemble_df is not None:
                best_model = "Ensemble"
                forecast_df = ensemble_df
            else:
                forecast_result = _fit_predict_model(
                    best_model, demand_df, granularity, h, uid, target_col
                )
                if forecast_result is None:
                    status = "fallback"
                    forecast_result = fit_predict_naive(
                        demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
                    )
                    best_model = forecast_result["model"]
                forecast_df = forecast_result["forecast"]

        # 7. Correccion de sesgo al forecast final
        forecast_df = _apply_bias_correction(forecast_df, best_bias)

    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "model": None,
            "mase": float("nan"),
            "bias": float("nan"),
            "forecast": pd.DataFrame(),
            "backtest": backtest_results,
            "season_length": season_length,
            "granularity": granularity,
            "h": h,
        }

    result = {
        "status": status,
        "model": best_model,
        "mase": best_mase,
        "bias": best_bias,
        "backtest": backtest_results,
        "forecast": forecast_df,
        "season_length": season_length,
        "granularity": granularity,
        "h": h,
    }
    if cv_df is not None:
        result["cv_df"] = cv_df
    return result


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _get_naive_type(sb_class: str, is_seasonal: bool) -> str:
    """Determina el tipo de benchmark correcto para MASE según la clasificación del SKU.

    - intermittent / lumpy  → "mean"     (lag-1/lag-12 son inestables con muchos ceros)
    - smooth / erratic estacional → "seasonal" (lag-12 es el benchmark relevante)
    - smooth / erratic no estacional → "lag1" (random walk, benchmark más honesto)
    """
    if sb_class in _INTERMITTENT_CLASSES:
        return "mean"
    if is_seasonal:
        return "seasonal"
    return "lag1"


def _pick_winner(
    backtest_results: dict[str, dict],
) -> tuple[str | None, float, float]:
    """Retorna ``(nombre, MASE, Bias)`` del modelo ganador.

    Criterios de seleccion en orden de prioridad:
    1. Filtrar candidatos validos (status="ok", MASE no NaN).
    2. Ordenar por MASE ascendente.
    3. RMSSE tiebreak: si |MASE_1 - MASE_2| < 0.02 y RMSSE_2 < RMSSE_1, preferir #2.
    4. Filtro de sesgo: si el ganador tiene |Bias| > 0.20 y existe un modelo
       con MASE dentro del 20% y menor |Bias|, preferir ese modelo.

    Retorna (None, NaN, NaN) si ningun modelo tiene MASE valido.
    """
    valid = [
        (
            name,
            m["mase"],
            m.get("rmsse", float("nan")),
            m.get("bias", float("nan")),
        )
        for name, m in backtest_results.items()
        if m.get("status") == "ok" and not math.isnan(m.get("mase", float("nan")))
    ]

    if not valid:
        return None, float("nan"), float("nan")

    valid.sort(key=lambda x: x[1])  # por MASE ascendente
    best_name, best_mase, best_rmsse, best_bias = valid[0]

    # RMSSE tiebreak
    if len(valid) >= 2:
        r_name, r_mase, r_rmsse, r_bias = valid[1]
        if abs(best_mase - r_mase) < _MASE_TIE_THRESHOLD:
            if not math.isnan(r_rmsse) and not math.isnan(best_rmsse) and r_rmsse < best_rmsse:
                best_name, best_mase, best_rmsse, best_bias = r_name, r_mase, r_rmsse, r_bias

    # Filtro de sesgo: si el ganador esta muy sesgado, buscar alternativa menos sesgada
    if not math.isnan(best_bias) and abs(best_bias) > _BIAS_HIGH_THRESHOLD:
        mase_ceiling = best_mase * (1.0 + _BIAS_PREFERENCE_MASE_SLACK)
        for name, mase, rmsse, bias in valid[1:]:
            if mase > mase_ceiling:
                break
            if not math.isnan(bias) and abs(bias) < abs(best_bias):
                best_name, best_mase, best_rmsse, best_bias = name, mase, rmsse, bias
                break

    return best_name, best_mase, best_bias


def _fit_predict_model(
    model_name: str,
    demand_df: pd.DataFrame,
    granularity: str,
    h: int,
    uid: str,
    target_col: str,
) -> dict | None:
    """Despacha la prediccion al modulo correcto segun el nombre del modelo.

    Retorna el dict ``{"forecast": pd.DataFrame, ...}`` del modelo o None si falla.
    """
    try:
        if model_name == "CrostonSBA":
            return fit_predict_sba(demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col)
        elif model_name == "ADIDA":
            return fit_predict_adida(demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col)
        elif model_name == "AutoETS":
            return fit_predict_ets(demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col)
        elif model_name == "AutoARIMA":
            return fit_predict_arima(demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col)
        elif model_name == "MSTL":
            return fit_predict_mstl(demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col)
        elif model_name == "LightGBM":
            return fit_predict_lgbm(demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col)
        elif model_name in ("SeasonalNaive", "HistoricAverage"):
            return fit_predict_naive(demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col)
        else:
            return None
    except Exception as exc:
        warnings.warn(
            f"_fit_predict_model: fallo al predecir con {model_name!r} para {uid!r}: {exc}. "
            "Modelo excluido del ensemble.",
            stacklevel=2,
        )
        return None


def _apply_ensemble(
    backtest_results: dict[str, dict],
    best_mase: float,
    demand_df: pd.DataFrame,
    granularity: str,
    h: int,
    uid: str,
    target_col: str,
) -> pd.DataFrame | None:
    """Genera forecast ensemble promediando los top-k modelos dentro del umbral MASE.

    Solo activa si hay >= 2 modelos con MASE <= best_mase * 1.25.
    Retorna None si el ensemble no aplica (candidatos insuficientes o fallos).
    """
    if math.isnan(best_mase):
        return None

    mase_ceiling = best_mase * _ENSEMBLE_MASE_THRESHOLD
    candidates = [
        name
        for name, m in backtest_results.items()
        if m.get("status") == "ok"
        and not math.isnan(m.get("mase", float("nan")))
        and m["mase"] <= mase_ceiling
    ]

    candidates.sort(key=lambda n: backtest_results[n]["mase"])
    candidates = candidates[:_ENSEMBLE_MAX_K]

    if len(candidates) < 2:
        return None

    forecasts: list[pd.DataFrame] = []
    for name in candidates:
        result = _fit_predict_model(name, demand_df, granularity, h, uid, target_col)
        if result is not None and not result["forecast"].empty:
            forecasts.append(result["forecast"])

    if len(forecasts) < 2:
        return None

    base = forecasts[0].copy()
    yhats = np.array([f["yhat"].values for f in forecasts])
    base["yhat"] = np.mean(yhats, axis=0).clip(min=0)

    for col in ("yhat_lo80", "yhat_hi80"):
        if col in base.columns and all(col in f.columns for f in forecasts):
            vals = np.array([f[col].values for f in forecasts])
            base[col] = np.mean(vals, axis=0).clip(min=0)

    return base


def _apply_bias_correction(forecast_df: pd.DataFrame, bias: float) -> pd.DataFrame:
    """Corrige el sesgo sistematico del forecast.

    yhat_corrected = yhat / (1 + bias), acotado a un ajuste maximo de ±30%.

    Bias positivo → modelo sobreestima → correccion reduce yhat.
    Bias negativo → modelo subestima → correccion incrementa yhat.

    No aplica si |bias| < 0.02 (ruido insignificante).
    """
    if math.isnan(bias) or abs(bias) < _BIAS_CORRECTION_MIN_ABS:
        return forecast_df.copy()

    raw_factor = 1.0 / (1.0 + bias)
    lo = 1.0 - _BIAS_CORRECTION_MAX_ADJ
    hi = 1.0 + _BIAS_CORRECTION_MAX_ADJ
    factor = max(lo, min(hi, raw_factor))

    df = forecast_df.copy()
    df["yhat"] = (df["yhat"] * factor).clip(lower=0)
    for col in ("yhat_lo80", "yhat_hi80"):
        if col in df.columns:
            df[col] = (df[col] * factor).clip(lower=0)
    return df
