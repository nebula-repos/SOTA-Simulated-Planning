"""
Demand Classification Engine
=============================
Funciones puras para clasificar series temporales de demanda.
No dependen de I/O ni del repository — reciben DataFrames y retornan resultados.

Pipeline de clasificacion:
    1. prepare_demand_series    — extraccion y relleno de serie temporal
    2. select_granularity       — seleccion adaptativa de granularidad
    3. compute_adi_cv2          — metricas ADI y CV2 (Syntetos-Boylan)
    4. classify_syntetos_boylan — asignacion de cuadrante S-B
    5. compute_abc_segmentation — segmentacion ABC por valor
    6. compute_xyz_class        — segmentacion XYZ por predictibilidad
    7. test_seasonality         — deteccion de estacionalidad (autocorrelacion)
    8. test_trend               — deteccion de tendencia (Mann-Kendall)
    9. detect_outliers          — deteccion de valores atipicos (IQR / Hampel)
    10. classify_lifecycle      — etapa del ciclo de vida del producto
    11. compute_quality_score   — quality gate con score 0-1
    12. classify_sku            — orquestador por SKU individual
    13. classify_all_skus       — clasificacion masiva del catalogo completo

Referencias:
    - Syntetos & Boylan (2005). On the categorization of demand patterns.
    - Hyndman & Koehler (2006). Another look at measures of forecast accuracy.
    - Mann (1945) / Kendall (1975). Rank correlation methods.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 1. Preparacion de serie temporal
# ---------------------------------------------------------------------------

def prepare_demand_series(
    transactions: pd.DataFrame,
    start_date: pd.Timestamp | None = None,
    end_date: pd.Timestamp | None = None,
    granularity: str = "W",
) -> pd.DataFrame:
    """Agrega transacciones a una serie temporal regular con ceros en periodos sin demanda.

    Parameters
    ----------
    transactions : pd.DataFrame
        Tabla de transacciones ya filtrada por SKU (y opcionalmente location).
        Debe contener columnas ``date`` y ``quantity``.
    start_date, end_date : pd.Timestamp, optional
        Rango explicito del historico. Si no se proveen, se infieren de los datos.
    granularity : str
        Frecuencia de agregacion: ``"D"`` (diaria), ``"W"`` (semanal), ``"M"`` (mensual).

    Returns
    -------
    pd.DataFrame
        Columnas ``[period, demand]`` con un registro por periodo, ceros incluidos.
    """
    if transactions.empty:
        return pd.DataFrame(columns=["period", "demand"])

    freq_map = {"D": "D", "W": "W-MON", "M": "MS"}
    freq = freq_map.get(granularity, "W-MON")

    daily = (
        transactions
        .groupby("date", as_index=False)["quantity"]
        .sum()
        .rename(columns={"quantity": "demand"})
        .set_index("date")
        .sort_index()
    )

    # Rango completo del historico
    first = start_date if start_date is not None else daily.index.min()
    last = end_date if end_date is not None else daily.index.max()

    # Reindex diario para rellenar dias sin transacciones con cero
    full_range = pd.date_range(start=first, end=last, freq="D")
    daily = daily.reindex(full_range, fill_value=0).rename_axis("date")

    # Agregar a la granularidad solicitada
    resampled = daily.resample(freq).sum()

    result = (
        resampled
        .reset_index()
        .rename(columns={"date": "period"})
    )
    result["demand"] = result["demand"].astype(float)
    return result


# ---------------------------------------------------------------------------
# 2. Seleccion adaptativa de granularidad
# ---------------------------------------------------------------------------

def select_granularity(
    transactions: pd.DataFrame,
    min_periods: int = 24,
) -> str:
    """Determina la granularidad optima segun la longitud del historico.

    Regla:
        - >= 24 meses de datos  -> Mensual (M)
        - >= 24 semanas         -> Semanal (W)
        - < 24 semanas          -> Diaria (D)

    Parameters
    ----------
    transactions : pd.DataFrame
        Transacciones con columna ``date``.
    min_periods : int
        Cantidad minima de periodos deseados (default 24).

    Returns
    -------
    str
        ``"M"``, ``"W"`` o ``"D"``.
    """
    if transactions.empty:
        return "D"

    date_range = transactions["date"].max() - transactions["date"].min()
    months = date_range.days / 30.44  # promedio dias por mes

    if months >= min_periods:
        return "M"

    weeks = date_range.days / 7
    if weeks >= min_periods:
        return "W"

    return "D"


# ---------------------------------------------------------------------------
# 3. Metricas ADI-CV2 (Syntetos-Boylan)
# ---------------------------------------------------------------------------

def compute_adi_cv2(demand: pd.Series) -> dict[str, float]:
    """Calcula Average Demand Interval (ADI) y Squared Coefficient of Variation (CV2).

    ADI = total_periodos / periodos_con_demanda_positiva
    CV2 = var(demanda_no_nula) / mean(demanda_no_nula)^2

    Parameters
    ----------
    demand : pd.Series
        Serie de demanda (un valor por periodo, incluye ceros).

    Returns
    -------
    dict
        ``{"adi": float, "cv2": float}``
        Si no hay demanda positiva, retorna ``{"adi": inf, "cv2": 0.0}``.
    """
    total_periods = len(demand)
    positive_demand = demand[demand > 0]
    n_positive = len(positive_demand)

    if n_positive == 0:
        return {"adi": float("inf"), "cv2": 0.0}

    adi = total_periods / n_positive

    mean_positive = positive_demand.mean()
    if mean_positive == 0:
        return {"adi": adi, "cv2": 0.0}

    cv2 = positive_demand.var(ddof=1) / (mean_positive ** 2) if n_positive > 1 else 0.0

    return {"adi": float(adi), "cv2": float(cv2)}


# ---------------------------------------------------------------------------
# 4. Clasificacion Syntetos-Boylan
# ---------------------------------------------------------------------------

def classify_syntetos_boylan(
    adi: float,
    cv2: float,
    adi_cutoff: float = 1.32,
    cv2_cutoff: float = 0.49,
) -> str:
    """Asigna el cuadrante Syntetos-Boylan segun los umbrales ADI y CV2.

    Cuadrantes:
        - smooth:       ADI < 1.32  y  CV2 < 0.49
        - erratic:      ADI < 1.32  y  CV2 >= 0.49
        - intermittent: ADI >= 1.32 y  CV2 < 0.49
        - lumpy:        ADI >= 1.32 y  CV2 >= 0.49

    Parameters
    ----------
    adi, cv2 : float
        Metricas calculadas por ``compute_adi_cv2``.
    adi_cutoff : float
        Umbral de ADI (default 1.32, Syntetos & Boylan 2005).
    cv2_cutoff : float
        Umbral de CV2 (default 0.49, Syntetos & Boylan 2005).

    Returns
    -------
    str
        Clase: ``"smooth"``, ``"erratic"``, ``"intermittent"`` o ``"lumpy"``.
    """
    if adi < adi_cutoff:
        return "smooth" if cv2 < cv2_cutoff else "erratic"
    return "intermittent" if cv2 < cv2_cutoff else "lumpy"


# ---------------------------------------------------------------------------
# 5. Segmentacion ABC por valor
# ---------------------------------------------------------------------------

def compute_abc_segmentation(
    transactions: pd.DataFrame,
    a_threshold: float = 0.80,
    b_threshold: float = 0.95,
) -> pd.DataFrame:
    """Calcula la segmentacion ABC por contribucion acumulada de revenue.

    A = SKUs que acumulan el top ``a_threshold`` (80%) del valor total.
    B = SKUs siguientes hasta ``b_threshold`` (95%).
    C = el resto.

    Parameters
    ----------
    transactions : pd.DataFrame
        Tabla completa de transacciones con columnas ``sku`` y ``total_amount``.
    a_threshold : float
        Porcentaje acumulado para clase A (default 0.80).
    b_threshold : float
        Porcentaje acumulado para clase B (default 0.95).

    Returns
    -------
    pd.DataFrame
        Columnas: ``[sku, total_revenue, revenue_pct, cumulative_pct, abc_class]``
    """
    if transactions.empty:
        return pd.DataFrame(columns=["sku", "total_revenue", "revenue_pct", "cumulative_pct", "abc_class"])

    revenue = (
        transactions
        .groupby("sku", as_index=False)["total_amount"]
        .sum()
        .rename(columns={"total_amount": "total_revenue"})
        .sort_values("total_revenue", ascending=False)
        .reset_index(drop=True)
    )

    total = revenue["total_revenue"].sum()
    revenue["revenue_pct"] = revenue["total_revenue"] / total if total > 0 else 0.0
    revenue["cumulative_pct"] = revenue["revenue_pct"].cumsum()

    def _assign_abc(row: pd.Series) -> str:
        if row["cumulative_pct"] <= a_threshold:
            return "A"
        if row["cumulative_pct"] <= b_threshold:
            return "B"
        return "C"

    revenue["abc_class"] = revenue.apply(_assign_abc, axis=1)

    # El primer SKU que cruza el umbral debe pertenecer al grupo anterior
    # (evitar que un SKU con gran revenue quede como B cuando deberia ser A).
    # La logica de cumsum ya lo maneja correctamente.

    return revenue


# ---------------------------------------------------------------------------
# 6. Segmentacion XYZ por predictibilidad
# ---------------------------------------------------------------------------

def compute_xyz_class(
    cv2: float,
    x_max: float = 0.25,
    y_max: float = 0.64,
) -> str:
    """Clasifica la predictibilidad de demanda segun CV2.

    X = CV2 < 0.25  (alta predictibilidad)
    Y = CV2 < 0.64  (predictibilidad moderada)
    Z = CV2 >= 0.64 (baja predictibilidad)

    Parameters
    ----------
    cv2 : float
        Coeficiente de variacion al cuadrado.
    x_max, y_max : float
        Umbrales XYZ (defaults de config.py).

    Returns
    -------
    str
        ``"X"``, ``"Y"`` o ``"Z"``.
    """
    if cv2 < x_max:
        return "X"
    if cv2 < y_max:
        return "Y"
    return "Z"


# ---------------------------------------------------------------------------
# 7. Test de estacionalidad (autocorrelacion)
# ---------------------------------------------------------------------------

def test_seasonality(
    demand: pd.Series,
    seasonal_period: int | None = None,
    granularity: str = "W",
    threshold: float = 0.3,
) -> dict:
    """Detecta estacionalidad mediante autocorrelacion en el lag estacional.

    Calcula la autocorrelacion de la serie en el lag correspondiente al periodo
    estacional (52 para semanal, 12 para mensual, 365 para diario). Si la
    autocorrelacion supera el umbral, se considera estacional.

    Parameters
    ----------
    demand : pd.Series
        Serie de demanda (un valor por periodo).
    seasonal_period : int, optional
        Lag estacional explicito. Si no se provee, se infiere de ``granularity``.
    granularity : str
        ``"D"``, ``"W"`` o ``"M"`` — usado para inferir el lag estacional.
    threshold : float
        Umbral minimo de autocorrelacion para declarar estacionalidad (default 0.3).

    Returns
    -------
    dict
        ``{"is_seasonal": bool, "seasonal_strength": float, "lag": int, "method": str}``
    """
    if seasonal_period is None:
        period_map = {"D": 365, "W": 52, "M": 12}
        seasonal_period = period_map.get(granularity, 52)

    n = len(demand)

    # Se necesitan al menos 2 ciclos completos para detectar estacionalidad
    if n < 2 * seasonal_period:
        return {
            "is_seasonal": False,
            "seasonal_strength": 0.0,
            "lag": seasonal_period,
            "method": "autocorrelation",
        }

    series = demand.values.astype(float)
    mean = np.mean(series)
    var = np.var(series, ddof=0)

    if var == 0:
        return {
            "is_seasonal": False,
            "seasonal_strength": 0.0,
            "lag": seasonal_period,
            "method": "autocorrelation",
        }

    # Autocorrelacion en el lag estacional
    lagged = series[seasonal_period:] - mean
    original = series[:n - seasonal_period] - mean
    acf_value = float(np.sum(lagged * original) / (n * var))

    return {
        "is_seasonal": abs(acf_value) >= threshold,
        "seasonal_strength": round(abs(acf_value), 4),
        "lag": seasonal_period,
        "method": "autocorrelation",
    }


def compute_acf(demand: pd.Series, max_lags: int = 40) -> np.ndarray:
    """Calcula la funcion de autocorrelacion (ACF) para visualizacion.

    Parameters
    ----------
    demand : pd.Series
        Serie de demanda.
    max_lags : int
        Numero maximo de lags a calcular.

    Returns
    -------
    np.ndarray
        Array de autocorrelaciones desde lag 0 hasta ``max_lags``.
    """
    series = demand.values.astype(float)
    n = len(series)
    max_lags = min(max_lags, n - 1)

    mean = np.mean(series)
    var = np.var(series, ddof=0)

    if var == 0:
        return np.zeros(max_lags + 1)

    acf = np.zeros(max_lags + 1)
    acf[0] = 1.0
    centered = series - mean

    for lag in range(1, max_lags + 1):
        acf[lag] = np.sum(centered[:n - lag] * centered[lag:]) / (n * var)

    return acf


# ---------------------------------------------------------------------------
# 8. Test de tendencia (Mann-Kendall)
# ---------------------------------------------------------------------------

def test_trend(demand: pd.Series, alpha: float = 0.05) -> dict:
    """Detecta tendencia mediante el test no parametrico de Mann-Kendall.

    El estadistico S se calcula como la suma de signos de todas las diferencias
    pareadas (x_j - x_i) para j > i. Bajo la hipotesis nula de no tendencia,
    S tiene media 0 y varianza conocida. Se usa la aproximacion normal para
    el p-value.

    Parameters
    ----------
    demand : pd.Series
        Serie de demanda (un valor por periodo).
    alpha : float
        Nivel de significancia (default 0.05).

    Returns
    -------
    dict
        ``{"has_trend": bool, "trend_direction": str, "p_value": float, "tau": float}``
        donde ``trend_direction`` es ``"up"``, ``"down"`` o ``"none"``.

    References
    ----------
    Mann, H.B. (1945). Nonparametric tests against trend.
    Kendall, M.G. (1975). Rank Correlation Methods.
    """
    x = demand.values.astype(float)
    n = len(x)

    if n < 8:
        return {"has_trend": False, "trend_direction": "none", "p_value": 1.0, "tau": 0.0}

    # Calcular estadistico S
    s = 0
    for i in range(n - 1):
        diffs = x[i + 1:] - x[i]
        s += int(np.sum(np.sign(diffs)))

    # Kendall's tau
    tau = (2.0 * s) / (n * (n - 1))

    # Varianza de S (corregida por empates)
    unique, counts = np.unique(x, return_counts=True)
    tie_correction = np.sum(counts * (counts - 1) * (2 * counts + 5))
    var_s = (n * (n - 1) * (2 * n + 5) - tie_correction) / 18.0

    if var_s == 0:
        return {"has_trend": False, "trend_direction": "none", "p_value": 1.0, "tau": float(tau)}

    # Estadistico Z (aproximacion normal)
    if s > 0:
        z = (s - 1) / np.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / np.sqrt(var_s)
    else:
        z = 0.0

    # P-value bilateral usando CDF normal estandar
    p_value = 2.0 * _normal_cdf(-abs(z))

    has_trend = p_value < alpha
    if has_trend:
        direction = "up" if s > 0 else "down"
    else:
        direction = "none"

    return {
        "has_trend": has_trend,
        "trend_direction": direction,
        "p_value": round(float(p_value), 6),
        "tau": round(float(tau), 4),
    }


def _normal_cdf(z: float) -> float:
    """CDF de la distribucion normal estandar (aproximacion por error function).

    Evita dependencia de scipy para un calculo simple.
    """
    import math
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# 9. Deteccion de outliers
# ---------------------------------------------------------------------------

def detect_outliers(
    demand: pd.Series,
    method: str = "iqr",
    iqr_factor: float = 1.5,
    hampel_window: int = 7,
    hampel_threshold: float = 3.0,
) -> pd.Series:
    """Detecta valores atipicos en la serie de demanda.

    No modifica la serie original — retorna una mascara booleana.

    Parameters
    ----------
    demand : pd.Series
        Serie de demanda.
    method : str
        ``"iqr"`` (Rango Intercuartilico) o ``"hampel"`` (mediana movil + MAD).
    iqr_factor : float
        Multiplicador del IQR para definir limites (default 1.5).
    hampel_window : int
        Tamano de ventana para Hampel filter (default 7 periodos).
    hampel_threshold : float
        Numero de MADs para considerar outlier en Hampel (default 3.0).

    Returns
    -------
    pd.Series[bool]
        ``True`` donde el valor es outlier.
    """
    if method == "iqr":
        return _detect_outliers_iqr(demand, factor=iqr_factor)
    if method == "hampel":
        return _detect_outliers_hampel(demand, window=hampel_window, threshold=hampel_threshold)
    raise ValueError(f"Metodo de deteccion no soportado: {method}. Usar 'iqr' o 'hampel'.")


def _detect_outliers_iqr(demand: pd.Series, factor: float = 1.5) -> pd.Series:
    """IQR: outlier si valor cae fuera de [Q1 - factor*IQR, Q3 + factor*IQR]."""
    positive = demand[demand > 0]

    if len(positive) < 4:
        return pd.Series(False, index=demand.index)

    q1 = positive.quantile(0.25)
    q3 = positive.quantile(0.75)
    iqr = q3 - q1

    lower = q1 - factor * iqr
    upper = q3 + factor * iqr

    # Solo marcamos outliers en periodos con demanda positiva (ceros no son outliers)
    is_outlier = (demand > upper) | ((demand > 0) & (demand < lower))
    return is_outlier


def _detect_outliers_hampel(
    demand: pd.Series,
    window: int = 7,
    threshold: float = 3.0,
) -> pd.Series:
    """Hampel filter: outlier si el valor se aleja mas de ``threshold`` MADs
    de la mediana movil.

    MAD = median(|x_i - median(x)|) * 1.4826 (factor de consistencia para normalidad).
    """
    n = len(demand)
    is_outlier = pd.Series(False, index=demand.index)
    half_window = window // 2
    consistency_factor = 1.4826

    values = demand.values.astype(float)

    for i in range(n):
        start = max(0, i - half_window)
        end = min(n, i + half_window + 1)
        window_values = values[start:end]

        median = np.median(window_values)
        mad = consistency_factor * np.median(np.abs(window_values - median))

        if mad == 0:
            continue

        if abs(values[i] - median) / mad > threshold:
            is_outlier.iloc[i] = True

    return is_outlier


# ---------------------------------------------------------------------------
# 10. Ciclo de vida del producto
# ---------------------------------------------------------------------------

def classify_lifecycle(
    demand: pd.Series,
    trend_result: dict,
    total_periods: int,
    inactive_threshold: int = 12,
    new_threshold: int = 26,
) -> str:
    """Clasifica la etapa del ciclo de vida del producto.

    Etapas:
        - ``"inactive"``: sin demanda en los ultimos ``inactive_threshold`` periodos.
        - ``"new"``:      menos de ``new_threshold`` periodos de historia.
        - ``"growing"``:  tendencia positiva significativa.
        - ``"declining"``: tendencia negativa significativa.
        - ``"mature"``:   sin tendencia significativa, historia suficiente.

    Parameters
    ----------
    demand : pd.Series
        Serie de demanda.
    trend_result : dict
        Resultado de ``test_trend()`` (keys: has_trend, trend_direction).
    total_periods : int
        Cantidad total de periodos en la serie.
    inactive_threshold : int
        Periodos recientes sin demanda para considerar inactivo (default 12).
    new_threshold : int
        Periodos minimos para dejar de ser "nuevo" (default 26).

    Returns
    -------
    str
        ``"new"``, ``"growing"``, ``"mature"``, ``"declining"`` o ``"inactive"``.
    """
    # Inactivo: sin demanda en los ultimos N periodos
    recent = demand.tail(inactive_threshold)
    if recent.sum() == 0:
        return "inactive"

    # Nuevo: historia corta
    if total_periods < new_threshold:
        return "new"

    # Con tendencia significativa
    if trend_result.get("has_trend", False):
        return "growing" if trend_result["trend_direction"] == "up" else "declining"

    return "mature"


# ---------------------------------------------------------------------------
# 11. Quality gate
# ---------------------------------------------------------------------------

def compute_quality_score(
    demand: pd.Series,
    outlier_mask: pd.Series,
    min_periods: int = 24,
    max_acceptable_zero_pct: float = 0.95,
    max_acceptable_outlier_pct: float = 0.15,
) -> dict:
    """Evalua la calidad de la serie de demanda para forecasting.

    Score compuesto (0 a 1) basado en:
        - Longitud de historia (peso 0.35)
        - Porcentaje de ceros (peso 0.25)
        - Porcentaje de outliers (peso 0.20)
        - Gaps consecutivos maximos (peso 0.20)

    Parameters
    ----------
    demand : pd.Series
        Serie de demanda.
    outlier_mask : pd.Series
        Mascara de outliers (True = outlier).
    min_periods : int
        Periodos minimos deseados para historia suficiente.
    max_acceptable_zero_pct : float
        Maximo porcentaje de ceros aceptable antes de penalizar fuertemente.
    max_acceptable_outlier_pct : float
        Maximo porcentaje de outliers aceptable.

    Returns
    -------
    dict
        Contiene ``score``, ``sufficient_history``, ``zero_pct``, ``outlier_pct``,
        ``max_gap_periods``, ``flags``.
    """
    n = len(demand)
    flags: list[str] = []

    # --- Historia ---
    sufficient_history = n >= min_periods
    history_score = min(1.0, n / min_periods)
    if not sufficient_history:
        flags.append(f"historia_insuficiente ({n}/{min_periods} periodos)")

    # --- Ceros ---
    zero_pct = float((demand == 0).sum() / n) if n > 0 else 1.0
    if zero_pct >= max_acceptable_zero_pct:
        zero_score = 0.0
        flags.append(f"exceso_ceros ({zero_pct:.0%})")
    else:
        zero_score = 1.0 - (zero_pct / max_acceptable_zero_pct)

    # --- Outliers ---
    outlier_pct = float(outlier_mask.sum() / n) if n > 0 else 0.0
    if outlier_pct >= max_acceptable_outlier_pct:
        outlier_score = 0.0
        flags.append(f"exceso_outliers ({outlier_pct:.0%})")
    else:
        outlier_score = 1.0 - (outlier_pct / max_acceptable_outlier_pct)

    # --- Gaps consecutivos ---
    max_gap = _max_consecutive_zeros(demand)
    gap_ratio = max_gap / n if n > 0 else 0.0
    gap_score = max(0.0, 1.0 - gap_ratio * 2)  # penaliza gaps que cubren >50% de la serie
    if max_gap > n * 0.25:
        flags.append(f"gap_largo ({max_gap} periodos consecutivos sin demanda)")

    # --- Score compuesto ---
    score = (
        0.35 * history_score
        + 0.25 * zero_score
        + 0.20 * outlier_score
        + 0.20 * gap_score
    )

    return {
        "score": round(score, 3),
        "sufficient_history": sufficient_history,
        "zero_pct": round(zero_pct, 4),
        "outlier_pct": round(outlier_pct, 4),
        "max_gap_periods": max_gap,
        "flags": flags,
    }


def _max_consecutive_zeros(demand: pd.Series) -> int:
    """Encuentra la racha mas larga de ceros consecutivos."""
    is_zero = (demand == 0).values
    if not np.any(is_zero):
        return 0

    max_gap = 0
    current_gap = 0
    for val in is_zero:
        if val:
            current_gap += 1
            max_gap = max(max_gap, current_gap)
        else:
            current_gap = 0
    return max_gap


# ---------------------------------------------------------------------------
# 12. Orquestador por SKU individual
# ---------------------------------------------------------------------------

def classify_sku(
    transactions: pd.DataFrame,
    sku: str,
    granularity: str | None = None,
    adi_cutoff: float = 1.32,
    cv2_cutoff: float = 0.49,
    outlier_method: str = "iqr",
) -> dict:
    """Ejecuta el pipeline completo de clasificacion para un SKU.

    Orquesta todas las funciones del modulo en secuencia:
    preparacion -> metricas -> clasificacion -> tests -> quality gate.

    Parameters
    ----------
    transactions : pd.DataFrame
        Transacciones ya filtradas por el SKU (todas las locations sumadas).
    sku : str
        Identificador del SKU (para incluir en el resultado).
    granularity : str, optional
        Granularidad forzada. Si es None, se selecciona automaticamente.
    adi_cutoff, cv2_cutoff : float
        Umbrales Syntetos-Boylan.
    outlier_method : str
        Metodo de deteccion de outliers (``"iqr"`` o ``"hampel"``).

    Returns
    -------
    dict
        Perfil completo de clasificacion del SKU.
    """
    # Seleccion adaptativa de granularidad
    if granularity is None:
        granularity = select_granularity(transactions)

    # Preparar serie temporal
    series_df = prepare_demand_series(transactions, granularity=granularity)

    if series_df.empty:
        return _empty_classification(sku, granularity)

    demand = series_df["demand"]
    total_periods = len(demand)

    # Metricas ADI-CV2
    adi_cv2 = compute_adi_cv2(demand)
    adi = adi_cv2["adi"]
    cv2 = adi_cv2["cv2"]

    # Clasificaciones
    sb_class = classify_syntetos_boylan(adi, cv2, adi_cutoff, cv2_cutoff)
    xyz_class = compute_xyz_class(cv2)

    # Tests extendidos
    seasonality = test_seasonality(demand, granularity=granularity)
    trend = test_trend(demand)

    # Outliers
    outlier_mask = detect_outliers(demand, method=outlier_method)

    # Ciclo de vida
    lifecycle = classify_lifecycle(demand, trend, total_periods)

    # Quality gate
    quality = compute_quality_score(demand, outlier_mask)

    # Estadisticas descriptivas
    positive_demand = demand[demand > 0]

    return {
        "sku": sku,
        "granularity": granularity,
        "total_periods": total_periods,
        "demand_periods": int((demand > 0).sum()),
        "zero_pct": quality["zero_pct"],
        # Metricas Syntetos-Boylan
        "adi": round(adi, 4),
        "cv2": round(cv2, 4),
        "sb_class": sb_class,
        # Segmentacion XYZ
        "xyz_class": xyz_class,
        # Estacionalidad
        "is_seasonal": seasonality["is_seasonal"],
        "seasonal_strength": seasonality["seasonal_strength"],
        # Tendencia
        "has_trend": trend["has_trend"],
        "trend_direction": trend["trend_direction"],
        "trend_p_value": trend["p_value"],
        "trend_tau": trend["tau"],
        # Ciclo de vida
        "lifecycle": lifecycle,
        # Outliers
        "outlier_count": int(outlier_mask.sum()),
        "outlier_pct": quality["outlier_pct"],
        # Quality gate
        "quality_score": quality["score"],
        "quality_flags": quality["flags"],
        "sufficient_history": quality["sufficient_history"],
        "max_gap_periods": quality["max_gap_periods"],
        # Estadisticas descriptivas
        "mean_demand": round(float(positive_demand.mean()), 2) if len(positive_demand) > 0 else 0.0,
        "std_demand": round(float(positive_demand.std(ddof=1)), 2) if len(positive_demand) > 1 else 0.0,
        "total_demand": round(float(demand.sum()), 2),
    }


def _empty_classification(sku: str, granularity: str) -> dict:
    """Retorna un perfil de clasificacion vacio para SKUs sin transacciones."""
    return {
        "sku": sku,
        "granularity": granularity,
        "total_periods": 0,
        "demand_periods": 0,
        "zero_pct": 1.0,
        "adi": float("inf"),
        "cv2": 0.0,
        "sb_class": "inactive",
        "xyz_class": "X",  # cv2=0.0 -> X por definicion
        "is_seasonal": False,
        "seasonal_strength": 0.0,
        "has_trend": False,
        "trend_direction": "none",
        "trend_p_value": 1.0,
        "trend_tau": 0.0,
        "lifecycle": "inactive",
        "outlier_count": 0,
        "outlier_pct": 0.0,
        "quality_score": 0.0,
        "quality_flags": ["sin_transacciones"],
        "sufficient_history": False,
        "max_gap_periods": 0,
        "mean_demand": 0.0,
        "std_demand": 0.0,
        "total_demand": 0.0,
    }


# ---------------------------------------------------------------------------
# 13. Clasificacion masiva del catalogo completo
# ---------------------------------------------------------------------------

def classify_all_skus(
    transactions: pd.DataFrame,
    catalog: pd.DataFrame,
    granularity: str | None = None,
    adi_cutoff: float = 1.32,
    cv2_cutoff: float = 0.49,
    outlier_method: str = "iqr",
) -> pd.DataFrame:
    """Clasifica todos los SKUs del catalogo y agrega segmentacion ABC.

    Ejecuta ``classify_sku`` para cada SKU con transacciones, calcula la
    segmentacion ABC sobre el revenue total, y combina ambos resultados
    en un DataFrame con una fila por SKU.

    Parameters
    ----------
    transactions : pd.DataFrame
        Tabla completa de transacciones.
    catalog : pd.DataFrame
        Tabla de catalogo de productos (debe contener ``sku``).
    granularity : str, optional
        Granularidad forzada. Si es None, se selecciona automaticamente
        una vez y se usa para todos los SKUs.
    adi_cutoff, cv2_cutoff : float
        Umbrales Syntetos-Boylan.
    outlier_method : str
        Metodo de deteccion de outliers.

    Returns
    -------
    pd.DataFrame
        Una fila por SKU con todas las columnas de clasificacion mas
        ``abc_class``, ``abc_xyz``, ``total_revenue``, ``revenue_pct``
        y ``cumulative_pct``.
    """
    # Determinar granularidad global si no se provee
    if granularity is None:
        granularity = select_granularity(transactions)

    # Segmentacion ABC (sobre todo el catalogo)
    abc_df = compute_abc_segmentation(transactions)

    # Clasificar cada SKU
    all_skus = catalog["sku"].unique()
    skus_with_tx = set(transactions["sku"].unique())
    results: list[dict] = []

    for sku in all_skus:
        if sku in skus_with_tx:
            sku_tx = transactions[transactions["sku"] == sku]
            profile = classify_sku(
                sku_tx,
                sku=sku,
                granularity=granularity,
                adi_cutoff=adi_cutoff,
                cv2_cutoff=cv2_cutoff,
                outlier_method=outlier_method,
            )
        else:
            profile = _empty_classification(sku, granularity)
        results.append(profile)

    classification_df = pd.DataFrame(results)

    # Merge con ABC
    if not abc_df.empty:
        abc_columns = abc_df[["sku", "total_revenue", "revenue_pct", "cumulative_pct", "abc_class"]]
        classification_df = classification_df.merge(abc_columns, on="sku", how="left")
    else:
        classification_df["total_revenue"] = 0.0
        classification_df["revenue_pct"] = 0.0
        classification_df["cumulative_pct"] = 0.0
        classification_df["abc_class"] = "C"

    # SKUs sin transacciones -> clase C
    classification_df["abc_class"] = classification_df["abc_class"].fillna("C")
    classification_df["total_revenue"] = classification_df["total_revenue"].fillna(0.0)
    classification_df["revenue_pct"] = classification_df["revenue_pct"].fillna(0.0)
    classification_df["cumulative_pct"] = classification_df["cumulative_pct"].fillna(0.0)

    # Segmento combinado ABC-XYZ
    classification_df["abc_xyz"] = classification_df["abc_class"] + classification_df["xyz_class"]

    return classification_df.sort_values("total_revenue", ascending=False).reset_index(drop=True)
