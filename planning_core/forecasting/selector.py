"""Selector automatico de modelo de forecast por SKU.

Dado el perfil de clasificacion de un SKU, determina los modelos candidatos
y ejecuta un horse-race empirico via backtest expanding-window para elegir
el modelo con menor MASE.

Reglas de candidatura
---------------------

Fase 2 (modelos estadisticos base):

| Clasificacion SB | Seasonal | Candidatos SF             |
|------------------|----------|---------------------------|
| smooth           | True     | AutoETS, AutoARIMA, MSTL, SeasonalNaive |
| smooth           | False    | AutoETS, AutoARIMA, SeasonalNaive        |
| erratic          | —        | AutoETS, AutoARIMA, SeasonalNaive        |
| intermittent     | —        | CrostonSBA, ADIDA                        |
| lumpy            | —        | CrostonSBA, ADIDA                        |
| inactive         | —        | sin forecast                             |

Fase 3 (modelos ML — camino separado):

LightGBM se evalua via ``run_backtest_lgbm()`` y sus resultados se fusionan
al backtest antes de elegir el ganador. Solo aplica a series smooth/erratic
con suficientes datos (>= 3 * season_length).

El modelo ganador es el que menor MASE promedio obtiene sobre el backtest.
Si todos los candidatos fallan (serie demasiado corta), se usa SeasonalNaive
o HistoricAverage como fallback obligatorio.

Uso tipico
----------
>>> result = select_and_forecast(profile, demand_df, granularity="M", h=3)
>>> result["model"]      # "AutoARIMA"
>>> result["mase"]       # 0.68
>>> result["forecast"]   # pd.DataFrame con [ds, yhat, yhat_lo80, yhat_hi80]
"""

from __future__ import annotations

import warnings
from typing import Any

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
# (mas restrictivo que los modelos estadisticos — necesita datos para generalizar)
_MIN_OBS_LGBM_FACTOR = 3


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
) -> dict:
    """Selecciona el mejor modelo para un SKU y genera el forecast.

    Flujo:
    1. Lee la clasificacion SB del perfil.
    2. Si el SKU es ``inactive``, retorna ``{"status": "no_forecast"}``.
    3. Determina los modelos candidatos estadisticos por clasificacion.
    4. Corre backtest expanding-window sobre todos los candidatos estadisticos.
    5. Opcionalmente, corre backtest LightGBM y fusiona al resultado.
    6. Elige el modelo con menor MASE sobre todos los candidatos.
    7. Genera el forecast final con ese modelo sobre el historico completo.

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

    Returns
    -------
    dict con claves:
        - ``status``: ``"ok"``, ``"no_forecast"``, ``"fallback"``, ``"error"``
        - ``model``: nombre del modelo ganador
        - ``mase``: MASE del modelo ganador en backtest
        - ``backtest``: dict completo del backtest (todos los candidatos)
        - ``forecast``: pd.DataFrame con ``[ds, yhat, yhat_lo80, yhat_hi80]``
        - ``season_length``: int
        - ``granularity``: str
        - ``h``: int
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
            "forecast": pd.DataFrame(),
            "backtest": {},
            "season_length": season_length,
            "granularity": granularity,
            "h": h,
        }

    # 2. Candidatos estadisticos segun clasificacion
    model_instances, model_names = get_model_candidates(sb_class, season_length, is_seasonal)

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
        )

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
                )
                backtest_results.update(lgbm_results)
            except Exception:
                pass  # LightGBM falla silenciosamente — los candidatos estadisticos siguen

    # 5. Elegir ganador (menor MASE sobre todos los candidatos)
    best_model, best_mase = _pick_winner(backtest_results)

    # 6. Generar forecast final con el modelo ganador
    status = "ok"
    try:
        if best_model is None:
            # Fallback: serie muy corta para backtest
            status = "fallback"
            forecast_result = fit_predict_naive(
                demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
            )
            best_model = forecast_result["model"]

        elif best_model == "CrostonSBA":
            forecast_result = fit_predict_sba(
                demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
            )

        elif best_model == "ADIDA":
            forecast_result = fit_predict_adida(
                demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
            )

        elif best_model == "AutoETS":
            try:
                forecast_result = fit_predict_ets(
                    demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
                )
            except ValueError:
                status = "fallback"
                forecast_result = fit_predict_naive(
                    demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
                )
                best_model = forecast_result["model"]

        elif best_model == "AutoARIMA":
            try:
                forecast_result = fit_predict_arima(
                    demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
                )
            except ValueError:
                status = "fallback"
                forecast_result = fit_predict_naive(
                    demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
                )
                best_model = forecast_result["model"]

        elif best_model == "MSTL":
            try:
                forecast_result = fit_predict_mstl(
                    demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
                )
            except ValueError:
                status = "fallback"
                forecast_result = fit_predict_naive(
                    demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
                )
                best_model = forecast_result["model"]

        elif best_model == "LightGBM":
            try:
                forecast_result = fit_predict_lgbm(
                    demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
                )
            except (ValueError, ImportError):
                status = "fallback"
                forecast_result = fit_predict_naive(
                    demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
                )
                best_model = forecast_result["model"]

        else:
            forecast_result = fit_predict_naive(
                demand_df, granularity=granularity, h=h, unique_id=uid, target_col=target_col
            )
            best_model = forecast_result["model"]

    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "model": None,
            "mase": float("nan"),
            "forecast": pd.DataFrame(),
            "backtest": backtest_results,
            "season_length": season_length,
            "granularity": granularity,
            "h": h,
        }

    return {
        "status": status,
        "model": best_model,
        "mase": best_mase,
        "backtest": backtest_results,
        "forecast": forecast_result["forecast"],
        "season_length": season_length,
        "granularity": granularity,
        "h": h,
    }


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _pick_winner(backtest_results: dict[str, dict]) -> tuple[str | None, float]:
    """Retorna el nombre y MASE del modelo con menor MASE en el backtest.

    Ignora modelos con status != "ok" o con MASE = NaN.
    Retorna (None, NaN) si ningun modelo tiene MASE valido.
    """
    best_name = None
    best_mase = float("nan")

    for model_name, metrics in backtest_results.items():
        if metrics.get("status") != "ok":
            continue
        mase = metrics.get("mase", float("nan"))
        if mase != mase:  # isnan check sin import math
            continue
        if best_name is None or mase < best_mase:
            best_name = model_name
            best_mase = mase

    return best_name, best_mase
