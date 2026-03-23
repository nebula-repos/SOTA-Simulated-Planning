from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from planning_core.repository import CanonicalRepository
from planning_core.services import PlanningService


app = FastAPI(
    title="SOTA Planning API",
    version="0.1.0",
    description="API liviana para explorar el modelo canonico de planning.",
)

repository = CanonicalRepository()
service = PlanningService(repository)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "dataset": service.dataset_overview(),
        "quality": service.dataset_health(),
    }


@app.get("/skus")
def list_skus(
    search: Optional[str] = Query(default=None, description="Filtro por sku, nombre, categoria o proveedor"),
    limit: int = Query(default=50, ge=1, le=500),
):
    return service.list_skus(search=search, limit=limit)


@app.get("/locations")
def list_locations():
    return service.list_locations()


@app.get("/sku/{sku}/summary")
def sku_summary(sku: str):
    summary = service.sku_summary(sku)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"SKU no encontrado: {sku}")
    return summary


@app.get("/sku/{sku}/timeseries")
def sku_timeseries(
    sku: str,
    location: Optional[str] = Query(default=None, description="Si se omite, agrega todas las locations"),
):
    if service.sku_summary(sku) is None:
        raise HTTPException(status_code=404, detail=f"SKU no encontrado: {sku}")
    dataframe = service.sku_timeseries(sku=sku, location=location)
    return dataframe.to_dict(orient="records")


@app.get("/sku/{sku}/supply")
def sku_supply(sku: str):
    if service.sku_summary(sku) is None:
        raise HTTPException(status_code=404, detail=f"SKU no encontrado: {sku}")
    return {
        "purchase_receipts": service.purchase_receipts_for_sku(sku).to_dict(orient="records"),
        "internal_transfers": service.internal_transfers_for_sku(sku).to_dict(orient="records"),
    }
