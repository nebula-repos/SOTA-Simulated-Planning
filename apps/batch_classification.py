"""CLI para ejecutar la clasificación de demanda sobre el catálogo completo.

Materializa el artefacto en
``output/derived/classification_catalog_{granularity}.parquet`` para que
``classify_catalog()`` lo consuma sin recalcular en cada request (D14).

Uso típico
----------
::

    # Clasificación mensual (recomendado, ~30s)
    python apps/batch_classification.py --granularity M

    # Ver estado del artefacto sin recalcular
    python apps/batch_classification.py --status

    # Granularidad semanal
    python apps/batch_classification.py --granularity W

Frecuencia recomendada
----------------------
- Mensual (M): ejecutar una vez al mes (artefacto válido 35 días).
- Semanal (W): ejecutar una vez por semana (artefacto válido 9 días).
- Diario (D): ejecutar diariamente (artefacto válido 2 días).

La clasificación cambia cuando llegan datos nuevos al repositorio. Con datos
que se actualizan mensualmente, el artefacto mensual es más que suficiente.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch de clasificación de demanda sobre el catálogo completo.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--granularity", "-g",
        default="M",
        choices=["M", "W", "D"],
        help="Granularidad temporal de la clasificación.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        default=False,
        help="Solo mostrar el estado del artefacto sin ejecutar el batch.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directorio de salida para el artefacto. Default: output/derived/",
    )
    return parser.parse_args()


def _show_status(args: argparse.Namespace) -> None:
    from planning_core.classification_store import ClassificationStore, DEFAULT_MAX_AGE_DAYS

    output_dir = args.output_dir or (Path("output") / "derived")
    store = ClassificationStore.load(output_dir, args.granularity)

    if store is None:
        print(f"[MISSING] No hay artefacto de clasificación para granularidad={args.granularity} en {output_dir}/")
        print(f"  → Ejecutar: python apps/batch_classification.py --granularity {args.granularity}")
        sys.exit(0)

    meta = store.metadata()
    is_stale = store.is_stale()
    max_age = DEFAULT_MAX_AGE_DAYS.get(args.granularity, 35)
    abc_dist = meta.get("abc_distribution", {})

    status_label = "STALE" if is_stale else "OK"
    print(f"[{status_label}] Artefacto classification_catalog_{args.granularity}")
    print(f"  run_date:          {meta.get('run_date', 'desconocido')}")
    print(f"  n_skus:            {meta.get('n_skus', '?')}")
    print(f"  scope:             {meta.get('classification_scope', '?')}")
    print(f"  abc_distribution:  A={abc_dist.get('A',0)} B={abc_dist.get('B',0)} C={abc_dist.get('C',0)}")
    print(f"  seasonal_pct:      {meta.get('seasonal_pct', 0):.1%}")
    print(f"  avg_quality_score: {meta.get('avg_quality_score', 0):.3f}")
    print(f"  stale_after:       {max_age} días")
    if is_stale:
        print(f"  → Re-ejecutar: python apps/batch_classification.py --granularity {args.granularity}")


def main() -> None:
    args = _parse_args()

    if args.status:
        _show_status(args)
        return

    from planning_core.repository import CanonicalRepository
    from planning_core.services import PlanningService

    print(f"Iniciando batch clasificación — granularidad={args.granularity}")
    print()

    svc = PlanningService(CanonicalRepository(), enable_console_log=True)

    output_dir = args.output_dir
    if output_dir:
        import planning_core.pipelines.classification as _cls_pipeline
        from pathlib import Path
        df = _cls_pipeline.run_catalog_classification(
            svc, granularity=args.granularity, persist=True, derived_dir=Path(output_dir)
        )
    else:
        df = svc.run_catalog_classification(granularity=args.granularity, persist=True)

    n_skus = len(df)
    abc_counts = df["abc_class"].value_counts().to_dict() if "abc_class" in df.columns else {}
    output_dir_final = output_dir or (Path("output") / "derived")
    parquet_path = output_dir_final / f"classification_catalog_{args.granularity}.parquet"

    print()
    print("=" * 72)
    print(f"✓  Clasificación completada — {n_skus} SKUs")
    print(f"   A={abc_counts.get('A', 0)} B={abc_counts.get('B', 0)} C={abc_counts.get('C', 0)}")
    print(f"   Artefacto: {parquet_path}")
    print()
    print("  La próxima llamada a classify_catalog() usará el artefacto persistido.")
    print("  Para ver el estado: python apps/batch_classification.py --status")


if __name__ == "__main__":
    main()
