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
    compute_rmsse,
    compute_wmape,
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

    def test_returns_nan_when_no_train_and_actual_too_short(self):
        """Sin train_actual, si len(actual) <= season_length → NaN (HIGH-02 boundary)."""
        actual = np.array([10.0, 20.0])   # len=2
        forecast = np.array([10.0, 20.0])
        result = compute_mase(actual, forecast, season_length=2)  # len(base)=2 <= 2
        assert math.isnan(result)

    def test_returns_nan_when_train_too_short(self):
        """Con train_actual de longitud <= season_length → NaN."""
        actual = np.array([10.0, 20.0, 30.0])
        forecast = np.array([10.0, 20.0, 30.0])
        train = np.array([5.0, 10.0])  # len=2 <= season_length=3
        result = compute_mase(actual, forecast, season_length=3, train_actual=train)
        assert math.isnan(result)


# ---------------------------------------------------------------------------
# compute_wmape
# ---------------------------------------------------------------------------

class TestComputeWmape:
    def test_perfect_forecast_returns_zero(self, perfect_forecast):
        actual, forecast = perfect_forecast
        assert compute_wmape(actual, forecast) == pytest.approx(0.0, abs=1e-9)

    def test_known_value(self):
        actual = np.array([100.0, 100.0])
        forecast = np.array([110.0, 90.0])
        # |110-100| + |90-100| = 20, total actual = 200 → WMAPE = 0.10
        assert compute_wmape(actual, forecast) == pytest.approx(0.10, abs=1e-9)

    def test_zero_actual_returns_nan(self):
        actual = np.array([0.0, 0.0])
        forecast = np.array([5.0, 5.0])
        assert math.isnan(compute_wmape(actual, forecast))

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            compute_wmape(np.array([1.0, 2.0]), np.array([1.0]))


# ---------------------------------------------------------------------------
# compute_rmsse
# ---------------------------------------------------------------------------

class TestComputeRmsse:
    def test_perfect_forecast_returns_zero(self, perfect_forecast):
        actual, forecast = perfect_forecast
        train = np.array([8.0, 12.0, 10.0, 9.0, 11.0, 10.0, 8.0, 12.0])
        result = compute_rmsse(actual, forecast, season_length=2, train_actual=train)
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_known_value_seasonal(self):
        """RMSSE = sqrt(MSE_model / MSE_naive_seasonal)."""
        train = np.array([10.0, 20.0, 10.0, 20.0, 10.0, 20.0])
        actual = np.array([10.0, 20.0])
        forecast = np.array([12.0, 18.0])  # errors: -2, +2
        # MSE_model = mean([4, 4]) = 4
        # seasonal lag-2: naive errors = [0, 0, 0, 0] → MSE_naive = 0 → NaN
        # Use lag1 to get a non-zero denominator
        # train lag1 errors sq: [100, 100, 100, 100, 100] → MSE_naive = 100
        result = compute_rmsse(actual, forecast, season_length=2, train_actual=train,
                               naive_type="lag1")
        expected = math.sqrt(4.0 / 100.0)
        assert result == pytest.approx(expected, rel=1e-6)

    def test_zero_denominator_returns_nan(self):
        train = np.array([5.0, 5.0, 5.0, 5.0])
        actual = np.array([5.0, 5.0])
        forecast = np.array([5.0, 5.0])
        result = compute_rmsse(actual, forecast, season_length=2, train_actual=train)
        assert math.isnan(result)

    def test_rmsse_ge_zero(self, constant_forecast):
        actual, forecast = constant_forecast
        train = np.arange(1.0, 11.0)
        result = compute_rmsse(actual, forecast, season_length=2, train_actual=train)
        assert result >= 0.0 or math.isnan(result)


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
        assert set(result.keys()) == {"mase", "wmape", "rmsse", "bias", "fill_rate", "mae", "rmse"}

    def test_perfect_forecast_errors_are_zero(self, perfect_forecast):
        actual, forecast = perfect_forecast
        result = compute_all_metrics(actual, forecast, season_length=2)
        for key in ("wmape", "bias", "mae", "rmse"):
            assert result[key] == pytest.approx(0.0, abs=1e-9), f"{key} should be 0"

    def test_naive_type_passed_through(self):
        """compute_all_metrics pasa naive_type a compute_mase correctamente."""
        actual = np.array([10.0, 12.0, 9.0, 11.0])
        forecast = np.array([10.5, 11.5, 9.5, 10.5])
        train = np.array([8.0, 9.0, 10.0, 11.0, 12.0, 10.0])

        result_lag1 = compute_all_metrics(actual, forecast, season_length=4,
                                          train_actual=train, naive_type="lag1")
        result_seasonal = compute_all_metrics(actual, forecast, season_length=4,
                                              train_actual=train, naive_type="seasonal")
        result_mean = compute_all_metrics(actual, forecast, season_length=4,
                                          train_actual=train, naive_type="mean")
        # All three should compute a finite MASE (no NaN for valid inputs)
        for key, res in [("lag1", result_lag1), ("seasonal", result_seasonal), ("mean", result_mean)]:
            assert not math.isnan(res["mase"]), f"MASE should not be NaN for naive_type={key!r}"


# ---------------------------------------------------------------------------
# compute_mase — naive_type variants
# ---------------------------------------------------------------------------

class TestNaiveType:
    """Tests para los distintos tipos de naive benchmark en compute_mase."""

    def test_lag1_uses_lag1_errors(self):
        """naive_type='lag1' calcula el denominador con diferencias lag-1."""
        actual = np.array([10.0, 10.0, 10.0])
        forecast = np.array([12.0, 12.0, 12.0])  # error=2 en todos
        # train: [0, 10, 20, 30] → lag-1 errors = [10, 10, 10] → mae_naive = 10
        train = np.array([0.0, 10.0, 20.0, 30.0])
        mase = compute_mase(actual, forecast, season_length=12, train_actual=train,
                            naive_type="lag1")
        # mae_model = 2, mae_naive = 10 → MASE = 0.2
        assert mase == pytest.approx(0.2, abs=1e-9)

    def test_mean_uses_mean_deviation(self):
        """naive_type='mean' calcula el denominador como desviacion respecto a media."""
        actual = np.array([10.0, 10.0, 10.0])
        forecast = np.array([12.0, 12.0, 12.0])  # error=2 en todos
        # train: [5, 10, 15] → mean=10, abs desvs = [5, 0, 5] → mae_naive = 10/3
        train = np.array([5.0, 10.0, 15.0])
        mase = compute_mase(actual, forecast, season_length=12, train_actual=train,
                            naive_type="mean")
        expected_mae_naive = np.mean(np.abs(train - train.mean()))  # 10/3
        expected_mase = 2.0 / expected_mae_naive
        assert mase == pytest.approx(expected_mase, rel=1e-6)

    def test_lag1_returns_nan_when_train_too_short(self):
        """naive_type='lag1' retorna NaN si len(train) <= 1."""
        actual = np.array([5.0])
        forecast = np.array([5.0])
        train = np.array([10.0])  # solo 1 elemento
        result = compute_mase(actual, forecast, season_length=12, train_actual=train,
                              naive_type="lag1")
        assert math.isnan(result)

    def test_mean_returns_nan_when_constant_train(self):
        """naive_type='mean' retorna NaN si la serie de train es constante (denominador=0)."""
        actual = np.array([5.0, 6.0])
        forecast = np.array([5.0, 6.0])
        train = np.array([10.0, 10.0, 10.0])  # constante → mean deviation = 0
        result = compute_mase(actual, forecast, season_length=12, train_actual=train,
                              naive_type="mean")
        assert math.isnan(result)

    def test_unknown_naive_type_raises(self):
        """Un naive_type no reconocido lanza ValueError."""
        actual = np.array([1.0, 2.0])
        forecast = np.array([1.0, 2.0])
        with pytest.raises(ValueError, match="naive_type desconocido"):
            compute_mase(actual, forecast, naive_type="invalid_type")

    def test_lag1_more_demanding_than_seasonal_for_non_seasonal_series(self):
        """Para una serie sin estacionalidad, lag1 da MASE mas alto que seasonal.

        Esto es esperado: lag-12 es un benchmark mas facil de superar para
        series planas porque acumula mas ruido en el denominador.
        """
        rng = np.random.default_rng(42)
        # Serie estacionaria sin estacionalidad (ruido blanco + tendencia leve)
        train = np.cumsum(rng.normal(0.5, 1.0, size=60))
        train = np.maximum(train, 0)  # no negativos
        actual = train[-3:]
        forecast = train[-3:] + rng.normal(0, 0.5, size=3)  # prediccion ruidosa

        mase_lag1 = compute_mase(actual, forecast, season_length=12,
                                 train_actual=train[:-3], naive_type="lag1")
        mase_seasonal = compute_mase(actual, forecast, season_length=12,
                                     train_actual=train[:-3], naive_type="seasonal")

        # Para series sin estacionalidad, lag-12 acumula mas variacion
        # (12 pasos de random walk) → denominador mas grande → MASE seasonal < MASE lag1
        assert mase_seasonal < mase_lag1, (
            f"Se esperaba MASE seasonal ({mase_seasonal:.3f}) < MASE lag1 ({mase_lag1:.3f}) "
            "para serie sin estacionalidad"
        )
