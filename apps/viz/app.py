from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from planning_core.classification import compute_acf, detect_outliers, prepare_demand_series, select_granularity
from planning_core.repository import CanonicalRepository
from planning_core.services import PlanningService


GRANULARITY_FREQUENCIES = {
    "Diaria": None,
    "Semanal": "W",
    "Mensual": "MS",
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

    return comparison_frame


def build_location_comparison_figure(
    dataframe: pd.DataFrame,
    title: str,
    selected_lines: list[str],
) -> go.Figure:
    figure = go.Figure()
    for line_name in selected_lines:
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

    figure.update_layout(
        title=title,
        xaxis_title="Fecha",
        yaxis_title="Cantidad",
        legend_title="Serie",
        margin=dict(l=20, r=20, t=60, b=20),
        height=360,
    )
    return figure


def render_line_selector_box(title: str, available_lines: list[str], key_prefix: str) -> list[str]:
    with st.container(border=True):
        st.caption(title)
        select_all_key = f"{key_prefix}_all"
        if select_all_key not in st.session_state:
            st.session_state[select_all_key] = True

        select_all = st.checkbox("Todas", key=select_all_key)
        selected_lines: list[str] = []
        for line_name in available_lines:
            line_key = f"{key_prefix}_{line_name}"
            if line_key not in st.session_state:
                st.session_state[line_key] = True
            if select_all:
                st.session_state[line_key] = True
            checked = st.checkbox(line_name, key=line_key)
            if checked:
                selected_lines.append(line_name)
        return selected_lines


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

    csv_content = dataframe.to_csv(index=False)
    actions_col_a, actions_col_b = st.columns([1.2, 4.8])
    with actions_col_a:
        st.download_button(
            "Descargar CSV",
            data=csv_content.encode("utf-8"),
            file_name=f"{key_prefix}.csv",
            mime="text/csv",
            key=f"{key_prefix}_download",
            use_container_width=True,
        )
    with actions_col_b:
        with st.expander("Copiar contenido"):
            st.text_area(
                "CSV",
                value=csv_content,
                height=140,
                key=f"{key_prefix}_copybox",
            )


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

CLASSIFICATION_GRANULARITY_OPTIONS = {
    "Automatica": None,
    "Mensual": "M",
    "Semanal": "W",
    "Diaria": "D",
}


@st.cache_data(ttl=600, show_spinner="Clasificando catalogo...")
def get_classification_data(_service: PlanningService, granularity: str | None = None) -> pd.DataFrame:
    return _service.classify_catalog(granularity=granularity)


def build_sb_scatter_figure(df: pd.DataFrame) -> go.Figure:
    """Scatter plot ADI vs CV2 con los 4 cuadrantes de Syntetos-Boylan."""
    figure = go.Figure()

    for sb_class, color in SB_COLORS.items():
        subset = df[df["sb_class"] == sb_class]
        if subset.empty:
            continue

        # Escalar tamaño por revenue (log scale para visibilidad)
        revenue = subset["total_revenue"].clip(lower=1)
        sizes = 5 + 15 * (np.log1p(revenue) / np.log1p(revenue.max()))

        figure.add_trace(go.Scatter(
            x=subset["adi"],
            y=subset["cv2"],
            mode="markers",
            name=f"{sb_class} ({len(subset)})",
            marker=dict(color=color, size=sizes, opacity=0.7, line=dict(width=0.5, color="white")),
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
                "Revenue: $%{customdata[3]}<br>"
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

    figure.update_layout(
        title="Clasificacion Syntetos-Boylan (ADI vs CV2)",
        xaxis_title="ADI (Average Demand Interval)",
        yaxis_title="CV2 (Squared Coefficient of Variation)",
        xaxis=dict(range=[0, max_adi]),
        yaxis=dict(range=[0, max_cv2]),
        legend_title="Clase S-B",
        margin=dict(l=20, r=20, t=60, b=20),
        height=480,
    )
    return figure


def build_abc_xyz_matrix_figure(df: pd.DataFrame) -> go.Figure:
    """Heatmap de la matriz ABC-XYZ (3x3)."""
    abc_order = ["A", "B", "C"]
    xyz_order = ["X", "Y", "Z"]
    matrix = pd.crosstab(df["abc_class"], df["xyz_class"]).reindex(index=abc_order, columns=xyz_order, fill_value=0)

    figure = go.Figure(data=go.Heatmap(
        z=matrix.values,
        x=xyz_order,
        y=abc_order,
        text=matrix.values,
        texttemplate="%{text}",
        textfont=dict(size=16),
        colorscale="Blues",
        showscale=False,
        hovertemplate="ABC: %{y} | XYZ: %{x}<br>SKUs: %{z}<extra></extra>",
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
) -> go.Figure:
    """Grafico de barras para distribucion de una variable categorica."""
    counts = df[column].value_counts()
    figure = go.Figure()

    for category in counts.index:
        figure.add_trace(go.Bar(
            x=[category],
            y=[counts[category]],
            name=category,
            marker_color=color_map.get(category, "#bdc3c7"),
            text=[counts[category]],
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
    """Vista panoramica de clasificacion del catalogo."""

    # --- KPIs ---
    total = len(classification_df)
    sb_counts = classification_df["sb_class"].value_counts()
    avg_quality = classification_df["quality_score"].mean()

    kpi_cols = st.columns(6)
    kpi_cols[0].metric("SKUs clasificados", total)
    kpi_cols[1].metric("Smooth", sb_counts.get("smooth", 0))
    kpi_cols[2].metric("Erratic", sb_counts.get("erratic", 0))
    kpi_cols[3].metric("Intermittent", sb_counts.get("intermittent", 0))
    kpi_cols[4].metric("Lumpy", sb_counts.get("lumpy", 0))
    kpi_cols[5].metric("Quality promedio", f"{avg_quality:.2f}")

    # --- Scatter ADI-CV2 ---
    # Filtrar infinitos para el scatter
    plot_df = classification_df[classification_df["adi"] != float("inf")].copy()
    if not plot_df.empty:
        scatter_fig = build_sb_scatter_figure(plot_df)
        st.plotly_chart(scatter_fig, use_container_width=True)

    # --- Matriz + distribuciones ---
    col_matrix, col_sb, col_lifecycle = st.columns(3)

    with col_matrix:
        matrix_fig = build_abc_xyz_matrix_figure(classification_df)
        st.plotly_chart(matrix_fig, use_container_width=True)

    with col_sb:
        sb_fig = build_distribution_bar_figure(
            classification_df, "sb_class", "Distribucion Syntetos-Boylan", SB_COLORS,
        )
        st.plotly_chart(sb_fig, use_container_width=True)

    with col_lifecycle:
        lc_fig = build_distribution_bar_figure(
            classification_df, "lifecycle", "Ciclo de vida", LIFECYCLE_COLORS,
        )
        st.plotly_chart(lc_fig, use_container_width=True)

    # --- Tabla completa de clasificacion ---
    st.write("Tabla de clasificacion completa")
    display_columns = [
        "sku", "abc_class", "xyz_class", "abc_xyz", "sb_class",
        "adi", "cv2", "is_seasonal", "has_trend", "trend_direction",
        "lifecycle", "quality_score", "total_demand", "total_revenue",
        "mean_demand", "zero_pct", "outlier_count",
    ]
    display_df = classification_df[[c for c in display_columns if c in classification_df.columns]]

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
            "total_revenue": st.column_config.NumberColumn("Revenue", format="%.0f"),
            "mean_demand": st.column_config.NumberColumn("Media demanda", format="%.1f"),
            "zero_pct": st.column_config.NumberColumn("% Ceros", format="%.1%"),
            "outlier_count": st.column_config.NumberColumn("Outliers", format="%d"),
        },
        key="classification_table",
    )

    csv_content = display_df.to_csv(index=False)
    dl_col, _ = st.columns([1.2, 4.8])
    with dl_col:
        st.download_button(
            "Descargar clasificacion CSV",
            data=csv_content.encode("utf-8"),
            file_name="classification.csv",
            mime="text/csv",
            key="classification_download",
            use_container_width=True,
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

    available_lines = ["Agregado sucursales", "Agregado total red"] + selectable_locations
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
        selected_flow_lines = render_line_selector_box(
            "Lineas",
            available_lines,
            "flow_line_selector",
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
        selected_inventory_lines = render_line_selector_box(
            "Lineas",
            available_lines,
            "inventory_line_selector",
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

    with flow_chart_col:
        if selected_flow_lines:
            sales_figure = build_location_comparison_figure(
                sales_comparison,
                f"{selected_flow_metric_label} por sucursal ({sales_granularity}, {flow_temporality})",
                selected_flow_lines,
            )
            st.plotly_chart(sales_figure, use_container_width=True)
            sales_table_columns = ["date"] + [
                line for line in selected_flow_lines if line in sales_comparison.columns
            ]
            render_copyable_dataframe(
                sales_comparison.loc[:, sales_table_columns].tail(60),
                "sku_flow_comparison",
            )
        else:
            st.warning("Selecciona al menos una linea para el grafico de flujo.")

    with inventory_chart_col:
        if selected_inventory_lines:
            inventory_figure = build_location_comparison_figure(
                inventory_comparison,
                f"{selected_inventory_metric_label} por sucursal ({inventory_granularity}, {inventory_temporality})",
                selected_inventory_lines,
            )
            st.plotly_chart(inventory_figure, use_container_width=True)
            inventory_table_columns = ["date"] + [
                line for line in selected_inventory_lines if line in inventory_comparison.columns
            ]
            render_copyable_dataframe(
                inventory_comparison.loc[:, inventory_table_columns].tail(60),
                "sku_inventory_comparison",
            )
        else:
            st.warning("Selecciona al menos una linea para el grafico de stock.")


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
    cls_cols = st.columns(8)
    cls_cols[0].metric("Clase S-B", profile["sb_class"])
    cls_cols[1].metric("ABC", profile.get("abc_class", "—"))
    cls_cols[2].metric("XYZ", profile["xyz_class"])
    cls_cols[3].metric("ADI", f"{profile['adi']:.2f}")
    cls_cols[4].metric("CV2", f"{profile['cv2']:.3f}")
    cls_cols[5].metric("Lifecycle", profile["lifecycle"])
    cls_cols[6].metric("Quality", f"{profile['quality_score']:.2f}")
    cls_cols[7].metric("Estacional", "Si" if profile["is_seasonal"] else "No")

    quality_flags = profile.get("quality_flags")
    if quality_flags:
        flags = quality_flags if isinstance(quality_flags, list) else []
        if flags:
            st.warning("Quality flags: " + ", ".join(flags))

    # Serie temporal con outliers + ACF
    series_df = service.sku_demand_series(selected_sku, granularity=granularity)

    if not series_df.empty:
        outlier_mask = detect_outliers(series_df["demand"], method="iqr")

        demand_col, acf_col = st.columns(2)

        with demand_col:
            demand_fig = build_demand_with_outliers_figure(
                series_df, outlier_mask,
                f"Demanda {selected_sku} (granularidad {granularity})",
            )
            st.plotly_chart(demand_fig, use_container_width=True)

        with acf_col:
            acf_data = service.sku_acf(selected_sku, granularity=granularity)
            if acf_data["lags"]:
                acf_fig = build_acf_figure(acf_data, f"Autocorrelacion (ACF) — {selected_sku}")
                st.plotly_chart(acf_fig, use_container_width=True)

        st.write("Serie temporal de demanda")
        series_display = series_df.copy()
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


def _render_sku_section_supply(service: PlanningService, selected_sku: str):
    """Seccion de abastecimiento: recepciones de compra y transferencias internas."""
    receipts = service.purchase_receipts_for_sku(selected_sku)
    transfers = service.internal_transfers_for_sku(selected_sku)

    detail_col_a, detail_col_b = st.columns(2)
    with detail_col_a:
        st.write("Recepciones de compra")
        render_copyable_dataframe(receipts.tail(100), "purchase_receipts_detail")
    with detail_col_b:
        st.write("Transferencias internas")
        render_copyable_dataframe(transfers.tail(100), "internal_transfers_detail")


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
    header_col_a, header_col_b = st.columns([4, 1])
    with header_col_a:
        catalog = summary["catalog"]
        st.caption(f"`{selected_sku}` — {catalog.get('name', '')} | {catalog.get('category', '')} | {catalog.get('supplier', '')}")
    with header_col_b:
        if st.button("Volver", key=back_callback_key):
            return "back"

    # --- KPIs operacionales (siempre visibles) ---
    kpi_cols = st.columns(6)
    kpi_cols[0].metric("Ventas totales", f"{summary['sales_qty_total']:,}")
    kpi_cols[1].metric("Revenue", f"${summary['sales_amount_total']:,.0f}")
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
        options=["Operacional", "Clasificacion", "Abastecimiento"],
        default="Operacional",
        selection_mode="single",
        key="sku_detail_section",
    )

    if sku_section == "Clasificacion":
        _render_sku_section_clasificacion(service, selected_sku, classification_df)
    elif sku_section == "Abastecimiento":
        _render_sku_section_supply(service, selected_sku)
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

    st.caption(f"Horizonte: {overview['date_range']['start']} a {overview['date_range']['end']}")
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
    browser_actions_col_a, browser_actions_col_b = st.columns([1.2, 4.8])
    with browser_actions_col_a:
        st.download_button(
            "Descargar CSV",
            data=browser_dataframe.to_csv(index=False).encode("utf-8"),
            file_name="sku_browser.csv",
            mime="text/csv",
            key="sku_browser_download",
            use_container_width=True,
        )
    with browser_actions_col_b:
        with st.expander("Copiar contenido"):
            st.text_area(
                "CSV",
                value=browser_dataframe.to_csv(index=False),
                height=140,
                key="sku_browser_copybox",
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
