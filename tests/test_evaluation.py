"""Tests unitarios para planning_core.forecasting.evaluation.

Cubre:
- EvalConfig / CatalogEvalResult (serialización round-trip)
- aggregator: métricas globales y por segmento
- comparator: compare_runs_by_segment, find_winner_changes
- run_store: save/load/list/delete
- catalog_runner: integración end-to-end sobre dataset mínimo
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from planning_core.forecasting.evaluation._types import CatalogEvalResult, EvalConfig
from planning_core.forecasting.evaluation import aggregator, comparator, run_store


# ---------------------------------------------------------------------------
# Fixtures sintéticos
# ---------------------------------------------------------------------------

def _make_sku_results(n: int = 10) -> pd.DataFrame:
    """DataFrame de sku_results sintético para tests de aggregator/comparator."""
    rng = np.random.default_rng(42)
    sb_classes = ["smooth", "smooth", "smooth", "intermittent", "lumpy"]
    abc_classes = ["A", "B", "C"]
    models = ["AutoETS", "AutoARIMA", "SeasonalNaive", "CrostonSBA"]
    statuses = ["ok", "ok", "ok", "ok", "fallback", "fallback", "no_forecast", "ok", "ok", "ok"]

    rows = []
    for i in range(n):
        status = statuses[i % len(statuses)]
        mase = float(rng.uniform(0.4, 1.8)) if status in ("ok", "fallback") else float("nan")
        rows.append({
            "sku":           f"SKU-{i:03d}",
            "sb_class":      sb_classes[i % len(sb_classes)],
            "abc_class":     abc_classes[i % len(abc_classes)],
            "xyz_class":     "X",
            "abc_xyz":       f"{abc_classes[i % len(abc_classes)]}X",
            "is_seasonal":   i % 3 == 0,
            "lifecycle":     "mature",
            "quality_score": float(rng.uniform(0.7, 1.0)),
            "has_censored_demand": False,
            "total_periods": 36,
            "status":        status,
            "model_winner":  models[i % len(models)] if status in ("ok", "fallback") else None,
            "mase":          mase,
            "wape":          float(rng.uniform(0.1, 0.5)) if not math.isnan(mase) else float("nan"),
            "bias":          float(rng.uniform(-0.3, 0.3)) if not math.isnan(mase) else float("nan"),
            "mae":           float(rng.uniform(5, 50)) if not math.isnan(mase) else float("nan"),
            "rmse":          float(rng.uniform(6, 60)) if not math.isnan(mase) else float("nan"),
            "granularity":   "M",
            "h":             3,
            "season_length": 12,
            "n_obs":         36,
            "elapsed_sku_s": 1.2,
            "error_msg":     None,
        })
    return pd.DataFrame(rows)


def _make_result(run_name: str = "test", n: int = 10) -> CatalogEvalResult:
    sku_results = _make_sku_results(n)
    status = sku_results["status"].value_counts()
    return CatalogEvalResult(
        config=EvalConfig(granularity="M", h=3, n_windows=3, run_name=run_name),
        run_id=f"20260101_000000_{run_name}",
        sku_results=sku_results,
        elapsed_seconds=12.3,
        n_ok=int(status.get("ok", 0)),
        n_fallback=int(status.get("fallback", 0)),
        n_no_forecast=int(status.get("no_forecast", 0)),
        n_error=int(status.get("error", 0)),
    )


# ---------------------------------------------------------------------------
# EvalConfig
# ---------------------------------------------------------------------------

class TestEvalConfig:
    def test_defaults(self):
        cfg = EvalConfig()
        assert cfg.granularity == "M"
        assert cfg.use_lgbm is False
        assert cfg.sample_n is None

    def test_round_trip_dict(self):
        cfg = EvalConfig(granularity="W", h=6, n_windows=4, run_name="prueba", use_lgbm=True)
        cfg2 = EvalConfig.from_dict(cfg.to_dict())
        assert cfg2.granularity == "W"
        assert cfg2.h == 6
        assert cfg2.use_lgbm is True
        assert cfg2.run_name == "prueba"

    def test_from_dict_ignores_unknown_keys(self):
        d = {"granularity": "M", "h": 3, "n_windows": 3, "unknown_key": "ignored"}
        cfg = EvalConfig.from_dict(d)
        assert cfg.granularity == "M"


# ---------------------------------------------------------------------------
# CatalogEvalResult
# ---------------------------------------------------------------------------

class TestCatalogEvalResult:
    def test_mase_global_median_ignores_no_forecast(self):
        result = _make_result()
        # Solo filas ok/fallback contribuyen al MASE mediano
        assert not math.isnan(result.mase_global_median)
        assert result.mase_global_median > 0

    def test_n_evaluated_equals_len_sku_results(self):
        result = _make_result(n=10)
        assert result.n_evaluated == 10

    def test_summary_dict_has_required_keys(self):
        result = _make_result()
        d = result.summary_dict()
        for key in ("n_skus_evaluated", "n_ok", "n_fallback", "mase_global_median", "elapsed_seconds"):
            assert key in d, f"Falta clave: {key}"


# ---------------------------------------------------------------------------
# aggregator
# ---------------------------------------------------------------------------

class TestAggregator:
    def test_global_metrics_keys(self):
        df = _make_sku_results(20)
        metrics = aggregator.compute_global_metrics(df)
        for key in ("n_total", "n_ok", "n_fallback", "mase_median", "mase_p90", "wape_median"):
            assert key in metrics, f"Falta clave: {key}"

    def test_global_n_total_equals_len(self):
        df = _make_sku_results(15)
        metrics = aggregator.compute_global_metrics(df)
        assert metrics["n_total"] == 15

    def test_segment_metrics_returns_all_segments(self):
        df = _make_sku_results(20)
        seg = aggregator.compute_segment_metrics(df, segment_cols=["sb_class", "abc_class"])
        assert set(seg["segment_col"].unique()) == {"sb_class", "abc_class"}

    def test_segment_metrics_mase_median_finite(self):
        df = _make_sku_results(20)
        seg = aggregator.compute_segment_metrics(df, segment_cols=["sb_class"])
        # Al menos algunas filas con mase_median finito
        finite = seg["mase_median"].dropna()
        assert len(finite) > 0

    def test_model_selection_summary_global(self):
        df = _make_sku_results(20)
        ms = aggregator.compute_model_selection_summary(df)
        assert "model_winner" in ms.columns
        assert "n_skus" in ms.columns
        assert "pct" in ms.columns
        assert ms["pct"].sum() == pytest.approx(1.0, abs=0.01)

    def test_model_selection_summary_by_segment(self):
        df = _make_sku_results(20)
        ms = aggregator.compute_model_selection_summary(df, by="sb_class")
        assert "sb_class" in ms.columns

    def test_metric_distribution_returns_percentiles(self):
        df = _make_sku_results(20)
        dist = aggregator.compute_metric_distribution(df, metric="mase")
        assert "p50" in dist.columns
        assert "p90" in dist.columns

    def test_metric_distribution_by_segment(self):
        df = _make_sku_results(20)
        dist = aggregator.compute_metric_distribution(df, metric="mase", by="sb_class")
        assert "sb_class" in dist.columns


# ---------------------------------------------------------------------------
# run_store
# ---------------------------------------------------------------------------

class TestRunStore:
    def test_save_and_load_round_trip(self, tmp_path):
        result = _make_result("save_test")
        run_store.save_run(result, tmp_path)
        loaded = run_store.load_run(result.run_id, tmp_path)

        assert loaded.run_id == result.run_id
        assert loaded.config.granularity == "M"
        assert loaded.n_ok == result.n_ok
        assert len(loaded.sku_results) == len(result.sku_results)

    def test_save_creates_metadata_and_parquet(self, tmp_path):
        result = _make_result("meta_test")
        run_dir = run_store.save_run(result, tmp_path)
        assert (run_dir / "run_metadata.json").exists()
        assert (run_dir / "sku_results.parquet").exists()

    def test_metadata_json_is_valid(self, tmp_path):
        result = _make_result("json_test")
        run_dir = run_store.save_run(result, tmp_path)
        with open(run_dir / "run_metadata.json") as f:
            meta = json.load(f)
        assert meta["run_id"] == result.run_id
        assert "config" in meta
        assert "summary" in meta

    def test_list_runs_returns_dataframe(self, tmp_path):
        run_store.save_run(_make_result("run_a"), tmp_path)
        run_store.save_run(_make_result("run_b"), tmp_path)
        df = run_store.list_runs(tmp_path)
        assert len(df) == 2
        assert "run_id" in df.columns
        assert "mase_global_median" in df.columns

    def test_list_runs_empty_dir(self, tmp_path):
        df = run_store.list_runs(tmp_path)
        assert df.empty

    def test_delete_run(self, tmp_path):
        result = _make_result("delete_test")
        run_store.save_run(result, tmp_path)
        run_store.delete_run(result.run_id, tmp_path)
        assert not (tmp_path / result.run_id).exists()

    def test_load_nonexistent_run_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            run_store.load_run("nonexistent_run_id", tmp_path)


# ---------------------------------------------------------------------------
# comparator
# ---------------------------------------------------------------------------

class TestComparator:
    def test_compare_runs_returns_one_row_per_run(self, tmp_path):
        r1 = _make_result("run1")
        r2 = _make_result("run2")
        run_store.save_run(r1, tmp_path)
        run_store.save_run(r2, tmp_path)
        comp = comparator.compare_runs([r1.run_id, r2.run_id], tmp_path)
        assert len(comp) == 2
        assert "mase_median" in comp.columns

    def test_compare_runs_by_segment(self, tmp_path):
        r1 = _make_result("seg1")
        r2 = _make_result("seg2")
        run_store.save_run(r1, tmp_path)
        run_store.save_run(r2, tmp_path)
        comp = comparator.compare_runs_by_segment(
            [r1.run_id, r2.run_id], segment_col="sb_class", base_dir=tmp_path
        )
        assert not comp.empty
        assert comp.index.name == "sb_class"

    def test_find_winner_changes_same_run(self, tmp_path):
        """Mismo run vs sí mismo → ningún cambio de modelo."""
        r = _make_result("same")
        run_store.save_run(r, tmp_path)
        changes = comparator.find_winner_changes(r.run_id, r.run_id, tmp_path)
        assert changes.empty
