"""Política de nivel de servicio (CSL) por segmento ABC.

Provee la conversión CSL → factor z y los targets diferenciados por segmento,
según la tabla de parámetros de la sección 8.4 del documento de referencia
"Gestión de Inventario Orientada a Decisiones" (Marzo 2026).

Diseño
------
- Sin dependencia de scipy: tabla hardcoded + interpolación lineal numpy.
- Configurable desde manifest bajo la clave ``service_level_policy``.
- ABC es el eje de segmentación de CSL (no ABC×XYZ): la dimensión XYZ afecta
  el *método* de cálculo del safety stock, no el nivel de servicio objetivo.

Funciones públicas
------------------
csl_to_z(csl)
    CSL (0–1) → factor z de la distribución normal estándar.
get_csl_target(abc_class, manifest_config)
    CSL objetivo para una clase ABC dada.
get_z_factor(abc_class, manifest_config)
    Factor z correspondiente al CSL objetivo del segmento.
get_service_level_config(abc_class, manifest_config)
    Objeto ServiceLevelConfig completo (CSL + z + método de SS recomendado).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# ---------------------------------------------------------------------------
# Tabla z (PDF sección 4.2) — puntos ancla + interpolación lineal
# ---------------------------------------------------------------------------

# CSL → z (cuantil de la distribución normal estándar)
# Fuente: PDF "Stock de Seguridad e Incertidumbre", sección 4.2
_Z_TABLE: dict[float, float] = {
    0.85: 1.04,
    0.88: 1.18,
    0.90: 1.28,
    0.92: 1.41,
    0.93: 1.48,
    0.94: 1.55,
    0.95: 1.65,
    0.96: 1.75,
    0.97: 1.88,
    0.98: 2.05,
    0.99: 2.33,
    0.995: 2.58,
}

_CSL_POINTS = np.array(sorted(_Z_TABLE.keys()), dtype=float)
_Z_POINTS = np.array([_Z_TABLE[c] for c in _CSL_POINTS], dtype=float)


# ---------------------------------------------------------------------------
# CSL defaults por clase ABC (midpoints de la tabla 8.4 del PDF)
# ---------------------------------------------------------------------------

CSL_DEFAULTS: dict[str, float] = {
    "A": 0.985,  # midpoint de 97–99.5% → z ≈ 2.17
    "B": 0.945,  # midpoint de 93–96%   → z ≈ 1.60
    "C": 0.885,  # midpoint de 85–92%   → z ≈ 1.20
}

# Método de safety stock recomendado por clase (tabla 8.4):
#   A → "extended"      : fórmula con variabilidad de lead time (σ_LT)
#   B → "standard"      : fórmula clásica z × σ_d × √LT
#   C → "simple_pct_lt" : regla simple (% de la demanda durante lead time)
_SS_METHOD_BY_ABC: dict[str, str] = {
    "A": "extended",
    "B": "extended",       # σ_LT calculado desde purchase history → usarlo siempre
    "C": "simple_pct_lt",
}


# ---------------------------------------------------------------------------
# Dataclass de resultado
# ---------------------------------------------------------------------------

@dataclass
class ServiceLevelConfig:
    """Configuración completa de nivel de servicio para un segmento ABC.

    Attributes
    ----------
    abc_class : str or None
        Clase ABC del SKU (``"A"``, ``"B"``, ``"C"``). None si no clasificado.
    csl_target : float
        Cycle Service Level objetivo (0–1).
    z_factor : float
        Factor z correspondiente al CSL objetivo.
    ss_method : str
        Método de cálculo de safety stock recomendado:
        ``"extended"`` | ``"standard"`` | ``"simple_pct_lt"``.
    """

    abc_class: str | None
    csl_target: float
    z_factor: float
    ss_method: str


# ---------------------------------------------------------------------------
# Funciones públicas
# ---------------------------------------------------------------------------

def csl_to_z(csl: float) -> float:
    """Convierte un CSL (0–1) al factor z de la distribución normal estándar.

    Usa la tabla de puntos del PDF (sección 4.2) con interpolación lineal
    entre puntos conocidos. No requiere scipy.

    Rango válido: [0.85, 0.995]. Valores fuera del rango se clampean al
    extremo más cercano de la tabla (sin lanzar excepción).

    Parameters
    ----------
    csl : float
        Cycle Service Level (ej. 0.95 para 95%).

    Returns
    -------
    float
        Factor z correspondiente.

    Examples
    --------
    >>> round(csl_to_z(0.95), 2)
    1.65
    >>> round(csl_to_z(0.98), 2)
    2.05
    """
    csl_clamped = float(np.clip(csl, _CSL_POINTS[0], _CSL_POINTS[-1]))
    return float(np.interp(csl_clamped, _CSL_POINTS, _Z_POINTS))


def get_csl_target(
    abc_class: str | None,
    manifest_config: dict | None = None,
) -> float:
    """Retorna el CSL objetivo para una clase ABC.

    Prioridad de fuentes:
    1. ``manifest_config[abc_class]["csl_target"]`` (override de negocio)
    2. ``CSL_DEFAULTS[abc_class]`` (valores del PDF sección 8.4)
    3. ``CSL_DEFAULTS["C"]`` si ``abc_class`` es None (fallback conservador)

    Parameters
    ----------
    abc_class : str or None
        ``"A"``, ``"B"``, ``"C"`` o None.
    manifest_config : dict or None
        Contenido de ``manifest["service_level_policy"]``, si existe.

    Returns
    -------
    float
        CSL objetivo (0–1).
    """
    effective_abc = abc_class if abc_class in CSL_DEFAULTS else "C"

    if manifest_config and effective_abc in manifest_config:
        override = manifest_config[effective_abc]
        if isinstance(override, dict) and "csl_target" in override:
            return float(override["csl_target"])

    return CSL_DEFAULTS[effective_abc]


def get_z_factor(
    abc_class: str | None,
    manifest_config: dict | None = None,
) -> float:
    """Retorna el factor z correspondiente al CSL objetivo del segmento.

    Parameters
    ----------
    abc_class : str or None
        ``"A"``, ``"B"``, ``"C"`` o None.
    manifest_config : dict or None
        Contenido de ``manifest["service_level_policy"]``, si existe.

    Returns
    -------
    float
        Factor z.
    """
    csl = get_csl_target(abc_class, manifest_config)
    return csl_to_z(csl)


def get_service_level_config(
    abc_class: str | None,
    manifest_config: dict | None = None,
) -> ServiceLevelConfig:
    """Retorna la configuración completa de nivel de servicio para un segmento.

    Parameters
    ----------
    abc_class : str or None
        ``"A"``, ``"B"``, ``"C"`` o None.
    manifest_config : dict or None
        Contenido de ``manifest["service_level_policy"]``, si existe.

    Returns
    -------
    ServiceLevelConfig
        Objeto con CSL objetivo, factor z y método de SS recomendado.
    """
    csl = get_csl_target(abc_class, manifest_config)
    z = csl_to_z(csl)
    effective_abc = abc_class if abc_class in _SS_METHOD_BY_ABC else "C"
    method = _SS_METHOD_BY_ABC[effective_abc]
    return ServiceLevelConfig(
        abc_class=abc_class,
        csl_target=csl,
        z_factor=z,
        ss_method=method,
    )
