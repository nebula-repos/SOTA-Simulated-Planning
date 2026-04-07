"""Pipeline de clasificación de demanda con soporte de materialización.

Contiene la lógica de orquestación para clasificar SKUs individuales y el
catálogo completo, incluyendo el enriquecimiento con censura de demanda y
la persistencia opcional del resultado como ``ClassificationStore``.

Funciones públicas
------------------
compute_censoring_info(sku_tx, sku_inv, granularity, stockout_threshold)
    Detecta periodos de demanda censurada para un SKU.

augment_profile_with_censoring(profile, sku_tx, sku_inv, granularity)
    Enriquece el perfil de clasificación de un SKU con métricas de censura.

augment_catalog_classification_with_censoring(classification_df, transactions, inventory, granularity)
    Enriquece el DataFrame de clasificación del catálogo con censura (loop por SKU).

run_sku_classification(service, sku, location, granularity)
    Clasifica un SKU individual con logging y enriquecimiento por censura.

run_catalog_classification_full(service, granularity)
    Recalcula la clasificación completa del catálogo (sin usar el store).

run_catalog_classification(service, granularity, persist, derived_dir)
    Clasifica el catálogo y opcionalmente persiste el artefacto.

catalog_classification_status(service, granularity, derived_dir)
    Lectura rápida del JSON de metadata — no abre el parquet.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from planning_core.services import PlanningService


def _counts_dict(values: pd.Series) -> dict[str, int]:
    if values.empty:
        return {}
    return {str(k): int(v) for k, v in values.value_counts(dropna=False).items()}


# ---------------------------------------------------------------------------
# Helpers de censura (funciones puras — no dependen de PlanningService)
# ---------------------------------------------------------------------------

def compute_censoring_info(
    sku_tx: pd.DataFrame,
    sku_inv: pd.DataFrame,
    granularity: str,
    stockout_threshold: float = 0.0,
) -> tuple[pd.DataFrame, pd.Series, dict]:
    """Detecta periodos de demanda censurada para un SKU.

    Parameters
    ----------
    sku_tx : pd.DataFrame
        Transacciones ya filtradas por SKU (y opcionalmente location).
    sku_inv : pd.DataFrame
        Snapshots de inventario ya filtrados por SKU.
    granularity : str
        Granularidad temporal (``"M"``, ``"W"`` o ``"D"``).
    stockout_threshold : float
        Umbral de stock para considerar un periodo como potencial stockout.

    Returns
    -------
    tuple[pd.DataFrame, pd.Series, dict]
        ``(demand_df, censored_mask, summary_dict)``
    """
    from planning_core.classification.core import prepare_demand_series
    from planning_core.preprocessing import censored_summary, mark_censored_demand

    demand_df = prepare_demand_series(sku_tx, granularity=granularity)
    censored = mark_censored_demand(
        demand_df,
        sku_inv,
        granularity=granularity,
        stockout_threshold=stockout_threshold,
    )
    summary = censored_summary(censored, demand_df)
    return demand_df, censored, summary


def _censoring_penalty(summary: dict) -> float:
    """Calcula la penalización de quality_score por censura (0–0.25)."""
    penalty = (
        0.15 * float(summary.get("censored_pct", 0.0))
        + 0.35 * float(summary.get("censored_demand_pct", 0.0))
    )
    return round(min(0.25, penalty), 3)


def augment_profile_with_censoring(
    profile: dict,
    sku_tx: pd.DataFrame,
    sku_inv: pd.DataFrame,
    granularity: str,
) -> dict:
    """Enriquece el perfil de clasificación de un SKU con métricas de censura.

    Agrega campos ``has_censored_demand``, ``censored_periods``,
    ``censored_pct``, ``censored_demand``, ``censored_demand_pct``,
    ``stockout_no_sale_periods``, ``quality_score_base``,
    ``censoring_penalty`` y ajusta ``quality_score``.

    Parameters
    ----------
    profile : dict
        Salida de ``classify_sku()`` (o dict equivalente).
    sku_tx : pd.DataFrame
        Transacciones ya filtradas por SKU.
    sku_inv : pd.DataFrame
        Snapshots de inventario ya filtrados por SKU.
    granularity : str
        Granularidad temporal.

    Returns
    -------
    dict
        Perfil enriquecido (modifica el dict en-place y lo retorna).
    """
    demand_df, censored, summary = compute_censoring_info(sku_tx, sku_inv, granularity=granularity)
    stockout_no_sale_periods = int(
        ((censored.values) & (demand_df["demand"].values == 0)).sum()
    ) if not demand_df.empty else 0
    total_periods = len(demand_df)
    penalty = _censoring_penalty(summary)

    base_quality = float(profile.get("quality_score", 0.0))
    quality_flags = profile.get("quality_flags", [])
    if not isinstance(quality_flags, list):
        quality_flags = []

    if summary["censored_periods"] > 0:
        quality_flags.append(
            f"demanda_censurada ({summary['censored_periods']}/{summary['total_periods']} "
            f"periodos; {summary['censored_demand_pct']:.1%} volumen)"
        )
    if stockout_no_sale_periods > 0:
        quality_flags.append(f"sin_venta_por_stockout ({stockout_no_sale_periods} periodos)")

    profile["has_censored_demand"] = bool(summary["censored_periods"] > 0)
    profile["censored_periods"] = int(summary["censored_periods"])
    profile["censored_pct"] = float(summary["censored_pct"])
    profile["censored_demand"] = float(summary["censored_demand"])
    profile["censored_demand_pct"] = float(summary["censored_demand_pct"])
    profile["stockout_no_sale_periods"] = stockout_no_sale_periods
    profile["stockout_no_sale_pct"] = (
        round(stockout_no_sale_periods / total_periods, 4) if total_periods > 0 else 0.0
    )
    profile["quality_score_base"] = round(base_quality, 3)
    profile["censoring_penalty"] = penalty
    profile["quality_score"] = round(max(0.0, base_quality - penalty), 3)
    profile["quality_flags"] = quality_flags
    return profile


def augment_catalog_classification_with_censoring(
    classification_df: pd.DataFrame,
    transactions: pd.DataFrame,
    inventory: pd.DataFrame,
    granularity: str,
) -> pd.DataFrame:
    """Enriquece el DataFrame de clasificación del catálogo con métricas de censura.

    Itera sobre cada SKU y aplica ``augment_profile_with_censoring``.

    Parameters
    ----------
    classification_df : pd.DataFrame
        Salida de ``classify_all_skus()``.
    transactions : pd.DataFrame
        Tabla completa de transacciones.
    inventory : pd.DataFrame
        Tabla completa de inventory_snapshot.
    granularity : str
        Granularidad temporal.

    Returns
    -------
    pd.DataFrame
        DataFrame enriquecido (una fila por SKU).
    """
    tx_groups = {sku: frame.copy() for sku, frame in transactions.groupby("sku")}
    inv_groups = {sku: frame.copy() for sku, frame in inventory.groupby("sku")}

    empty_tx = transactions.iloc[0:0].copy()
    empty_inv = inventory.iloc[0:0].copy()

    enriched_rows = []
    for _, row in classification_df.iterrows():
        sku = row["sku"]
        enriched_rows.append(
            augment_profile_with_censoring(
                row.to_dict(),
                sku_tx=tx_groups.get(sku, empty_tx),
                sku_inv=inv_groups.get(sku, empty_inv),
                granularity=granularity,
            )
        )
    return pd.DataFrame(enriched_rows)


# ---------------------------------------------------------------------------
# run_sku_classification
# ---------------------------------------------------------------------------

def run_sku_classification(
    service: "PlanningService",
    sku: str,
    location: str | None = None,
    granularity: str | None = None,
) -> dict | None:
    """Clasifica un SKU individual con logging y enriquecimiento por censura.

    Parameters
    ----------
    service : PlanningService
        Servicio con acceso al repositorio y al logger.
    sku : str
        Identificador del SKU.
    location : str or None
        Si se provee, filtra la serie a esa sucursal.
        Si es None, usa el agregado de red (clasificación oficial).
    granularity : str or None
        Granularidad temporal. Si None, usa la oficial (sin location) o
        la derivada de los datos (con location).

    Returns
    -------
    dict or None
        Perfil de clasificación enriquecido, o None si el SKU no existe.
    """
    from planning_core.classification.core import classify_sku, select_granularity

    transactions = service.repository.load_table("transactions")
    inventory = service.repository.load_table("inventory_snapshot")

    sku_tx = transactions[transactions["sku"] == sku]
    sku_inv = inventory[inventory["sku"] == sku]

    if location:
        sku_tx = sku_tx[sku_tx["location"] == location]
        sku_inv = sku_inv[sku_inv["location"] == location]

    if sku_tx.empty:
        granularity_out = granularity or service.official_classification_granularity()
        result = {
            "sku": sku,
            "sb_class": "inactive",
            "abc_class": None,
            "xyz_class": None,
            "abc_xyz": None,
            "is_seasonal": False,
            "has_trend": False,
            "quality_score": 0.0,
            "quality_flags": ["sin_transacciones"],
            "granularity": granularity_out,
            "classification_scope": service.classification_scope() if location is None else "location",
        }
        service.event_logger.emit(
            event_name="classification.sku.completed",
            module="classification",
            level="WARN",
            status="inactive",
            entity_type="sku",
            entity_id=sku,
            params={"location": location, "granularity": granularity_out},
            result={
                "sb_class": result["sb_class"],
                "abc_class": result["abc_class"],
                "xyz_class": result["xyz_class"],
                "classification_scope": result["classification_scope"],
            },
            metrics={"quality_score": result["quality_score"]},
        )
        return result

    if granularity is None:
        granularity = (
            service.official_classification_granularity()
            if location is None
            else select_granularity(sku_tx)
        )

    with service.event_logger.span(
        "classification.sku",
        module="classification",
        entity_type="sku",
        entity_id=sku,
        params={"location": location, "granularity": granularity},
    ) as span:
        profile = classify_sku(sku_tx, sku=sku, granularity=granularity)
        profile["classification_scope"] = (
            service.classification_scope() if location is None else "location"
        )
        profile = augment_profile_with_censoring(profile, sku_tx, sku_inv, granularity)
        if profile.get("sb_class") == "inactive":
            span.set_status("inactive")
        span.set_metrics(
            quality_score=profile.get("quality_score"),
            quality_score_base=profile.get("quality_score_base"),
            censored_pct=profile.get("censored_pct"),
            censored_demand_pct=profile.get("censored_demand_pct"),
        )
        span.set_result(
            sb_class=profile.get("sb_class"),
            abc_class=profile.get("abc_class"),
            xyz_class=profile.get("xyz_class"),
            abc_xyz=profile.get("abc_xyz"),
            is_seasonal=profile.get("is_seasonal"),
            has_trend=profile.get("has_trend"),
            classification_scope=profile.get("classification_scope"),
        )
        return profile


# ---------------------------------------------------------------------------
# run_catalog_classification_full
# ---------------------------------------------------------------------------

def run_catalog_classification_full(
    service: "PlanningService",
    granularity: str,
) -> pd.DataFrame:
    """Recalcula la clasificación completa del catálogo sin usar el store.

    Ejecuta ``classify_all_skus()`` y enriquece cada SKU con métricas de
    censura de demanda. Esta es la ruta de recálculo real — ``classify_catalog``
    en ``services.py`` la llama cuando el store está ausente, stale o cuando
    se fuerza con ``_skip_store=True``.

    Parameters
    ----------
    service : PlanningService
        Servicio con acceso al repositorio y al logger.
    granularity : str
        Granularidad temporal (``"M"``, ``"W"`` o ``"D"``).

    Returns
    -------
    pd.DataFrame
        DataFrame de clasificación completa del catálogo.
    """
    from planning_core.classification.core import classify_all_skus

    transactions = service.repository.load_table("transactions")
    catalog = service.repository.load_table("product_catalog")
    inventory = service.repository.load_table("inventory_snapshot")

    with service.event_logger.span(
        "classification.catalog",
        module="classification",
        entity_type="catalog",
        entity_id="all",
        params={"granularity": granularity},
    ) as span:
        classification_df = classify_all_skus(transactions, catalog, granularity=granularity)
        classification_df["classification_scope"] = service.classification_scope()
        classification_df = augment_catalog_classification_with_censoring(
            classification_df=classification_df,
            transactions=transactions,
            inventory=inventory,
            granularity=granularity,
        )
        span.set_metrics(
            n_skus=int(len(classification_df)),
            seasonal_pct=round(float(classification_df["is_seasonal"].mean()), 4) if not classification_df.empty else 0.0,
            censored_sku_pct=round(float(classification_df["has_censored_demand"].mean()), 4) if not classification_df.empty else 0.0,
        )
        span.set_result(
            granularity=granularity,
            classification_scope=service.classification_scope(),
            sb_distribution=_counts_dict(classification_df["sb_class"]) if "sb_class" in classification_df else {},
            abc_distribution=_counts_dict(classification_df["abc_class"]) if "abc_class" in classification_df else {},
        )
        return classification_df


# ---------------------------------------------------------------------------
# run_classification_summary
# ---------------------------------------------------------------------------

def run_classification_summary(
    service: "PlanningService",
    granularity: str | None = None,
) -> dict:
    """Resumen agregado de la clasificación del catálogo.

    Parameters
    ----------
    service : PlanningService
        Servicio con acceso al repositorio.
    granularity : str or None
        Granularidad. Si None, usa la oficial del manifest.

    Returns
    -------
    dict
        KPIs: total_skus, sb_counts, abc_counts, xyz_counts, lifecycle_counts,
        abc_xyz_matrix, avg_quality_score, seasonal_pct, trend_pct, censored_sku_pct.
    """
    df = service.classify_catalog(granularity=granularity)

    sb_counts = df["sb_class"].value_counts().to_dict()
    abc_counts = df["abc_class"].value_counts().to_dict()
    xyz_counts = df["xyz_class"].value_counts().to_dict()
    lifecycle_counts = df["lifecycle"].value_counts().to_dict() if "lifecycle" in df.columns else {}
    abc_xyz_counts = df["abc_xyz"].value_counts().to_dict() if "abc_xyz" in df.columns else {}

    abc_xyz_matrix: dict[str, dict[str, int]] = {}
    for abc in ["A", "B", "C"]:
        abc_xyz_matrix[abc] = {}
        for xyz in ["X", "Y", "Z"]:
            abc_xyz_matrix[abc][xyz] = int(abc_xyz_counts.get(f"{abc}{xyz}", 0))

    return {
        "total_skus": len(df),
        "granularity": df["granularity"].iloc[0] if not df.empty else None,
        "classification_scope": df["classification_scope"].iloc[0] if not df.empty else service.classification_scope(),
        "sb_counts": sb_counts,
        "abc_counts": abc_counts,
        "xyz_counts": xyz_counts,
        "lifecycle_counts": lifecycle_counts,
        "abc_xyz_matrix": abc_xyz_matrix,
        "avg_quality_score": round(float(df["quality_score"].mean()), 3) if not df.empty else 0.0,
        "seasonal_pct": round(float(df["is_seasonal"].mean()), 3) if not df.empty else 0.0,
        "trend_pct": round(float(df["has_trend"].mean()), 3) if not df.empty else 0.0,
        "censored_sku_pct": round(float(df["has_censored_demand"].mean()), 3) if not df.empty else 0.0,
    }


# ---------------------------------------------------------------------------
# run_catalog_classification
# ---------------------------------------------------------------------------

def run_catalog_classification(
    service: "PlanningService",
    granularity: str | None = None,
    persist: bool = True,
    derived_dir: Path | None = None,
) -> pd.DataFrame:
    """Clasifica el catálogo completo y opcionalmente materializa el artefacto.

    Cuando ``persist=True`` el resultado se guarda en
    ``output/derived/classification_catalog_{granularity}.parquet``.
    En la próxima llamada a ``service.classify_catalog()`` el store fresco
    se retornará instantáneamente sin recalcular.

    Parameters
    ----------
    service : PlanningService
        Servicio con acceso al repositorio.
    granularity : str, optional
        ``"M"``, ``"W"`` o ``"D"``. Si None, usa la oficial del manifest.
    persist : bool
        Si True (default), persiste el resultado en ``output/derived/``.
    derived_dir : Path, optional
        Directorio de destino. Default: ``output/derived/``.

    Returns
    -------
    pd.DataFrame
        DataFrame de clasificación completa (misma salida que ``classify_catalog()``).
    """
    from planning_core.classification.store import ClassificationStore

    if granularity is None:
        granularity = service.official_classification_granularity()

    _output_dir = derived_dir or (Path("output") / "derived")

    with service.event_logger.span(
        "classification.catalog.batch",
        module="classification",
        entity_type="catalog",
        entity_id="all",
        params={"granularity": granularity, "persist": persist},
    ) as span:
        classification_df = run_catalog_classification_full(service, granularity)

        n_skus = int(len(classification_df))
        span.set_metrics(n_skus=n_skus)

        if persist and not classification_df.empty:
            scope = service.classification_scope()
            saved_path = ClassificationStore.save(
                df=classification_df,
                granularity=granularity,
                base_dir=_output_dir,
                classification_scope=scope,
            )
            span.set_result(
                persisted=True,
                path=str(saved_path),
                n_skus=n_skus,
                granularity=granularity,
            )
        else:
            span.set_result(persisted=False, n_skus=n_skus)

        return classification_df


# ---------------------------------------------------------------------------
# catalog_classification_status
# ---------------------------------------------------------------------------

def catalog_classification_status(
    service: "PlanningService",
    granularity: str | None = None,
    derived_dir: Path | None = None,
) -> dict:
    """Retorna el estado del artefacto de clasificación materializado.

    Lee solo el JSON de metadata — no abre el parquet.

    Returns
    -------
    dict
        ``{status, run_date, n_skus, classification_scope, abc_distribution,
           seasonal_pct, avg_quality_score, is_stale}``
        ``status`` puede ser ``"ok"``, ``"stale"`` o ``"missing"``.
    """
    from planning_core.classification.store import ClassificationStore

    if granularity is None:
        granularity = service.official_classification_granularity()

    _output_dir = derived_dir or (Path("output") / "derived")
    store = ClassificationStore.load(_output_dir, granularity)

    if store is None:
        return {"status": "missing", "granularity": granularity}

    meta = store.metadata()
    is_stale = store.is_stale()

    return {
        "status": "stale" if is_stale else "ok",
        "granularity": granularity,
        "run_date": meta.get("run_date"),
        "n_skus": meta.get("n_skus"),
        "classification_scope": meta.get("classification_scope"),
        "abc_distribution": meta.get("abc_distribution"),
        "seasonal_pct": meta.get("seasonal_pct"),
        "avg_quality_score": meta.get("avg_quality_score"),
        "is_stale": is_stale,
    }
