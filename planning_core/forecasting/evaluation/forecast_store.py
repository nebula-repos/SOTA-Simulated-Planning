"""Almacén de forecasts materializados del catálogo completo.

Persiste los resultados del batch forecast en ``output/derived/`` para que
``catalog_health_report`` los consuma sin recalcular. Cada granularidad tiene
su propio archivo parquet; el batch sobreescribe el archivo anterior.

Formato en disco
----------------
``output/derived/forecast_catalog_{granularity}.parquet``
    Una fila por SKU con las stats derivadas del forecast.
``output/derived/forecast_catalog_{granularity}_meta.json``
    Metadata liviana (run_date, n_skus, coverage_pct, top_model).
    Se lee en ``catalog_forecast_status()`` sin abrir el parquet.

Integración con safety stock
-----------------------------
``forecast_mean_daily``
    Media del yhat sobre el horizonte h, convertida a unidades/día.
    Reemplaza la media histórica en el cálculo del ROP.
``forecast_sigma_daily``
    RMSE del backtest dividido por √(días/período).
    Reemplaza la σ histórica en el Safety Stock.

Cuando el store no existe o está stale, ``catalog_health_report`` cae
automáticamente a la señal histórica sin interrumpir el flujo.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


_DAYS_PER_PERIOD: dict[str, float] = {
    "M": 365.25 / 12,
    "W": 7.0,
    "D": 1.0,
}

_PARQUET_FILE = "forecast_catalog_{granularity}.parquet"
_META_FILE = "forecast_catalog_{granularity}_meta.json"

# Máximo de días antes de considerar el store stale, por granularidad
DEFAULT_MAX_AGE_DAYS: dict[str, int] = {
    "M": 35,
    "W": 9,
    "D": 2,
}


@dataclass
class ForecastStoreEntry:
    """Resultado de forecast para un SKU, listo para inyectar en safety stock."""

    sku: str
    status: str                         # ok | fallback | no_forecast | error
    model: str | None
    mase: float | None
    forecast_mean_daily: float | None   # mean(yhat) / days_per_period
    forecast_sigma_daily: float | None  # rmse_backtest / sqrt(days_per_period)
    granularity: str
    h: int
    run_date: str                       # ISO date del batch que generó este entry


def _nan_to_none(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


class ForecastStore:
    """Acceso de lectura al artefacto de forecast materializado.

    Se instancia con ``ForecastStore.load()`` — retorna None si el archivo
    no existe, evitando que el caller tenga que manejar FileNotFoundError.
    """

    def __init__(self, df: pd.DataFrame, meta: dict, granularity: str) -> None:
        self._index: dict[str, ForecastStoreEntry] = {}
        self._meta = meta
        self._granularity = granularity
        for _, row in df.iterrows():
            entry = ForecastStoreEntry(
                sku=str(row["sku"]),
                status=str(row.get("status") or ""),
                model=row.get("model") or None,
                mase=_nan_to_none(row.get("mase")),
                forecast_mean_daily=_nan_to_none(row.get("forecast_mean_daily")),
                forecast_sigma_daily=_nan_to_none(row.get("forecast_sigma_daily")),
                granularity=str(row.get("granularity") or granularity),
                h=int(row.get("h") or 0),
                run_date=str(row.get("run_date") or ""),
            )
            self._index[entry.sku] = entry

    # ------------------------------------------------------------------
    # Carga
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, base_dir: Path, granularity: str) -> "ForecastStore | None":
        """Carga el store desde disco. Retorna None si no existe."""
        parquet_path = base_dir / _PARQUET_FILE.format(granularity=granularity)
        meta_path = base_dir / _META_FILE.format(granularity=granularity)

        if not parquet_path.exists():
            return None
        try:
            df = pd.read_parquet(parquet_path)
            meta: dict = {}
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return cls(df, meta, granularity)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Acceso por SKU
    # ------------------------------------------------------------------

    def get(self, sku: str) -> ForecastStoreEntry | None:
        """Retorna el entry del SKU o None si no está en el store."""
        return self._index.get(sku)

    def __len__(self) -> int:
        return len(self._index)

    # ------------------------------------------------------------------
    # Frescura
    # ------------------------------------------------------------------

    def is_stale(self, max_age_days: int | None = None) -> bool:
        """True si el store tiene más de max_age_days desde el run_date."""
        run_date_str = self._meta.get("run_date")
        if not run_date_str:
            return True
        try:
            run_dt = datetime.fromisoformat(run_date_str.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - run_dt).days
            threshold = max_age_days if max_age_days is not None else DEFAULT_MAX_AGE_DAYS.get(
                self._granularity, 35
            )
            return age_days > threshold
        except Exception:
            return True

    def metadata(self) -> dict:
        """Metadata del store: run_date, n_skus, cobertura, modelo dominante."""
        return dict(self._meta)

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    @staticmethod
    def save(
        entries: list[ForecastStoreEntry],
        base_dir: Path,
        granularity: str,
    ) -> Path:
        """Serializa la lista de entries a parquet + meta JSON.

        Usa escritura atómica (write-then-rename) para garantizar que un batch
        interrumpido no corrompe el artefacto anterior. El artefacto final solo
        reemplaza al previo cuando ambos archivos (.parquet y .json) están
        completamente escritos.
        """
        import os

        base_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = base_dir / _PARQUET_FILE.format(granularity=granularity)
        meta_path = base_dir / _META_FILE.format(granularity=granularity)

        # Rutas temporales en el mismo directorio (os.replace es atómico en el mismo filesystem)
        parquet_tmp = base_dir / (_PARQUET_FILE.format(granularity=granularity) + ".tmp")
        meta_tmp = base_dir / (_META_FILE.format(granularity=granularity) + ".tmp")

        run_date = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

        rows = [asdict(e) for e in entries]
        df = pd.DataFrame(rows)

        # Escribir a archivos temporales primero
        df.to_parquet(parquet_tmp, index=False)

        n_ok = sum(1 for e in entries if e.status == "ok")
        n_no_forecast = sum(1 for e in entries if e.status == "no_forecast")
        n_with_forecast = sum(
            1 for e in entries
            if e.forecast_mean_daily is not None and e.forecast_mean_daily > 0
        )
        coverage_pct = round(n_with_forecast / len(entries), 4) if entries else 0.0

        # Modelo más frecuente entre los ok
        ok_models = [e.model for e in entries if e.status == "ok" and e.model]
        top_model: str | None = None
        if ok_models:
            from collections import Counter
            top_model = Counter(ok_models).most_common(1)[0][0]

        meta = {
            "run_date": run_date,
            "granularity": granularity,
            "n_skus": len(entries),
            "n_ok": n_ok,
            "n_no_forecast": n_no_forecast,
            "coverage_pct": coverage_pct,
            "top_model": top_model,
        }
        meta_tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        # Rename atómico: solo en este punto el artefacto anterior queda reemplazado
        os.replace(parquet_tmp, parquet_path)
        os.replace(meta_tmp, meta_path)

        return parquet_path


def build_store_entries(
    sku_results: pd.DataFrame,
    granularity: str,
) -> list[ForecastStoreEntry]:
    """Convierte el DataFrame de sku_results (catalog_runner) en entries del store.

    Espera las columnas ``forecast_mean_daily`` y ``forecast_sigma_daily`` que
    ``_evaluate_sku`` agrega cuando captura el yhat del forecast.
    """
    run_date = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    entries: list[ForecastStoreEntry] = []
    for _, row in sku_results.iterrows():
        entries.append(ForecastStoreEntry(
            sku=str(row["sku"]),
            status=str(row.get("status") or ""),
            model=row.get("model_winner") or None,
            mase=_nan_to_none(row.get("mase")),
            forecast_mean_daily=_nan_to_none(row.get("forecast_mean_daily")),
            forecast_sigma_daily=_nan_to_none(row.get("forecast_sigma_daily")),
            granularity=str(row.get("granularity") or granularity),
            h=int(row.get("h") or 0),
            run_date=run_date,
        ))
    return entries


__all__ = [
    "ForecastStore",
    "ForecastStoreEntry",
    "build_store_entries",
    "DEFAULT_MAX_AGE_DAYS",
]
