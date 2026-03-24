"""Tests de integracion para los modelos de forecast (Fase 3).

Estos tests usan datos sinteticos — no requieren el dataset canonico.
Verifican que cada modelo:
  1. Retorna el formato correcto (dict con model, forecast, season_length)
  2. El DataFrame de forecast tiene las columnas esperadas
  3. El numero de filas == h
  4. Los valores de yhat son >= 0 (demanda no negativa)
  5. yhat_lo80 <= yhat <= yhat_hi80 (coherencia de intervalos)

Para correr: pytest tests/test_models.py -v
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from planning_core.forecasting.models.ets import fit_predict_ets
from planning_core.forecasting.models.naive import fit_predict_naive
from planning_core.forecasting.models.sba import fit_predict_adida, fit_predict_sba
from planning_core.forecasting.utils import to_nixtla_df


# ---------------------------------------------------------------------------
# Fixtures de series sinteticas
# ---------------------------------------------------------------------------

def make_smooth_monthly(n: int = 36, seed: int = 42) -> pd.DataFrame:
    """Serie mensual suave con estacionalidad anual."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    seasonality = 10 * np.sin(2 * np.pi * t / 12)
    trend = 0.5 * t
    noise = rng.normal(0, 2, n)
    demand = 50 + trend + seasonality + noise
    demand = np.clip(demand, 0, None)

    dates = pd.date_range("2021-01-01", periods=n, freq="MS")
    return pd.DataFrame({"period": dates, "demand": demand})


def make_intermittent_monthly(n: int = 36, seed: int = 7) -> pd.DataFrame:
    """Serie mensual intermitente: ~60% ceros."""
    rng = np.random.default_rng(seed)
    demand = rng.choice([0, 0, 0, 5, 10, 15], size=n, replace=True).astype(float)
    dates = pd.date_range("2021-01-01", periods=n, freq="MS")
    return pd.DataFrame({"period": dates, "demand": demand})


def make_short_monthly(n: int = 10) -> pd.DataFrame:
    """Serie mensual corta (< 2 * season_length = 24 obs)."""
    demand = np.array([5.0, 10.0, 8.0, 12.0, 7.0, 9.0, 11.0, 6.0, 8.0, 10.0])
    dates = pd.date_range("2023-01-01", periods=n, freq="MS")
    return pd.DataFrame({"period": dates, "demand": demand})


# ---------------------------------------------------------------------------
# Helpers de asercion
# ---------------------------------------------------------------------------

def assert_forecast_shape(result: dict, h: int) -> None:
    assert "model" in result
    assert "forecast" in result
    assert "season_length" in result

    fc = result["forecast"]
    assert isinstance(fc, pd.DataFrame), "forecast debe ser DataFrame"
    assert set(fc.columns) >= {"ds", "yhat", "yhat_lo80", "yhat_hi80"}, \
        f"Columnas faltantes: {fc.columns.tolist()}"
    assert len(fc) == h, f"Esperaba {h} filas, obtuvo {len(fc)}"


def assert_non_negative(result: dict) -> None:
    fc = result["forecast"]
    assert (fc["yhat"] >= 0).all(), "yhat contiene valores negativos"
    assert (fc["yhat_lo80"] >= 0).all(), "yhat_lo80 contiene valores negativos"


def assert_intervals_consistent(result: dict) -> None:
    fc = result["forecast"]
    assert (fc["yhat_lo80"] <= fc["yhat"] + 1e-9).all(), "yhat_lo80 > yhat"
    assert (fc["yhat"] <= fc["yhat_hi80"] + 1e-9).all(), "yhat > yhat_hi80"


# ---------------------------------------------------------------------------
# Tests: SeasonalNaive / HistoricAverage
# ---------------------------------------------------------------------------

class TestNaiveModel:
    def test_seasonal_naive_on_long_series(self):
        demand_df = make_smooth_monthly(n=36)
        result = fit_predict_naive(demand_df, granularity="M", h=6)
        assert result["model"] == "SeasonalNaive"
        assert_forecast_shape(result, h=6)
        assert_non_negative(result)
        assert_intervals_consistent(result)

    def test_historic_average_fallback_on_short_series(self):
        """Series < 24 obs mensual deben caer a HistoricAverage."""
        demand_df = make_short_monthly(n=10)
        result = fit_predict_naive(demand_df, granularity="M", h=3)
        assert result["model"] == "HistoricAverage"
        assert_forecast_shape(result, h=3)
        assert_non_negative(result)

    def test_season_length_is_12_for_monthly(self):
        demand_df = make_smooth_monthly(n=36)
        result = fit_predict_naive(demand_df, granularity="M", h=1)
        assert result["season_length"] == 12

    def test_h1_forecast(self):
        demand_df = make_smooth_monthly(n=36)
        result = fit_predict_naive(demand_df, granularity="M", h=1)
        assert_forecast_shape(result, h=1)


# ---------------------------------------------------------------------------
# Tests: AutoETS
# ---------------------------------------------------------------------------

class TestEtsModel:
    def test_ets_on_smooth_series(self):
        demand_df = make_smooth_monthly(n=36)
        result = fit_predict_ets(demand_df, granularity="M", h=3)
        assert result["model"] == "AutoETS"
        assert_forecast_shape(result, h=3)
        assert_non_negative(result)
        assert_intervals_consistent(result)

    def test_ets_raises_on_short_series(self):
        """ETS requiere >= 24 obs para mensual — debe lanzar ValueError."""
        demand_df = make_short_monthly(n=10)
        with pytest.raises(ValueError, match="AutoETS requiere"):
            fit_predict_ets(demand_df, granularity="M", h=3)

    def test_ets_h6_forecast(self):
        demand_df = make_smooth_monthly(n=48)
        result = fit_predict_ets(demand_df, granularity="M", h=6)
        assert_forecast_shape(result, h=6)

    def test_ets_intervals_wider_for_longer_horizon(self):
        """Los intervalos deberian ampliarse con el horizonte (h=6 > h=1)."""
        demand_df = make_smooth_monthly(n=36)
        r1 = fit_predict_ets(demand_df, granularity="M", h=1)
        r6 = fit_predict_ets(demand_df, granularity="M", h=6)
        width_h1 = (r1["forecast"]["yhat_hi80"] - r1["forecast"]["yhat_lo80"]).mean()
        width_h6 = (r6["forecast"]["yhat_hi80"] - r6["forecast"]["yhat_lo80"]).mean()
        assert width_h6 >= width_h1


# ---------------------------------------------------------------------------
# Tests: CrostonSBA / ADIDA
# ---------------------------------------------------------------------------

class TestSbaModel:
    def test_sba_on_intermittent_series(self):
        demand_df = make_intermittent_monthly(n=36)
        result = fit_predict_sba(demand_df, granularity="M", h=3)
        assert result["model"] == "CrostonSBA"
        assert_forecast_shape(result, h=3)
        assert_non_negative(result)

    def test_sba_no_confidence_intervals(self):
        """SBA no tiene IC — lo y hi deben ser iguales a yhat."""
        demand_df = make_intermittent_monthly(n=36)
        result = fit_predict_sba(demand_df, granularity="M", h=3)
        fc = result["forecast"]
        pd.testing.assert_series_equal(fc["yhat_lo80"], fc["yhat"], check_names=False)
        pd.testing.assert_series_equal(fc["yhat_hi80"], fc["yhat"], check_names=False)

    def test_adida_fallback_on_very_sparse_series(self):
        """Series con < 3 periodos con demanda > 0 usan ADIDA."""
        dates = pd.date_range("2023-01-01", periods=24, freq="MS")
        demand = np.zeros(24)
        demand[5] = 10.0  # solo 1 periodo no-cero
        demand_df = pd.DataFrame({"period": dates, "demand": demand})
        result = fit_predict_sba(demand_df, granularity="M", h=3)
        assert result["model"] == "ADIDA"
        assert_forecast_shape(result, h=3)

    def test_adida_direct(self):
        demand_df = make_intermittent_monthly(n=36)
        result = fit_predict_adida(demand_df, granularity="M", h=3)
        assert result["model"] == "ADIDA"
        assert_forecast_shape(result, h=3)
        assert_non_negative(result)


# ---------------------------------------------------------------------------
# Tests: to_nixtla_df (util)
# ---------------------------------------------------------------------------

class TestToNixtlaDf:
    def test_output_columns(self):
        demand_df = make_smooth_monthly(n=12)
        result = to_nixtla_df(demand_df, unique_id="SKU-TEST")
        assert list(result.columns) == ["unique_id", "ds", "y"]

    def test_unique_id_propagated(self):
        demand_df = make_smooth_monthly(n=12)
        result = to_nixtla_df(demand_df, unique_id="TEST-123")
        assert (result["unique_id"] == "TEST-123").all()

    def test_empty_input_returns_empty(self):
        empty = pd.DataFrame(columns=["period", "demand"])
        result = to_nixtla_df(empty)
        assert result.empty
        assert list(result.columns) == ["unique_id", "ds", "y"]
