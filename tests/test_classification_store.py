"""Tests para ClassificationStore — round-trip, escritura atómica, frescura."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from planning_core.classification_store import ClassificationStore, DEFAULT_MAX_AGE_DAYS


# ---------------------------------------------------------------------------
# Fixture: DataFrame mínimo de clasificación
# ---------------------------------------------------------------------------

def _make_classification_df(n: int = 5) -> pd.DataFrame:
    rows = []
    for i in range(n):
        sku = f"SKU-{i:03d}"
        rows.append({
            "sku": sku,
            "abc_class": ["A", "B", "C"][i % 3],
            "sb_class": "smooth",
            "xyz_class": "X",
            "abc_xyz": f"{['A','B','C'][i % 3]}X",
            "is_seasonal": i % 2 == 0,
            "has_trend": False,
            "has_censored_demand": False,
            "quality_score": 0.8,
            "quality_score_base": 0.8,
            "censoring_penalty": 0.0,
            "granularity": "M",
            "lifecycle": "active",
            "total_periods": 24,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Round-trip: save + load
# ---------------------------------------------------------------------------

class TestClassificationStoreRoundTrip:

    def test_save_and_load_basic(self, tmp_path: Path):
        df = _make_classification_df(10)
        ClassificationStore.save(df, "M", tmp_path)

        store = ClassificationStore.load(tmp_path, "M")
        assert store is not None
        assert len(store) == 10

    def test_all_skus_df_returns_copy(self, tmp_path: Path):
        df = _make_classification_df(5)
        ClassificationStore.save(df, "M", tmp_path)

        store = ClassificationStore.load(tmp_path, "M")
        loaded_df = store.all_skus_df()
        assert set(loaded_df["sku"].tolist()) == set(df["sku"].tolist())

    def test_get_existing_sku(self, tmp_path: Path):
        df = _make_classification_df(3)
        ClassificationStore.save(df, "M", tmp_path)

        store = ClassificationStore.load(tmp_path, "M")
        entry = store.get("SKU-000")
        assert entry is not None
        assert entry["sku"] == "SKU-000"
        assert entry["abc_class"] == "A"

    def test_get_missing_sku_returns_none(self, tmp_path: Path):
        df = _make_classification_df(3)
        ClassificationStore.save(df, "M", tmp_path)

        store = ClassificationStore.load(tmp_path, "M")
        assert store.get("SKU-INEXISTENTE") is None

    def test_load_missing_returns_none(self, tmp_path: Path):
        store = ClassificationStore.load(tmp_path / "nonexistent", "M")
        assert store is None

    def test_metadata_keys_present(self, tmp_path: Path):
        df = _make_classification_df(5)
        ClassificationStore.save(df, "M", tmp_path, classification_scope="network_aggregate")

        store = ClassificationStore.load(tmp_path, "M")
        meta = store.metadata()
        assert "run_date" in meta
        assert "n_skus" in meta
        assert meta["n_skus"] == 5
        assert "abc_distribution" in meta
        assert "classification_scope" in meta
        assert meta["classification_scope"] == "network_aggregate"
        assert "seasonal_pct" in meta
        assert "avg_quality_score" in meta

    def test_abc_distribution_in_metadata(self, tmp_path: Path):
        df = _make_classification_df(6)  # 2A, 2B, 2C
        ClassificationStore.save(df, "M", tmp_path)

        store = ClassificationStore.load(tmp_path, "M")
        meta = store.metadata()
        abc = meta["abc_distribution"]
        assert abc.get("A", 0) == 2
        assert abc.get("B", 0) == 2
        assert abc.get("C", 0) == 2

    def test_granularity_weekly(self, tmp_path: Path):
        df = _make_classification_df(3)
        ClassificationStore.save(df, "W", tmp_path)

        store = ClassificationStore.load(tmp_path, "W")
        assert store is not None
        # No confunde granularidades
        store_m = ClassificationStore.load(tmp_path, "M")
        assert store_m is None


# ---------------------------------------------------------------------------
# Escritura atómica
# ---------------------------------------------------------------------------

class TestClassificationStoreAtomicWrite:

    def test_no_tmp_files_after_save(self, tmp_path: Path):
        df = _make_classification_df(3)
        ClassificationStore.save(df, "M", tmp_path)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Archivos .tmp no eliminados: {tmp_files}"

    def test_overwrite_replaces_previous(self, tmp_path: Path):
        df_v1 = _make_classification_df(3)
        ClassificationStore.save(df_v1, "M", tmp_path)

        df_v2 = _make_classification_df(10)
        ClassificationStore.save(df_v2, "M", tmp_path)

        store = ClassificationStore.load(tmp_path, "M")
        assert len(store) == 10


# ---------------------------------------------------------------------------
# Frescura (is_stale)
# ---------------------------------------------------------------------------

class TestClassificationStoreStale:

    def _save_with_run_date(self, tmp_path: Path, run_date: datetime) -> None:
        df = _make_classification_df(3)
        ClassificationStore.save(df, "M", tmp_path)
        meta_path = tmp_path / "classification_catalog_M_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["run_date"] = run_date.isoformat(timespec="seconds").replace("+00:00", "Z")
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    def test_not_stale_for_fresh_store(self, tmp_path: Path):
        self._save_with_run_date(tmp_path, datetime.now(timezone.utc))
        store = ClassificationStore.load(tmp_path, "M")
        assert not store.is_stale()

    def test_stale_after_max_age(self, tmp_path: Path):
        max_age = DEFAULT_MAX_AGE_DAYS["M"]
        old_date = datetime.now(timezone.utc) - timedelta(days=max_age + 1)
        self._save_with_run_date(tmp_path, old_date)
        store = ClassificationStore.load(tmp_path, "M")
        assert store.is_stale()

    def test_custom_max_age_days(self, tmp_path: Path):
        # Con max_age_days=1, un store de hoy no es stale
        self._save_with_run_date(tmp_path, datetime.now(timezone.utc))
        store = ClassificationStore.load(tmp_path, "M")
        assert not store.is_stale(max_age_days=1)

        # Un store de hace 2 días con max_age_days=1 sí es stale
        old = datetime.now(timezone.utc) - timedelta(days=2)
        self._save_with_run_date(tmp_path, old)
        store2 = ClassificationStore.load(tmp_path, "M")
        assert store2.is_stale(max_age_days=1)

    def test_stale_with_missing_run_date(self, tmp_path: Path):
        df = _make_classification_df(2)
        ClassificationStore.save(df, "M", tmp_path)
        meta_path = tmp_path / "classification_catalog_M_meta.json"
        meta = json.loads(meta_path.read_text())
        del meta["run_date"]
        meta_path.write_text(json.dumps(meta))
        store = ClassificationStore.load(tmp_path, "M")
        assert store.is_stale()
