"""
Preprocessing de Series de Demanda para Forecasting
======================================================
Funciones de limpieza y calidad de datos que se ejecutan ANTES del entrenamiento
de modelos de forecast. Complementan la clasificacion de la Fase 1 produciendo
series listas para modelado.

Modulo actual:
    1. mark_censored_demand  — deteccion de periodos con demanda censurada (lost sales)

Contexto de uso:
    La secuencia correcta antes de entrenar cualquier modelo es:
        1. prepare_demand_series()   → serie con ceros rellenados
        2. detect_outliers()         → mascara de valores atipicos
        3. treat_outliers()          → serie limpia (clean_series)
        4. mark_censored_demand()    → mascara de periodos censurados (censored_mask)
        5. [entrenar modelo sobre clean_series excluyendo periodos censored]

Referencias:
    - Hyndman & Athanasopoulos (2021). Forecasting: Principles and Practice, Cap. 12.
    - Gutierrez et al. (2008). Lumpy demand planning: forecasting methods and service
      level agreements.
"""

from __future__ import annotations

import pandas as pd


# ---------------------------------------------------------------------------
# 1. Deteccion de demanda censurada (Lost Sales)
# ---------------------------------------------------------------------------

def mark_censored_demand(
    demand_df: pd.DataFrame,
    inventory_df: pd.DataFrame,
    granularity: str = "M",
    stockout_threshold: float = 0.0,
) -> pd.Series:
    """Identifica periodos de demanda censurada cruzando la demanda con el inventario.

    Un periodo se considera **censurado** (lost sale) cuando el stock disponible
    agregado de todas las ubicaciones llego a cero (o por debajo del umbral) en
    algun momento del periodo. En esos periodos, la demanda observada en
    ``transactions.csv`` es menor a la demanda real porque el producto no estaba
    fisicamente disponible para ser vendido o consumido.

    Entrenar un modelo con esos periodos produce una subestimacion sistematica
    de la demanda futura que no se corrige aumentando la complejidad del modelo
    — el sesgo esta en los datos, no en el algoritmo.

    Parameters
    ----------
    demand_df : pd.DataFrame
        Serie de demanda preparada. Columnas requeridas: ``[period, demand]``.
        Resultado de ``prepare_demand_series()``. El campo ``period`` debe ser
        un datetime alineado al inicio del periodo (mes, semana o dia).
    inventory_df : pd.DataFrame
        Snapshots diarios de inventario para el SKU. Columnas requeridas:
        ``[snapshot_date, on_hand_qty]``. Puede contener multiples locations;
        se agregan sumando ``on_hand_qty`` por fecha antes de evaluar.
    granularity : str
        Granularidad de la serie de demanda: ``"M"`` (mensual), ``"W"``
        (semanal) o ``"D"`` (diaria). Debe coincidir con la usada en
        ``prepare_demand_series()``.
    stockout_threshold : float
        Nivel de stock minimo para considerar el periodo censurado.
        - ``0.0`` (default): solo marca si el stock llego exactamente a cero.
        - Un valor positivo (ej. ``5.0``) marca periodos con stock < 5 unidades,
          util si el sistema de gestion tiene un buffer minimo operativo.

    Returns
    -------
    pd.Series[bool]
        Mascara booleana de longitud ``len(demand_df)``.
        ``True`` = periodo censurado (stock alcanzo el umbral o menos en algun
        dia del periodo). ``False`` = stock disponible durante todo el periodo.

    Notes
    -----
    **Logica de agregacion temporal:**

    El inventario se resamplea a la misma granularidad que la demanda tomando
    el **minimo** de ``on_hand_qty`` en cada periodo. Esto es conservador:
    si el stock llego a cero aunque sea un dia dentro del periodo mensual,
    el periodo completo se marca como censurado.

    **Periodos sin snapshot de inventario:**

    Si un periodo de demanda no tiene ningun snapshot correspondiente (puede
    ocurrir en los primeros dias del catalogo), se asume ``on_hand > threshold``
    (beneficio de la duda, no se censura). Esto evita marcar incorrectamente
    el inicio del historico cuando el inventario aun no esta registrado.

    **Limitaciones:**

    - Asume que ``on_hand_qty = 0`` implica quiebre de stock. En la practica,
      puede haber stock en transito (``on_order_qty > 0``) que cubrio parte
      de la demanda. Para mayor precision, cruzar tambien con ``on_order_qty``.
    - No distingue entre quiebre planificado (cierre de linea) y quiebre
      operacional (falla de reposicion). Ambos se marcan como censurados.
    - En el perfil industrial con nodo central, el inventario de sucursales
      puede llegar a cero incluso si el central tiene stock. El comportamiento
      dependera de si se pasa ``inventory_df`` filtrado por sucursal o consolidado.
    - Para catalogo de miles de SKUs, este calculo es liviano (solo pandas
      groupby + resample), pero la carga de ``inventory_snapshot.csv`` completo
      es pesada. Llamar con el DataFrame ya filtrado por SKU.

    Examples
    --------
    >>> demand_df = prepare_demand_series(sku_tx, granularity="M")
    >>> sku_inv = inventory_snapshot[inventory_snapshot["sku"] == sku]
    >>> censored = mark_censored_demand(demand_df, sku_inv, granularity="M")
    >>> # Cuantos periodos censurados?
    >>> print(censored.sum(), "de", len(censored), "periodos")
    """
    n = len(demand_df)
    no_censoring = pd.Series(False, index=range(n))

    if inventory_df.empty or demand_df.empty:
        return no_censoring

    # --- Agregar todas las locations: stock total disponible por dia ---
    daily_inv = (
        inventory_df
        .groupby("snapshot_date", as_index=False)["on_hand_qty"]
        .sum()
    )
    daily_inv["snapshot_date"] = pd.to_datetime(daily_inv["snapshot_date"])
    daily_inv = daily_inv.set_index("snapshot_date").sort_index()

    # --- Resamplear al periodo de la demanda tomando el MINIMO ---
    # (si el stock toca cero algun dia del periodo, el periodo esta censurado)
    freq_map = {"D": "D", "W": "W-MON", "M": "MS"}
    freq = freq_map.get(granularity, "MS")

    inv_by_period = daily_inv["on_hand_qty"].resample(freq).min()
    inv_by_period = inv_by_period.reset_index()
    inv_by_period.columns = ["period", "min_on_hand"]
    inv_by_period["period"] = pd.to_datetime(inv_by_period["period"])

    # --- Merge con los periodos de la serie de demanda ---
    demand_periods = demand_df[["period"]].copy()
    demand_periods["period"] = pd.to_datetime(demand_periods["period"])

    merged = demand_periods.merge(inv_by_period, on="period", how="left")

    # Periodos sin snapshot -> beneficio de la duda (no censurado)
    merged["min_on_hand"] = merged["min_on_hand"].fillna(stockout_threshold + 1.0)

    censored = (merged["min_on_hand"] <= stockout_threshold).reset_index(drop=True)
    return censored


def censored_summary(censored_mask: pd.Series, demand_df: pd.DataFrame) -> dict:
    """Resumen estadistico de la censura de demanda para un SKU.

    Parameters
    ----------
    censored_mask : pd.Series[bool]
        Resultado de ``mark_censored_demand()``.
    demand_df : pd.DataFrame
        Serie de demanda original. Columnas: ``[period, demand]``.

    Returns
    -------
    dict
        Contiene ``censored_periods``, ``total_periods``, ``censored_pct``,
        ``censored_demand``, ``total_demand``, ``censored_demand_pct``.
        La ultima metrica indica que fraccion del volumen total ocurrio en
        periodos con potencial stockout — es la mas relevante para decidir
        si vale la pena modelar la censura.
    """
    total = len(censored_mask)
    n_censored = int(censored_mask.sum())

    demand_in_censored = float(demand_df.loc[censored_mask.values, "demand"].sum()) \
        if n_censored > 0 else 0.0
    total_demand = float(demand_df["demand"].sum())

    return {
        "censored_periods": n_censored,
        "total_periods": total,
        "censored_pct": round(n_censored / total, 4) if total > 0 else 0.0,
        "censored_demand": round(demand_in_censored, 2),
        "total_demand": round(total_demand, 2),
        "censored_demand_pct": round(demand_in_censored / total_demand, 4)
        if total_demand > 0 else 0.0,
    }
