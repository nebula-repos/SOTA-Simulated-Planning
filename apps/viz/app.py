from __future__ import annotations

import math
from urllib.parse import urlencode

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


XYZ_COLORS = {"X": "#5b8a72", "Y": "#c58b39", "Z": "#b05d4f"}

APP_NAV_ITEMS = {
    "dashboard": {
        "label": "Dashboard",
        "icon": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10.5V20h13V10.5"/><path d="M9.5 20v-5h5v5"/></svg>',
    },
    "catalogo": {
        "label": "Catálogo",
        "icon": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7.5 12 4l8 3.5-8 3.5-8-3.5Z"/><path d="M4 7.5V16l8 4 8-4V7.5"/><path d="M12 11v9"/></svg>',
    },
    "clasificacion": {
        "label": "Clasificación",
        "icon": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8"/><path d="M12 12 17 7"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/></svg>',
    },
    "alertas": {
        "label": "Alertas",
        "icon": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4 21 20H3L12 4Z"/><path d="M12 9v4.5"/><circle cx="12" cy="17" r="1" fill="currentColor" stroke="none"/></svg>',
    },
    "escenarios": {
        "label": "Escenarios",
        "icon": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 20 7.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="M12 3v18"/><path d="M4 7.5 12 12l8-4.5"/></svg>',
    },
}


def inject_app_styles(sidebar_compact: bool = False) -> None:
    sidebar_state_css = """
        :root {
            --sidebar-current-w: var(--sidebar-expanded-w);
        }

        [data-testid="stSidebar"] {
            min-width: var(--sidebar-current-w) !important;
            max-width: var(--sidebar-current-w) !important;
        }

        [data-testid="stSidebar"] > div:first-child {
            width: var(--sidebar-current-w) !important;
            min-width: var(--sidebar-current-w) !important;
            padding-top: 4.2rem;
        }
    """

    if sidebar_compact:
        sidebar_state_css = """
        :root {
            --sidebar-current-w: var(--sidebar-collapsed-w);
        }

        [data-testid="stSidebar"] {
            min-width: var(--sidebar-current-w) !important;
            max-width: var(--sidebar-current-w) !important;
        }

        [data-testid="stSidebar"] > div:first-child {
            width: var(--sidebar-current-w) !important;
            min-width: var(--sidebar-current-w) !important;
            padding-top: 4.2rem;
        }

        [data-testid="stSidebar"] .sota-sidebar-copy,
        [data-testid="stSidebar"] .sota-sidebar-meta,
        [data-testid="stSidebar"] hr {
            display: none !important;
        }

        div[role="radiogroup"][aria-orientation="vertical"] label {
            justify-content: center;
            padding-inline: 0.2rem;
        }
        """

    css = """
        <style>
        :root {
            --appbar-h: 4.65rem;
            --content-gutter: 1.7rem;
            --sidebar-expanded-w: 15.75rem;
            --sidebar-collapsed-w: 4.9rem;
            --bg: #f6f1e7;
            --surface: rgba(255, 252, 247, 0.90);
            --surface-strong: #fffdf9;
            --border: rgba(107, 92, 72, 0.16);
            --text: #2c241b;
            --muted: #6b5c48;
        }

        __SIDEBAR_STATE_CSS__

        [data-testid="stAppViewContainer"] {
            background:
                linear-gradient(rgba(120, 103, 83, 0.05) 1px, transparent 1px),
                linear-gradient(90deg, rgba(120, 103, 83, 0.05) 1px, transparent 1px),
                linear-gradient(180deg, #faf6ef 0%, #f5efe4 100%);
            background-size: 28px 28px, 28px 28px, 100% 100%;
            color: var(--text);
        }

        [data-testid="stHeader"] {
            display: none !important;
            height: 0 !important;
        }

        [data-testid="stToolbar"] {
            display: none !important;
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #efe6d8 0%, #e8dece 100%);
            border-right: 1px solid var(--border);
        }

        [data-testid="stSidebar"] * {
            color: var(--text);
        }

        .main .block-container {
            max-width: 1480px;
            padding-top: 5.55rem;
            padding-left: var(--content-gutter);
            padding-right: var(--content-gutter);
            padding-bottom: 2.25rem;
        }

        div[data-testid="stMetric"] {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 0.85rem 1rem;
            box-shadow: 0 10px 28px rgba(83, 66, 45, 0.06);
        }

        div[data-testid="stMetric"] label,
        div[data-testid="stMetric"] [data-testid="stMetricLabel"],
        div[data-testid="stMetric"] [data-testid="stMetricValue"],
        div[data-testid="stMetric"] [data-testid="stMetricDelta"],
        div[data-testid="stMetric"] p,
        div[data-testid="stMetric"] span {
            color: var(--text) !important;
            opacity: 1 !important;
            fill: var(--text) !important;
        }

        div[data-testid="stMetric"] [data-testid="stMetricLabel"] {
            color: var(--muted) !important;
        }

        div[data-testid="stDataFrame"] *,
        div[data-testid="stSelectbox"] *,
        div[data-testid="stMultiSelect"] *,
        div[data-testid="stTextInput"] *,
        div[data-testid="stNumberInput"] *,
        div[data-testid="stRadio"] *,
        div[data-testid="stExpander"] * {
            color: var(--text) !important;
        }

        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        .stTextInput input,
        .stNumberInput input {
            background: rgba(255, 252, 247, 0.92) !important;
            color: var(--text) !important;
            border-color: var(--border) !important;
        }

        div[data-testid="stSpinner"] {
            position: fixed;
            inset: 0;
            display: flex;
            justify-content: center;
            align-items: center;
            margin: 0;
            padding: 0;
            background: rgba(246, 241, 231, 0.34);
            backdrop-filter: blur(2px);
            z-index: 1400;
        }

        div[data-testid="stSpinner"] > div {
            position: relative;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            gap: 0.7rem;
            width: 10.5rem;
            min-height: 10.5rem;
            background: rgba(255, 252, 247, 0.94);
            border: 1px solid rgba(107, 92, 72, 0.10);
            border-radius: 26px;
            padding: 1.1rem;
            box-shadow: 0 18px 34px rgba(83, 66, 45, 0.08);
        }

        div[data-testid="stSpinner"] > div::before {
            content: "";
            flex: 0 0 auto;
            width: 3.2rem;
            height: 3.2rem;
            border-radius: 999px;
            border: 4px solid rgba(109, 127, 97, 0.14);
            border-top-color: #6d7f61;
            border-right-color: #7f9570;
            box-shadow: inset 0 0 0 1px rgba(255, 252, 247, 0.65);
            animation: sota-spin 0.82s linear infinite;
        }

        div[data-testid="stSpinner"] svg {
            display: none !important;
        }

        div[data-testid="stSpinner"] p {
            margin: 0 !important;
            font-size: 0.84rem !important;
            font-weight: 700 !important;
            letter-spacing: -0.01em;
            color: var(--muted) !important;
            text-align: center;
            max-width: 8rem;
        }

        div[data-testid="stSpinner"] * {
            color: var(--text) !important;
        }

        @keyframes sota-spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }

        button[kind],
        [data-testid="baseButton-secondary"],
        [data-testid="baseButton-tertiary"] {
            color: var(--text);
        }

        [data-testid="stMarkdownContainer"] p,
        [data-testid="stCaptionContainer"] {
            color: var(--text);
        }

        div[role="radiogroup"][aria-orientation="vertical"] label {
            background: transparent;
            border: 1px solid transparent;
            border-radius: 14px;
            padding: 0.42rem 0.55rem;
            transition: background 0.18s ease, border-color 0.18s ease;
        }

        div[role="radiogroup"][aria-orientation="vertical"] label:hover {
            background: rgba(255, 252, 247, 0.78);
        }

        div[role="radiogroup"][aria-orientation="vertical"] label:has(input:checked) {
            background: rgba(109, 127, 97, 0.10);
            border-color: rgba(109, 127, 97, 0.22);
        }

        div[role="radiogroup"][aria-orientation="vertical"] label > div:first-child {
            display: none;
        }

        div[role="radiogroup"][aria-orientation="horizontal"] {
            gap: 1.35rem;
            border-bottom: 1px solid rgba(107, 92, 72, 0.16);
            padding-bottom: 0.1rem;
            margin-bottom: 0.85rem;
        }

        div[role="radiogroup"][aria-orientation="horizontal"] label {
            background: transparent;
            border: none;
            border-radius: 0;
            padding: 0 0 0.62rem 0;
            margin: 0;
        }

        div[role="radiogroup"][aria-orientation="horizontal"] label > div:first-child {
            display: none;
        }

        div[role="radiogroup"][aria-orientation="horizontal"] label p {
            color: var(--muted) !important;
            font-size: 1rem;
            font-weight: 650;
        }

        div[role="radiogroup"][aria-orientation="horizontal"] label:has(input:checked) {
            border-bottom: 2px solid #6d7f61;
        }

        div[role="radiogroup"][aria-orientation="horizontal"] label:has(input:checked) p {
            color: var(--text) !important;
        }

        [data-baseweb="tab-list"] {
            gap: 0.5rem;
            background: transparent !important;
        }

        [data-baseweb="tab"] {
            background: rgba(255, 252, 247, 0.74) !important;
            border: 1px solid var(--border) !important;
            border-radius: 999px !important;
            color: var(--text) !important;
            padding: 0.45rem 0.9rem !important;
        }

        [data-baseweb="tab"][aria-selected="true"] {
            background: #6d7f61 !important;
            color: #fff !important;
        }

        code {
            background: rgba(130, 111, 88, 0.12) !important;
            color: var(--text) !important;
            border-radius: 8px;
            padding: 0.08rem 0.35rem;
        }

        .sota-appbar {
            position: fixed;
            top: 0.7rem;
            left: 50%;
            transform: translateX(-50%);
            width: min(1480px, calc(100vw - (2 * var(--content-gutter))));
            z-index: 998;
        }

        .sota-appbar-inner {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1.25rem;
            min-height: 3.5rem;
            background: rgba(255, 252, 247, 0.84);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 0.68rem 1rem;
            backdrop-filter: blur(12px);
            box-shadow: 0 10px 28px rgba(83, 66, 45, 0.06);
        }

        .sota-appbar-brand {
            display: flex;
            align-items: center;
            gap: 0.9rem;
            min-width: 0;
        }

        .sota-appbar-mark {
            width: 0.78rem;
            height: 0.78rem;
            border-radius: 999px;
            background: linear-gradient(180deg, #6d7f61 0%, #8ea27f 100%);
            box-shadow: 0 0 0 6px rgba(109, 127, 97, 0.12);
            flex: 0 0 auto;
        }

        .sota-appbar-links {
            display: flex;
            align-items: center;
            gap: 0.3rem;
            flex-wrap: wrap;
        }

        .sota-appbar-link {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            height: 2.35rem;
            padding: 0 0.8rem;
            border-radius: 999px;
            color: var(--muted);
            text-decoration: none;
            font-size: 0.92rem;
            font-weight: 650;
            transition: background 0.16s ease, color 0.16s ease, border-color 0.16s ease;
        }

        .sota-appbar-link:hover {
            background: rgba(109, 127, 97, 0.08);
            color: var(--text);
        }

        .sota-appbar-link.is-active {
            background: rgba(109, 127, 97, 0.12);
            color: var(--text);
            border: 1px solid rgba(109, 127, 97, 0.16);
        }

        .sota-appbar-icon {
            width: 0.9rem;
            height: 0.9rem;
            display: inline-flex;
            color: currentColor;
            opacity: 0.9;
        }

        .sota-appbar-icon svg {
            width: 100%;
            height: 100%;
            display: block;
        }

        .sota-appbar-copy {
            min-width: 0;
        }

        .sota-appbar-title {
            color: var(--text);
            font-size: 1.15rem;
            font-weight: 800;
            line-height: 1;
            letter-spacing: -0.02em;
            margin: 0;
        }

        .sota-appbar-subtitle {
            color: var(--muted);
            font-size: 0.82rem;
            line-height: 1.2;
            margin-top: 0.2rem;
        }

        .sota-appbar-pill {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            border: 1px solid rgba(109, 127, 97, 0.16);
            background: rgba(109, 127, 97, 0.12);
            color: var(--text);
            font-size: 0.8rem;
            font-weight: 700;
            padding: 0.38rem 0.72rem;
            white-space: nowrap;
        }

        .sota-sidebar-copy,
        .sota-sidebar-meta {
            color: var(--muted);
        }

        .sota-sidebar-chip {
            display: inline-block;
            background: rgba(109, 127, 97, 0.18);
            color: var(--text);
            border: 1px solid rgba(109, 127, 97, 0.18);
            border-radius: 999px;
            padding: 0.18rem 0.55rem;
            font-size: 0.86rem;
            font-weight: 600;
        }

        .sota-top-actions {
            margin-top: -0.15rem;
            margin-bottom: 0.35rem;
        }

        .sota-header-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 1rem 1.1rem 0.9rem 1.1rem;
            margin-bottom: 0.75rem;
            box-shadow: 0 10px 30px rgba(83, 66, 45, 0.05);
        }

        .sota-overline {
            color: var(--muted);
            font-size: 0.78rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            margin-bottom: 0.35rem;
        }

        .sota-title {
            color: var(--text);
            font-size: 1.65rem;
            font-weight: 700;
            line-height: 1.15;
            margin: 0;
        }

        .sota-subtitle {
            color: var(--muted);
            font-size: 0.95rem;
            margin-top: 0.3rem;
        }

        .sota-badge-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-top: 0.85rem;
        }

        .sota-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            border-radius: 999px;
            padding: 0.34rem 0.7rem;
            font-size: 0.78rem;
            font-weight: 600;
            border: 1px solid rgba(0, 0, 0, 0.06);
            color: #fff;
        }

        .sota-section-note {
            color: var(--muted);
            font-size: 0.88rem;
            margin-top: -0.2rem;
            margin-bottom: 0.65rem;
        }

        .sota-hero {
            max-width: 760px;
            margin: 0.8rem auto 1.4rem auto;
            text-align: center;
            background: linear-gradient(180deg, rgba(255, 252, 247, 0.94) 0%, rgba(248, 241, 230, 0.90) 100%);
            border: 1px solid var(--border);
            border-radius: 28px;
            padding: 1.4rem 1.6rem 1.25rem 1.6rem;
            box-shadow: 0 20px 44px rgba(83, 66, 45, 0.08);
        }

        .sota-hero-kicker {
            color: #8a7458;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            margin-bottom: 0.45rem;
        }

        .sota-hero-title {
            color: var(--text);
            font-size: 2rem;
            font-weight: 800;
            line-height: 1.05;
            margin: 0;
        }

        .sota-hero-copy {
            color: var(--muted);
            font-size: 1rem;
            line-height: 1.55;
            margin-top: 0.7rem;
        }

        @media (max-width: 920px) {
            .sota-appbar {
                width: calc(100vw - 1rem);
            }

            .sota-appbar-inner {
                padding-inline: 0.85rem;
            }

            .sota-appbar-pill {
                display: none;
            }
        }
        </style>
        """
    st.markdown(css.replace("__SIDEBAR_STATE_CSS__", sidebar_state_css), unsafe_allow_html=True)


def _escape_html(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def render_centered_hero(kicker: str, title: str, copy: str) -> None:
    st.markdown(
        (
            '<div class="sota-hero">'
            f'<div class="sota-hero-kicker">{_escape_html(kicker)}</div>'
            f'<div class="sota-hero-title">{_escape_html(title)}</div>'
            f'<div class="sota-hero-copy">{_escape_html(copy)}</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


def render_app_header(current_view: str) -> None:
    nav_links = []
    for key, item in APP_NAV_ITEMS.items():
        active_class = " is-active" if key == current_view else ""
        href = f"?{urlencode({'view': key})}"
        nav_links.append(
            '<a class="sota-appbar-link'
            f'{active_class}" href="{href}">'
            f'<span class="sota-appbar-icon">{item["icon"]}</span>'
            f'<span>{_escape_html(item["label"])}</span>'
            '</a>'
        )

    st.markdown(
        (
            '<div class="sota-appbar">'
            '<div class="sota-appbar-inner">'
            '<div class="sota-appbar-brand">'
            '<span class="sota-appbar-mark"></span>'
            '<div class="sota-appbar-copy">'
            '<div class="sota-appbar-title">SOTA Planning Viz</div>'
            '<div class="sota-appbar-subtitle">Operación, clasificación y forecast sobre el canónico experimental.</div>'
            '</div>'
            '</div>'
            f'<div class="sota-appbar-links">{"".join(nav_links)}</div>'
            '<div class="sota-appbar-pill">planning_core lab</div>'
            '</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


def render_sidebar_toggle_fab() -> None:
    compact = st.session_state.get("sidebar_compact", False)
    label = "▸" if compact else "◂"
    if st.button(label, key="sidebar_toggle_fab", help="Mostrar u ocultar navegación"):
        st.session_state["sidebar_compact"] = not compact
        st.rerun()


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


@st.cache_data(ttl=600, show_spinner="Cargando")
def get_dashboard_data(_service: PlanningService) -> dict:
    transactions = _service.repository.load_table("transactions")
    inventory = _service.repository.load_table("inventory_snapshot")
    receipts = _service.repository.load_table("purchase_receipts")
    transfers = _service.repository.load_table("internal_transfers")
    catalog = _service.repository.load_table("product_catalog")

    daily_sales = (
        transactions.groupby("date", as_index=False)[["quantity", "total_amount"]]
        .sum()
        .rename(columns={"quantity": "sales_qty", "total_amount": "sales_amount"})
    )
    daily_inventory = (
        inventory.groupby("snapshot_date", as_index=False)[["on_hand_qty", "on_order_qty"]]
        .sum()
        .rename(columns={"snapshot_date": "date"})
    )
    daily_receipts = (
        receipts.groupby("receipt_date", as_index=False)[["received_qty"]]
        .sum()
        .rename(columns={"receipt_date": "date", "received_qty": "purchase_receipt_qty"})
    )
    daily_transfers_out = (
        transfers.groupby("ship_date", as_index=False)[["transfer_qty"]]
        .sum()
        .rename(columns={"ship_date": "date", "transfer_qty": "transfer_out_qty"})
    )
    daily_transfers_in = (
        transfers.dropna(subset=["receipt_date"])
        .groupby("receipt_date", as_index=False)[["transfer_qty"]]
        .sum()
        .rename(columns={"receipt_date": "date", "transfer_qty": "transfer_in_qty"})
    )

    network_ts = daily_inventory.merge(daily_sales, on="date", how="left")
    network_ts = network_ts.merge(daily_receipts, on="date", how="left")
    network_ts = network_ts.merge(daily_transfers_in, on="date", how="left")
    network_ts = network_ts.merge(daily_transfers_out, on="date", how="left")

    numeric_columns = [
        "sales_qty",
        "sales_amount",
        "purchase_receipt_qty",
        "transfer_in_qty",
        "transfer_out_qty",
        "on_hand_qty",
        "on_order_qty",
    ]
    network_ts[numeric_columns] = network_ts[numeric_columns].fillna(0)
    network_ts[[
        "sales_qty",
        "purchase_receipt_qty",
        "transfer_in_qty",
        "transfer_out_qty",
        "on_hand_qty",
        "on_order_qty",
    ]] = network_ts[[
        "sales_qty",
        "purchase_receipt_qty",
        "transfer_in_qty",
        "transfer_out_qty",
        "on_hand_qty",
        "on_order_qty",
    ]].astype(int)
    network_ts["sales_amount"] = network_ts["sales_amount"].astype(float)
    network_ts = network_ts.sort_values("date").reset_index(drop=True)

    latest_snapshot_date = inventory["snapshot_date"].max()
    latest_inventory = inventory.loc[inventory["snapshot_date"] == latest_snapshot_date].copy()

    inventory_by_location = (
        latest_inventory.groupby("location", as_index=False)[["on_hand_qty", "on_order_qty"]]
        .sum()
        .sort_values("on_hand_qty", ascending=False)
        .reset_index(drop=True)
    )
    inventory_value_by_location = (
        latest_inventory.merge(catalog[["sku", "cost"]], on="sku", how="left")
        .assign(inventory_value=lambda df: df["on_hand_qty"] * df["cost"].fillna(0))
        .groupby("location", as_index=False)["inventory_value"]
        .sum()
        .sort_values("inventory_value", ascending=False)
        .reset_index(drop=True)
    )
    sales_by_location = (
        transactions.groupby("location", as_index=False)[["quantity", "total_amount"]]
        .sum()
        .rename(columns={"quantity": "sales_qty", "total_amount": "sales_amount"})
        .sort_values("sales_amount", ascending=False)
        .reset_index(drop=True)
    )

    return {
        "network_timeseries": network_ts,
        "inventory_by_location": inventory_by_location,
        "inventory_value_by_location": inventory_value_by_location,
        "sales_by_location": sales_by_location,
        "latest_snapshot_date": latest_snapshot_date,
        "sales_qty_total": int(transactions["quantity"].sum()) if not transactions.empty else 0,
        "sales_amount_total": float(transactions["total_amount"].sum()) if not transactions.empty else 0.0,
        "on_hand_total": int(latest_inventory["on_hand_qty"].sum()) if not latest_inventory.empty else 0,
        "on_order_total": int(latest_inventory["on_order_qty"].sum()) if not latest_inventory.empty else 0,
        "inventory_value_total": float(inventory_value_by_location["inventory_value"].sum()) if not inventory_value_by_location.empty else 0.0,
    }


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


def build_metric_bar_figure(
    dataframe: pd.DataFrame,
    x_col: str,
    y_col: str,
    title: str,
    color: str,
    y_title: str,
    horizontal: bool = False,
) -> go.Figure:
    figure = go.Figure()
    if horizontal:
        figure.add_trace(
            go.Bar(
                x=dataframe[y_col],
                y=dataframe[x_col],
                orientation="h",
                marker_color=color,
                text=dataframe[y_col],
                textposition="auto",
            )
        )
    else:
        figure.add_trace(
            go.Bar(
                x=dataframe[x_col],
                y=dataframe[y_col],
                marker_color=color,
                text=dataframe[y_col],
                textposition="outside",
            )
        )

    figure.update_layout(
        title=title,
        xaxis_title="" if horizontal else x_col,
        yaxis_title=y_title if not horizontal else "",
        margin=dict(l=20, r=20, t=60, b=20),
        height=360,
        showlegend=False,
    )
    if horizontal:
        figure.update_yaxes(autorange="reversed")
        figure.update_xaxes(title=y_title)
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
    aggregated[integer_columns + ["sales_amount"]] = aggregated[integer_columns + ["sales_amount"]].fillna(0)
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

    comparison_frame = comparison_frame.sort_values("date").reset_index(drop=True)
    value_columns = [column for column in comparison_frame.columns if column != "date"]
    comparison_frame[value_columns] = comparison_frame[value_columns].fillna(0)
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


def get_profile_for_sku(
    service: PlanningService,
    selected_sku: str,
    classification_df: pd.DataFrame | None = None,
) -> pd.Series | None:
    if classification_df is not None and not classification_df.empty:
        sku_row = classification_df[classification_df["sku"] == selected_sku]
        if not sku_row.empty:
            return sku_row.iloc[0]

    fallback = service.classify_single_sku(selected_sku)
    if fallback is None:
        return None
    return pd.Series(fallback)


def render_sku_header_card(summary: dict, profile: pd.Series | None, currency_code: str) -> None:
    catalog = summary["catalog"]
    badge_specs: list[tuple[str, str]] = []

    if profile is not None:
        abc_class = profile.get("abc_class")
        xyz_class = profile.get("xyz_class")
        sb_class = profile.get("sb_class")
        lifecycle = profile.get("lifecycle")
        quality_score = profile.get("quality_score")

        if abc_class:
            badge_specs.append((f"ABC {abc_class}", ABC_COLORS.get(abc_class, "#7f8c8d")))
        if xyz_class:
            badge_specs.append((f"XYZ {xyz_class}", XYZ_COLORS.get(xyz_class, "#7f8c8d")))
        if sb_class:
            badge_specs.append((sb_class.upper(), SB_COLORS.get(sb_class, "#7f8c8d")))
        if lifecycle:
            badge_specs.append((lifecycle.capitalize(), LIFECYCLE_COLORS.get(lifecycle, "#7f8c8d")))
        if quality_score is not None:
            badge_specs.append((f"Quality {float(quality_score):.2f}", "#7a6a55"))

    badge_html = "".join(
        f'<span class="sota-badge" style="background:{color};">{_escape_html(label)}</span>'
        for label, color in badge_specs
    )

    subtitle = (
        f"{catalog.get('category', '')} | {catalog.get('supplier', '')} | "
        f"MOQ {catalog.get('moq', '—')} | Precio base {format_currency(catalog.get('base_price', 0))} {currency_code}"
    )
    st.markdown(
        (
            '<div class="sota-header-card">'
            '<div class="sota-overline">Producto</div>'
            f'<div class="sota-title">{_escape_html(selected_sku := summary["sku"])} · {_escape_html(catalog.get("name", ""))}</div>'
            f'<div class="sota-subtitle">{_escape_html(subtitle)}</div>'
            f'<div class="sota-badge-row">{badge_html}</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


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


@st.cache_data(ttl=600, show_spinner="Clasificando")
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
        st.session_state["selected_sku"] = selected_sku
        st.session_state["classification_selected_sku"] = selected_sku
        st.session_state["classification_view"] = "Detalle"
        st.rerun()


def _render_sku_section_resumen(
    service: PlanningService,
    selected_sku: str,
    summary: dict,
    profile: pd.Series | None,
):
    st.markdown('<div class="sota-section-note">Resumen rápido del SKU: desempeño, stock y clasificación base.</div>', unsafe_allow_html=True)

    if profile is not None:
        summary_cols = st.columns(5)
        summary_cols[0].metric("Clase S-B", profile.get("sb_class", "—"))
        summary_cols[1].metric("ABC-XYZ", profile.get("abc_xyz") or "—")
        summary_cols[2].metric("Lifecycle", profile.get("lifecycle", "—"))
        summary_cols[3].metric("Calidad", f"{float(profile.get('quality_score', 0.0)):.2f}")
        summary_cols[4].metric("Vol. censurado", f"{float(profile.get('censored_demand_pct', 0.0)):.1%}")

    network_ts = service.sku_timeseries(selected_sku)
    network_ts = apply_temporality_filter(network_ts, "Ultimos 365 dias")

    if network_ts.empty:
        st.info("No hay serie operacional disponible para este SKU.")
        return

    chart_cols = st.columns(2)
    with chart_cols[0]:
        flow_fig = build_line_figure(
            network_ts,
            f"Flujo agregado de red — {selected_sku}",
            [
                ("sales_qty", "Ventas"),
                ("purchase_receipt_qty", "Recepcion compra"),
            ],
        )
        st.plotly_chart(flow_fig, use_container_width=True)

    with chart_cols[1]:
        stock_fig = build_line_figure(
            network_ts,
            f"Stock agregado de red — {selected_sku}",
            [
                ("on_hand_qty", "On hand"),
                ("on_order_qty", "On order"),
            ],
        )
        st.plotly_chart(stock_fig, use_container_width=True)

    supply_cols = st.columns(2)
    with supply_cols[0]:
        st.write("Recepciones recientes")
        receipts_df = service.purchase_receipts_for_sku(selected_sku).tail(10)
        render_copyable_dataframe(receipts_df, f"sku_receipts_recent_{selected_sku}", height=260)

    with supply_cols[1]:
        st.write("Transferencias recientes")
        transfers_df = service.internal_transfers_for_sku(selected_sku).tail(10)
        render_copyable_dataframe(transfers_df, f"sku_transfers_recent_{selected_sku}", height=260)


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
            .loc[lambda df: df["is_stockout_no_sale"].eq(True)]
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

    with st.spinner("Calculando"):
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
    winner_model = result.get("model")
    winner_metrics = result.get("backtest", {}).get(winner_model, {}) if winner_model else {}

    kpi_cols = st.columns(6)
    kpi_cols[0].metric("Estado", status)
    kpi_cols[1].metric("Modelo ganador", winner_model or "—")
    mase_val = result.get("mase")
    mase_str = f"{mase_val:.3f}" if (mase_val is not None and not math.isnan(mase_val)) else "N/A"
    kpi_cols[2].metric("MASE", mase_str)
    wape_val = winner_metrics.get("wape")
    wape_str = f"{wape_val:.1%}" if (wape_val is not None and not math.isnan(wape_val)) else "N/A"
    kpi_cols[3].metric("WAPE", wape_str)
    rmse_val = winner_metrics.get("rmse")
    rmse_str = f"{rmse_val:.3f}" if (rmse_val is not None and not math.isnan(rmse_val)) else "N/A"
    kpi_cols[4].metric("RMSE", rmse_str)
    kpi_cols[5].metric("Horizonte", f"{result.get('h', h)} periodos")

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
            summary_df = summary_df.reindex(
                columns=["model", "mase", "wape", "rmse", "bias", "n_windows", "status"],
                fill_value=np.nan,
            )
            render_copyable_dataframe(
                summary_df,
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

    profile = get_profile_for_sku(service, selected_sku, classification_df)

    # --- Header: boton volver + card principal ---
    currency_code = service.currency_code()
    action_cols = st.columns([9.6, 1.4])
    with action_cols[1]:
        st.markdown('<div class="sota-top-actions"></div>', unsafe_allow_html=True)
        if st.button("← Volver", key=back_callback_key, type="tertiary"):
            return "back"
    render_sku_header_card(summary, profile, currency_code)

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
    section_options = {
        "resumen": "Resumen",
        "operacion": "Operación",
        "clasificacion": "Clasificación",
        "forecast": "Forecast",
    }
    sku_section = st.radio(
        "Sección del producto",
        options=list(section_options.keys()),
        format_func=lambda value: section_options[value],
        horizontal=True,
        key="sku_detail_section",
        label_visibility="visible",
    )

    if sku_section == "resumen":
        _render_sku_section_resumen(service, selected_sku, summary, profile)
    elif sku_section == "clasificacion":
        _render_sku_section_clasificacion(service, selected_sku, classification_df)
    elif sku_section == "forecast":
        _render_sku_section_forecast(service, selected_sku)
    else:
        _render_sku_section_operacional(service, selected_sku, summary)

    return None


def render_classification_tab(service: PlanningService):
    """Tab principal de clasificacion de demanda."""
    if "classification_view" not in st.session_state:
        st.session_state["classification_view"] = "Panorama"

    current_view = st.session_state["classification_view"]

    if current_view == "Panorama":
        render_centered_hero(
            "Clasificación oficial",
            "Mapa analítico del catálogo",
            "Explora el catálogo ya clasificado por la lógica oficial del repo, filtra segmentos y navega al detalle del producto sin perder contexto.",
        )
    else:
        st.markdown("## Clasificación")
        st.markdown(
            '<div class="sota-section-note">Vista analítica del catálogo clasificado, con filtros y navegación al detalle del producto.</div>',
            unsafe_allow_html=True,
        )

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


def render_dashboard_tab(service: PlanningService, classification_df: pd.DataFrame):
    overview = service.dataset_overview()
    quality = service.dataset_health()
    dashboard = get_dashboard_data(service)

    st.markdown("## Dashboard")
    st.markdown(
        '<div class="sota-section-note">Lectura agregada de la operación simulada: demanda, inventario, mix y exposición por location.</div>',
        unsafe_allow_html=True,
    )

    control_cols = st.columns([1.2, 1.2, 4.6])
    with control_cols[0]:
        dashboard_granularity = st.selectbox(
            "Granularidad",
            list(GRANULARITY_FREQUENCIES.keys()),
            index=2,
            key="dashboard_granularity",
        )
    with control_cols[1]:
        dashboard_temporality = st.selectbox(
            "Temporalidad",
            list(TEMPORALITY_WINDOWS.keys()),
            index=3,
            key="dashboard_temporality",
        )

    timeline = aggregate_timeseries(dashboard["network_timeseries"], dashboard_granularity)
    timeline = apply_temporality_filter(timeline, dashboard_temporality)
    central_location = overview.get("central_location")
    central_on_hand = 0
    if central_location:
        latest_inv = dashboard["inventory_by_location"]
        central_row = latest_inv[latest_inv["location"] == central_location]
        if not central_row.empty:
            central_on_hand = int(central_row["on_hand_qty"].iloc[0])

    kpi_cols = st.columns(6)
    kpi_cols[0].metric("Ventas totales", f"{dashboard['sales_qty_total']:,}")
    kpi_cols[1].metric("Revenue total", format_currency(dashboard["sales_amount_total"]))
    kpi_cols[2].metric("Inventario on hand", f"{dashboard['on_hand_total']:,}")
    kpi_cols[3].metric("Inventario on order", f"{dashboard['on_order_total']:,}")
    kpi_cols[4].metric("Stock CD central", f"{central_on_hand:,}")
    kpi_cols[5].metric("SKUs con censura", int(classification_df["has_censored_demand"].sum()))

    chart_cols = st.columns(2)
    with chart_cols[0]:
        flow_fig = build_line_figure(
            timeline,
            f"Flujo agregado de red ({dashboard_granularity}, {dashboard_temporality})",
            [
                ("sales_qty", "Ventas"),
                ("purchase_receipt_qty", "Recepciones compra"),
            ],
        )
        st.plotly_chart(flow_fig, use_container_width=True)

    with chart_cols[1]:
        stock_fig = build_line_figure(
            timeline,
            f"Inventario agregado de red ({dashboard_granularity}, {dashboard_temporality})",
            [
                ("on_hand_qty", "On hand"),
                ("on_order_qty", "On order"),
            ],
        )
        st.plotly_chart(stock_fig, use_container_width=True)

    dist_cols = st.columns(2)
    with dist_cols[0]:
        inv_fig = build_metric_bar_figure(
            dashboard["inventory_by_location"],
            "location",
            "on_hand_qty",
            f"Inventario actual por location ({dashboard['latest_snapshot_date'].date().isoformat()})",
            color="#6d7f61",
            y_title="On hand",
            horizontal=True,
        )
        st.plotly_chart(inv_fig, use_container_width=True)

    with dist_cols[1]:
        sales_loc_fig = build_metric_bar_figure(
            dashboard["sales_by_location"],
            "location",
            "sales_amount",
            "Revenue acumulado por location",
            color="#b97d4b",
            y_title="Revenue",
            horizontal=True,
        )
        st.plotly_chart(sales_loc_fig, use_container_width=True)

    mix_cols = st.columns(3)
    with mix_cols[0]:
        abc_fig = build_distribution_bar_figure(
            classification_df,
            "abc_class",
            "Mix ABC",
            ABC_COLORS,
        )
        st.plotly_chart(abc_fig, use_container_width=True)
    with mix_cols[1]:
        sb_fig = build_distribution_bar_figure(
            classification_df,
            "sb_class",
            "Mix Syntetos-Boylan",
            SB_COLORS,
        )
        st.plotly_chart(sb_fig, use_container_width=True)
    with mix_cols[2]:
        inventory_value_fig = build_metric_bar_figure(
            dashboard["inventory_value_by_location"],
            "location",
            "inventory_value",
            "Valor inventario por location",
            color="#8e6a4f",
            y_title=f"Valor ({overview['currency']})",
            horizontal=True,
        )
        st.plotly_chart(inventory_value_fig, use_container_width=True)

    table_cols = st.columns(2)
    with table_cols[0]:
        st.write("Top SKUs por revenue")
        top_revenue = (
            classification_df.sort_values("total_revenue", ascending=False)
            .loc[:, ["sku", "abc_class", "sb_class", "total_revenue", "quality_score"]]
            .head(10)
        )
        render_copyable_dataframe(top_revenue, "dashboard_top_revenue", height=330)

    with table_cols[1]:
        st.write("SKUs con mayor censura")
        top_censored = (
            classification_df.sort_values(
                ["censored_demand_pct", "censored_pct", "total_revenue"],
                ascending=[False, False, False],
            )
            .loc[:, ["sku", "abc_class", "sb_class", "censored_demand_pct", "censored_pct", "quality_score"]]
            .head(10)
        )
        render_copyable_dataframe(top_censored, "dashboard_top_censored", height=330)

    with st.expander("Dataset técnico y chequeos básicos"):
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
        st.write("Chequeos básicos")
        render_copyable_dataframe(
            pd.DataFrame(
                [{"check": check_name, "value": value} for check_name, value in quality.items()]
            ),
            "dataset_quality_checks",
        )


def render_catalog_browser(service: PlanningService, classification_df: pd.DataFrame) -> None:
    st.markdown("## Catálogo")
    st.markdown(
        '<div class="sota-section-note">Listado navegable de productos con clasificación oficial embebida.</div>',
        unsafe_allow_html=True,
    )

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
    browser_dataframe = browser_dataframe.merge(
        classification_df.loc[:, ["sku", "abc_class", "xyz_class", "abc_xyz", "sb_class", "quality_score"]],
        on="sku",
        how="left",
    )
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
            "abc_class": st.column_config.TextColumn("ABC", width="small"),
            "xyz_class": st.column_config.TextColumn("XYZ", width="small"),
            "sb_class": st.column_config.TextColumn("S-B", width="small"),
            "category": st.column_config.TextColumn("Categoria", width="medium"),
            "supplier": st.column_config.TextColumn("Proveedor", width="medium"),
            "brand": st.column_config.TextColumn("Marca", width="small"),
            "base_price": st.column_config.NumberColumn("Precio base", format="%.0f"),
            "moq": st.column_config.NumberColumn("MOQ", format="%d"),
            "quality_score": st.column_config.NumberColumn("Quality", format="%.2f"),
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
        st.session_state["catalog_view"] = "Detalle SKU"
        st.rerun()


def render_catalog_tab(service: PlanningService, classification_df: pd.DataFrame):
    if "catalog_view" not in st.session_state:
        st.session_state["catalog_view"] = "Listado"

    current_sku_view = st.session_state["catalog_view"]

    if current_sku_view == "Listado":
        render_catalog_browser(service, classification_df)
        return

    selected_sku = st.session_state.get("selected_sku")
    if not selected_sku:
        st.info("Selecciona un SKU desde el listado para ver el detalle.")
        st.session_state["catalog_view"] = "Listado"
        st.rerun()

    result = render_sku_detail_unified(
        service, selected_sku,
        back_callback_key="back_to_catalog_browser",
        classification_df=classification_df,
    )
    if result == "back":
        st.session_state["catalog_view"] = "Listado"
        st.rerun()


def render_future_view(title: str, description: str):
    st.markdown(f"## {title}")
    st.info(description)


def render_sidebar_navigation(service: PlanningService) -> str:
    overview = service.dataset_overview()
    compact = st.session_state.get("sidebar_compact", False)
    nav_options_full = {
        "dashboard": "◫ Dashboard",
        "catalogo": "◻ Catalogo",
        "clasificacion": "◎ Clasificacion",
        "alertas": "◇ Alertas",
        "escenarios": "△ Escenarios",
    }
    nav_options_compact = {
        "dashboard": "◫",
        "catalogo": "◻",
        "clasificacion": "◎",
        "alertas": "◇",
        "escenarios": "△",
    }
    nav_options = nav_options_compact if compact else nav_options_full

    with st.sidebar:
        if not compact:
            st.markdown('<div class="sota-sidebar-copy">Navegacion principal</div>', unsafe_allow_html=True)
        selected_key = st.radio(
            "Navegación",
            list(nav_options.keys()),
            format_func=lambda value: nav_options[value],
            key="active_view_sidebar",
        )
        if not compact:
            st.markdown("---")
            st.markdown('<div class="sota-sidebar-meta"><strong>Dataset activo</strong></div>', unsafe_allow_html=True)
            st.caption(
                f"{overview.get('profile', 'dataset')} | {overview.get('currency', '—')} | "
                f"{overview['sku_count']} SKUs | {overview['location_count']} locaciones"
            )
            if overview.get("central_location"):
                st.caption(f"Nodo central: {overview['central_location']}")

            active_sku = st.session_state.get("selected_sku") or st.session_state.get("classification_selected_sku")
            if active_sku:
                st.markdown('<div class="sota-sidebar-meta"><strong>SKU activo</strong></div>', unsafe_allow_html=True)
                st.markdown(
                    f'<span class="sota-sidebar-chip">{_escape_html(active_sku)}</span>',
                    unsafe_allow_html=True,
                )

    return selected_key


def main():
    st.set_page_config(page_title="SOTA Planning Viz", page_icon=":bar_chart:", layout="wide")
    service = get_service()
    inject_app_styles()

    current_view = st.query_params.get("view", "dashboard")
    if isinstance(current_view, list):
        current_view = current_view[0] if current_view else "dashboard"
    if current_view not in APP_NAV_ITEMS:
        current_view = "dashboard"

    render_app_header(current_view)

    official_classification_df = get_classification_data(service, granularity="M")

    if current_view == "dashboard":
        render_dashboard_tab(service, official_classification_df)
    elif current_view == "catalogo":
        render_catalog_tab(service, official_classification_df)
    elif current_view == "clasificacion":
        render_classification_tab(service)
    elif current_view == "alertas":
        render_future_view(
            "Alertas",
            "Vista reservada para alertas operacionales y de calidad. Aún no está implementada en esta UI experimental.",
        )
    else:
        render_future_view(
            "Escenarios",
            "Vista reservada para escenarios, simulaciones y futuras capas de decisión. Aún no está implementada.",
        )


if __name__ == "__main__":
    main()
