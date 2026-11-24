# ==============================================================================
# TEMA VISUAL del Portal VoC (colores, CSS base y plantilla de gráficos)
# ==============================================================================
import streamlit as st

# Paleta (la misma identidad: azul noche + amarillo)
C = {
    "navy": "#1A1A2E", "amarillo": "#FFD600", "naranja": "#FF8C00",
    "ok": "#388E3C", "alerta": "#F57F17", "mal": "#D32F2F", "gris": "#757575",
    "fondo": "#F5F6FA", "tarjeta": "#FFFFFF", "linea": "#E3E6EE", "texto": "#1A1A2E",
}
# Colores categóricos (dealers / asesores): distinguibles y con buen contraste sobre blanco
COLORWAY = ["#1976D2", "#D32F2F", "#388E3C", "#7B1FA2", "#F57C00", "#00838F", "#5D4037", "#455A64",
            "#C2185B", "#689F38", "#512DA8", "#0097A7"]

CSS_BASE = """@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;900&family=Barlow:wght@400;500;600&display=swap');
html,body,[class*="css"]{font-family:'Barlow',sans-serif!important}
[data-testid="stSidebar"]{background:#1A1A2E!important;min-width:230px!important;max-width:230px!important}
[data-testid="stSidebar"] *{color:#E0E0E0!important}
[data-testid="stSidebarNav"]{display:none!important}
[data-testid="stSidebar"] .stButton>button{background:transparent!important;border:none!important;border-left:3px solid transparent!important;border-radius:0!important;text-align:left!important;width:100%!important;padding:8px 16px!important;font-family:'Barlow Condensed',sans-serif!important;font-size:15px!important;font-weight:600!important;transition:all 0.15s ease!important}
[data-testid="stSidebar"] .stButton>button:hover{background:rgba(255,255,255,0.08)!important;border-left:3px solid #FFD600!important}
[data-testid="stSidebar"] .stButton>button p,[data-testid="stSidebar"] .stButton>button:hover p{font-size:15px!important}
[data-testid="stSidebar"] hr{border-color:rgba(255,255,255,0.12)!important}
:root{color-scheme:light}
.stApp,[data-testid="stAppViewContainer"],[data-testid="stMain"]{background:#F5F6FA!important;color:#1A1A2E}
.block-container{padding-top:1rem!important;max-width:100%!important}
.ciudad-banner{background:#1A1A2E;border-radius:10px;padding:12px 20px;margin-bottom:16px;display:flex;align-items:center;gap:16px}
.ciudad-label{font-family:'Barlow Condensed',sans-serif;font-size:13px;font-weight:700;color:#FFD600;letter-spacing:1px;text-transform:uppercase;white-space:nowrap}
.seccion-titulo{font-family:'Barlow Condensed',sans-serif;font-size:26px;font-weight:900;color:#1A1A2E;letter-spacing:1px;text-transform:uppercase;border-bottom:3px solid #FFD600;padding-bottom:6px;margin-bottom:16px}
.kpi-card{background:white;border-radius:10px;padding:14px 18px;box-shadow:0 2px 8px rgba(0,0,0,0.08);border-top:4px solid #1A1A2E;text-align:center}
.kpi-label{font-family:'Barlow Condensed',sans-serif;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#666;margin-bottom:2px}
.kpi-value{font-family:'Barlow Condensed',sans-serif;font-size:34px;font-weight:900;color:#1A1A2E;line-height:1}
.kpi-sub{font-size:11px;color:#999;margin-top:2px}
.chart-box{background:white;border-radius:10px;padding:16px 20px;box-shadow:0 2px 8px rgba(0,0,0,0.07);margin-bottom:16px}
.tabla-voc th{background:#1A1A2E!important;color:white!important;font-family:'Barlow Condensed',sans-serif;font-size:12px;font-weight:700;text-align:center;padding:7px 8px}
.tabla-voc{background:#fff;color:#1A1A1A}
.tabla-voc td{font-size:12px;text-align:center;padding:5px 8px;color:#1A1A1A;background:#fff}
.tabla-voc tr:nth-child(even) td{background:#F4F5F9}
.alerta-card{background:#fff3f3;border-left:5px solid #D32F2F;border-radius:6px;padding:10px 14px;margin-bottom:8px;font-family:'Barlow Condensed',sans-serif}
.alerta-card .an{font-size:15px;font-weight:700;color:#1A1A1A}
.alerta-card .av{font-size:20px;font-weight:900;color:#D32F2F}
.alerta-card .ag{font-size:12px;color:#888}
.table-scroll{overflow-x:auto;border-radius:8px}
div[data-testid="stSelectbox"] label p{font-weight:700!important;font-size:12px!important;color:#1A1A2E!important;text-transform:uppercase;letter-spacing:0.5px}
/* Contraste: textos del área principal legibles aunque el navegador use tema oscuro */
[data-testid="stMain"] [data-testid="stMarkdownContainer"]{color:#1A1A2E}
[data-testid="stMain"] [data-testid="stWidgetLabel"] p{color:#1A1A2E!important;font-weight:700!important}
[data-testid="stMain"] [data-testid="stCaptionContainer"],[data-testid="stMain"] [data-testid="stCaptionContainer"] p{color:#555!important}

/* Contraste: encabezados y textos generales siempre legibles, aunque el navegador use tema oscuro */
[data-testid="stMain"] h1,[data-testid="stMain"] h2,[data-testid="stMain"] h3,[data-testid="stMain"] h4,[data-testid="stMain"] h5{color:#1A1A2E}
[data-testid="stMain"] [data-baseweb="select"] > div{background:#fff;color:#1A1A2E}
/* Botones primarios: texto blanco sobre azul noche */
[data-testid^="stBaseButton-primary"],[data-testid^="stBaseButton-primary"] *{color:#fff!important}
[data-testid="stMain"] [data-testid="stRadio"] label p,[data-testid="stMain"] [data-testid="stCheckbox"] label p{color:#1A1A2E!important}

/* ---- Tarjetas y KPIs (fase 2) ---- */
div[class*="st-key-card"]{background:#fff;border-radius:12px;box-shadow:0 2px 10px rgba(26,26,46,.07);padding:14px 18px;margin-bottom:16px}
[data-testid="stElementContainer"]:has(.chart-box:empty){display:none}
.kpi2{background:#fff;border-radius:12px;padding:14px 18px 12px;box-shadow:0 2px 10px rgba(26,26,46,.07);border-left:5px solid var(--c,#1A1A2E);height:100%}
.kpi2 .l{font:700 11px 'Barlow Condensed',sans-serif;letter-spacing:1px;text-transform:uppercase;color:#666}
.kpi2 .v{font:900 38px/1.05 'Barlow Condensed',sans-serif;color:var(--c,#1A1A2E)}
.kpi2 .s{font-size:12px;color:#888;margin-top:2px}
.kpi2 .ch{margin-top:6px;min-height:20px}
.dchip{font-weight:700;font-size:12px;padding:1px 7px;border-radius:10px}
.dchip.up{background:#E8F5E9;color:#2E7D32}.dchip.down{background:#FFEBEE;color:#C62828}.dchip.flat{background:#EEE;color:#555}
.dref{font-size:11px;color:#888;margin-left:4px}
.banner-alerta{background:#FFF3F3;border-left:5px solid #D32F2F;border-radius:8px;padding:10px 16px;margin:0 0 14px;font:600 15px 'Barlow',sans-serif;color:#7A1C1C}
.card-titulo{font:900 18px 'Barlow Condensed',sans-serif;letter-spacing:.5px;text-transform:uppercase;color:#1A1A2E;margin:0 0 4px}
"""

def aplicar_estilos():
    """Inyecta el CSS base. Llamar una vez por ejecución del script."""
    st.markdown(f"<style>{CSS_BASE}</style>", unsafe_allow_html=True)

_PLOTLY_OK = None
def plotly_layout(fig, titulo=None, alto=380, leyenda_abajo=True):
    """Aplica la plantilla común a una figura Plotly (fondo blanco, tipografía Barlow, sin ruido).
    Plotly dibuja en el navegador: el servidor solo envía los datos (JSON), no imágenes."""
    fig.update_layout(
        title=dict(text=titulo, x=0.0, font=dict(size=16, color=C["texto"])) if titulo else None,
        height=alto, paper_bgcolor="white", plot_bgcolor="white",
        font=dict(family="Barlow, sans-serif", size=13, color=C["texto"]),
        colorway=COLORWAY, margin=dict(l=10, r=10, t=50 if titulo else 20, b=10),
        hoverlabel=dict(bgcolor="white", font_size=13, font_family="Barlow, sans-serif"),
        legend=dict(orientation="h", yanchor="top", y=-0.15, x=0) if leyenda_abajo else dict(),
    )
    fig.update_xaxes(showgrid=False, linecolor=C["linea"], zeroline=False)
    fig.update_yaxes(gridcolor=C["linea"], zeroline=False)
    return fig

def mostrar_plotly(fig, key=None):
    """st.plotly_chart con barra de herramientas mínima (la descarga PNG la hace el navegador)."""
    st.plotly_chart(fig, width="stretch", key=key, config={
        "displaylogo": False, "responsive": True,
        "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
        "toImageButtonOptions": {"format": "png", "scale": 2},
    })

def delta_chip(delta, unidad="pp", ref=""):
    """Chip de variación (▲ verde / ▼ rojo). delta en puntos porcentuales (pp) o unidades absolutas."""
    if delta is None or delta != delta:
        return ""
    cls = "up" if delta > 0.0001 else ("down" if delta < -0.0001 else "flat")
    flecha = {"up": "▲", "down": "▼", "flat": "■"}[cls]
    txt = f"{abs(delta):.1f} pp" if unidad == "pp" else f"{abs(delta):,.0f}"
    return f"<span class='dchip {cls}'>{flecha} {txt}</span><span class='dref'>{ref}</span>"

def kpi_card(label, valor, sub="", chip="", color="#1A1A2E"):
    """Tarjeta KPI: valor grande, subtítulo (meta) y chip de variación."""
    return (f"<div class='kpi2' style='--c:{color}'><div class='l'>{label}</div><div class='v'>{valor}</div>"
            f"<div class='s'>{sub}</div><div class='ch'>{chip}</div></div>")
