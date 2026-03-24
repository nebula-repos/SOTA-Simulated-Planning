"""Tests unitarios para planning_core.forecasting.metrics."""

import math

import numpy as np
import pytest

from planning_core.forecasting.metrics import (
    compute_all_metrics,
    compute_bias,
    compute_mae,
    compute_mase,
    compute_rmse,
    compute_wape,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def perfect_forecast():
    """Forecast identico al real → todas las metricas de error = 0."""
    actual = np.array([10.0, 20.0, 15.0, 25.0, 10.0])
    forecast = actual.copy()
    return actual, forecast


@pytest.fixture
def constant_forecast():
    """Forecast constante = 10, actual variable."""
    actual = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    forecast = np.full(5, 10.0)
    return actual, forecast


# ---------------------------------------------------------------------------
# compute_mase
# ---------------------------------------------------------------------------

class TestComputeMase:
    def test_perfect_forecast_returns_zero(self, perfect_forecast):
        actual, forecast = perfect_forecast
        mase = compute_mase(actual, forecast, season_length=2)
        assert mase == pytest.approx(0.0, abs=1e-9)

    def test_mase_below_one_for_good_model(self):
        """Un modelo con menor error que naive estacional tiene MASE < 1."""
        actual = np.array([10.0, 12.0, 11.0, 13.0, 10.0, 12.0, 11.0, 13.0])
        # naive estacional (m=2) prediccion: [10, 12, 11, 13] → error ~0.5 promedio
        forecast = np.array([10.5, 12.5, 11.5, 12.5])
        train = actual[:4]
        mase = compute_mase(actual[4:], forecast, season_length=2, train_actual=train)
        assert mase < 1.5  # modelo razonable

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="mismo largo"):
            compute_mase(np.array([1.0, 2.0]), np.array([1.0]))

    def test_zero_denominator_returns_nan(self):
        """Si la serie de entrenamiento es constante, MASE = nan."""
        actual = np.array([5.0, 5.0, 5.0, 5.0])
        forecast = np.array([5.0, 5.0, 5.0, 5.0])
        train = np.array([5.0, 5.0, 5.0, 5.0, 5.0, 5.0])
        result = compute_mase(actual, forecast, season_length=2, train_actual=train)
        assert math.isnan(result)

    def test_uses_actual_as_proxy_when_no_train(self):
        """Sin train_actual, usa actual como denominador proxy (non-zero naive error)."""
        # Serie con variacion estacional no perfecta → denominador > 0
        actual = np.array([10.0, 20.0, 12.0, 18.0, 11.0, 21.0])
        forecast = actual.copy()  # forecast perfecto → MASE = 0
        result = compute_mase(actual, forecast, season_length=2)
        assert result == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# compute_wape
# ---------------------------------------------------------------------------

class TestComputeWape:
    def test_perfect_forecast_returns_zero(self, perfect_forecast):
        actual, forecast = perfect_forecast
        assert compute_wape(actual, forecast) == pytest.approx(0.0, abs=1e-9)

    def test_known_value(self):
        actual = np.array([100.0, 100.0])
        forecast = np.array([110.0, 90.0])
        # |110-100| + |90-100| = 20, total actual = 200 → WAPE = 0.10
        assert compute_wape(actual, forecast) == pytest.approx(0.10, abs=1e-9)

    def test_zero_actual_returns_nan(self):
        actual = np.array([0.0, 0.0])
        forecast = np.array([5.0, 5.0])
        assert math.isnan(compute_wape(actual, forecast))

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            compute_wape(np.array([1.0, 2.0]), np.array([1.0]))


# ---------------------------------------------------------------------------
# compute_bias
# ---------------------------------------------------------------------------

class TestComputeBias:
    def test_no_bias_perfect_forecast(self, perfect_forecast):
        actual, forecast = perfect_forecast
        assert compute_bias(actual, forecast) == pytest.approx(0.0, abs=1e-9)

    def test_positive_bias_over_estimation(self):
        actual = np.array([10.0, 10.0])
        forecast = np.array([12.0, 12.0])
        # mean(forecast - actual) = 2, mean(actual) = 10 → bias = 0.2
        assert compute_bias(actual, forecast) == pytest.approx(0.2, abs=1e-9)

    def test_negative_bias_under_estimation(self):
        actual = np.array([10.0, 10.0])
        forecast = np.array([8.0, 8.0])
        assert compute_bias(actual, forecast) == pytest.approx(-0.2, abs=1e-9)

    def test_zero_actual_returns_nan(self):
        assert math.isnan(compute_bias(np.array([0.0]), np.array([5.0])))


# ---------------------------------------------------------------------------
# compute_mae / compute_rmse
# ---------------------------------------------------------------------------

class TestComputeMaeRmse:
    def test_mae_known_value(self):
        actual = np.array([0.0, 10.0])
        forecast = np.array([5.0, 5.0])
        # |5| + |5| / 2 = 5.0
        assert compute_mae(actual, forecast) == pytest.approx(5.0)

    def test_rmse_known_value(self):
        actual = np.array([0.0, 10.0])
        forecast = np.array([5.0, 5.0])
        # sqrt((25 + 25) / 2) = sqrt(25) = 5.0
        assert compute_rmse(actual, forecast) == pytest.approx(5.0)

    def test_rmse_always_ge_mae(self, constant_forecast):
        actual, forecast = constant_forecast
        assert compute_rmse(actual, forecast) >= compute_mae(actual, forecast)


# ---------------------------------------------------------------------------
# compute_all_metrics
# ---------------------------------------------------------------------------

class TestComputeAllMetrics:
    def test_returns_all_keys(self, perfect_forecast):
        actual, forecast = perfect_forecast
        result = compute_all_metrics(actual, forecast, season_length=2)
        assert set(result.keys()) == {"mase", "wape", "bias", "mae", "rmse"}

    def test_perfect_forecast_errors_are_zero(self, perfect_forecast):
        actual, forecast = perfect_forecast
        result = compute_all_metrics(actual, forecast, season_length=2)
        for key in ("wape", "bias", "mae", "rmse"):
            assert result[key] == pytest.approx(0.0, abs=1e-9), f"{key} should be 0"
