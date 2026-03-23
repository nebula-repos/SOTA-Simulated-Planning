import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from planning_core.repository import CanonicalRepository
from planning_core.services import PlanningService


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
    st.dataframe(
        pd.DataFrame(
            [{"table": table_name, "rows": rows} for table_name, rows in overview["table_rows"].items()]
        ),
        width="stretch",
        hide_index=True,
    )

    st.write("Chequeos basicos")
    st.dataframe(
        pd.DataFrame(
            [{"check": check_name, "value": value} for check_name, value in quality.items()]
        ),
        width="stretch",
        hide_index=True,
    )


def render_sku_tab(service: PlanningService):
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
    else:
        selected_sku = st.session_state.get("selected_sku", browser_dataframe.iloc[0]["sku"])
        if selected_sku not in set(browser_dataframe["sku"].tolist()):
            selected_sku = browser_dataframe.iloc[0]["sku"]
            st.session_state["selected_sku"] = selected_sku

    st.caption(f"SKU seleccionado: `{selected_sku}`")

    summary = service.sku_summary(selected_sku)
    if summary is None:
        st.error("No se pudo cargar el resumen del SKU.")
        return

    summary_columns = st.columns(6)
    summary_columns[0].metric("Ventas", f"{summary['sales_qty_total']:,}")
    summary_columns[1].metric("Ingresos", f"{summary['sales_amount_total']:,.0f}")
    summary_columns[2].metric("Stock actual", f"{summary['last_on_hand_total']:,}")
    summary_columns[3].metric("En orden", f"{summary['last_on_order_total']:,}")
    summary_columns[4].metric("Locaciones activas", summary["active_locations"])
    summary_columns[5].metric("Recibido central", f"{summary['purchase_receipt_qty_total']:,}")

    st.write("Atributos del SKU")
    st.dataframe(
        pd.DataFrame([summary["catalog"]]),
        width="stretch",
        hide_index=True,
    )

    location_options = ["__all__"] + service.list_sku_locations(selected_sku)
    selected_location = st.selectbox(
        "Location",
        location_options,
        format_func=lambda value: "Todas las locations" if value == "__all__" else value,
    )
    location_filter = None if selected_location == "__all__" else selected_location

    timeseries = service.sku_timeseries(selected_sku, location=location_filter)
    if timeseries.empty:
        st.warning("No hay serie disponible para este SKU/location.")
        return

    sales_figure = build_line_figure(
        timeseries,
        "Ventas y entradas",
        [
            ("sales_qty", "Ventas"),
            ("purchase_receipt_qty", "Recepcion compra"),
            ("transfer_in_qty", "Transferencia recibida"),
            ("transfer_out_qty", "Transferencia despachada"),
        ],
    )
    inventory_figure = build_line_figure(
        timeseries,
        "Stock y stock en orden",
        [
            ("on_hand_qty", "On hand"),
            ("on_order_qty", "On order"),
        ],
    )

    st.plotly_chart(sales_figure, width="stretch")
    st.plotly_chart(inventory_figure, width="stretch")

    st.write("Serie diaria")
    st.dataframe(timeseries.tail(60), width="stretch", hide_index=True)

    receipts = service.purchase_receipts_for_sku(selected_sku)
    transfers = service.internal_transfers_for_sku(selected_sku, location=location_filter)

    detail_col_a, detail_col_b = st.columns(2)
    with detail_col_a:
        st.write("Recepciones de compra")
        st.dataframe(receipts.tail(100), width="stretch", hide_index=True)
    with detail_col_b:
        st.write("Transferencias internas")
        st.dataframe(transfers.tail(100), width="stretch", hide_index=True)


def main():
    st.set_page_config(page_title="SOTA Planning Viz", page_icon=":bar_chart:", layout="wide")
    st.title("SOTA Planning Viz")
    st.caption("Visualizadora basica del modelo canonico operacional.")

    service = get_service()
    dataset_tab, sku_tab = st.tabs(["Dataset", "SKU Explorer"])

    with dataset_tab:
        render_dataset_tab(service)

    with sku_tab:
        render_sku_tab(service)


if __name__ == "__main__":
    main()
