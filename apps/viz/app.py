from __future__ import annotations

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from planning_core.classification import compute_acf, detect_outliers, prepare_demand_series, select_granularity
from planning_core.forecasting.backtest import backtest_summary
from planning_core.repository import CanonicalRepository
from planning_core.services import PlanningService


# Frecuencias pandas para resample directo en la capa viz
GRANULARITY_FREQUENCIES = {
    "Diaria": None,
    "Semanal": "W-MON",  # pandas usa W-MON (inicio lunes) para series semanales
    "Mensual": "MS",
}

# Claves de granularidad para la API de planning_core (distinto de las frecuencias pandas)
GRANULARITY_PLANNING_KEYS = {
    "Diaria": "D",
    "Semanal": "W",   # planning_core convierte W -> W-MON internamente via FREQ_MAP
    "Mensual": "M",
}

TEMPORALITY_WINDOWS = {
    "Completo": None,
    "Ultimos 30 dias": 30,
    "Ultimos 90 dias": 90,
    "Ultimos 180 dias": 180,
    "Ultimos 365 dias": 365,
    "YTD": "YTD",
}

FLOW_METRICS = {
    "Ventas": "sales_qty",
    "Recepcion compra": "purchase_receipt_qty",
    "Transferencia recibida": "transfer_in_qty",
    "Transferencia despachada": "transfer_out_qty",
}

INVENTORY_METRICS = {
    "On hand": "on_hand_qty",
    "On order": "on_order_qty",
}


@st.cache_resource
def get_service() -> PlanningService:
    return PlanningService(CanonicalRepository())


@st.cache_data(show_spinner=False)
def _run_sku_forecast(_service: PlanningService, sku: str, granularity: str, h: int, n_windows: int) -> dict:
    """Wrapper cacheado para sku_forecast.

    El prefijo ``_`` en ``_service`` indica a Streamlit que no intente hashearlo.
    El cache evita que Streamlit rerrun el horse-race completo cuando el usuario
    navega entre secciones sin cambiar los parámetros de forecast.
    """
    return _service.sku_forecast(sku, granularity=granularity, h=h, n_windows=n_windows, return_cv=True)


def build_backtest_figure(
    cv_df: pd.DataFrame,
    hist_df: pd.DataFrame,
    winner_model: str,
    backtest_metrics: dict,
    sku: str,
) -> go.Figure:
    """Gráfico del horse-race de backtest: demanda histórica + predicciones por modelo por ventana."""
    COLORS = {
        "AutoETS": "#e67e22",
        "AutoARIMA": "#27ae60",
        "SeasonalNaive": "#8e44ad",
        "MSTL": "#2980b9",
        "CrostonSBA": "#c0392b",
        "ADIDA": "#16a085",
        "LightGBM": "#f39c12",
    }

    fig = go.Figure()
    cutoffs = sorted(cv_df["cutoff"].unique())
    first_cutoff = cutoffs[0]

    # Histórico hasta el primer cutoff
    pre = hist_df[hist_df["ds"] <= first_cutoff]
    fig.add_trace(go.Scatter(
        x=pre["ds"], y=pre["y"],
        name="Histórico",
        line=dict(color="#2980b9", width=2),
    ))

    # Demanda real en cada ventana de backtest
    for i, cutoff in enumerate(cutoffs):
        window = cv_df[cv_df["cutoff"] == cutoff]
        fig.add_trace(go.Scatter(
            x=window["ds"], y=window["y"],
            name="Real (backtest)" if i == 0 else None,
            showlegend=(i == 0),
            line=dict(color="#00FF7F", width=3),
            mode="lines+markers",
            marker=dict(size=7, color="#00FF7F", line=dict(color="#000000", width=1)),
        ))
        fig.add_vline(x=str(cutoff), line_dash="dot", line_color="gray", opacity=0.4)

    # Predicciones por modelo
    model_cols = [c for c in cv_df.columns if c not in ("unique_id", "ds", "cutoff", "y")]
    for model in model_cols:
        mase = backtest_metrics.get(model, {}).get("mase", float("nan"))
        label = f"{model}  MASE={mase:.3f}" if not math.isnan(mase) else model
        is_winner = (model == winner_model)
        color = COLORS.get(model, "#999999")

        for i, cutoff in enumerate(cutoffs):
            window = cv_df[cv_df["cutoff"] == cutoff]
            fig.add_trace(go.Scatter(
                x=window["ds"], y=window[model].clip(lower=0),
                name=label if i == 0 else None,
                showlegend=(i == 0),
                line=dict(
                    color=color,
                    width=3 if is_winner else 1.5,
                    dash="solid" if is_winner else "dash",
                ),
                opacity=1.0 if is_winner else 0.65,
            ))

    fig.update_layout(
        title=f"{sku} — Backtest horse-race  (ganador: {winner_model})",
        xaxis_title="Período",
        yaxis_title="Demanda",
        template="plotly_white",
        height=440,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=20, r=20, t=100, b=20),
    )
    return fig


def format_currency(value: float | int) -> str:
    return f"$ {value:,.0f}"


def build_line_figure(dataframe: pd.DataFrame, title: str, series: list[tuple[str, str]]) -> go.Figure:
    figure = go.Figure()
    for column_name, label in series:
        if column_name not in dataframe.columns:
            continue
        figure.add_trace(
            go.Scatter(
                x=dataframe["date"],
                y=dataframe[column_name],
                mode="lines",
                name=label,
            )
        )

    figure.update_layout(
        title=title,
        xaxis_title="Fecha",
        yaxis_title="Cantidad",
        legend_title="Serie",
        margin=dict(l=20, r=20, t=60, b=20),
        height=360,
    )
    return figure


def aggregate_timeseries(dataframe: pd.DataFrame, granularity: str) -> pd.DataFrame:
    frequency = GRANULARITY_FREQUENCIES[granularity]
    if frequency is None:
        return dataframe.copy()

    aggregated = (
        dataframe
        .set_index("date")
        .resample(frequency)
        .agg(
            {
                "sales_qty": "sum",
                "sales_amount": "sum",
                "purchase_receipt_qty": "sum",
                "transfer_in_qty": "sum",
                "transfer_out_qty": "sum",
                "on_hand_qty": "last",
                "on_order_qty": "last",
            }
        )
        .fillna(0)
        .reset_index()
    )

    integer_columns = [
        "sales_qty",
        "purchase_receipt_qty",
        "transfer_in_qty",
        "transfer_out_qty",
        "on_hand_qty",
        "on_order_qty",
    ]
    aggregated[integer_columns] = aggregated[integer_columns].astype(int)
    aggregated["sales_amount"] = aggregated["sales_amount"].astype(float)
    return aggregated


def apply_temporality_filter(dataframe: pd.DataFrame, temporality: str) -> pd.DataFrame:
    window = TEMPORALITY_WINDOWS[temporality]
    if window is None or dataframe.empty:
        return dataframe.copy()

    max_date = dataframe["date"].max()
    if window == "YTD":
        start_date = pd.Timestamp(year=max_date.year, month=1, day=1)
    else:
        start_date = max_date - pd.Timedelta(days=int(window) - 1)
    return dataframe.loc[dataframe["date"] >= start_date].copy()


def build_location_comparison_frame(
    service: PlanningService,
    sku: str,
    line_locations: list[str],
    aggregate_locations: list[str],
    metric: str,
    granularity: str,
    temporality: str,
) -> pd.DataFrame:
    comparison_frame = None
    active_locations: list[str] = []

    for location in line_locations:
        location_timeseries = service.sku_timeseries(sku, location=location)
        if location_timeseries.empty:
            continue
        location_timeseries = aggregate_timeseries(location_timeseries, granularity)
        location_timeseries = apply_temporality_filter(location_timeseries, temporality)
        location_frame = location_timeseries.loc[:, ["date", metric]].rename(columns={metric: location})
        if comparison_frame is None:
            comparison_frame = location_frame
        else:
            comparison_frame = comparison_frame.merge(location_frame, on="date", how="outer")
        active_locations.append(location)

    if comparison_frame is None:
        return pd.DataFrame(columns=["date", "Agregado sucursales", "Agregado total red"])

    comparison_frame = comparison_frame.sort_values("date").fillna(0).reset_index(drop=True)
    comparison_frame[active_locations] = comparison_frame[active_locations].astype(float)
    aggregate_columns = [location for location in aggregate_locations if location in active_locations]
    if aggregate_columns:
        comparison_frame["Agregado sucursales"] = comparison_frame[aggregate_columns].sum(axis=1)
    else:
        comparison_frame["Agregado sucursales"] = 0
    if active_locations:
        comparison_frame["Agregado total red"] = comparison_frame[active_locations].sum(axis=1)
    else:
        comparison_frame["Agregado total red"] = 0

    integer_metrics = {
        "sales_qty",
        "purchase_receipt_qty",
        "transfer_in_qty",
        "transfer_out_qty",
        "on_hand_qty",
        "on_order_qty",
    }
    if metric in integer_metrics:
        numeric_columns = active_locations + ["Agregado sucursales", "Agregado total red"]
        comparison_frame[numeric_columns] = comparison_frame[numeric_columns].round(0).astype(int)

    ordered_columns = ["date", "Agregado sucursales", "Agregado total red"] + active_locations
    return comparison_frame.loc[:, ordered_columns]


def build_location_comparison_figure(
    dataframe: pd.DataFrame,
    title: str,
    stockout_points: pd.DataFrame | None = None,
) -> go.Figure:
    figure = go.Figure()
    for line_name in [column for column in dataframe.columns if column != "date"]:
        if line_name not in dataframe.columns:
            continue
        figure.add_trace(
            go.Scatter(
                x=dataframe["date"],
                y=dataframe[line_name],
                mode="lines",
                name=line_name,
            )
        )

    if stockout_points is not None and not stockout_points.empty:
        figure.add_trace(
            go.Scatter(
                x=stockout_points["date"],
                y=stockout_points["y"],
                mode="markers",
                name="Sin venta por stockout",
                marker=dict(color="#c0392b", size=10, symbol="x"),
                hovertemplate=(
                    "<b>Sin venta por stockout</b><br>"
                    "Fecha: %{x}<br>"
                    "Ventas red: %{y:.0f}"
                    "<extra></extra>"
                ),
            )
        )

    figure.update_layout(
        title=title,
        xaxis_title="Fecha",
        yaxis_title="Cantidad",
        legend_title="Serie",
        margin=dict(l=20, r=20, t=60, b=20),
        height=360,
    )
    return figure


def render_copyable_dataframe(
    dataframe: pd.DataFrame,
    key_prefix: str,
    *,
    height: int | None = None,
    hide_index: bool = True,
    column_config=None,
):
    dataframe_args = {
        "width": "stretch",
        "hide_index": hide_index,
    }
    if height is not None:
        dataframe_args["height"] = height
    if column_config is not None:
        dataframe_args["column_config"] = column_config

    st.dataframe(dataframe, **dataframe_args)


SB_COLORS = {
    "smooth": "#2ecc71",
    "erratic": "#e67e22",
    "intermittent": "#3498db",
    "lumpy": "#e74c3c",
    "inactive": "#95a5a6",
}

ABC_COLORS = {"A": "#e74c3c", "B": "#f39c12", "C": "#3498db"}

LIFECYCLE_COLORS = {
    "new": "#9b59b6",
    "growing": "#2ecc71",
    "mature": "#3498db",
    "declining": "#e67e22",
    "inactive": "#95a5a6",
}

# Opciones de granularidad para clasificacion — pasa claves planning_core (no pandas freq)
CLASSIFICATION_GRANULARITY_OPTIONS = {
    "Mensual (Oficial)": "M",
    "Automatica": None,
    "Semanal": "W",
    "Diaria": "D",
}


@st.cache_data(ttl=600, show_spinner="Clasificando catalogo...")
def get_classification_data(_service: PlanningService, granularity: str | None = None) -> pd.DataFrame:
    return _service.classify_catalog(granularity=granularity)


def build_sb_scatter_figure(
    df: pd.DataFrame,
    currency_code: str,
    highlight_mask: pd.Series | None = None,
) -> go.Figure:
    """Scatter plot ADI vs CV2 con los 4 cuadrantes de Syntetos-Boylan.

    Si ``highlight_mask`` se provee, los puntos filtrados se muestran opacos
    y el resto atenuado.
    """
    figure = go.Figure()
    has_filter = highlight_mask is not None and not highlight_mask.all()

    for sb_class, color in SB_COLORS.items():
        subset = df[df["sb_class"] == sb_class]
        if subset.empty:
            continue

        # Escalar tamaño por revenue (log scale para visibilidad)
        max_rev = df["total_revenue"].clip(lower=1).max()
        revenue = subset["total_revenue"].clip(lower=1)
        sizes = 5 + 15 * (np.log1p(revenue) / np.log1p(max_rev))

        # Opacidad: resaltado vs atenuado
        if has_filter:
            subset_mask = highlight_mask.reindex(subset.index, fill_value=False)
            opacities = np.where(subset_mask.values, 0.85, 0.08)
        else:
            opacities = np.full(len(subset), 0.7)

        figure.add_trace(go.Scatter(
            x=subset["adi"],
            y=subset["cv2"],
            mode="markers",
            name=f"{sb_class} ({len(subset)})",
            marker=dict(color=color, size=sizes, opacity=opacities, line=dict(width=0.5, color="white")),
            text=subset["sku"],
            customdata=np.stack([
                subset["abc_class"],
                subset["xyz_class"],
                subset["lifecycle"],
                subset["total_revenue"].map(lambda v: f"{v:,.0f}"),
                subset["quality_score"],
            ], axis=-1),
            hovertemplate=(
                "<b>%{text}</b><br>"
                "ADI: %{x:.2f}<br>"
                "CV2: %{y:.3f}<br>"
                "ABC: %{customdata[0]} | XYZ: %{customdata[1]}<br>"
                "Lifecycle: %{customdata[2]}<br>"
                f"Revenue ({currency_code}): $%{{customdata[3]}}<br>"
                "Quality: %{customdata[4]:.2f}"
                "<extra></extra>"
            ),
        ))

    # Lineas de cuadrante
    adi_cutoff = 1.32
    cv2_cutoff = 0.49
    max_adi = min(df["adi"].replace([np.inf], np.nan).max() * 1.1, 20) if not df.empty else 10
    max_cv2 = df["cv2"].max() * 1.1 if not df.empty else 2

    figure.add_hline(y=cv2_cutoff, line_dash="dash", line_color="gray", opacity=0.5,
                     annotation_text=f"CV2 = {cv2_cutoff}", annotation_position="top right")
    figure.add_vline(x=adi_cutoff, line_dash="dash", line_color="gray", opacity=0.5,
                     annotation_text=f"ADI = {adi_cutoff}", annotation_position="top right")

    # Etiquetas de cuadrante
    figure.add_annotation(x=adi_cutoff / 2, y=max_cv2 * 0.05, text="SMOOTH", showarrow=False,
                          font=dict(size=11, color="gray"), opacity=0.6)
    figure.add_annotation(x=adi_cutoff / 2, y=max_cv2 * 0.95, text="ERRATIC", showarrow=False,
                          font=dict(size=11, color="gray"), opacity=0.6)
    figure.add_annotation(x=(adi_cutoff + max_adi) / 2, y=max_cv2 * 0.05, text="INTERMITTENT", showarrow=False,
                          font=dict(size=11, color="gray"), opacity=0.6)
    figure.add_annotation(x=(adi_cutoff + max_adi) / 2, y=max_cv2 * 0.95, text="LUMPY", showarrow=False,
                          font=dict(size=11, color="gray"), opacity=0.6)

    n_highlighted = int(highlight_mask.sum()) if has_filter else len(df)
    title_suffix = f" — {n_highlighted}/{len(df)} SKUs" if has_filter else ""

    figure.update_layout(
        title=f"Clasificacion Syntetos-Boylan (ADI vs CV2){title_suffix}",
        xaxis_title="ADI (Average Demand Interval)",
        yaxis_title="CV2 (Squared Coefficient of Variation)",
        xaxis=dict(range=[0, max_adi]),
        yaxis=dict(range=[0, max_cv2]),
        legend_title="Clase S-B",
        margin=dict(l=20, r=20, t=60, b=20),
        height=480,
    )
    return figure


def build_abc_xyz_matrix_figure(
    df: pd.DataFrame,
    highlight_abc: list[str] | None = None,
    highlight_xyz: list[str] | None = None,
) -> go.Figure:
    """Heatmap de la matriz ABC-XYZ (3x3) con celdas seleccionadas resaltadas."""
    abc_order = ["A", "B", "C"]
    xyz_order = ["X", "Y", "Z"]
    matrix = pd.crosstab(df["abc_class"], df["xyz_class"]).reindex(index=abc_order, columns=xyz_order, fill_value=0)

    # Mascara de celdas activas (para borde de seleccion)
    has_filter = (highlight_abc is not None and len(highlight_abc) < 3) or \
                 (highlight_xyz is not None and len(highlight_xyz) < 3)

    if has_filter:
        active_abc = set(highlight_abc) if highlight_abc else set(abc_order)
        active_xyz = set(highlight_xyz) if highlight_xyz else set(xyz_order)
        # Atenuar celdas no seleccionadas
        display_matrix = matrix.copy().astype(float)
        for i, abc in enumerate(abc_order):
            for j, xyz in enumerate(xyz_order):
                if abc not in active_abc or xyz not in active_xyz:
                    display_matrix.iloc[i, j] = display_matrix.iloc[i, j] * 0.15
    else:
        display_matrix = matrix.astype(float)

    figure = go.Figure(data=go.Heatmap(
        z=display_matrix.values,
        x=xyz_order,
        y=abc_order,
        text=matrix.values,
        texttemplate="%{text}",
        textfont=dict(size=16),
        colorscale="Blues",
        showscale=False,
        hovertemplate="ABC: %{y} | XYZ: %{x}<br>SKUs: %{text}<extra></extra>",
    ))

    figure.update_layout(
        title="Matriz ABC-XYZ",
        xaxis_title="XYZ (Predictibilidad)",
        yaxis_title="ABC (Valor)",
        margin=dict(l=20, r=20, t=60, b=20),
        height=300,
        yaxis=dict(autorange="reversed"),
    )
    return figure


def build_distribution_bar_figure(
    df: pd.DataFrame,
    column: str,
    title: str,
    color_map: dict[str, str],
    highlight_values: list[str] | None = None,
) -> go.Figure:
    """Grafico de barras con valores seleccionados resaltados."""
    all_values = sorted(df[column].unique(), key=lambda v: -df[column].value_counts().get(v, 0))
    counts = df[column].value_counts()
    has_filter = highlight_values is not None and len(highlight_values) < len(all_values)

    figure = go.Figure()

    for category in all_values:
        is_active = not has_filter or category in (highlight_values or [])
        opacity = 1.0 if is_active else 0.15
        base_color = color_map.get(category, "#bdc3c7")

        figure.add_trace(go.Bar(
            x=[category],
            y=[counts.get(category, 0)],
            name=category,
            marker_color=base_color,
            opacity=opacity,
            text=[counts.get(category, 0)],
            textposition="outside",
        ))

    figure.update_layout(
        title=title,
        xaxis_title="Clase",
        yaxis_title="Cantidad SKUs",
        showlegend=False,
        margin=dict(l=20, r=20, t=60, b=20),
        height=300,
    )
    return figure


def build_demand_with_outliers_figure(
    series_df: pd.DataFrame,
    outlier_mask: pd.Series,
    title: str,
    stockout_points: pd.DataFrame | None = None,
) -> go.Figure:
    """Serie temporal de demanda con outliers marcados en rojo."""
    figure = go.Figure()

    figure.add_trace(go.Scatter(
        x=series_df["period"],
        y=series_df["demand"],
        mode="lines",
        name="Demanda",
        line=dict(color="#3498db"),
    ))

    outlier_points = series_df[outlier_mask.values]
    if not outlier_points.empty:
        figure.add_trace(go.Scatter(
            x=outlier_points["period"],
            y=outlier_points["demand"],
            mode="markers",
            name="Outliers",
            marker=dict(color="#e74c3c", size=9, symbol="x"),
        ))

    if stockout_points is not None and not stockout_points.empty:
        figure.add_trace(go.Scatter(
            x=stockout_points["period"],
            y=stockout_points["demand"],
            mode="markers",
            name="Sin venta por stockout",
            marker=dict(color="#c0392b", size=10, symbol="diamond"),
            hovertemplate=(
                "<b>Sin venta por stockout</b><br>"
                "Periodo: %{x}<br>"
                "Demanda observada: %{y:.0f}"
                "<extra></extra>"
            ),
        ))

    figure.update_layout(
        title=title,
        xaxis_title="Periodo",
        yaxis_title="Demanda",
        margin=dict(l=20, r=20, t=60, b=20),
        height=360,
    )
    return figure


def build_acf_figure(acf_data: dict, title: str) -> go.Figure:
    """Grafico de barras de autocorrelacion (ACF)."""
    lags = acf_data["lags"]
    acf_values = acf_data["acf"]
    confidence = acf_data["confidence_bound"]

    figure = go.Figure()

    figure.add_trace(go.Bar(
        x=lags,
        y=acf_values,
        marker_color=["#e74c3c" if abs(v) > confidence else "#3498db" for v in acf_values],
        name="ACF",
    ))

    # Bandas de confianza al 95%
    figure.add_hline(y=confidence, line_dash="dash", line_color="gray", opacity=0.6)
    figure.add_hline(y=-confidence, line_dash="dash", line_color="gray", opacity=0.6)
    figure.add_hline(y=0, line_color="black", opacity=0.3)

    figure.update_layout(
        title=title,
        xaxis_title="Lag",
        yaxis_title="Autocorrelacion",
        margin=dict(l=20, r=20, t=60, b=20),
        height=300,
        showlegend=False,
    )
    return figure


def render_classification_panoramic(service: PlanningService, classification_df: pd.DataFrame):
    """Vista panoramica de clasificacion con filtros interactivos.

    Los filtros (multiselects y selectboxes) controlan simultaneamente:
    - Resaltado/atenuacion en todos los graficos
    - Contenido de la tabla inferior
    - KPIs agregados
    """
    currency_code = service.currency_code()
    # Valores unicos disponibles en el dataset
    all_sb = sorted(classification_df["sb_class"].dropna().unique())
    all_abc = sorted(classification_df["abc_class"].dropna().unique())
    all_xyz = sorted(classification_df["xyz_class"].dropna().unique())
    all_lifecycle = sorted(classification_df["lifecycle"].dropna().unique())

    # --- Controles de filtro ---
    with st.expander("Filtros de clasificacion", expanded=True):
        fc = st.columns([1.2, 0.8, 0.8, 1.2, 1.5, 0.5])
        with fc[0]:
            sel_sb = st.multiselect("Clase S-B", all_sb, default=all_sb, key="clf_f_sb")
        with fc[1]:
            sel_abc = st.multiselect("ABC", all_abc, default=all_abc, key="clf_f_abc")
        with fc[2]:
            sel_xyz = st.multiselect("XYZ", all_xyz, default=all_xyz, key="clf_f_xyz")
        with fc[3]:
            sel_lc = st.multiselect("Lifecycle", all_lifecycle, default=all_lifecycle, key="clf_f_lc")
        with fc[4]:
            sc = st.columns(2)
            with sc[0]:
                sel_seas = st.selectbox("Estacional", ["Todos", "Si", "No"], key="clf_f_seas")
            with sc[1]:
                sel_trend = st.selectbox("Tendencia", ["Todos", "Si", "No"], key="clf_f_trend")
        with fc[5]:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Limpiar", key="clf_reset_filters", use_container_width=True):
                st.session_state["clf_f_sb"] = all_sb
                st.session_state["clf_f_abc"] = all_abc
                st.session_state["clf_f_xyz"] = all_xyz
                st.session_state["clf_f_lc"] = all_lifecycle
                st.session_state["clf_f_seas"] = "Todos"
                st.session_state["clf_f_trend"] = "Todos"
                st.rerun()

    # --- Mascara combinada de filtros ---
    mask = pd.Series(True, index=classification_df.index)

    if sel_sb:
        mask &= classification_df["sb_class"].isin(sel_sb)
    else:
        mask[:] = False

    if sel_abc:
        mask &= classification_df["abc_class"].isin(sel_abc)
    else:
        mask[:] = False

    if sel_xyz:
        mask &= classification_df["xyz_class"].isin(sel_xyz)
    else:
        mask[:] = False

    if sel_lc:
        mask &= classification_df["lifecycle"].isin(sel_lc)
    else:
        mask[:] = False

    if sel_seas == "Si":
        mask &= classification_df["is_seasonal"]
    elif sel_seas == "No":
        mask &= ~classification_df["is_seasonal"]

    if sel_trend == "Si":
        mask &= classification_df["has_trend"]
    elif sel_trend == "No":
        mask &= ~classification_df["has_trend"]

    has_active_filter = not mask.all()
    filtered_df = classification_df[mask]

    # --- KPIs (reflejan filtro activo) ---
    total = len(classification_df)
    n_filtered = len(filtered_df)
    sb_counts = filtered_df["sb_class"].value_counts()
    avg_quality = filtered_df["quality_score"].mean() if not filtered_df.empty else 0.0

    kpi_cols = st.columns(6)
    kpi_cols[0].metric("SKUs", f"{n_filtered} / {total}" if has_active_filter else str(total))
    kpi_cols[1].metric("Smooth", sb_counts.get("smooth", 0))
    kpi_cols[2].metric("Erratic", sb_counts.get("erratic", 0))
    kpi_cols[3].metric("Intermittent", sb_counts.get("intermittent", 0))
    kpi_cols[4].metric("Lumpy", sb_counts.get("lumpy", 0))
    kpi_cols[5].metric("Quality promedio", f"{avg_quality:.2f}")

    # --- Scatter ADI-CV2 (resalta puntos filtrados) ---
    plot_df = classification_df[classification_df["adi"] != float("inf")].copy()
    if not plot_df.empty:
        scatter_mask = mask.reindex(plot_df.index, fill_value=False) if has_active_filter else None
        scatter_fig = build_sb_scatter_figure(plot_df, currency_code=currency_code, highlight_mask=scatter_mask)
        st.plotly_chart(scatter_fig, use_container_width=True)

    # --- Matriz ABC-XYZ + distribuciones (resaltan dimensiones filtradas) ---
    col_matrix, col_sb, col_lifecycle = st.columns(3)

    with col_matrix:
        hl_abc = sel_abc if len(sel_abc) < len(all_abc) else None
        hl_xyz = sel_xyz if len(sel_xyz) < len(all_xyz) else None
        matrix_fig = build_abc_xyz_matrix_figure(classification_df, highlight_abc=hl_abc, highlight_xyz=hl_xyz)
        st.plotly_chart(matrix_fig, use_container_width=True)

    with col_sb:
        hl_sb = sel_sb if len(sel_sb) < len(all_sb) else None
        sb_fig = build_distribution_bar_figure(
            classification_df, "sb_class", "Distribucion Syntetos-Boylan", SB_COLORS,
            highlight_values=hl_sb,
        )
        st.plotly_chart(sb_fig, use_container_width=True)

    with col_lifecycle:
        hl_lc = sel_lc if len(sel_lc) < len(all_lifecycle) else None
        lc_fig = build_distribution_bar_figure(
            classification_df, "lifecycle", "Ciclo de vida", LIFECYCLE_COLORS,
            highlight_values=hl_lc,
        )
        st.plotly_chart(lc_fig, use_container_width=True)

    # --- Tabla de clasificacion (filtrada) ---
    table_label = f"**Tabla de clasificacion** — {n_filtered} de {total} SKUs" if has_active_filter else "**Tabla de clasificacion completa**"
    st.markdown(table_label)

    display_columns = [
        "sku", "abc_class", "xyz_class", "abc_xyz", "sb_class",
        "adi", "cv2", "is_seasonal", "has_trend", "trend_direction",
        "lifecycle", "quality_score", "total_demand", "total_revenue",
        "mean_demand", "zero_pct", "outlier_count", "censored_pct", "censored_demand_pct",
    ]
    display_df = filtered_df[[c for c in display_columns if c in filtered_df.columns]]

    browser_event = st.dataframe(
        display_df,
        width="stretch",
        hide_index=True,
        height=400,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "sku": st.column_config.TextColumn("SKU", width="small"),
            "abc_class": st.column_config.TextColumn("ABC", width="small"),
            "xyz_class": st.column_config.TextColumn("XYZ", width="small"),
            "abc_xyz": st.column_config.TextColumn("ABC-XYZ", width="small"),
            "sb_class": st.column_config.TextColumn("S-B", width="small"),
            "adi": st.column_config.NumberColumn("ADI", format="%.2f"),
            "cv2": st.column_config.NumberColumn("CV2", format="%.3f"),
            "is_seasonal": st.column_config.CheckboxColumn("Estacional"),
            "has_trend": st.column_config.CheckboxColumn("Tendencia"),
            "trend_direction": st.column_config.TextColumn("Dir. tendencia", width="small"),
            "lifecycle": st.column_config.TextColumn("Lifecycle", width="small"),
            "quality_score": st.column_config.NumberColumn("Quality", format="%.2f"),
            "total_demand": st.column_config.NumberColumn("Demanda total", format="%.0f"),
            "total_revenue": st.column_config.NumberColumn(f"Revenue ({currency_code})", format="%.0f"),
            "mean_demand": st.column_config.NumberColumn("Media demanda", format="%.1f"),
            "zero_pct": st.column_config.NumberColumn("% Ceros", format="%.1%"),
            "outlier_count": st.column_config.NumberColumn("Outliers", format="%d"),
            "censored_pct": st.column_config.NumberColumn("% Periodos cens.", format="%.1%"),
            "censored_demand_pct": st.column_config.NumberColumn("% Volumen cens.", format="%.1%"),
        },
        key="classification_table",
    )

    # Detectar seleccion en tabla para navegar a detalle
    selected_rows = []
    if hasattr(browser_event, "selection") and hasattr(browser_event.selection, "rows"):
        selected_rows = list(browser_event.selection.rows)
    elif isinstance(browser_event, dict):
        selected_rows = browser_event.get("selection", {}).get("rows", [])

    if selected_rows:
        selected_sku = display_df.iloc[selected_rows[0]]["sku"]
        st.session_state["classification_selected_sku"] = selected_sku
        st.session_state["classification_view"] = "Detalle"
        st.rerun()


def _render_sku_section_operacional(service: PlanningService, selected_sku: str, summary: dict):
    """Seccion operacional: flujo y stock por location."""
    central_location = summary.get("central_location")
    branch_locations = [
        location
        for location in service.list_sku_locations(selected_sku)
        if location != central_location
    ]
    if not branch_locations:
        st.warning("No hay sucursales operativas disponibles para este SKU.")
        return

    selectable_locations = branch_locations.copy()
    if central_location and central_location in service.list_sku_locations(selected_sku):
        selectable_locations.append(central_location)

    flow_chart_col, flow_control_col = st.columns([4.8, 1.4])
    with flow_control_col:
        st.markdown("**Configuracion flujo**")
        selected_flow_metric_label = st.selectbox(
            "Medida",
            list(FLOW_METRICS.keys()),
            index=0,
            key="flow_metric_selector",
        )
        sales_granularity = st.selectbox(
            "Granularidad",
            list(GRANULARITY_FREQUENCIES.keys()),
            index=0,
            key="sales_granularity_selector",
        )
        flow_temporality = st.selectbox(
            "Temporalidad",
            list(TEMPORALITY_WINDOWS.keys()),
            index=0,
            key="flow_temporality_selector",
        )

    inventory_chart_col, inventory_control_col = st.columns([4.8, 1.4])
    with inventory_control_col:
        st.markdown("**Configuracion stock**")
        selected_inventory_metric_label = st.selectbox(
            "Medida",
            list(INVENTORY_METRICS.keys()),
            index=0,
            key="inventory_metric_selector",
        )
        inventory_granularity = st.selectbox(
            "Granularidad",
            list(GRANULARITY_FREQUENCIES.keys()),
            index=0,
            key="inventory_granularity_selector",
        )
        inventory_temporality = st.selectbox(
            "Temporalidad",
            list(TEMPORALITY_WINDOWS.keys()),
            index=0,
            key="inventory_temporality_selector",
        )

    selected_flow_metric = FLOW_METRICS[selected_flow_metric_label]
    selected_inventory_metric = INVENTORY_METRICS[selected_inventory_metric_label]

    sales_comparison = build_location_comparison_frame(
        service=service,
        sku=selected_sku,
        line_locations=selectable_locations,
        aggregate_locations=branch_locations,
        metric=selected_flow_metric,
        granularity=sales_granularity,
        temporality=flow_temporality,
    )
    inventory_comparison = build_location_comparison_frame(
        service=service,
        sku=selected_sku,
        line_locations=selectable_locations,
        aggregate_locations=branch_locations,
        metric=selected_inventory_metric,
        granularity=inventory_granularity,
        temporality=inventory_temporality,
    )

    stockout_points = None
    if selected_flow_metric == "sales_qty" and not sales_comparison.empty:
        censor_info = service.sku_censored_mask(selected_sku, granularity=GRANULARITY_PLANNING_KEYS[sales_granularity])
        stockout_series = (
            censor_info["series"]
            .rename(columns={"period": "date"})
            .loc[:, ["date", "is_stockout_no_sale"]]
        )
        stockout_series = apply_temporality_filter(stockout_series, flow_temporality)
        stockout_points = (
            sales_comparison[["date", "Agregado total red"]]
            .merge(stockout_series, on="date", how="left")
            .loc[lambda df: df["is_stockout_no_sale"].fillna(False)]
            .rename(columns={"Agregado total red": "y"})
            .loc[:, ["date", "y"]]
        )

    with flow_chart_col:
        sales_figure = build_location_comparison_figure(
            sales_comparison,
            f"{selected_flow_metric_label} por sucursal ({sales_granularity}, {flow_temporality})",
            stockout_points=stockout_points,
        )
        st.plotly_chart(sales_figure, use_container_width=True)
        if stockout_points is not None and not stockout_points.empty:
            st.caption("Marcadores rojos: periodos sin venta observada en la red con quiebre de stock.")
        render_copyable_dataframe(
            sales_comparison.tail(60),
            "sku_flow_comparison",
        )

    with inventory_chart_col:
        inventory_figure = build_location_comparison_figure(
            inventory_comparison,
            f"{selected_inventory_metric_label} por sucursal ({inventory_granularity}, {inventory_temporality})",
        )
        st.plotly_chart(inventory_figure, use_container_width=True)
        render_copyable_dataframe(
            inventory_comparison.tail(60),
            "sku_inventory_comparison",
        )


def _render_sku_section_clasificacion(service: PlanningService, selected_sku: str, classification_df: pd.DataFrame | None):
    """Seccion de clasificacion: serie con outliers, ACF, perfil completo."""
    # Obtener perfil de clasificacion
    profile = None
    granularity = None

    if classification_df is not None:
        sku_row = classification_df[classification_df["sku"] == selected_sku]
        if not sku_row.empty:
            profile = sku_row.iloc[0]
            granularity = profile["granularity"]

    if profile is None:
        # Clasificar on-demand si no hay datos cacheados
        result = service.classify_single_sku(selected_sku)
        if result is None:
            st.info("No hay datos de demanda para clasificar este SKU.")
            return
        profile = pd.Series(result)
        granularity = profile["granularity"]

    # KPIs de clasificacion
    cls_cols = st.columns(10)
    cls_cols[0].metric("Clase S-B", profile["sb_class"])
    cls_cols[1].metric("ABC", profile.get("abc_class", "—"))
    cls_cols[2].metric("XYZ", profile["xyz_class"])
    cls_cols[3].metric("ADI", f"{profile['adi']:.2f}")
    cls_cols[4].metric("CV2", f"{profile['cv2']:.3f}")
    cls_cols[5].metric("Lifecycle", profile["lifecycle"])
    cls_cols[6].metric("Quality", f"{profile['quality_score']:.2f}")
    cls_cols[7].metric("Estacional", "Si" if profile["is_seasonal"] else "No")
    cls_cols[8].metric("Periodos cens.", f"{profile.get('censored_pct', 0.0):.1%}")
    cls_cols[9].metric("Vol. cens.", f"{profile.get('censored_demand_pct', 0.0):.1%}")

    quality_flags = profile.get("quality_flags")
    if quality_flags:
        flags = quality_flags if isinstance(quality_flags, list) else []
        if flags:
            st.warning("Quality flags: " + ", ".join(flags))

    # Serie temporal con outliers + ACF
    series_df = service.sku_demand_series(selected_sku, granularity=granularity)
    censor_info = service.sku_censored_mask(selected_sku, granularity=granularity)

    if not series_df.empty:
        outlier_mask = detect_outliers(series_df["demand"], method="iqr")
        stockout_points = censor_info["series"].loc[
            censor_info["series"]["is_stockout_no_sale"],
            ["period", "demand"],
        ]

        demand_col, acf_col = st.columns(2)

        with demand_col:
            demand_fig = build_demand_with_outliers_figure(
                series_df,
                outlier_mask,
                f"Demanda {selected_sku} (granularidad {granularity})",
                stockout_points=stockout_points,
            )
            st.plotly_chart(demand_fig, use_container_width=True)
            if not stockout_points.empty:
                st.caption("Diamantes rojos: periodos sin venta observada con quiebre de stock.")

        with acf_col:
            acf_data = service.sku_acf(selected_sku, granularity=granularity)
            if acf_data["lags"]:
                acf_fig = build_acf_figure(acf_data, f"Autocorrelacion (ACF) — {selected_sku}")
                st.plotly_chart(acf_fig, use_container_width=True)

        st.write("Serie temporal de demanda")
        series_display = series_df.copy().merge(
            censor_info["series"][["period", "is_censored", "is_stockout_no_sale"]],
            on="period",
            how="left",
        )
        series_display["is_outlier"] = outlier_mask.values
        render_copyable_dataframe(series_display, f"demand_series_{selected_sku}")
    else:
        st.info("No hay datos de demanda para este SKU.")

    # Perfil completo
    st.write("Perfil de clasificacion completo")
    profile_dict = profile.to_dict() if isinstance(profile, pd.Series) else profile
    profile_display = pd.DataFrame([
        {"Metrica": k, "Valor": str(v)} for k, v in profile_dict.items()
    ])
    render_copyable_dataframe(profile_display, f"classification_profile_{selected_sku}")


def _render_sku_section_forecast(service: PlanningService, selected_sku: str):
    """Seccion de forecast: selector automatico + visualizacion del pronostico."""
    ctrl_cols = st.columns([1.5, 1, 1, 3.5])
    with ctrl_cols[0]:
        granularity_label = st.selectbox(
            "Granularidad",
            list(GRANULARITY_PLANNING_KEYS.keys()),
            index=2,  # Mensual por defecto
            key="forecast_granularity",
        )
    with ctrl_cols[1]:
        h = st.number_input("Horizonte (h)", min_value=1, max_value=36, value=6, key="forecast_h")
    with ctrl_cols[2]:
        n_windows = st.number_input("Ventanas backtest", min_value=2, max_value=10, value=3, key="forecast_n_windows")

    granularity = GRANULARITY_PLANNING_KEYS[granularity_label]

    with st.spinner("Corriendo horse-race de modelos..."):
        result = _run_sku_forecast(service, selected_sku, granularity, int(h), int(n_windows))

    status = result.get("status", "error")

    if status == "no_forecast":
        st.info("Este SKU está clasificado como inactivo: no se genera forecast.")
        return

    if status == "no_data":
        st.warning("No hay transacciones registradas para este SKU: no se puede generar forecast.")
        return

    if status == "error":
        st.error(f"Error al generar el forecast: {result.get('error', 'desconocido')}")
        return

    # --- KPIs del resultado ---
    kpi_cols = st.columns(4)
    kpi_cols[0].metric("Estado", status)
    kpi_cols[1].metric("Modelo ganador", result.get("model") or "—")
    mase_val = result.get("mase")
    mase_str = f"{mase_val:.3f}" if (mase_val is not None and not math.isnan(mase_val)) else "N/A"
    kpi_cols[2].metric("MASE", mase_str)
    kpi_cols[3].metric("Horizonte", f"{result.get('h', h)} periodos")

    if status == "fallback":
        st.caption("El backtest no pudo evaluar los modelos (serie corta o todos fallaron). Se usó el baseline como fallback.")

    # --- Tabs: Forecast | Backtest horse-race ---
    forecast_df = result.get("forecast")
    demand_series = result.get("demand_series")
    hist = (
        demand_series.rename(columns={"period": "ds", "demand": "y"})
        if demand_series is not None and not demand_series.empty
        else pd.DataFrame(columns=["ds", "y"])
    )

    tab_fc, tab_bt = st.tabs(["Forecast", "Backtest horse-race"])

    with tab_fc:
        if forecast_df is not None and not forecast_df.empty:
            title_mase = f"MASE={mase_str}"
            fig = go.Figure()
            if not hist.empty:
                fig.add_trace(go.Scatter(
                    x=hist["ds"], y=hist["y"],
                    name="Demanda histórica (limpia)",
                    line=dict(color="#2980b9"),
                ))
            fig.add_trace(go.Scatter(
                x=forecast_df["ds"], y=forecast_df["yhat"],
                name=f"Forecast ({result.get('model', '?')})",
                line=dict(color="#e74c3c", dash="dash"),
                mode="lines+markers",
            ))
            if "yhat_lo80" in forecast_df.columns and "yhat_hi80" in forecast_df.columns:
                fig.add_trace(go.Scatter(
                    x=list(forecast_df["ds"]) + list(forecast_df["ds"][::-1]),
                    y=list(forecast_df["yhat_hi80"]) + list(forecast_df["yhat_lo80"][::-1]),
                    fill="toself",
                    fillcolor="rgba(231,76,60,0.12)",
                    line=dict(color="rgba(0,0,0,0)"),
                    name="IC 80%",
                    hoverinfo="skip",
                ))
            fig.update_layout(
                title=f"{selected_sku} — {result.get('model', '?')}  ({title_mase})",
                xaxis_title="Periodo",
                yaxis_title="Demanda",
                template="plotly_white",
                height=420,
                margin=dict(l=20, r=20, t=60, b=20),
            )
            st.plotly_chart(fig, use_container_width=True)

            st.write("Pronostico por periodo")
            fc_display = forecast_df.copy()
            fc_display["ds"] = fc_display["ds"].astype(str)
            render_copyable_dataframe(fc_display, f"forecast_{selected_sku}")

    with tab_bt:
        cv_df = result.get("cv_df")
        if cv_df is not None and not cv_df.empty:
            bt_fig = build_backtest_figure(
                cv_df=cv_df,
                hist_df=hist,
                winner_model=result.get("model", ""),
                backtest_metrics=result.get("backtest", {}),
                sku=selected_sku,
            )
            st.plotly_chart(bt_fig, use_container_width=True)
        else:
            st.info("No hay datos de backtest disponibles (serie muy corta o SKU en fallback).")

        backtest_data = result.get("backtest")
        if backtest_data:
            st.write("Comparacion de modelos (backtest)")
            summary_df = backtest_summary(backtest_data)
            render_copyable_dataframe(
                summary_df[["model", "mase", "wape", "bias", "n_windows", "status"]],
                f"backtest_summary_{selected_sku}",
            )


def render_sku_detail_unified(
    service: PlanningService,
    selected_sku: str,
    back_callback_key: str,
    classification_df: pd.DataFrame | None = None,
):
    """Vista unificada de detalle de un SKU.

    Combina informacion operacional, de clasificacion y de abastecimiento
    en una sola vista con sub-menu interno.
    """
    summary = service.sku_summary(selected_sku)
    if summary is None:
        st.error("No se pudo cargar el resumen del SKU.")
        return

    # --- Header: SKU + boton volver ---
    currency_code = service.currency_code()
    header_col_a, header_col_b = st.columns([8.5, 1])
    with header_col_a:
        catalog = summary["catalog"]
        st.caption(f"`{selected_sku}` — {catalog.get('name', '')} | {catalog.get('category', '')} | {catalog.get('supplier', '')}")
    with header_col_b:
        if st.button("← Volver", key=back_callback_key, type="tertiary"):
            return "back"

    # --- KPIs operacionales (siempre visibles) ---
    kpi_cols = st.columns([1, 1.45, 1, 1, 1, 1])
    kpi_cols[0].metric("Ventas totales", f"{summary['sales_qty_total']:,}")
    with kpi_cols[1]:
        st.caption(f"Revenue ({currency_code})")
        st.markdown(f"#### {format_currency(summary['sales_amount_total'])}")
    kpi_cols[2].metric("Stock actual", f"{summary['last_on_hand_total']:,}")
    kpi_cols[3].metric("En orden", f"{summary['last_on_order_total']:,}")
    kpi_cols[4].metric("Locaciones", summary["active_locations"])
    kpi_cols[5].metric("Recibido central", f"{summary['purchase_receipt_qty_total']:,}")

    # --- Atributos del SKU ---
    with st.expander("Atributos del SKU"):
        render_copyable_dataframe(pd.DataFrame([summary["catalog"]]), "sku_catalog_attrs")

    # --- Sub-menu interno ---
    sku_section = st.segmented_control(
        "Seccion",
        options=["Operacional", "Clasificacion", "Forecast"],
        default="Operacional",
        selection_mode="single",
        key="sku_detail_section",
    )

    if sku_section == "Clasificacion":
        _render_sku_section_clasificacion(service, selected_sku, classification_df)
    elif sku_section == "Forecast":
        _render_sku_section_forecast(service, selected_sku)
    else:
        _render_sku_section_operacional(service, selected_sku, summary)

    return None


def render_classification_tab(service: PlanningService):
    """Tab principal de clasificacion de demanda."""
    if "classification_view" not in st.session_state:
        st.session_state["classification_view"] = "Panorama"

    # Controles de granularidad
    control_cols = st.columns([1.5, 4.5])
    with control_cols[0]:
        granularity_label = st.selectbox(
            "Granularidad de clasificacion",
            list(CLASSIFICATION_GRANULARITY_OPTIONS.keys()),
            index=0,
            key="classification_granularity",
        )
    granularity = CLASSIFICATION_GRANULARITY_OPTIONS[granularity_label]

    # Cargar datos clasificados (cacheados)
    classification_df = get_classification_data(service, granularity=granularity)

    current_view = st.session_state["classification_view"]

    if current_view == "Panorama":
        render_classification_panoramic(service, classification_df)
    else:
        selected_sku = st.session_state.get("classification_selected_sku")
        if not selected_sku:
            st.session_state["classification_view"] = "Panorama"
            st.rerun()
        result = render_sku_detail_unified(
            service, selected_sku,
            back_callback_key="back_to_classification_panoramic",
            classification_df=classification_df,
        )
        if result == "back":
            st.session_state["classification_view"] = "Panorama"
            st.rerun()


def render_dataset_tab(service: PlanningService):
    overview = service.dataset_overview()
    quality = service.dataset_health()

    metric_columns = st.columns(5)
    metric_columns[0].metric("SKUs", overview["sku_count"])
    metric_columns[1].metric("Locaciones", overview["location_count"])
    metric_columns[2].metric("Filas ventas", overview["table_rows"]["transactions"])
    metric_columns[3].metric("Filas stock", overview["table_rows"]["inventory_snapshot"])
    metric_columns[4].metric("Filas transfer.", overview["table_rows"]["internal_transfers"])

    caption_parts = [f"Horizonte: {overview['date_range']['start']} a {overview['date_range']['end']}"]
    if overview.get("profile"):
        caption_parts.append(f"Perfil: {overview['profile']}")
    if overview.get("currency"):
        caption_parts.append(f"Moneda: {overview['currency']}")
    st.caption(" | ".join(caption_parts))
    st.write("Tablas cargadas")
    render_copyable_dataframe(
        pd.DataFrame(
            [{"table": table_name, "rows": rows} for table_name, rows in overview["table_rows"].items()]
        ),
        "dataset_table_rows",
    )

    st.write("Chequeos basicos")
    render_copyable_dataframe(
        pd.DataFrame(
            [{"check": check_name, "value": value} for check_name, value in quality.items()]
        ),
        "dataset_quality_checks",
    )


def render_sku_browser(service: PlanningService) -> None:
    filter_columns = st.columns([1.4, 1, 1])
    with filter_columns[0]:
        search_text = st.text_input("Buscar SKU", placeholder="SKU, nombre, categoria o proveedor")
    with filter_columns[1]:
        category_options = ["__all__"] + service.list_categories()
        selected_category = st.selectbox(
            "Categoria",
            category_options,
            format_func=lambda value: "Todas" if value == "__all__" else value,
        )
    with filter_columns[2]:
        supplier_options = ["__all__"] + service.list_suppliers()
        selected_supplier = st.selectbox(
            "Proveedor",
            supplier_options,
            format_func=lambda value: "Todos" if value == "__all__" else value,
        )

    category_filter = None if selected_category == "__all__" else selected_category
    supplier_filter = None if selected_supplier == "__all__" else selected_supplier
    sku_options = service.list_skus(
        search=search_text,
        category=category_filter,
        supplier=supplier_filter,
        limit=None,
    )

    if not sku_options:
        st.warning("No hay resultados para ese filtro.")
        return

    browser_dataframe = pd.DataFrame(sku_options).loc[
        :,
        ["sku", "name", "category", "supplier", "brand", "base_price", "moq"],
    ]
    st.caption(f"Resultados: {len(browser_dataframe):,} SKUs. Selecciona una fila para explorar el detalle.")
    browser_event = st.dataframe(
        browser_dataframe,
        width="stretch",
        hide_index=True,
        height=320,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "sku": st.column_config.TextColumn("SKU", width="small"),
            "name": st.column_config.TextColumn("Producto", width="large"),
            "category": st.column_config.TextColumn("Categoria", width="medium"),
            "supplier": st.column_config.TextColumn("Proveedor", width="medium"),
            "brand": st.column_config.TextColumn("Marca", width="small"),
            "base_price": st.column_config.NumberColumn("Precio base", format="%.0f"),
            "moq": st.column_config.NumberColumn("MOQ", format="%d"),
        },
        key="sku_browser_table",
    )

    selected_rows = []
    if hasattr(browser_event, "selection") and hasattr(browser_event.selection, "rows"):
        selected_rows = list(browser_event.selection.rows)
    elif isinstance(browser_event, dict):
        selected_rows = browser_event.get("selection", {}).get("rows", [])

    if selected_rows:
        selected_sku = browser_dataframe.iloc[selected_rows[0]]["sku"]
        st.session_state["selected_sku"] = selected_sku
        st.session_state["sku_explorer_view"] = "Detalle SKU"
        st.rerun()


def render_sku_tab(service: PlanningService):
    if "sku_explorer_view" not in st.session_state:
        st.session_state["sku_explorer_view"] = "Listado"

    current_sku_view = st.session_state["sku_explorer_view"]

    if current_sku_view == "Listado":
        render_sku_browser(service)
        return

    selected_sku = st.session_state.get("selected_sku")
    if not selected_sku:
        st.info("Selecciona un SKU desde el listado para ver el detalle.")
        st.session_state["sku_explorer_view"] = "Listado"
        st.rerun()

    result = render_sku_detail_unified(
        service, selected_sku,
        back_callback_key="back_to_sku_browser",
    )
    if result == "back":
        st.session_state["sku_explorer_view"] = "Listado"
        st.rerun()


def main():
    st.set_page_config(page_title="SOTA Planning Viz", page_icon=":bar_chart:", layout="wide")
    st.title("SOTA Planning Viz")
    st.caption("Visualizadora basica del modelo canonico operacional.")

    service = get_service()
    current_view = st.segmented_control(
        "Vista",
        options=["Dataset", "SKU Explorer", "Clasificacion"],
        default="SKU Explorer",
        selection_mode="single",
        key="active_view",
    )

    if current_view == "Dataset":
        render_dataset_tab(service)
    elif current_view == "Clasificacion":
        render_classification_tab(service)
    else:
        render_sku_tab(service)


if __name__ == "__main__":
    main()
