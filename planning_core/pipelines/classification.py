"""Pipeline de clasificación de demanda con soporte de materialización.

Contiene la lógica de orquestación para clasificar el catálogo completo y
opcionalmente persistir el resultado como ``ClassificationStore`` en
``output/derived/``.

Funciones públicas
------------------
run_catalog_classification(service, granularity, persist, derived_dir)
    Clasifica el catálogo completo. Si ``persist=True``, guarda el artefacto
    en disco con escritura atómica.

catalog_classification_status(service, granularity, derived_dir)
    Lectura rápida del JSON de metadata — no abre el parquet.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from planning_core.services import PlanningService


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
    from planning_core.classification_store import ClassificationStore

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
        # Ejecutar clasificación completa (con censura)
        classification_df = service.classify_catalog(
            granularity=granularity,
            _skip_store=True,  # Forzar recálculo completo, no usar store existente
        )

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
    from planning_core.classification_store import ClassificationStore

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
