import streamlit as st
import pandas as pd
import plotly.express as px
import urllib.request
from sqlalchemy import create_engine

st.set_page_config(page_title="Cuentas Contables", layout="wide")

# ── Connection ────────────────────────────────────────────────────────────────
@st.cache_resource
def get_engine():
    creds = st.secrets["db"]
    params = urllib.parse.quote_plus(
        f"DRIVER={{{creds['driver']}}};"
        f"SERVER={creds['server']};"
        f"DATABASE={creds['database']};"
        f"UID={creds['username']};"
        f"PWD={creds['password']};"
        f"TrustServerCertificate=yes;"  
    )
    return create_engine(f"mssql+pyodbc:///?odbc_connect={params}")

# ── Query ─────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_data():
    query = """
    WITH cuenta AS (
        SELECT
            cta_contable_skey,
            fecha_saldo,
            SUM(TRY_CAST(debe  AS DECIMAL(18,2))) AS suma_debe_diario,
            SUM(TRY_CAST(haber AS DECIMAL(18,2))) AS suma_haber_diario,
            SUM(TRY_CAST(saldo AS DECIMAL(18,2))) AS suma_saldo_diario
        FROM fac_ctas_contables
        WHERE debe IS NOT NULL OR haber IS NOT NULL
        GROUP BY cta_contable_skey, fecha_saldo
    )
    SELECT TOP 10000
        c.fecha_saldo,
        c.fecha_saldo / 10000        AS anio,
        (c.fecha_saldo / 100) % 100  AS mes,
        d.nivel,
        d.clasificacion,
        d.rubro,
        d.nom_empresa,
        d.cod_cta,
        d.nom_cta,
        d.deudor_acreedor,
        c.suma_debe_diario,
        c.suma_haber_diario,
        c.suma_saldo_diario
    FROM cuenta c
    INNER JOIN dim_ctas_contables d
        ON c.cta_contable_skey = d.cta_contable_skey
    ORDER BY c.fecha_saldo DESC
    """
    df = pd.read_sql(query, get_engine())

    df["fecha_dt"] = pd.to_datetime(df["fecha_saldo"].astype(str), format="%Y%m%d")

    return df


# ── App ───────────────────────────────────────────────────────────────────────
st.title("📊 Dashboard Cuentas Contables")

with st.spinner("Cargando datos..."):
    df = load_data()

# ── Sidebar Filters ───────────────────────────────────────────────────────────
st.sidebar.header("Filtros")
empresas = st.sidebar.multiselect("Empresa",  sorted(df["nom_empresa"].dropna().unique()))
rubros   = st.sidebar.multiselect("Rubro",    sorted(df["rubro"].dropna().unique()))
anios    = st.sidebar.multiselect("Año",      sorted(df["anio"].unique()))

filtered = df.copy()
if empresas: filtered = filtered[filtered["nom_empresa"].isin(empresas)]
if rubros:   filtered = filtered[filtered["rubro"].isin(rubros)]
if anios:    filtered = filtered[filtered["anio"].isin(anios)]

if st.sidebar.button("🔄 Refrescar datos"):
    st.cache_data.clear()
    st.rerun()

# ── KPIs ──────────────────────────────────────────────────────────────────────
st.subheader("Resumen")
col1, col2, col3 = st.columns(3)
col1.metric("Total Debe",  f"{filtered['suma_debe_diario'].sum():,.2f}")
col2.metric("Total Haber", f"{filtered['suma_haber_diario'].sum():,.2f}")
col3.metric("Saldo Neto",  f"{filtered['suma_saldo_diario'].sum():,.2f}")

st.divider()

# ── Debe vs Haber por Mes + Saldo por Rubro ───────────────────────────────────
col4, col5 = st.columns(2)

with col4:
    st.subheader("Debe vs Haber por Mes")
    monthly = (
        filtered.groupby(["anio", "mes"])[["suma_debe_diario", "suma_haber_diario"]]
        .sum().reset_index()
    )
    monthly["periodo"] = (
        monthly["anio"].astype(str) + "-" +
        monthly["mes"].astype(str).str.zfill(2)
    )
    fig_bar = px.bar(
        monthly, x="periodo",
        y=["suma_debe_diario", "suma_haber_diario"],
        barmode="group",
        labels={"value": "Monto", "variable": ""},
        color_discrete_map={
            "suma_debe_diario":  "#636EFA",
            "suma_haber_diario": "#EF553B"
        }
    )
    st.plotly_chart(fig_bar, use_container_width=True)

with col5:
    st.subheader("Saldo por Rubro")
    by_rubro = (
        filtered.groupby("rubro")["suma_saldo_diario"]
        .sum().reset_index()
    )
    fig_pie = px.pie(
        by_rubro, names="rubro", values="suma_saldo_diario",
        hole=0.3  # donut style
    )
    st.plotly_chart(fig_pie, use_container_width=True)

st.divider()

# ── Time Series ───────────────────────────────────────────────────────────────
st.subheader("📈 Series de Tiempo")

col_metric, col_group, col_ma = st.columns(3)

with col_metric:
    metrica = st.selectbox(
        "Métrica",
        ["suma_debe_diario", "suma_haber_diario", "suma_saldo_diario"]
    )

with col_group:
    agrupador = st.selectbox(
        "Agrupar por",
        ["Total", "rubro", "clasificacion", "nom_empresa", "deudor_acreedor"]
    )

with col_ma:
    ventana_ma = st.selectbox(
        "Media móvil",
        [None, 7, 30, 90],
        format_func=lambda x: "Sin media móvil" if x is None else f"{x} días"
    )

# Build time series
if agrupador == "Total":
    ts = filtered.groupby("fecha_dt")[metrica].sum().reset_index()
    fig_ts = px.line(
        ts, x="fecha_dt", y=metrica,
        title=f"{metrica} en el tiempo",
        labels={"fecha_dt": "Fecha", metrica: "Monto"}
    )
else:
    ts = (
        filtered.groupby(["fecha_dt", agrupador])[metrica]
        .sum().reset_index()
    )
    fig_ts = px.line(
        ts, x="fecha_dt", y=metrica, color=agrupador,
        title=f"{metrica} en el tiempo por {agrupador}",
        labels={"fecha_dt": "Fecha", metrica: "Monto"}
    )

# Rolling average overlay 
if ventana_ma and agrupador == "Total":
    ts["media_movil"] = ts[metrica].rolling(ventana_ma).mean()
    fig_ts.add_scatter(
        x=ts["fecha_dt"], y=ts["media_movil"],
        name=f"Media móvil {ventana_ma}d",
        line=dict(dash="dash", color="orange")
    )

fig_ts.update_xaxes(
    rangeslider_visible=True,
    rangeselectorbuttons=[
        dict(count=1,  label="1M",  step="month", stepmode="backward"),
        dict(count=3,  label="3M",  step="month", stepmode="backward"),
        dict(count=6,  label="6M",  step="month", stepmode="backward"),
        dict(count=1,  label="YTD", step="year",  stepmode="todate"),
        dict(step="all", label="Todo")
    ]
)
fig_ts.update_layout(hovermode="x unified")

st.plotly_chart(fig_ts, use_container_width=True)

st.divider()

# ── Raw Data ──────────────────────────────────────────────────────────────────
st.subheader("📋 Datos")
st.dataframe(
    filtered.drop(columns=["fecha_dt"]),  
    use_container_width=True
)