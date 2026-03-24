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


# ------------------------------------------------------------------
# Clasificacion de demanda (Fase 1)
# ------------------------------------------------------------------

@app.get("/classification")
def classification(
    granularity: Optional[str] = Query(
        default=None, description="Granularidad: M, W, D. None = default oficial del repo (mensual, agregado de red).",
    ),
    abc_class: Optional[str] = Query(default=None, description="Filtrar por clase ABC (A, B, C)"),
    sb_class: Optional[str] = Query(default=None, description="Filtrar por clase Syntetos-Boylan"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Clasificacion completa del catalogo con filtros opcionales."""
    df = service.classify_catalog(granularity=granularity)

    if abc_class:
        df = df[df["abc_class"] == abc_class.upper()]
    if sb_class:
        df = df[df["sb_class"] == sb_class.lower()]

    total = len(df)
    page = df.iloc[offset:offset + limit]

    # Convertir quality_flags list a string para JSON serialization
    records = page.to_dict(orient="records")
    for record in records:
        if isinstance(record.get("quality_flags"), list):
            record["quality_flags"] = record["quality_flags"]

    return {"total": total, "offset": offset, "limit": limit, "items": records}


@app.get("/classification/summary")
def classification_summary(
    granularity: Optional[str] = Query(default=None, description="Granularidad: M, W, D. None = default oficial del repo."),
):
    """Resumen agregado: conteos por clase, matriz ABC-XYZ, distribuciones."""
    return service.classification_summary(granularity=granularity)


@app.get("/sku/{sku}/classification")
def sku_classification(
    sku: str,
    location: Optional[str] = Query(default=None, description="Location especifica (default: global)"),
    granularity: Optional[str] = Query(default=None, description="Granularidad: M, W, D. None = mensual oficial si no hay location; automatica si se consulta una location."),
):
    """Clasificacion detallada de un SKU individual."""
    result = service.classify_single_sku(sku, location=location, granularity=granularity)
    if result is None:
        raise HTTPException(status_code=404, detail=f"SKU sin transacciones: {sku}")
    return result


@app.get("/sku/{sku}/demand-series")
def sku_demand_series(
    sku: str,
    location: Optional[str] = Query(default=None),
    granularity: Optional[str] = Query(default=None),
):
    """Serie temporal de demanda preparada (con ceros rellenados)."""
    if service.sku_summary(sku) is None:
        raise HTTPException(status_code=404, detail=f"SKU no encontrado: {sku}")
    df = service.sku_demand_series(sku, location=location, granularity=granularity)
    return df.to_dict(orient="records")


@app.get("/sku/{sku}/acf")
def sku_acf(
    sku: str,
    location: Optional[str] = Query(default=None),
    granularity: Optional[str] = Query(default=None),
    max_lags: int = Query(default=40, ge=1, le=200),
):
    """Funcion de autocorrelacion (ACF) para un SKU."""
    if service.sku_summary(sku) is None:
        raise HTTPException(status_code=404, detail=f"SKU no encontrado: {sku}")
    return service.sku_acf(sku, location=location, granularity=granularity, max_lags=max_lags)
