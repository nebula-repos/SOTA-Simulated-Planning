"""Tests para ForecastStore — escritura atómica, load/get, frescura."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from planning_core.forecasting.evaluation.forecast_store import (
    DEFAULT_MAX_AGE_DAYS,
    ForecastStore,
    ForecastStoreEntry,
    build_store_entries,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_entry(sku: str, status: str = "ok", mase: float = 0.8,
                forecast_mean_daily: float | None = 3.2,
                forecast_sigma_daily: float | None = 0.9,
                model: str | None = "AutoETS") -> ForecastStoreEntry:
    return ForecastStoreEntry(
        sku=sku,
        status=status,
        model=model,
        mase=mase,
        forecast_mean_daily=forecast_mean_daily,
        forecast_sigma_daily=forecast_sigma_daily,
        granularity="M",
        h=3,
        run_date=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


# ---------------------------------------------------------------------------
# ForecastStore.save + load — round-trip
# ---------------------------------------------------------------------------

class TestForecastStoreRoundTrip:

    def test_save_and_load_basic(self, tmp_path: Path):
        entries = [_make_entry("SKU-001"), _make_entry("SKU-002", status="no_forecast",
                                                        forecast_mean_daily=None,
                                                        forecast_sigma_daily=None,
                                                        model=None)]
        ForecastStore.save(entries, tmp_path, "M")

        store = ForecastStore.load(tmp_path, "M")
        assert store is not None
        assert len(store) == 2

    def test_get_existing_sku(self, tmp_path: Path):
        entries = [_make_entry("SKU-A", mase=0.75, forecast_mean_daily=5.0)]
        ForecastStore.save(entries, tmp_path, "M")

        store = ForecastStore.load(tmp_path, "M")
        entry = store.get("SKU-A")
        assert entry is not None
        assert entry.sku == "SKU-A"
        assert entry.mase == pytest.approx(0.75)
        assert entry.forecast_mean_daily == pytest.approx(5.0)

    def test_get_missing_sku_returns_none(self, tmp_path: Path):
        entries = [_make_entry("SKU-A")]
        ForecastStore.save(entries, tmp_path, "M")

        store = ForecastStore.load(tmp_path, "M")
        assert store.get("SKU-INEXISTENTE") is None

    def test_load_missing_dir_returns_none(self, tmp_path: Path):
        store = ForecastStore.load(tmp_path / "nonexistent", "M")
        assert store is None

    def test_metadata_keys(self, tmp_path: Path):
        entries = [_make_entry("SKU-001"), _make_entry("SKU-002")]
        ForecastStore.save(entries, tmp_path, "M")

        store = ForecastStore.load(tmp_path, "M")
        meta = store.metadata()
        assert "run_date" in meta
        assert "n_skus" in meta
        assert meta["n_skus"] == 2
        assert "coverage_pct" in meta
        assert "top_model" in meta

    def test_nan_forecast_mean_is_none_in_entry(self, tmp_path: Path):
        entries = [_make_entry("SKU-NAN", forecast_mean_daily=float("nan"))]
        ForecastStore.save(entries, tmp_path, "M")

        store = ForecastStore.load(tmp_path, "M")
        entry = store.get("SKU-NAN")
        assert entry is not None
        # NaN se convierte a None al cargar
        assert entry.forecast_mean_daily is None


# ---------------------------------------------------------------------------
# Escritura atómica
# ---------------------------------------------------------------------------

class TestForecastStoreAtomicWrite:

    def test_previous_artifact_survives_if_tmp_deleted(self, tmp_path: Path):
        """Si el .tmp se elimina antes del rename, el artefacto anterior permanece."""
        # Guardar artefacto inicial
        entries_v1 = [_make_entry("SKU-V1")]
        ForecastStore.save(entries_v1, tmp_path, "M")

        # Simular: parquet guardado (v1 en disco) — el artefacto anterior existe
        store_before = ForecastStore.load(tmp_path, "M")
        assert store_before is not None
        assert store_before.get("SKU-V1") is not None

        # Guardar v2 normalmente
        entries_v2 = [_make_entry("SKU-V2")]
        ForecastStore.save(entries_v2, tmp_path, "M")

        store_after = ForecastStore.load(tmp_path, "M")
        assert store_after is not None
        assert store_after.get("SKU-V2") is not None
        # v1 ya no está (fue reemplazado)
        assert store_after.get("SKU-V1") is None

    def test_no_tmp_files_left_after_save(self, tmp_path: Path):
        entries = [_make_entry("SKU-001")]
        ForecastStore.save(entries, tmp_path, "M")

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Archivos .tmp no eliminados: {tmp_files}"


# ---------------------------------------------------------------------------
# Frescura (is_stale)
# ---------------------------------------------------------------------------

class TestForecastStoreStale:

    def _save_with_run_date(self, tmp_path: Path, run_date: datetime) -> None:
        entries = [_make_entry("SKU-001")]
        # Guardar normalmente y luego parchear el meta JSON
        ForecastStore.save(entries, tmp_path, "M")
        meta_path = tmp_path / "forecast_catalog_M_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["run_date"] = run_date.isoformat(timespec="seconds").replace("+00:00", "Z")
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    def test_not_stale_for_fresh_store(self, tmp_path: Path):
        self._save_with_run_date(tmp_path, datetime.now(timezone.utc))
        store = ForecastStore.load(tmp_path, "M")
        assert not store.is_stale()

    def test_stale_after_max_age(self, tmp_path: Path):
        max_age = DEFAULT_MAX_AGE_DAYS["M"]
        old_date = datetime.now(timezone.utc) - timedelta(days=max_age + 1)
        self._save_with_run_date(tmp_path, old_date)
        store = ForecastStore.load(tmp_path, "M")
        assert store.is_stale()

    def test_stale_with_missing_run_date(self, tmp_path: Path):
        entries = [_make_entry("SKU-001")]
        ForecastStore.save(entries, tmp_path, "M")
        meta_path = tmp_path / "forecast_catalog_M_meta.json"
        meta = json.loads(meta_path.read_text())
        del meta["run_date"]
        meta_path.write_text(json.dumps(meta))
        store = ForecastStore.load(tmp_path, "M")
        assert store.is_stale()


# ---------------------------------------------------------------------------
# build_store_entries
# ---------------------------------------------------------------------------

class TestBuildStoreEntries:

    def test_basic_conversion(self):
        df = pd.DataFrame([
            {"sku": "SKU-001", "status": "ok", "model_winner": "AutoETS",
             "mase": 0.8, "forecast_mean_daily": 3.2, "forecast_sigma_daily": 0.4,
             "granularity": "M", "h": 3},
        ])
        entries = build_store_entries(df, "M")
        assert len(entries) == 1
        assert entries[0].sku == "SKU-001"
        assert entries[0].model == "AutoETS"
        assert entries[0].forecast_mean_daily == pytest.approx(3.2)

    def test_nan_fields_become_none(self):
        df = pd.DataFrame([
            {"sku": "SKU-NAN", "status": "no_forecast", "model_winner": None,
             "mase": float("nan"), "forecast_mean_daily": float("nan"),
             "forecast_sigma_daily": float("nan"), "granularity": "M", "h": 3},
        ])
        entries = build_store_entries(df, "M")
        assert entries[0].mase is None
        assert entries[0].forecast_mean_daily is None
