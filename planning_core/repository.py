from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from planning_core.paths import OUTPUT_DIR


TABLE_FILES = {
    "product_catalog": "product_catalog.csv",
    "transactions": "transactions.csv",
    "inventory_snapshot": "inventory_snapshot.csv",
    "internal_transfers": "internal_transfers.csv",
    "purchase_orders": "purchase_orders.csv",
    "purchase_order_lines": "purchase_order_lines.csv",
    "purchase_receipts": "purchase_receipts.csv",
}

MANIFEST_FILE = "dataset_manifest.json"

DATE_COLUMNS = {
    "transactions": ["date"],
    "inventory_snapshot": ["snapshot_date"],
    "internal_transfers": ["ship_date", "expected_receipt_date", "receipt_date"],
    "purchase_orders": ["order_date", "expected_receipt_date"],
    "purchase_receipts": ["receipt_date"],
}


class CanonicalRepository:
    """Capa de acceso a las tablas canonicas exportadas por el simulador."""

    def __init__(self, base_path: str | Path | None = None):
        self.base_path = Path(base_path) if base_path else OUTPUT_DIR
        self._cache: dict[str, pd.DataFrame] = {}
        self._manifest_cache: dict | None = None

    def available_tables(self) -> list[str]:
        return [table_name for table_name in TABLE_FILES if self.file_path(table_name).exists()]

    def file_path(self, table_name: str) -> Path:
        if table_name not in TABLE_FILES:
            raise KeyError(f"Tabla no soportada: {table_name}")
        return self.base_path / TABLE_FILES[table_name]

    def load_table(self, table_name: str) -> pd.DataFrame:
        if table_name not in self._cache:
            file_path = self.file_path(table_name)
            if not file_path.exists():
                raise FileNotFoundError(f"No existe el archivo esperado para {table_name}: {file_path}")
            self._cache[table_name] = pd.read_csv(
                file_path,
                parse_dates=DATE_COLUMNS.get(table_name, []),
                low_memory=False,
            )
        return self._cache[table_name].copy()

    def manifest_path(self) -> Path:
        return self.base_path / MANIFEST_FILE

    def load_manifest(self) -> dict:
        if self._manifest_cache is None:
            manifest_path = self.manifest_path()
            if not manifest_path.exists():
                self._manifest_cache = {}
            else:
                with open(manifest_path, "r", encoding="utf-8") as manifest_file:
                    self._manifest_cache = json.load(manifest_file)
        return dict(self._manifest_cache)
