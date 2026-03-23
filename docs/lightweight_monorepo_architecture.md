# Arquitectura Monorepo Liviana

## Objetivo

Mantener este repositorio como entorno de experimentacion unico, pero separando responsabilidades para que:

- la simulacion siga evolucionando sin contaminar la capa analitica
- la matematica de planning quede reusable y testeable
- exista una visualizadora basica para explorar productos, sucursales y flujos de abastecimiento
- mas adelante sea facil extraer repos separados para `planning-core`, `planning-api` y `frontend`

## Decision

Se adopta una arquitectura monorepo liviana, Python-first, con tres capas:

1. `simulator`
   - genera los CSV canonicos operacionales
   - concentra su propia configuracion en `apps/simulator/config.py`
   - genera desde `apps/simulator/generate_canonical_dataset.py`

2. `planning_core`
   - carga el modelo canonico
   - expone consultas, agregaciones y validaciones reutilizables
   - no depende de Streamlit ni de FastAPI

3. `viz`
   - UI exploratoria basica
   - consume `planning_core`
   - no implementa logica de negocio pesada

La API HTTP queda preparada como capa opcional, tambien apoyada en `planning_core`.

## Estructura propuesta

```text
SOTA-Simulated-Planning/
├── apps/
│   ├── api/
│   │   └── main.py
│   ├── simulator/
│   │   ├── config.py
│   │   ├── generate_canonical_dataset.py
│   │   └── run.py
│   └── viz/
│       └── app.py
├── docs/
│   ├── lightweight_monorepo_architecture.md
│   ├── output_er_model.md
│   └── purchase_data_schema.md
├── planning_core/
│   ├── __init__.py
│   ├── paths.py
│   ├── repository.py
│   ├── services.py
│   └── validation.py
├── pyproject.toml
└── requirements.txt
```

## Responsabilidades por capa

### `simulator`

- genera el modelo canonico en `output/`
- no debe conocer la UI
- concentra los perfiles y parametros de simulacion
- puede seguir creciendo con nuevas reglas operacionales

### `planning_core`

- es la frontera semantica del modelo
- centraliza lecturas de CSV
- define consultas por SKU, sucursal y nodo central
- expone validaciones de conciliacion
- sirve tanto para notebooks como para API o UI

### `viz`

- explora tablas y series
- ofrece navegacion rapida por SKU
- grafica ventas, stock, compras y transferencias
- no recalcula toda la logica del dominio

### `api`

- expone endpoints livianos para consumo externo
- queda como adaptador, no como fuente de verdad de negocio

## Dependencias por capa

### Base

- `numpy`
- `pandas`

### `viz`

- `streamlit`
- `plotly`

### `api`

- `fastapi`
- `uvicorn`

### `dev`

- `pytest`
- `ruff`

## Principios de modularidad

- cada app mantiene su propia configuracion cuando la necesita
- la capa reusable vive en `planning_core`
- la UI solo llama servicios ya consolidados
- la simulacion produce datos canonicos y no depende de agregaciones derivadas
- las validaciones de consistencia viven fuera de la UI
- cualquier futura separacion de repos debe poder hacerse moviendo carpetas, no reescribiendo logica

## Consultas minimas a soportar

`planning_core` debe cubrir al menos:

- listado de SKUs y filtros basicos
- listado de locaciones
- resumen de un SKU
- serie diaria por SKU y locacion
- serie agregada por SKU
- eventos de abastecimiento de un SKU
- resumen de salud y conciliacion del dataset

## Fases de implementacion

### Fase 1

- documentar la arquitectura
- crear `planning_core`
- crear wrapper del simulador
- crear visualizadora basica

### Fase 2

- sumar API HTTP
- extraer contratos mas formales
- agregar tests de servicios y validaciones

### Fase 3

- separar repos si deja de convenir el monorepo
- mover la visualizadora a un frontend dedicado si hace falta una experiencia mas rica

## Decision para este repo

En esta etapa se privilegia velocidad de iteracion sobre sofisticacion de despliegue.

Por eso:

- no se implementa aun un frontend JavaScript
- no se introduce una base de datos transaccional adicional
- no se centraliza configuracion compartida entre apps
- se crea primero una capa central reusable y una visualizacion basica

## Paso a paso ejecutable

1. Generar o regenerar `output/` con `python3 -m apps.simulator.generate_canonical_dataset`.
2. Cargar tablas canonicas desde `planning_core`.
3. Explorar el dataset desde `apps/viz/app.py`.
4. Exponer endpoints basicos desde `apps/api/main.py` cuando se necesiten integraciones.

## Resultado esperado de este primer corte

- mismo repo
- capas separadas
- dependencias desacopladas por extras
- visualizacion suficiente para inspeccion de datos y trabajo matematico
- camino claro para extraer componentes a repos futuros
