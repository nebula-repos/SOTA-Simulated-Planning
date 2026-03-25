"""Tests de integración para run_backtest y select_and_forecast.

Verifica el comportamiento end-to-end del horse-race de modelos:
  - run_backtest retorna métricas finitas con series suficientes
  - run_backtest devuelve status='series_too_short' con series cortas
  - select_and_forecast elige un ganador y produce un forecast válido
  - SKUs inactivos retornan no_forecast sin error
  - Series intermitentes se enrutan a CrostonSBA / ADIDA

Para correr: pytest tests/test_backtest_selector.py -v
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from statsforecast.models import SeasonalNaive

from planning_core.forecasting.backtest import run_backtest
from planning_core.forecasting.selector import select_and_forecast


# ---------------------------------------------------------------------------
# Fixtures de series sintéticas (independientes de test_models.py)
# ---------------------------------------------------------------------------

def _make_smooth_monthly(n: int = 36, seed: int = 42) -> pd.DataFrame:
    """Serie mensual suave con estacionalidad anual."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    demand = np.clip(50 + 0.5 * t + 10 * np.sin(2 * np.pi * t / 12) + rng.normal(0, 2, n), 0, None)
    dates = pd.date_range("2021-01-01", periods=n, freq="MS")
    return pd.DataFrame({"period": dates, "demand": demand})


def _make_intermittent_monthly(n: int = 36, seed: int = 7) -> pd.DataFrame:
    """Serie mensual intermitente: ~60 % ceros."""
    rng = np.random.default_rng(seed)
    demand = rng.choice([0, 0, 0, 5, 10, 15], size=n, replace=True).astype(float)
    dates = pd.date_range("2021-01-01", periods=n, freq="MS")
    return pd.DataFrame({"period": dates, "demand": demand})


# ---------------------------------------------------------------------------
# Tests: run_backtest
# ---------------------------------------------------------------------------

class TestRunBacktest:
    def test_returns_finite_mase_for_seasonal_naive(self):
        """run_backtest con SeasonalNaive en serie larga devuelve MASE finito."""
        demand_df = _make_smooth_monthly(n=36)
        results = run_backtest(
            demand_df=demand_df,
            model_instances=[SeasonalNaive(season_length=12)],
            model_names=["SeasonalNaive"],
            granularity="M",
            h=3,
            n_windows=3,
        )
        assert "SeasonalNaive" in results
        assert results["SeasonalNaive"]["status"] == "ok"
        mase = results["SeasonalNaive"]["mase"]
        assert not math.isnan(mase), "MASE no debe ser NaN con serie suficientemente larga"
        assert mase > 0, "MASE debe ser positivo"

    def test_result_contains_all_metric_keys(self):
        """El resultado de run_backtest debe contener todas las claves de métricas."""
        demand_df = _make_smooth_monthly(n=36)
        results = run_backtest(
            demand_df=demand_df,
            model_instances=[SeasonalNaive(season_length=12)],
            model_names=["SeasonalNaive"],
            granularity="M",
            h=3,
            n_windows=3,
        )
        metrics = results["SeasonalNaive"]
        for key in ("status", "mase", "wape", "bias", "mae", "rmse", "n_windows", "h"):
            assert key in metrics, f"Falta clave en resultado: {key}"

    def test_short_series_returns_series_too_short(self):
        """Serie muy corta devuelve status='series_too_short' para todos los modelos.

        min_required = season_length + h * n_windows = 12 + 3 * 3 = 21.
        Con n=8, la serie es demasiado corta.
        """
        demand_df = _make_smooth_monthly(n=8)
        results = run_backtest(
            demand_df=demand_df,
            model_instances=[SeasonalNaive(season_length=12)],
            model_names=["SeasonalNaive"],
            granularity="M",
            h=3,
            n_windows=3,
        )
        assert results["SeasonalNaive"]["status"] == "series_too_short"
        assert math.isnan(results["SeasonalNaive"]["mase"])

    def test_n_windows_in_result_equals_actual_windows(self):
        """n_windows en el resultado debe reflejar las ventanas efectivamente evaluadas."""
        demand_df = _make_smooth_monthly(n=36)
        results = run_backtest(
            demand_df=demand_df,
            model_instances=[SeasonalNaive(season_length=12)],
            model_names=["SeasonalNaive"],
            granularity="M",
            h=3,
            n_windows=3,
        )
        assert results["SeasonalNaive"]["n_windows"] == 3


# ---------------------------------------------------------------------------
# Tests: select_and_forecast
# ---------------------------------------------------------------------------

class TestSelectAndForecast:
    def test_smooth_series_returns_ok_or_fallback(self):
        """select_and_forecast en serie suave devuelve status ok o fallback."""
        demand_df = _make_smooth_monthly(n=36)
        profile = {"sb_class": "smooth", "sku": "TEST-SKU", "is_seasonal": False}
        result = select_and_forecast(
            profile=profile,
            demand_df=demand_df,
            granularity="M",
            h=3,
            n_windows=3,
            use_lgbm=False,
        )
        assert result["status"] in {"ok", "fallback"}
        fc = result["forecast"]
        assert isinstance(fc, pd.DataFrame)
        assert not fc.empty
        assert set(fc.columns) >= {"ds", "yhat", "yhat_lo80", "yhat_hi80"}
        assert len(fc) == 3

    def test_inactive_sku_returns_no_forecast(self):
        """SKU inactivo no genera forecast — status='no_forecast', forecast vacío."""
        demand_df = _make_smooth_monthly(n=36)
        profile = {"sb_class": "inactive", "sku": "INACTIVE-SKU"}
        result = select_and_forecast(
            profile=profile,
            demand_df=demand_df,
            granularity="M",
            h=3,
        )
        assert result["status"] == "no_forecast"
        assert result["model"] is None
        assert result["forecast"].empty

    def test_intermittent_series_uses_croston_or_adida(self):
        """Series intermitentes deben usar CrostonSBA o ADIDA como modelo ganador."""
        demand_df = _make_intermittent_monthly(n=36)
        profile = {"sb_class": "intermittent", "sku": "INT-SKU", "is_seasonal": False}
        result = select_and_forecast(
            profile=profile,
            demand_df=demand_df,
            granularity="M",
            h=3,
            n_windows=2,
            use_lgbm=False,
        )
        assert result["status"] in {"ok", "fallback"}
        assert result["model"] in {"CrostonSBA", "ADIDA"}

    def test_forecast_yhat_non_negative(self):
        """Todos los valores de yhat deben ser >= 0 (demanda no negativa)."""
        demand_df = _make_smooth_monthly(n=36)
        profile = {"sb_class": "smooth", "sku": "TEST-SKU", "is_seasonal": False}
        result = select_and_forecast(
            profile=profile,
            demand_df=demand_df,
            granularity="M",
            h=6,
            n_windows=3,
            use_lgbm=False,
        )
        if result["status"] != "no_forecast":
            fc = result["forecast"]
            assert (fc["yhat"] >= 0).all(), "yhat contiene valores negativos"
            assert (fc["yhat_lo80"] >= 0).all(), "yhat_lo80 contiene valores negativos"

    def test_result_includes_backtest_dict(self):
        """El resultado siempre incluye 'backtest' como dict."""
        demand_df = _make_smooth_monthly(n=36)
        profile = {"sb_class": "smooth", "sku": "TEST-SKU"}
        result = select_and_forecast(
            profile=profile,
            demand_df=demand_df,
            granularity="M",
            h=3,
            n_windows=3,
            use_lgbm=False,
        )
        assert "backtest" in result
        assert isinstance(result["backtest"], dict)

    def test_result_includes_season_length_and_granularity(self):
        """El resultado incluye season_length y granularity correctos."""
        demand_df = _make_smooth_monthly(n=36)
        profile = {"sb_class": "smooth", "sku": "TEST-SKU"}
        result = select_and_forecast(
            profile=profile,
            demand_df=demand_df,
            granularity="M",
            h=3,
            n_windows=3,
            use_lgbm=False,
        )
        assert result["season_length"] == 12
        assert result["granularity"] == "M"
        assert result["h"] == 3

    def test_none_sb_class_falls_back_to_smooth(self):
        """sb_class=None usa 'smooth' como fallback y emite warning."""
        demand_df = _make_smooth_monthly(n=36)
        profile = {"sb_class": None, "sku": "NULL-SKU"}
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = select_and_forecast(
                profile=profile,
                demand_df=demand_df,
                granularity="M",
                h=3,
                n_windows=3,
                use_lgbm=False,
            )
        assert result["status"] in {"ok", "fallback"}
        warning_messages = [str(w.message) for w in caught]
        assert any("sb_class" in msg for msg in warning_messages), \
            "Debe emitir warning cuando sb_class es None"
