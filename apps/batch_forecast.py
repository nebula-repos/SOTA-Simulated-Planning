"""CLI para ejecutar el batch de forecast sobre el catálogo completo.

Materializa el artefacto en ``output/derived/forecast_catalog_{granularity}.parquet``
para que ``catalog_health_report`` lo consuma automáticamente en el próximo
cálculo de safety stock (Opción C).

Uso típico
----------
::

    # Batch mensual con 4 procesos (recomendado)
    python apps/batch_forecast.py --granularity M --jobs 4

    # Con LightGBM activado
    python apps/batch_forecast.py --granularity M --jobs 4 --lgbm

    # Semanal, secuencial (desarrollo / debug)
    python apps/batch_forecast.py --granularity W

    # Ver estado del artefacto sin recalcular
    python apps/batch_forecast.py --status

Tiempo estimado
---------------
Con ``--jobs 4``: ~800 SKUs en ~2.5 min (mensual sin LGBM).
Con ``--jobs 1``:  ~800 SKUs en ~10 min.

Frecuencia recomendada
----------------------
- Mensual (M): ejecutar una vez al mes (artefacto válido 35 días).
- Semanal (W): ejecutar una vez por semana (artefacto válido 9 días).
- Diario (D): ejecutar diariamente (artefacto válido 2 días).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch de forecast sobre el catálogo completo (Opción C).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--granularity", "-g",
        default="M",
        choices=["M", "W", "D"],
        help="Granularidad temporal del forecast.",
    )
    parser.add_argument(
        "--jobs", "-j",
        type=int,
        default=1,
        help="Número de procesos paralelos. -1 = todos los CPUs.",
    )
    parser.add_argument(
        "--lgbm",
        action="store_true",
        default=False,
        help="Incluir LightGBM en el horse-race (más lento, potencialmente mejor).",
    )
    parser.add_argument(
        "--windows", "-w",
        type=int,
        default=3,
        help="Número de ventanas del backtest expanding-window.",
    )
    parser.add_argument(
        "--horizon", "-H",
        type=int,
        default=3,
        help="Horizonte de forecast en períodos.",
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
    from planning_core.forecasting.evaluation.forecast_store import ForecastStore, DEFAULT_MAX_AGE_DAYS

    output_dir = args.output_dir or (Path("output") / "derived")
    store = ForecastStore.load(output_dir, args.granularity)

    if store is None:
        print(f"[MISSING] No hay artefacto para granularidad={args.granularity} en {output_dir}/")
        print(f"  → Ejecutar: python apps/batch_forecast.py --granularity {args.granularity} --jobs 4")
        sys.exit(0)

    meta = store.metadata()
    is_stale = store.is_stale()
    max_age = DEFAULT_MAX_AGE_DAYS.get(args.granularity, 35)

    status_label = "STALE" if is_stale else "OK"
    print(f"[{status_label}] Artefacto forecast_catalog_{args.granularity}")
    print(f"  run_date:     {meta.get('run_date', 'desconocido')}")
    print(f"  n_skus:       {meta.get('n_skus', '?')}")
    print(f"  n_ok:         {meta.get('n_ok', '?')}")
    print(f"  coverage_pct: {meta.get('coverage_pct', 0):.1%}")
    print(f"  top_model:    {meta.get('top_model', '?')}")
    print(f"  stale_after:  {max_age} días")
    if is_stale:
        print(f"  → Re-ejecutar: python apps/batch_forecast.py --granularity {args.granularity} --jobs 4")


def main() -> None:
    args = _parse_args()

    if args.status:
        _show_status(args)
        return

    # Importar aquí para no penalizar --status y --help
    from planning_core.repository import CanonicalRepository
    from planning_core.services import PlanningService

    print(f"Iniciando batch forecast — granularidad={args.granularity}, jobs={args.jobs}, lgbm={args.lgbm}")
    print()

    svc = PlanningService(CanonicalRepository(), enable_console_log=True)

    result = svc.run_catalog_forecast(
        granularity=args.granularity,
        n_jobs=args.jobs,
        use_lgbm=args.lgbm,
        n_windows=args.windows,
        h=args.horizon,
    )

    output_dir = args.output_dir or (Path("output") / "derived")
    parquet_path = output_dir / f"forecast_catalog_{args.granularity}.parquet"

    print()
    print("=" * 72)
    print(f"✓  Batch completado en {result.elapsed_seconds:.1f}s")
    print(f"   {result.n_ok} ok | {result.n_fallback} fallback | "
          f"{result.n_no_forecast} sin forecast | {result.n_error} errores")
    print(f"   Artefacto: {parquet_path}")
    print()
    print("  El próximo catalog_health_report usará señal forward-looking.")
    print("  Para ver el estado: python apps/batch_forecast.py --status")


if __name__ == "__main__":
    main()
