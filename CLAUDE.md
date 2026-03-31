# CLAUDE.md — SOTA Simulated Planning

## Comandos esenciales

```bash
# Tests
python3 -m pytest tests/ -q                  # suite completa (~276 tests)
python3 -m pytest tests/test_api.py -q       # solo API (54 tests)
python3 -m pytest tests/ -q -x               # detener al primer fallo

# API
uvicorn apps.api.main:app --reload --port 8000

# UI
streamlit run apps/viz/app.py

# Linting
ruff check planning_core/ apps/ tests/
```

## Arquitectura

```
planning_core/          # lógica de negocio pura (sin UI ni API)
  classification.py     # ADI-CV², ABC-XYZ, selección de granularidad
  preprocessing.py      # outliers, censura de demanda
  validation.py         # basic_health_report (D08: incompleto aún)
  repository.py         # CanonicalRepository — acceso a parquet/CSV en output/
  services.py           # PlanningService — fachada de todos los módulos
  forecasting/          # backtest, horse-race, modelos (AutoETS, MSTL, LGBM, SBA…)
  inventory/            # safety stock, ROP, diagnóstico, CSL por ABC
apps/
  api/main.py           # FastAPI — endpoints REST sobre PlanningService
  viz/app.py            # Streamlit — UI de exploración
tests/                  # pytest; test_api.py usa FastAPI TestClient
output/                 # datos canónicos (parquet). No commitear datos reales.
docs/                   # PDFs de guías, registro de deuda técnica, planes
```

## Convenciones clave

- **Idioma**: código en inglés (variables, funciones, clases); docstrings y comentarios en español.
- **Imports**: `from __future__ import annotations` en todos los módulos nuevos.
- **Serialización API**: usar `_sanitize(obj)` antes de retornar respuestas (convierte NaN/Inf → None).
- **Validación en API**: `_check_granularity()`, `_check_location()`, `_require_sku()` — lanzar 422/404/503, no 500.
- **Granularidades válidas**: `{"M", "W", "D"}` — mensual, semanal, diario.
- **Caché en UI**: `@st.cache_data` en todas las llamadas costosas al servicio.
- **Tests**: no mockear el repositorio en tests de integración (ver D09 en registro de deuda).

## Modelo de datos canónico

Tablas en `output/`: `product_catalog`, `inventory_snapshot`, `transactions`, `purchase_order_lines`, `purchase_receipts`, `internal_transfers`.

El manifiesto (`output/manifest.json`) define: `profile`, `currency`, `central_location`, `classification_scope`, `classification_default_granularity`, parámetros de inventario por ABC class.

## Fases del roadmap

| Fase | Módulo | Estado |
|---|---|---|
| 0 | Datos canónicos + repositorio | ✅ |
| 1 | Clasificación de demanda (ADI-CV², ABC-XYZ) | ✅ |
| 2 | Forecasting (backtest, horse-race, modelos) | ✅ |
| 3 | Inventario (SS, ROP, diagnóstico) | ✅ |
| 4 | Health Status Report (alertas catálogo) | Pendiente |
| 5 | Motor de Decisión de Reposición | ✅ |

## Deuda técnica activa

Ver [docs/technical_debt_register.md](docs/technical_debt_register.md). Items de mayor prioridad:

- **D08** (Alta): `validation.py` incompleto — faltan FK checks, reconciliación de inventario
- **D09** (Media): cobertura 0 en `classification.py`, `preprocessing.py`, `validation.py`
- **D38** (Media): `fill_rate` en backtest debería reportar mínimo además del promedio

## Notas operacionales

- El `CanonicalRepository` carga datos desde `output/` en cada llamada (sin caché interno). La UI mitiga esto con `@st.cache_data`. La API (D14) no tiene caché aún.
- `classify_catalog()` es costoso en la API — se recalcula en cada request.
- El forecast usa un horse-race de modelos con backtest expanding-window. El modelo ganador se elige por MASE mínimo.
