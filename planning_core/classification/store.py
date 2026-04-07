"""Almacén de clasificaciones materializadas del catálogo completo.

Persiste el resultado de ``classify_all_skus()`` en ``output/derived/`` para que
la API y el health report lo consuman sin recalcular en cada request (D14).

Formato en disco
----------------
``output/derived/classification_catalog_{granularity}.parquet``
    Una fila por SKU con todas las columnas de clasificación.
``output/derived/classification_catalog_{granularity}_meta.json``
    Metadata liviana (run_date, n_skus, scope, distribución ABC).
    Se lee en ``catalog_classification_status()`` sin abrir el parquet.

Frescura
--------
El store tiene el mismo modelo de frescura que ``ForecastStore``:

    DEFAULT_MAX_AGE_DAYS = {"M": 35, "W": 9, "D": 2}

La clasificación cambia cuando llegan datos nuevos al repositorio. Con una
frecuencia de actualización mensual, un artefacto de 35 días es aceptable.

Escritura atómica
-----------------
``ClassificationStore.save()`` escribe primero a archivos ``.tmp`` y luego
hace ``os.replace()`` para garantizar que un proceso interrumpido no deja
el artefacto previo corrupto.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


_PARQUET_FILE = "classification_catalog_{granularity}.parquet"
_META_FILE = "classification_catalog_{granularity}_meta.json"

DEFAULT_MAX_AGE_DAYS: dict[str, int] = {
    "M": 35,
    "W": 9,
    "D": 2,
}

# Columnas mínimas esperadas en el DataFrame de clasificación
_REQUIRED_COLS = ["sku", "abc_class", "sb_class", "xyz_class", "is_seasonal",
                  "has_trend", "quality_score", "granularity"]


def _nan_to_none(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


class ClassificationStore:
    """Acceso al artefacto de clasificación materializado.

    Se instancia con ``ClassificationStore.load()`` — retorna None si el
    archivo no existe, evitando que el caller tenga que manejar
    ``FileNotFoundError``.

    Uso típico
    ----------
    ::

        store = ClassificationStore.load(Path("output/derived"), "M")
        if store is not None and not store.is_stale():
            df = store.all_skus_df()
        else:
            df = service.classify_catalog(granularity="M")  # fallback
    """

    def __init__(self, df: pd.DataFrame, meta: dict, granularity: str) -> None:
        self._df = df.copy()
        self._meta = meta
        self._granularity = granularity
        # Índice por SKU para acceso O(1)
        self._index: dict[str, dict] = {
            str(row["sku"]): row.to_dict()
            for _, row in df.iterrows()
        }

    # ------------------------------------------------------------------
    # Carga
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, base_dir: Path, granularity: str) -> "ClassificationStore | None":
        """Carga el store desde disco. Retorna None si no existe o está corrupto."""
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
        except Exception as exc:
            import warnings
            warnings.warn(
                f"ClassificationStore.load: error al leer artefacto ({exc}) — retornando None.",
                stacklevel=2,
            )
            return None

    # ------------------------------------------------------------------
    # Acceso
    # ------------------------------------------------------------------

    def get(self, sku: str) -> dict | None:
        """Retorna el dict de clasificación del SKU o None si no está."""
        return self._index.get(sku)

    def all_skus_df(self) -> pd.DataFrame:
        """Retorna el DataFrame completo de clasificación (copia defensiva)."""
        return self._df.copy()

    def __len__(self) -> int:
        return len(self._index)

    # ------------------------------------------------------------------
    # Frescura
    # ------------------------------------------------------------------

    def is_stale(self, max_age_days: int | None = None) -> bool:
        """True si el store tiene más de ``max_age_days`` desde el ``run_date``."""
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
        """Metadata del store: run_date, n_skus, scope, distribución ABC."""
        return dict(self._meta)

    # ------------------------------------------------------------------
    # Persistencia atómica
    # ------------------------------------------------------------------

    @staticmethod
    def save(
        df: pd.DataFrame,
        granularity: str,
        base_dir: Path,
        classification_scope: str = "network_aggregate",
    ) -> Path:
        """Serializa el DataFrame de clasificación a parquet + meta JSON.

        Usa escritura atómica (write-then-rename) para garantizar que un
        proceso interrumpido no corrompe el artefacto anterior.

        Parameters
        ----------
        df : pd.DataFrame
            Salida de ``classify_all_skus()`` (o equivalente enriquecido con censura).
            Debe tener al menos las columnas en ``_REQUIRED_COLS``.
        granularity : str
            Granularidad del catálogo (``"M"``, ``"W"`` o ``"D"``).
        base_dir : Path
            Directorio de destino. Se crea si no existe.
        classification_scope : str
            Alcance de la clasificación (``"network_aggregate"`` por defecto).

        Returns
        -------
        Path
            Ruta del parquet final escrito.
        """
        base_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = base_dir / _PARQUET_FILE.format(granularity=granularity)
        meta_path = base_dir / _META_FILE.format(granularity=granularity)
        parquet_tmp = base_dir / (_PARQUET_FILE.format(granularity=granularity) + ".tmp")
        meta_tmp = base_dir / (_META_FILE.format(granularity=granularity) + ".tmp")

        run_date = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

        # Escribir parquet temporal
        df.to_parquet(parquet_tmp, index=False)

        # Calcular metadata
        n_skus = int(len(df))
        abc_distribution: dict[str, int] = {}
        sb_distribution: dict[str, int] = {}
        if "abc_class" in df.columns:
            abc_distribution = {
                str(k): int(v)
                for k, v in df["abc_class"].value_counts(dropna=False).items()
            }
        if "sb_class" in df.columns:
            sb_distribution = {
                str(k): int(v)
                for k, v in df["sb_class"].value_counts(dropna=False).items()
            }
        seasonal_pct = round(float(df["is_seasonal"].mean()), 4) if "is_seasonal" in df.columns and n_skus > 0 else 0.0
        avg_quality = round(float(df["quality_score"].mean()), 4) if "quality_score" in df.columns and n_skus > 0 else 0.0

        meta = {
            "run_date": run_date,
            "granularity": granularity,
            "n_skus": n_skus,
            "classification_scope": classification_scope,
            "abc_distribution": abc_distribution,
            "sb_distribution": sb_distribution,
            "seasonal_pct": seasonal_pct,
            "avg_quality_score": avg_quality,
        }
        meta_tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        # Rename atómico: solo aquí el artefacto anterior queda reemplazado
        os.replace(parquet_tmp, parquet_path)
        os.replace(meta_tmp, meta_path)

        return parquet_path


__all__ = [
    "ClassificationStore",
    "DEFAULT_MAX_AGE_DAYS",
]
