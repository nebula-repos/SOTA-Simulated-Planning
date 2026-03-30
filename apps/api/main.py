from __future__ import annotations

import math
from typing import Any, Optional

import pandas as pd
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

# ---------------------------------------------------------------------------
# Valores válidos para parámetros de query
# ---------------------------------------------------------------------------

_VALID_GRANULARITIES = {"M", "W", "D"}


# ---------------------------------------------------------------------------
# Serialización segura (NaN / Inf → None)
# ---------------------------------------------------------------------------

def _sanitize(obj: Any) -> Any:
    """Reemplaza recursivamente float NaN/Inf por None para serialización JSON."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Helpers de validación
# ---------------------------------------------------------------------------

def _require_sku(sku: str) -> None:
    """Lanza 404 si el SKU no existe en el catálogo."""
    try:
        if service.sku_summary(sku) is None:
            raise HTTPException(status_code=404, detail=f"SKU no encontrado: {sku}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc


def _check_granularity(granularity: str | None) -> None:
    """Lanza 422 si la granularidad no es válida."""
    if granularity is not None and granularity not in _VALID_GRANULARITIES:
        raise HTTPException(
            status_code=422,
            detail=f"Granularidad inválida: {granularity!r}. Valores válidos: {sorted(_VALID_GRANULARITIES)}.",
        )


def _check_location(location: str | None) -> None:
    """Lanza 422 si la location no existe en el repositorio."""
    if location is None:
        return
    try:
        known = set(service.list_locations())  # list_locations() retorna list[str]
    except Exception:
        return  # Si no podemos verificar, dejamos pasar y el servicio retornará vacío
    if location not in known:
        raise HTTPException(
            status_code=422,
            detail=f"Location desconocida: {location!r}. Ver GET /locations para opciones válidas.",
        )


# ---------------------------------------------------------------------------
# Endpoints de salud y catálogo
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    try:
        return {
            "status": "ok",
            "dataset": service.dataset_overview(),
            "quality": service.dataset_health(),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Error al cargar el dataset: {exc}") from exc


@app.get("/skus")
def list_skus(
    search: Optional[str] = Query(default=None, description="Filtro por sku, nombre, categoria o proveedor"),
    limit: int = Query(default=50, ge=1, le=500),
):
    try:
        return _sanitize(service.list_skus(search=search, limit=limit))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error interno: {exc}") from exc


@app.get("/locations")
def list_locations():
    try:
        return service.list_locations()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error interno: {exc}") from exc


@app.get("/sku/{sku}/summary")
def sku_summary(sku: str):
    try:
        summary = service.sku_summary(sku)
        if summary is None:
            raise HTTPException(status_code=404, detail=f"SKU no encontrado: {sku}")
        return summary
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error interno: {exc}") from exc


@app.get("/sku/{sku}/timeseries")
def sku_timeseries(
    sku: str,
    location: Optional[str] = Query(default=None, description="Si se omite, agrega todas las locations"),
):
    _require_sku(sku)
    _check_location(location)
    try:
        dataframe = service.sku_timeseries(sku=sku, location=location)
        return dataframe.to_dict(orient="records")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error interno: {exc}") from exc


@app.get("/sku/{sku}/supply")
def sku_supply(sku: str):
    _require_sku(sku)
    try:
        return {
            "purchase_receipts": service.purchase_receipts_for_sku(sku).to_dict(orient="records"),
            "internal_transfers": service.internal_transfers_for_sku(sku).to_dict(orient="records"),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error interno: {exc}") from exc


# ---------------------------------------------------------------------------
# Clasificacion de demanda (Fase 1)
# ---------------------------------------------------------------------------

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
    _check_granularity(granularity)
    try:
        df = service.classify_catalog(granularity=granularity)

        if abc_class:
            df = df[df["abc_class"] == abc_class.upper()]
        if sb_class:
            df = df[df["sb_class"] == sb_class.lower()]

        total = len(df)
        page = df.iloc[offset:offset + limit]

        records = page.to_dict(orient="records")
        for record in records:
            # Asegurar que quality_flags sea serializable como lista JSON
            if isinstance(record.get("quality_flags"), list):
                record["quality_flags"] = [str(f) for f in record["quality_flags"]]

        return {"total": total, "offset": offset, "limit": limit, "items": records}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error interno: {exc}") from exc


@app.get("/classification/summary")
def classification_summary(
    granularity: Optional[str] = Query(default=None, description="Granularidad: M, W, D. None = default oficial del repo."),
):
    """Resumen agregado: conteos por clase, matriz ABC-XYZ, distribuciones."""
    _check_granularity(granularity)
    try:
        return service.classification_summary(granularity=granularity)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error interno: {exc}") from exc


@app.get("/sku/{sku}/classification")
def sku_classification(
    sku: str,
    location: Optional[str] = Query(default=None, description="Location especifica (default: global)"),
    granularity: Optional[str] = Query(default=None, description="Granularidad: M, W, D. None = mensual oficial si no hay location; automatica si se consulta una location."),
):
    """Clasificacion detallada de un SKU individual.

    Retorna sb_class='inactive' para SKUs sin transacciones en lugar de 404.
    """
    _check_granularity(granularity)
    _check_location(location)
    try:
        result = service.classify_single_sku(sku, location=location, granularity=granularity)
        if result is None:
            raise HTTPException(status_code=404, detail=f"SKU no encontrado en el catálogo: {sku}")
        return result
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error interno: {exc}") from exc


@app.get("/sku/{sku}/demand-series")
def sku_demand_series(
    sku: str,
    location: Optional[str] = Query(default=None),
    granularity: Optional[str] = Query(default=None),
):
    """Serie temporal de demanda preparada (con ceros rellenados)."""
    _check_granularity(granularity)
    _require_sku(sku)
    _check_location(location)
    try:
        df = service.sku_demand_series(sku, location=location, granularity=granularity)
        return df.to_dict(orient="records")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error interno: {exc}") from exc


@app.get("/sku/{sku}/acf")
def sku_acf(
    sku: str,
    location: Optional[str] = Query(default=None),
    granularity: Optional[str] = Query(default=None),
    max_lags: int = Query(default=40, ge=1, le=200),
):
    """Funcion de autocorrelacion (ACF) para un SKU."""
    _check_granularity(granularity)
    _require_sku(sku)
    _check_location(location)
    try:
        return service.sku_acf(sku, location=location, granularity=granularity, max_lags=max_lags)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error interno: {exc}") from exc


# ---------------------------------------------------------------------------
# Forecasting (Fase 2)
# ---------------------------------------------------------------------------

@app.get("/sku/{sku}/forecast")
def sku_forecast(
    sku: str,
    granularity: Optional[str] = Query(
        default=None,
        description="Granularidad: M, W, D. None = mensual por defecto.",
    ),
    h: int = Query(default=6, ge=1, le=36, description="Horizonte de forecast en periodos."),
    n_windows: int = Query(default=3, ge=2, le=10, description="Ventanas del backtest expanding-window."),
    location: Optional[str] = Query(default=None, description="Location especifica (default: red completa)."),
):
    """Forecast automatico para un SKU.

    Corre el horse-race de modelos (backtest expanding-window) y devuelve
    el pronostico del modelo ganador junto con el resumen del backtest.

    El campo ``forecast`` es una lista de registros con ``ds``, ``yhat`` y,
    si el modelo los soporta, ``yhat_lo80`` / ``yhat_hi80``.
    """
    _check_granularity(granularity)
    _require_sku(sku)
    _check_location(location)
    try:
        result = service.sku_forecast(
            sku,
            location=location,
            granularity=granularity,
            h=h,
            n_windows=n_windows,
        )

        # Serializar el DataFrame de forecast a lista de registros JSON-friendly
        output = dict(result)
        output.pop("demand_series", None)  # campo interno — no exponer en API
        if isinstance(output.get("forecast"), pd.DataFrame):
            fc = output["forecast"].copy()
            if "ds" in fc.columns:
                fc["ds"] = fc["ds"].astype(str)
            output["forecast"] = fc.to_dict(orient="records")

        return _sanitize(output)
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Repositorio no disponible: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error interno al generar forecast: {exc}") from exc
