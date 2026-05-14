# ==============================================================================
# DASHBOARD VoC
# ==============================================================================

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from scipy.interpolate import make_interp_spline
import io, textwrap
import os
from pathlib import Path
from PIL import Image

st.set_page_config(page_title="Portal VoC Taiyo", layout="wide", initial_sidebar_state="expanded")

if st.session_state.get("datos_recargados"):
    st.toast("Base de datos actualizada con éxito")
    st.session_state["datos_recargados"] = False

CARPETA_DATOS = Path("datos_procesados")

COLOR_BARRAS   = "#3A3A3A"
COLOR_LINEA    = "#FFD600"
COLOR_OBJETIVO = "#FF8C00"

COLORES_DEALERS = {
    "Centro Nacional de Servicio": "#1976D2",
    "El Alto"                    : "#D32F2F",
    "Express"                    : "#388E3C",
    "SIN DEALER"                 : "#9E9E9E",
}
MARKERS_DEALERS = {
    "Centro Nacional de Servicio": "o",
    "El Alto"                    : "s",
    "Express"                    : "^",
    "SIN DEALER"                 : "D",
}

# ==============================================================================
# CARGA DE DATOS
# ==============================================================================
from google.cloud import storage
from google.oauth2 import service_account
import io

_MESES_NUM = {'Ene':1,'Feb':2,'Mar':3,'Abr':4,'May':5,'Jun':6,
              'Jul':7,'Ago':8,'Sep':9,'Oct':10,'Nov':11,'Dic':12}

def _reconstruir_mes_anio(df):
    """Reconstruye mes_anio y orden_mes en verbalizaciones que sólo tienen fytd+mes."""
    if df.empty:
        return df
    df = df.copy()

    # Forzar dtype object para poder asignar strings sin conflicto de tipos
    df["mes_anio"]  = df["mes_anio"].astype(object)
    df["orden_mes"] = df["orden_mes"].astype(object)

    mask = df["mes_anio"].isna() | df["mes_anio"].astype(str).str.contains(r"\?", na=True)
    if not mask.any():
        return df

    def _anio(row):
        try:
            fytd_y = int(str(row["fytd"]).replace("FYTD", "").strip())
            mes_n  = _MESES_NUM.get(str(row["mes"]).strip().capitalize(), 0)
            return fytd_y if mes_n >= 4 else fytd_y + 1
        except Exception:
            return 0

    reconstruido = df[mask].apply(
        lambda r: pd.Series({
            "mes_anio" : f"{str(r['mes']).strip().capitalize()} {_anio(r)}",
            "orden_mes": _anio(r) * 100 + _MESES_NUM.get(str(r["mes"]).strip().capitalize(), 0),
        }), axis=1
    )
    df.loc[mask, "mes_anio"]  = reconstruido["mes_anio"]
    df.loc[mask, "orden_mes"] = reconstruido["orden_mes"]
    return df

@st.cache_data(ttl=3600, show_spinner=False)
def cargar_datos():
    """
    Lee datos desde GCS soportando dos estructuras:

    Nueva (por ciudad/período):
      datos_procesados/{ciudad_slug}/{YYYY}_{Mes}/01..10_*.csv
      datos_procesados/{ciudad_slug}/11_atributos_especiales.csv
      datos_procesados/{ciudad_slug}/12_verbalizaciones.csv

    Antigua (compatibilidad hacia atrás — archivos planos en raíz):
      datos_procesados/01_isc_base.csv  …  datos_procesados/12_verbalizaciones.csv

    Global (siempre en raíz):
      datos_procesados/07_objetivos.csv
      datos_procesados/INFO_BASE.xlsx
    """
    BUCKET_NAME = "bk_voc"
    credentials_dict = st.secrets["gcp_service_account"]
    credentials = service_account.Credentials.from_service_account_info(credentials_dict)
    client = storage.Client(credentials=credentials)
    bucket = client.bucket(BUCKET_NAME)

    # Mapa nombre-de-archivo → clave interna
    ARCHIVOS_PERIODO = {
        "01_isc_base.csv"            : "isc_base",
        "02_tre_base.csv"            : "tre_base",
        "03_isc_mensual_dealer.csv"  : "isc_mensual",
        "04_tre_mensual_dealer.csv"  : "tre_mensual",
        "05_atributos_resumen.csv"   : "atributos",
        "06_pendientes.csv"          : "pendientes",
        "08_isc_mensual_aps.csv"     : "isc_aps",
        "09_tre_mensual_aps.csv"     : "tre_aps",
        "10_atributos_aps.csv"       : "atrib_aps",
        "12_verbalizaciones.csv"     : "verbaliz",
    }
    ARCHIVOS_CIUDAD = {
        "11_atributos_especiales.csv": "especiales",
    }
    TODOS_ARCHIVOS = {**ARCHIVOS_PERIODO, **ARCHIVOS_CIUDAD}

    acum = {clave: [] for clave in TODOS_ARCHIVOS.values()}

    # 07_objetivos.csv — siempre en raíz
    blob_obj = bucket.blob("datos_procesados/07_objetivos.csv")
    dfs_obj = pd.read_csv(io.BytesIO(blob_obj.download_as_bytes()), low_memory=False) \
              if blob_obj.exists() else pd.DataFrame()

    # Recorrer todos los blobs
    for blob in bucket.list_blobs(prefix="datos_procesados/"):
        partes = blob.name.split("/")
        n = len(partes)

        if n == 4:
            # Nueva estructura: datos_procesados/{ciudad}/{periodo}/{archivo}
            archivo = partes[3]
            if archivo in TODOS_ARCHIVOS:
                clave = TODOS_ARCHIVOS[archivo]
                acum[clave].append(
                    pd.read_csv(io.BytesIO(blob.download_as_bytes()), low_memory=False)
                )

        elif n == 3:
            # Nivel ciudad (sin período): datos_procesados/{ciudad}/{archivo}
            archivo = partes[2]
            if archivo in ARCHIVOS_CIUDAD:
                clave = ARCHIVOS_CIUDAD[archivo]
                acum[clave].append(
                    pd.read_csv(io.BytesIO(blob.download_as_bytes()), low_memory=False)
                )

        elif n == 2:
            # Estructura antigua (compatibilidad): datos_procesados/{archivo}
            archivo = partes[1]
            if archivo in TODOS_ARCHIVOS:
                clave = TODOS_ARCHIVOS[archivo]
                acum[clave].append(
                    pd.read_csv(io.BytesIO(blob.download_as_bytes()), low_memory=False)
                )

    # Consolidar
    dfs = {"objetivos": dfs_obj}
    for clave, lista in acum.items():
        if lista:
            df_concat = pd.concat(lista, ignore_index=True)
            if clave == "verbaliz":
                if "mes_anio" not in df_concat.columns:
                    df_concat["mes_anio"] = np.nan
                if "orden_mes" not in df_concat.columns:
                    df_concat["orden_mes"] = np.nan
                df_concat = _reconstruir_mes_anio(df_concat)
            # Deduplicar si hay overlap entre estructura nueva y antigua
            if clave in ("isc_mensual", "tre_mensual"):
                df_concat = df_concat.drop_duplicates(
                    subset=["mes_anio", "ciudad", "dealer"], keep="last"
                )
            elif clave in ("isc_aps", "tre_aps"):
                df_concat = df_concat.drop_duplicates(
                    subset=["mes_anio", "ciudad", "dealer", "aps_nombre"], keep="last"
                )
            elif clave == "atributos":
                df_concat = df_concat.drop_duplicates(
                    subset=["mes_anio", "ciudad", "dealer", "atributo"], keep="last"
                )
            elif clave == "atrib_aps":
                df_concat = df_concat.drop_duplicates(
                    subset=["mes_anio", "ciudad", "dealer", "aps_nombre", "atributo"], keep="last"
                )
            dfs[clave] = df_concat
        else:
            dfs[clave] = pd.DataFrame()

    return dfs

def get_obj(df_obj, fytd, campo):
    row = df_obj[df_obj["fytd"]==fytd]
    if row.empty or campo not in row.columns: return 0.0
    return float(row[campo].values[0])

def color_obj(v, obj):
    if pd.isna(v) or isinstance(v, str) or obj == 0: return ""
    if v >= obj: return "background-color:#ccffcc;color:black;font-weight:bold"
    return "background-color:#ffcccc;color:black;font-weight:bold"

def fig_to_buf(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="jpeg", facecolor="white", bbox_inches="tight", dpi=150)
    plt.close(fig); buf.seek(0); return buf

def styler_to_jpg_buf(styler):
    """
    Convierte un pandas Styler a JPEG usando matplotlib.
    Extrae colores y texto exactos del HTML renderizado por pandas.
    No requiere Playwright, Chromium ni dataframe_image.
    """
    import re as _re

    html = styler.to_html()

    # 1. Style map: (row, col) -> props dict
    style_map = {}
    style_block = _re.search(r'<style[^>]*>(.*?)</style>', html, _re.DOTALL)
    if style_block:
        for selector_raw, props_raw in _re.findall(r'([^{}]+)\{([^{}]+)\}', style_block.group(1)):
            props = {}
            for decl in props_raw.split(";"):
                if ":" not in decl: continue
                k, v = decl.split(":", 1)
                props[k.strip()] = v.strip()
            if not props: continue
            for m in _re.finditer(r'_row(\d+)_col(\d+)', selector_raw):
                style_map[(int(m.group(1)), int(m.group(2)))] = props

    # 2. Cabeceras desde <th> con id nivel col
    th_matches = _re.findall(r'<th[^>]*id="T_\w+_level0_col(\d+)"[^>]*>([\s\S]*?)</th>', html)
    headers_by_col = {int(col_s): _re.sub(r'<[^>]+>', '', content).strip()
                      for col_s, content in th_matches}
    data_col_ids = sorted(headers_by_col.keys())
    header_texts = [headers_by_col[c] for c in data_col_ids]

    # 3. Filas desde <tbody>
    rows_data = []
    tbody = _re.search(r'<tbody>([\s\S]*?)</tbody>', html)
    if tbody:
        for tr in _re.findall(r'<tr>([\s\S]*?)</tr>', tbody.group(1)):
            td_matches = _re.findall(
                r'<td[^>]*id="T_\w+_row(\d+)_col(\d+)"[^>]*>([\s\S]*?)</td>', tr)
            if not td_matches: continue
            td_by_col = {}
            for ri_s, ci_s, content in td_matches:
                ri, ci = int(ri_s), int(ci_s)
                text  = _re.sub(r'<[^>]+>', '', content).strip()
                props = style_map.get((ri, ci), {})
                bg    = props.get("background-color", "#FFFFFF")
                fg    = props.get("color", "#1A1A1A")
                bold  = props.get("font-weight", "") in ("bold", "700")
                td_by_col[ci] = (text, bg, fg, bold)
            row = [td_by_col.get(ci, ("", "#FFFFFF", "#1A1A1A", False)) for ci in data_col_ids]
            if row: rows_data.append(row)

    # Fallback
    if not rows_data or not header_texts:
        df2 = styler.data
        header_texts = list(df2.columns)
        data_col_ids = list(range(len(header_texts)))
        rows_data = [[(str(v), "#FFFFFF", "#1A1A1A", False) for v in r] for r in df2.values]

    n_rows = len(rows_data)

    # 4. Anchos de columna proporcionales
    col_max = [len(str(h)) for h in header_texts]
    for row in rows_data:
        for ci, (txt, *_) in enumerate(row):
            if ci < len(col_max): col_max[ci] = max(col_max[ci], len(str(txt)))
    total = sum(col_max) or 1
    col_w = [c / total for c in col_max]
    fig_w = max(8, min(26, total * 0.20 + 1.0))
    row_h = 0.44; hdr_h = 0.56; fig_h = hdr_h + row_h * n_rows + 0.2

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor="white")
    ax.set_xlim(0, 1); ax.set_ylim(0, fig_h); ax.axis("off")

    # 5. Cabecera
    x = 0.0; y_hdr = fig_h - hdr_h
    for h, cw in zip(header_texts, col_w):
        ax.add_patch(plt.Rectangle((x, y_hdr), cw, hdr_h,
                     facecolor="#1A1A2E", edgecolor="white", linewidth=0.5))
        words = str(h).split(); line = ""; lines = []
        for w in words:
            test = (line + " " + w).strip()
            if len(test) > max(8, int(cw * total * 0.45)):
                if line: lines.append(line)
                line = w
            else:
                line = test
        if line: lines.append(line)
        ax.text(x + cw/2, y_hdr + hdr_h/2, "\n".join(lines),
                ha="center", va="center", fontsize=7.0 if len(lines)>1 else 7.5,
                fontweight="bold", color="white", linespacing=1.3)
        x += cw

    # 6. Filas
    for ri, row in enumerate(rows_data):
        y_row = fig_h - hdr_h - row_h * (ri + 1); x = 0.0
        def_bg = "#F5F5F5" if ri % 2 == 1 else "#FFFFFF"
        for ci, cw in enumerate(col_w):
            txt, bg, fg, bold = row[ci] if ci < len(row) else ("", def_bg, "#1A1A1A", False)
            cell_bg = bg if bg not in ("#FFFFFF", "", "white") else def_bg
            ax.add_patch(plt.Rectangle((x, y_row), cw, row_h,
                         facecolor=cell_bg, edgecolor="#E8E8E8", linewidth=0.3))
            ax.text(x + cw/2, y_row + row_h/2, str(txt),
                    ha="center", va="center", fontsize=7,
                    fontweight="bold" if bold else "normal", color=fg)
            x += cw

    buf = io.BytesIO()
    fig.savefig(buf, format="jpeg", facecolor="white", bbox_inches="tight", dpi=180)
    plt.close(fig); buf.seek(0); return buf

def aplicar_filtro_ciudad(df, ciudad):
    if ciudad=="TODAS" or "ciudad" not in df.columns: return df
    return df[df["ciudad"]==ciudad]

def filtrar(df, fytd=None, mes=None, ciudad=None, dealer=None, aps=None):
    if df.empty: return df
    if fytd and "fytd" in df.columns: df=df[df["fytd"]==fytd]
    if mes and mes!="TODOS" and "mes_anio" in df.columns: df=df[df["mes_anio"]==mes]
    if ciudad and ciudad!="TODAS" and "ciudad" in df.columns: df=df[df["ciudad"]==ciudad]
    if dealer and dealer!="GENERAL" and "dealer" in df.columns: df=df[df["dealer"]==dealer]
    if aps and aps!="TODOS" and "aps_nombre" in df.columns: df=df[df["aps_nombre"]==aps]
    return df

def meses_de(df, fytd, ciudad=None, dealer=None):
    d=filtrar(df,fytd=fytd,ciudad=ciudad,dealer=dealer)
    if d.empty: return []
    return sorted(d.drop_duplicates("mes_anio").sort_values("orden_mes")["mes_anio"].tolist())

def tabla_html(styled): return f"<div class='table-scroll'>{styled.to_html()}</div>"

def kpi_html(label,val,fmt="{:.1%}",sub="",color="#1A1A2E"):
    v=fmt.format(val) if val is not None else "—"
    return f"""<div class='kpi-card'><div class='kpi-label'>{label}</div>
    <div class='kpi-value' style='color:{color}'>{v}</div>
    <div class='kpi-sub'>{sub}</div></div>"""
# ==============================================================================
# CSS
# ==============================================================================

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;900&family=Barlow:wght@400;500;600&display=swap');
html,body,[class*="css"]{font-family:'Barlow',sans-serif!important}
[data-testid="stSidebar"]{background:#1A1A2E!important;min-width:230px!important;max-width:230px!important}
[data-testid="stSidebar"] *{color:#E0E0E0!important}
[data-testid="stSidebarNav"]{display:none!important}
[data-testid="stSidebar"] .stButton>button{background:transparent!important;border:none!important;border-left:3px solid transparent!important;border-radius:0!important;text-align:left!important;width:100%!important;padding:8px 16px!important;font-family:'Barlow Condensed',sans-serif!important;font-size:15px!important;font-weight:600!important;transition:all 0.15s ease!important}
[data-testid="stSidebar"] .stButton>button:hover{background:rgba(255,255,255,0.08)!important;border-left:3px solid #FFD600!important}
[data-testid="stSidebar"] .stButton>button p,[data-testid="stSidebar"] .stButton>button:hover p{font-size:15px!important}
[data-testid="stSidebar"] hr{border-color:rgba(255,255,255,0.12)!important}
section[data-testid="stMain"]{background:#F5F6FA!important}
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
.tabla-voc td{font-size:12px;text-align:center;padding:5px 8px;color:#1A1A1A}
.tabla-voc tr:nth-child(even) td{background:#F8F8F8}
.alerta-card{background:#fff3f3;border-left:5px solid #D32F2F;border-radius:6px;padding:10px 14px;margin-bottom:8px;font-family:'Barlow Condensed',sans-serif}
.alerta-card .an{font-size:15px;font-weight:700;color:#1A1A1A}
.alerta-card .av{font-size:20px;font-weight:900;color:#D32F2F}
.alerta-card .ag{font-size:12px;color:#888}
.table-scroll{overflow-x:auto;border-radius:8px}
div[data-testid="stSelectbox"] label p{font-weight:700!important;font-size:12px!important;color:#1A1A2E!important;text-transform:uppercase;letter-spacing:0.5px}
</style>
""", unsafe_allow_html=True)

# ==============================================================================
# SIDEBAR + SESSION STATE
# ==============================================================================
if "seccion" not in st.session_state: st.session_state.seccion = "caratula"
if "perfil" not in st.session_state: st.session_state.perfil = None
if "ciudad_sel" not in st.session_state: st.session_state.ciudad_sel = "TODAS"

D = cargar_datos()

if D["isc_mensual"].empty and D["tre_mensual"].empty:
    st.warning("Sin datos. Ejecuta primero preprocesador.py.")
    st.stop()

todos_fytd = sorted(D["isc_mensual"]["fytd"].unique(), reverse=True) if not D["isc_mensual"].empty else []
todas_ciudades = ["TODAS"]
if not D["isc_mensual"].empty and "ciudad" in D["isc_mensual"].columns:
    todas_ciudades += sorted([c for c in D["isc_mensual"]["ciudad"].unique() if c != "Sin Ciudad"])

def dealers_para_ciudad(ciudad):
    df = aplicar_filtro_ciudad(D["isc_mensual"], ciudad)
    return ["GENERAL"] + sorted([d for d in df["dealer"].unique() if d != "SIN DEALER"])

def aps_para_filtro(ciudad, dealer, fytd=None):
    df = D["isc_aps"].copy() if not D["isc_aps"].empty else pd.DataFrame()
    if df.empty: return ["TODOS"]
    if fytd: df = df[df["fytd"]==fytd]
    df = aplicar_filtro_ciudad(df, ciudad)
    if dealer != "GENERAL": df = df[df["dealer"]==dealer]
    return ["TODOS"] + sorted(df["aps_nombre"].unique())

sec = st.session_state.seccion
perfil = st.session_state.perfil

if sec != "caratula":
    with st.sidebar:
        st.markdown('<div style="text-align:center; margin-bottom:1.5cm; margin-top:-25px;"><div style="color:#FFFFFF; font-family:\'Barlow\',sans-serif; font-size:13px; font-weight:500; letter-spacing:6px; text-transform:uppercase; margin-bottom:4px;">TAIYO MOTORS</div><div style="display:flex; flex-direction:column; align-items:center; line-height:0.9;"><div style="background:linear-gradient(90deg, #E0E0E0 0%, #9E9E9E 50%, #616161 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; font-family:\'Barlow Condensed\',sans-serif; font-size:26px; font-weight:900; letter-spacing:1px; text-transform:uppercase; margin-bottom:2px;">VOC INSIGHTS</div><div style="background:linear-gradient(90deg, #E0E0E0 0%, #9E9E9E 50%, #616161 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; font-family:\'Barlow Condensed\',sans-serif; font-size:26px; font-weight:900; letter-spacing:2px; text-transform:uppercase;">ANALYTICS</div></div></div>', unsafe_allow_html=True)
        
        if st.button("INICIO", key="nav_ini"):  
            st.session_state.seccion = "caratula"
            st.session_state.perfil = None
            st.rerun()
        st.markdown("<hr>", unsafe_allow_html=True)
        
        if perfil == "GENERAL":
            st.markdown("<div style='padding:8px 16px;font-size:11px;color:#666;letter-spacing:1px;font-weight:700'>MEDICIÓN</div>", unsafe_allow_html=True)
            if st.button("GENERAL",         key="nav_gen"):  st.session_state.seccion="general"
            if st.button("ATRIBUTOS",       key="nav_atr"):  st.session_state.seccion="atributos"
            if st.button("RADIAL",          key="nav_rad"):  st.session_state.seccion="radial"
            if st.button("VERBALIZACIONES", key="nav_ver"):  st.session_state.seccion="verbalizaciones"
            if st.button("TENDENCIA ISC",   key="nav_ten"):  st.session_state.seccion="tendencia"
            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown("<div style='padding:8px 16px;font-size:11px;color:#666;letter-spacing:1px;font-weight:700'>OPERATIVO</div>", unsafe_allow_html=True)
            if st.button("PENDIENTES",    key="nav_pen"):  st.session_state.seccion="pendientes"
            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown("<div style='padding:8px 16px;font-size:11px;color:#666;letter-spacing:1px;font-weight:700'>MI PERFIL</div>", unsafe_allow_html=True)
            if st.button("VISTA APS", key="nav_aps"): st.session_state.seccion="vista_aps"
            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown("<div style='padding:8px 16px;font-size:11px;color:#666;letter-spacing:1px;font-weight:700'>EXPORTAR</div>", unsafe_allow_html=True)
            if st.button("DESCARGAR PDF", key="nav_dl"): st.session_state.seccion="descargar_pdf"
        
        elif perfil == "APS":
            st.markdown("<div style='padding:8px 16px;font-size:11px;color:#666;letter-spacing:1px;font-weight:700'>MI PERFIL</div>", unsafe_allow_html=True)
            if st.button("VISTA APS", key="nav_aps"): st.session_state.seccion="vista_aps"
        
        if st.button("Recargar datos",key="nav_rel"):
            st.cache_data.clear()
            st.session_state["datos_recargados"] = True
            st.rerun()

    ciudad_cols = st.columns([0.18, 0.82])
    with ciudad_cols[0]:
        ciudad_nueva = st.selectbox(
            "CIUDAD:", todas_ciudades,
            index=todas_ciudades.index(st.session_state.ciudad_sel) if st.session_state.ciudad_sel in todas_ciudades else 0,
            key="ciudad_top"
        )
        if ciudad_nueva != st.session_state.ciudad_sel:
            st.session_state.ciudad_sel = ciudad_nueva
            st.rerun()

    CIUDAD = st.session_state.ciudad_sel
    if CIUDAD != "TODAS":
        st.markdown(
            f"<div style='background:#1A1A2E;border-radius:8px;padding:8px 16px;margin-bottom:12px;"
            f"font-family:Barlow Condensed,sans-serif;font-size:14px;color:#FFD600;letter-spacing:1px'>"
            f"Mostrando datos para: <b>{CIUDAD}</b></div>", unsafe_allow_html=True)
else:
    CIUDAD = st.session_state.ciudad_sel 

# ==============================================================================
# SECCIÓN 1: GENERAL
# ==============================================================================

@st.cache_data(show_spinner=False, max_entries=15)
def cached_jpg_gen(df, t_type, obj_t, obj_i):
    if t_type == 'gral':
        def sty_d(row):
            s=[""]*len(row)
            s[list(row.index).index("TRE %")] = color_obj(row["TRE %"], obj_t)
            s[list(row.index).index("%ISC")]  = color_obj(row["%ISC"], obj_i)
            for i2, cn in enumerate(row.index):
                if cn in ("Env.","Comp.","Falta","1-6","7-8"): s[i2] = "color:#555"
            if row["Nombre"] == "TOTAL GENERAL":
                for i2 in range(len(s)): 
                    if s[i2] == "" or s[i2] == "color:#555": s[i2] = "background-color:#E8E8E8;font-weight:bold"
            return s
        st2 = df.style.apply(sty_d,axis=1).format({"TRE %":"{:.1%}","%ISC":"{:.1%}","Env.":"{:.0f}","Comp.":"{:.0f}","Falta":"{:.0f}","1-6":"{:.0f}","7-8":"{:.0f}"}).set_table_attributes('class="tabla-voc"').hide(axis="index")
        return styler_to_jpg_buf(st2).getvalue()
    elif t_type == 'aps' or t_type == 'topbot':
        def sty_a(row):
            s = [""] * len(row)
            s[list(row.index).index("TRE %")] = color_obj(row["TRE %"], obj_t)
            s[list(row.index).index("% ISC")] = color_obj(row["% ISC"], obj_i)
            if t_type == 'aps':
                for i2, cn in enumerate(row.index):
                    if cn in ("Enviadas","Completadas","1-6","7-8"): s[i2] = "color:#555"
                if "Nombre" in row.index and row["Nombre"] == "TOTAL GENERAL":
                    for i2 in range(len(s)): 
                        if s[i2] == "" or s[i2] == "color:#555": s[i2] = "background-color:#E8E8E8;font-weight:bold"
            return s
        if t_type == 'aps':
            st_ap = df.style.apply(sty_a, axis=1).format({"TRE %":"{:.1%}","% ISC":"{:.1%}","Enviadas":"{:.0f}","Completadas":"{:.0f}","1-6":"{:.0f}","7-8":"{:.0f}"}).set_table_attributes('class="tabla-voc"').hide(axis='index')
        else:
            st_ap = df.style.apply(sty_a, axis=1).format({"TRE %":"{:.1%}","% ISC":"{:.1%}"}).set_table_attributes('class="tabla-voc"').hide(axis="index")
        return styler_to_jpg_buf(st_ap).getvalue()

def render_general():
    st.markdown("<div class='seccion-titulo'>Medición General — TRE & ISC</div>",unsafe_allow_html=True)
    
    col1,col2,col3,col4,col5 = st.columns([2,2,2,1,1])
    with col1: fytd_sel=st.selectbox("Periodo:",todos_fytd,key="g_fytd")
    with col2:
        mm=meses_de(D["isc_mensual"],fytd_sel,CIUDAD)
        mes_sel=st.selectbox("Mes/Año:",["TODOS"]+mm,key="g_mes")
    with col3:
        dlrs=dealers_para_ciudad(CIUDAD)
        dealer_sel=st.selectbox("Dealer:",dlrs,key="g_dlr")
    
    if "gen_acum" not in st.session_state: st.session_state.gen_acum = False
    with col4:
        st.markdown("<div style='margin-top:28px'></div>",unsafe_allow_html=True)
        if st.button("Cargar",key="g_btn",use_container_width=True,type="primary"):
            st.session_state.gen_acum = False; st.rerun()
    with col5:
        st.markdown("<div style='margin-top:28px'></div>",unsafe_allow_html=True)
        if st.button("ACUM",key="g_btn_acum",use_container_width=True):
            st.session_state.gen_acum = True; st.rerun()

    if mes_sel == "TODOS": meses_a_procesar = mm
    elif st.session_state.gen_acum:
        if mes_sel in mm: meses_a_procesar = mm[:mm.index(mes_sel)+1]
        else: meses_a_procesar = []
    else: meses_a_procesar = [mes_sel]

    if not meses_a_procesar: st.info("Sin datos para la selección."); return

    df_im = filtrar(D["isc_mensual"][D["isc_mensual"]["mes_anio"].isin(meses_a_procesar)].copy(), fytd=fytd_sel, ciudad=CIUDAD)
    df_tm = filtrar(D["tre_mensual"][D["tre_mensual"]["mes_anio"].isin(meses_a_procesar)].copy(), fytd=fytd_sel, ciudad=CIUDAD)
    df_ia = filtrar(D["isc_aps"][D["isc_aps"]["mes_anio"].isin(meses_a_procesar)].copy() if not D["isc_aps"].empty else pd.DataFrame(), fytd=fytd_sel, ciudad=CIUDAD, dealer=dealer_sel if dealer_sel!="GENERAL" else None)
    df_ta = filtrar(D["tre_aps"][D["tre_aps"]["mes_anio"].isin(meses_a_procesar)].copy() if not D["tre_aps"].empty else pd.DataFrame(), fytd=fytd_sel, ciudad=CIUDAD, dealer=dealer_sel if dealer_sel!="GENERAL" else None)

    if df_im.empty: st.info("Sin datos para la selección."); return

    obj_obj=D["objetivos"]; obj_tre=get_obj(obj_obj,fytd_sel,"obj_tre"); obj_isc=get_obj(obj_obj,fytd_sel,"obj_isc")

    df_im = df_im[~df_im["dealer"].astype(str).str.upper().str.contains("SIN DEALER", na=False)]
    df_tm = df_tm[~df_tm["dealer"].astype(str).str.upper().str.contains("SIN DEALER", na=False)]
    
    agg_isc=df_im.groupby("dealer").agg(enc=("total_encuestas","sum"),i16=("I16","sum"),i78=("I78","sum")).reset_index()
    agg_tre=df_tm.groupby("dealer").agg(E=("E","sum"),C=("C","sum"),F=("F","sum")).reset_index()
    
    df_d=agg_tre.merge(agg_isc,on="dealer",how="outer").fillna(0)
    df_d["prom_tre"] = np.where(df_d["E"]>0, df_d["C"]/df_d["E"], 0)
    df_d["prom_isc"] = np.where(df_d["enc"]>0, (df_d["enc"]-df_d["i16"]*2-df_d["i78"])/df_d["enc"], 0)

    c_tot=df_d["enc"].sum(); i16t=df_d["i16"].sum(); i78t=df_d["i78"].sum()
    tot={"dealer":"TOTAL GENERAL","E":df_d["E"].sum(),"C":df_d["C"].sum(),
         "F":df_d["F"].sum(),"enc":c_tot,"i16":i16t,"i78":i78t,
         "prom_tre":df_d["C"].sum()/df_d["E"].sum() if df_d["E"].sum()>0 else 0,
         "prom_isc":(c_tot-i16t*2-i78t)/c_tot if c_tot>0 else 0}
    df_show=pd.concat([df_d,pd.DataFrame([tot])],ignore_index=True)

    str_acum = " (ACUM)" if st.session_state.gen_acum and mes_sel != "TODOS" else ""

    ks=st.columns(3)
    for i,(lbl,val,fmt,sub) in enumerate([
        ("Total Enviadas",tot["E"],"{:.0f}",f"FYTD {fytd_sel.replace('FYTD ','')} {str_acum}"),
        ("TRE %",tot["prom_tre"],"{:.1%}",f"Obj {obj_tre:.0%}"),
        ("ISC %",tot["prom_isc"],"{:.1%}",f"Obj {obj_isc:.0%}"),
    ]): ks[i].markdown(kpi_html(lbl,val,fmt,sub),unsafe_allow_html=True)
    st.markdown("<div style='height:16px'></div>",unsafe_allow_html=True)

    cl,cr=st.columns([1,1.4])
    with cl:
        st.markdown("<div class='chart-box'>",unsafe_allow_html=True)
        st.markdown(f"**DEALER{str_acum}**")
        ds=df_show[["dealer","E","C","F","prom_tre","i16","i78","prom_isc"]].copy()
        ds.columns=["Nombre","Env.","Comp.","Falta","TRE %","1-6","7-8","%ISC"]
        
        def sty_d(row):
            s=[""]*len(row)
            s[list(row.index).index("TRE %")] = color_obj(row["TRE %"], obj_tre)
            s[list(row.index).index("%ISC")]  = color_obj(row["%ISC"], obj_isc)
            for i2, cn in enumerate(row.index):
                if cn in ("Env.","Comp.","Falta","1-6","7-8"): s[i2] = "color:#555"
            if row["Nombre"] == "TOTAL GENERAL":
                for i2 in range(len(s)): 
                    if s[i2] == "" or s[i2] == "color:#555": s[i2] = "background-color:#E8E8E8;font-weight:bold"
            return s
            
        st2=ds.style.apply(sty_d,axis=1)\
            .format({"TRE %":"{:.1%}","%ISC":"{:.1%}","Env.":"{:.0f}","Comp.":"{:.0f}","Falta":"{:.0f}","1-6":"{:.0f}","7-8":"{:.0f}"})\
            .set_table_attributes('class="tabla-voc"').hide(axis="index")
        st.markdown(tabla_html(st2),unsafe_allow_html=True)
        
        st.download_button("Tabla General", data=cached_jpg_gen(ds, 'gral', obj_tre, obj_isc), file_name="Tabla_General.jpg", mime="image/jpeg", key="dl_gd")
        st.markdown("</div>",unsafe_allow_html=True)

    with cr:
        st.markdown("<div class='chart-box'>",unsafe_allow_html=True)
        st.markdown(f"**Detalle por APS — {dealer_sel} · {CIUDAD}{str_acum}**")
        
        if not df_ia.empty and not df_ta.empty:
            agg_ti = df_ta.groupby("aps_nombre").agg(E=("E","sum"), C=("C","sum")).reset_index()
            agg_ii = df_ia.groupby("aps_nombre").agg(enc=("total_encuestas","sum"), i16=("I16","sum"), i78=("I78","sum")).reset_index()
            df_ap = agg_ti.merge(agg_ii, on="aps_nombre", how="outer").fillna(0)
            df_ap["prom_tre"] = np.where(df_ap["E"]>0, df_ap["C"]/df_ap["E"], 0)
            df_ap["prom_isc"] = np.where(df_ap["enc"]>0, (df_ap["enc"]-df_ap["i16"]*2-df_ap["i78"])/df_ap["enc"], 0)
            
            tot_ap = {"aps_nombre":"TOTAL GENERAL", "E":df_ap["E"].sum(), "C":df_ap["C"].sum(), "enc":df_ap["enc"].sum(), "i16":df_ap["i16"].sum(), "i78":df_ap["i78"].sum()}
            tot_ap["prom_tre"] = tot_ap["C"]/tot_ap["E"] if tot_ap["E"]>0 else 0
            tot_ap["prom_isc"] = (tot_ap["enc"] - tot_ap["i16"]*2 - tot_ap["i78"])/tot_ap["enc"] if tot_ap["enc"]>0 else 0
            
            df_af = pd.concat([df_ap, pd.DataFrame([tot_ap])], ignore_index=True)
            ds_ap = df_af[["aps_nombre","E","C","prom_tre","i16","i78","prom_isc"]].copy()
            ds_ap.columns = ["Nombre","Enviadas","Completadas","TRE %","1-6","7-8","% ISC"]
            
            def sty_a(row):
                s = [""] * len(row)
                s[list(row.index).index("TRE %")] = color_obj(row["TRE %"], obj_tre)
                s[list(row.index).index("% ISC")] = color_obj(row["% ISC"], obj_isc)
                if row["Nombre"] == "TOTAL GENERAL":
                    for i2 in range(len(s)): 
                        if s[i2] == "": s[i2] = "background-color:#E8E8E8;font-weight:bold"
                return s
            
            st_ap = ds_ap.style.apply(sty_a, axis=1).format({"TRE %":"{:.1%}","% ISC":"{:.1%}","Enviadas":"{:.0f}","Completadas":"{:.0f}","1-6":"{:.0f}","7-8":"{:.0f}"}).set_table_attributes('class="tabla-voc"').hide(axis='index')
            st.markdown(tabla_html(st_ap), unsafe_allow_html=True)
            
            st.download_button("Descargar Tabla APS", data=cached_jpg_gen(ds_ap, 'aps', obj_tre, obj_isc), file_name="Tabla_APS.jpg", mime="image/jpeg", key="dl_aps_gen")
            
            c_t, c_b = st.columns(2)
            validos = df_ap[df_ap["prom_isc"].notna() & np.isfinite(df_ap["prom_isc"]) & (df_ap["aps_nombre"] != "TOTAL GENERAL")]
            ds_validos = validos[["aps_nombre", "prom_tre", "prom_isc"]].rename(columns={"aps_nombre":"Nombre", "prom_tre":"TRE %", "prom_isc":"% ISC"})
            
            def sty_a_tb(row):
                s = [""] * len(row)
                s[list(row.index).index("TRE %")] = color_obj(row["TRE %"], obj_tre)
                s[list(row.index).index("% ISC")] = color_obj(row["% ISC"], obj_isc)
                return s

            st_top = ds_validos.nlargest(3,"% ISC").style.apply(sty_a_tb, axis=1).format({"TRE %":"{:.1%}","% ISC":"{:.1%}"}).set_table_attributes('class="tabla-voc"').hide(axis="index")
            st_bot = ds_validos.nsmallest(3,"% ISC").style.apply(sty_a_tb, axis=1).format({"TRE %":"{:.1%}","% ISC":"{:.1%}"}).set_table_attributes('class="tabla-voc"').hide(axis="index")
            
            c_t.download_button("TOP 3", data=cached_jpg_gen(ds_validos.nlargest(3,"% ISC"), 'topbot', obj_tre, obj_isc), file_name="Top3.jpg", mime="image/jpeg", key="dl_top3")
            c_b.download_button("BOTTOM 3", data=cached_jpg_gen(ds_validos.nsmallest(3,"% ISC"), 'topbot', obj_tre, obj_isc), file_name="Bot3.jpg", mime="image/jpeg", key="dl_bot3")
        else: st.info("Sin datos para el filtro.")
        st.markdown("</div>", unsafe_allow_html=True)

    # =========================================================================
    # INDICADORES CIRCULARES (Z, AA, AB)
    # =========================================================================
    st.markdown("<hr style='border: 1px solid rgba(26,26,26,0.1); margin-top: 30px; margin-bottom: 20px;'>", unsafe_allow_html=True)
    st.markdown("<h3 style='text-align:center; color:#1A1A2E; font-family:\"Barlow Condensed\", sans-serif; font-size: 26px; text-transform: uppercase;'>Atributos Especiales</h3>", unsafe_allow_html=True)
    
    nombres_especiales = ["Bien a la primera H1", "Customer Expectations CES", "Alertas atendidas en 24 Hrs"]

    # Bug fix: usar D["especiales"] (cargado desde GCS) en vez de leer archivo local
    df_esp = D["especiales"].copy() if not D["especiales"].empty else pd.DataFrame()
    
    import unicodedata
    def norm_t(t): return unicodedata.normalize('NFKD', str(t).strip().lower()).encode('ASCII', 'ignore').decode('utf-8') if pd.notna(t) else ""

    c_z, c_aa, c_ab = st.columns(3)
    cols_redondos = [c_z, c_aa, c_ab]
    
    if not df_esp.empty:
        df_esp['fytd'] = df_esp['fytd'].astype(str).str.strip().str.upper()
        df_esp['mes'] = df_esp['mes'].astype(str).str.strip().str.capitalize()
        if 'ciudad' in df_esp.columns:
            df_esp['ciudad'] = df_esp['ciudad'].astype(str).str.strip().str.upper()
    
    meses_cortos = [str(m).split()[0].strip().capitalize() for m in meses_a_procesar]
    fytd_str = str(fytd_sel).strip().upper()
    
    for i, attr_name in enumerate(nombres_especiales):
        val_actual = 0.0
        if not df_esp.empty and meses_cortos:
            # Bug fix: filtrar también por ciudad activa
            mask = (df_esp['fytd'] == fytd_str) & (df_esp['mes'].isin(meses_cortos))
            if 'ciudad' in df_esp.columns and CIUDAD != "TODAS":
                mask = mask & (df_esp['ciudad'] == CIUDAD.upper())
            row_e = df_esp[mask]
            if not row_e.empty and attr_name in row_e.columns: 
                v_mean = row_e[attr_name].mean()
                val_actual = float(v_mean) if pd.notna(v_mean) else 0.0

        obj_val = 0.85
        if not obj_obj.empty:
            for col in obj_obj.columns:
                if norm_t(col) == norm_t(attr_name):
                    r_o = obj_obj[obj_obj["fytd"] == fytd_sel]
                    if not r_o.empty:
                        raw_o = float(r_o[col].values[0])
                        obj_val = raw_o / 100.0 if raw_o > 2 else raw_o
                    break
        
        cumplio = val_actual >= obj_val
        color_c = "#388E3C" if cumplio else "#D32F2F"
        color_bg = "#F2F9F2" if cumplio else "#FFEBEE"
        
        html_r = f"""
        <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; background: {color_bg}; border-radius: 16px; border: 2px solid {color_c}; padding: 25px 15px; margin-bottom: 20px; box-shadow: 0 6px 12px rgba(0,0,0,0.08);">
            <div style="width: 140px; height: 140px; border-radius: 50%; background-color: {color_c}; display: flex; align-items: center; justify-content: center; box-shadow: inset 0 4px 6px rgba(0,0,0,0.2), 0 5px 12px rgba(0,0,0,0.2); border: 5px solid white;">
                <span style="color: white; font-size: 38px; font-weight: 900; font-family: 'Barlow Condensed', sans-serif; text-shadow: 1px 2px 3px rgba(0,0,0,0.3);">{val_actual:.0%}</span>
            </div>
            <div style="text-align: center; margin-top: 20px;">
                <div style="font-size: 17px; font-weight: 900; color: #1A1A2E; text-transform: uppercase; font-family: 'Barlow Condensed', sans-serif; letter-spacing: 0.5px;">{attr_name}</div>
                <div style="display: inline-block; background: white; padding: 4px 12px; border-radius: 20px; border: 1px solid #CCC; margin-top: 8px;">
                    <span style="font-size: 13px; color: #555; font-weight: 700;">Objetivo: {obj_val:.0%}</span>
                </div>
                <div style="font-size: 17px; font-weight: 900; color: {color_c}; margin-top: 12px; letter-spacing: 0.5px; text-shadow: 0.5px 0.5px 0px rgba(0,0,0,0.1);">{"¡CUMPLIDO!" if cumplio else "NO CUMPLIDO"}</div>
            </div>
        </div>
        """
        cols_redondos[i].markdown(html_r, unsafe_allow_html=True)

# ==============================================================================
# SECCIÓN 2: TENDENCIA ISC 
# ==============================================================================

@st.cache_data(show_spinner=False, max_entries=20)
def cached_plot_tendencia(meses_ord, totales, prom_vals, series_sec, colores_sec, markers_sec, obj_isc, lbl_prom, titulo, lbl_enc):
    margen_bottom = min(0.45, 0.18 + (1 + len(series_sec) + 1) * 0.045)
    fig, ax1 = plt.subplots(figsize=(14, 7), facecolor="white")
    plt.subplots_adjust(bottom=margen_bottom)
    ax2 = ax1.twinx()
    x_idx = np.arange(len(meses_ord))

    ax1.bar(x_idx, totales, color=COLOR_BARRAS, width=0.6, zorder=2)
    ax1.set_xticks([])
    ax1.set_ylabel("Cant. Encuestas", color=COLOR_BARRAS, fontsize=10, fontweight="bold")
    ax1.tick_params(axis="y", colors=COLOR_BARRAS)
    ax1.spines["top"].set_visible(False)

    valid_idx = [i for i,v in enumerate(prom_vals) if v is not None and not np.isnan(v)]
    valid_val = [prom_vals[i] for i in valid_idx]
    
    if len(valid_idx) >= 4:
        spline_x = np.linspace(min(valid_idx), max(valid_idx), 300)
        spline_y = make_interp_spline(valid_idx, valid_val, k=3)(spline_x)
        ax2.plot(spline_x, spline_y, color=COLOR_LINEA, linewidth=3, zorder=4)
    elif valid_idx:
        ax2.plot(x_idx, prom_vals, color=COLOR_LINEA, linewidth=3, zorder=4)

    ax2.scatter(x_idx, prom_vals, color=COLOR_LINEA, s=120, edgecolors="black", linewidths=1.2, zorder=6, label=lbl_prom)
    
    efecto = [path_effects.withStroke(linewidth=2.5, foreground="white", alpha=0.9)]
    
    for j, v in enumerate(prom_vals):
        if v is not None and not np.isnan(v):
            ax2.text(j, v + 0.030, f"{v:.1%}", ha='center', fontsize=11, fontweight='900', color="#1A1A1A", path_effects=efecto, zorder=7)

    for nombre, pts in series_sec.items():
        ax2.scatter(x_idx, pts, color=colores_sec[nombre], marker=markers_sec[nombre], s=70, alpha=0.9, edgecolors="white", zorder=5, label=f"{nombre} (●)")
        for j, v in enumerate(pts):
            if v is not None and not np.isnan(v):
                ax2.text(j, v - 0.030, f"{v:.1%}", ha='center', fontsize=8.5, fontweight='bold', color=colores_sec[nombre], path_effects=efecto, zorder=6)

    ax2.axhline(obj_isc, color=COLOR_OBJETIVO, linewidth=1.5, linestyle="--", alpha=0.7, label=f"Objetivo ({obj_isc:.0%})")

    ax2.yaxis.set_major_formatter(mtick.PercentFormatter(1.0))
    all_vals = [v for pts in list(series_sec.values()) + [prom_vals] for v in pts if v is not None and not np.isnan(v)]
    min_y = max(0.4, min(all_vals + [obj_isc]) - 0.08) if all_vals else 0.4
    ax2.set_ylim(min_y, 1.12)
    ax2.tick_params(axis="y", colors="#333")
    ax2.set_ylabel("% ISC", color="#333", fontsize=10, fontweight="bold")
    ax2.spines["top"].set_visible(False)
    ax2.set_title(titulo, fontsize=13, fontweight="bold", pad=18, color="#1A1A1A")
    ax2.legend(loc="upper right", fontsize=8, frameon=True, facecolor="white", edgecolor="#CCC", framealpha=0.9)

    row_labels = [lbl_enc]; row_colors = [COLOR_BARRAS]; cell_data = [[f"{v:.0f}" for v in totales]]
    for nombre, pts in series_sec.items():
        row_labels.append(f"{nombre if len(nombre) <= 22 else nombre[:20]+'…'} (●)")
        row_colors.append(colores_sec[nombre])
        cell_data.append([f"{v:.1%}" if v is not None and not np.isnan(v) else "—" for v in pts])
    
    row_labels.append(lbl_prom); row_colors.append(COLOR_LINEA)
    cell_data.append([f"{v:.1%}" if v is not None and not np.isnan(v) else "—" for v in prom_vals])

    tbl = plt.table(cellText=cell_data, rowLabels=row_labels, colLabels=meses_ord, loc="bottom", cellLoc="center", rowColours=row_colors)
    for (r, c), cell in tbl.get_celld().items():
        if c == -1 and r > 0:
            cell.get_text().set_color("white"); cell.get_text().set_weight("bold"); cell.get_text().set_fontsize(7.5)
        if r == 0:
            cell.set_facecolor("#2C2C2C"); cell.get_text().set_color("white"); cell.get_text().set_weight("bold"); cell.get_text().set_fontsize(7.5)
        elif c != -1:
            cell.set_facecolor("#F4F4F4"); cell.get_text().set_color("#1A1A1A"); cell.get_text().set_fontsize(7.5)
    tbl.auto_set_font_size(False); tbl.scale(1, 1.6)

    buf = io.BytesIO()
    fig.savefig(buf, format="jpeg", facecolor="white", bbox_inches="tight", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

def render_tendencia():
    st.markdown("<div class='seccion-titulo'>Tendencia Histórica ISC</div>",unsafe_allow_html=True)

    PALETA_APS = ["#E91E63","#9C27B0","#3F51B5","#00BCD4","#4CAF50","#FF9800","#795548","#607D8B","#F44336","#009688","#CDDC39","#FF5722"]

    c1,c2,c3,c4 = st.columns([2,2,1,1])
    with c1: fytd_sel  = st.selectbox("Rango Graf:", todos_fytd, key="t_fytd")
    with c2: dealer_sel = st.selectbox("Filtro Dealer:", dealers_para_ciudad(CIUDAD), key="t_dlr")
    with c3:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        btn_gen  = st.button("Generar", key="t_btn",  use_container_width=True, type="primary")
    with c4:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        btn_acum = st.button("ACUM",    key="t_acum", use_container_width=True)

    if "tend_params" not in st.session_state: st.session_state.tend_params = {}
    if btn_gen or btn_acum: st.session_state.tend_params = {"fytd": fytd_sel, "dealer": dealer_sel, "acum": btn_acum, "ciudad": CIUDAD}
    elif st.session_state.tend_params.get("ciudad") != CIUDAD: st.session_state.tend_params = {}

    p = st.session_state.tend_params
    if not p: st.info("Selecciona los parámetros y presiona **Generar**."); return

    fytd = p["fytd"]; dealer = p["dealer"]; acum = p.get("acum", False); ciudad_p = p.get("ciudad", CIUDAD)
    modo_dealer = (dealer != "GENERAL")

    if modo_dealer:
        df_base = filtrar(D["isc_aps"], fytd=fytd, ciudad=ciudad_p, dealer=dealer)
        df_prom = filtrar(D["isc_mensual"], fytd=fytd, ciudad=ciudad_p, dealer=dealer)
    else:
        df_base = filtrar(D["isc_mensual"], fytd=fytd, ciudad=ciudad_p)
        df_prom = df_base

    if df_base.empty: st.warning("Sin datos para la selección."); return

    meses_ord = (df_base.drop_duplicates("mes_anio").sort_values("orden_mes")["mes_anio"].tolist())
    obj_isc = get_obj(D["objetivos"], fytd, "obj_isc")

    def serie(sub_df):
        pts = []
        for mes in meses_ord:
            if mes not in sub_df["mes_anio"].values: pts.append(np.nan); continue
            if acum: g = sub_df[sub_df["orden_mes"] <= sub_df[sub_df["mes_anio"]==mes]["orden_mes"].values[0]]
            else: g = sub_df[sub_df["mes_anio"] == mes]
            c = g["total_encuestas"].sum(); i16 = g["I16"].sum(); i78 = g["I78"].sum()
            pts.append((c - i16*2 - i78)/c if c > 0 else np.nan)
        return pts

    def totales_serie(sub_df):
        return [sub_df[sub_df["mes_anio"]==m]["total_encuestas"].sum() if m in sub_df["mes_anio"].values else 0 for m in meses_ord]

    prom_vals = serie(df_prom); totales = totales_serie(df_prom)
    series_sec = {}; colores_sec = {}; markers_sec = {}

    if modo_dealer:
        aps_lista = sorted([a for a in df_base["aps_nombre"].unique() if str(a).strip().upper() != "SIN DEALER"])
        for i, aps_n in enumerate(aps_lista):
            series_sec[aps_n] = serie(df_base[df_base["aps_nombre"] == aps_n])
            colores_sec[aps_n] = PALETA_APS[i % len(PALETA_APS)]; markers_sec[aps_n] = "o"
        lbl_prom = f"PROM {dealer} (●)"; titulo = f"ISC DEALER: {dealer.upper()} — {fytd}"; lbl_enc = f"Cant. Encuestas {dealer} (■)"
    else:
        dealers_lista = sorted([d for d in df_base["dealer"].unique() if d != "SIN DEALER"])
        for d in dealers_lista:
            series_sec[d] = serie(df_base[df_base["dealer"] == d])
            colores_sec[d] = COLORES_DEALERS.get(d, "#607D8B"); markers_sec[d] = MARKERS_DEALERS.get(d, "o")
        lbl_prom = f"PROM GENERAL (●)"; titulo = f"ISC VoC — {ciudad_p} — {fytd}"; lbl_enc = "Cant. Encuestas (■)"

    if acum: titulo += " (Acumulado)"

    img_bytes = cached_plot_tendencia(meses_ord, totales, prom_vals, series_sec, colores_sec, markers_sec, obj_isc, lbl_prom, titulo, lbl_enc)

    st.markdown("<div class='chart-box'>", unsafe_allow_html=True)
    st.image(img_bytes, use_container_width=True)
    st.download_button("Descargar Gráfica ISC", data=img_bytes, file_name="Tendencia_ISC.jpg", mime="image/jpeg", key="dl_tend")
    st.markdown("</div>", unsafe_allow_html=True)

# ==============================================================================

# SECCIÓN 3: ATRIBUTOS

# ==============================================================================

import matplotlib.patheffects as path_effects

@st.cache_data(show_spinner=False, max_entries=20)

def cached_jpg_attr_table(df_resumen, cols_eval):

    def sty_attr_c(row):

        s = [""] * len(row)

        for i2, cn in enumerate(row.index):

            if cn == "GAP": s[i2] = "color:#D32F2F;font-weight:700" if (pd.notna(row[cn]) and row[cn]<0) else "color:#388E3C;font-weight:700"

            elif cn in cols_eval: s[i2] = color_obj(row[cn], row.get("Objetivo", np.nan))

        return s

    fmt_dict = {'Objetivo': '{:.1%}', 'GAP': '{:+.1%}'}

    for c in cols_eval: fmt_dict[c] = '{:.1%}'

    styler_res = df_resumen.style.apply(sty_attr_c, axis=1).format(fmt_dict, na_rep="—").set_table_attributes('class="tabla-voc"').hide(axis="index")

    styler_res.map(lambda v: 'background-color: #A6A6A6; color: black;', subset=['Atributo'])

    return styler_to_jpg_buf(styler_res).getvalue()


@st.cache_data(show_spinner=False, max_entries=20)

def cached_hist_plot_attr(datos_hist_tabla, color_map, meses_h, obj_h, atr_hist_sel, dealer_sel, aps_sel):

    fig_h, ax_h = plt.subplots(figsize=(13, 7.5), facecolor="white")

    x_h = np.arange(len(meses_h))

    efecto = [path_effects.withStroke(linewidth=2.5, foreground="white", alpha=0.9)]

    for nombre, y_vals in datos_hist_tabla.items():

        if nombre == "TOTAL GENERAL" or nombre.startswith("TOTAL "):

            c = color_map.get(nombre, "#000000") 

            ax_h.plot(x_h, y_vals, color=c, linewidth=4, marker='D', markersize=8, zorder=6)

            for j, v in enumerate(y_vals):

                if pd.notna(v): ax_h.text(j, v + 0.035, f"{v:.1%}", ha='center', fontsize=10, fontweight='900', color=c, path_effects=efecto, zorder=7)

        else:

            c = color_map.get(nombre, "#1976D2")

            if aps_sel != "TODOS" and dealer_sel != "GENERAL":

                ax_h.plot(x_h, y_vals, color=c, linewidth=3, marker='o', markersize=9, zorder=3)

                for j, v in enumerate(y_vals):

                    if pd.notna(v): ax_h.text(j, v + 0.035, f"{v:.1%}", ha='center', fontsize=9.5, fontweight='bold', color=c, path_effects=efecto, zorder=5)

            else:

                ax_h.scatter(x_h, y_vals, color=c, s=90, zorder=4, alpha=0.9, edgecolors="white")

                for j, v in enumerate(y_vals):

                    if pd.notna(v): ax_h.text(j, v - 0.035, f"{v:.1%}", ha='center', fontsize=8, color=c, fontweight='bold', path_effects=efecto, zorder=5)



    ax_h.axhline(obj_h, color=COLOR_OBJETIVO, linestyle="--", linewidth=2, zorder=1)
    

    ax_h.set_xticks(x_h)

    ax_h.set_xticklabels([]) 

    ax_h.yaxis.set_major_formatter(mtick.PercentFormatter(1.0))

    

    all_vals = []

    for vals in datos_hist_tabla.values(): all_vals.extend([v for v in vals if pd.notna(v)])

    min_y = max(0, min(all_vals + [obj_h]) - 0.15) if all_vals else 0

    ax_h.set_ylim(min_y, 1.15)

    ax_h.set_title(f"Tendencia FYTD: {atr_hist_sel}\n({dealer_sel} / {aps_sel})", fontsize=13, fontweight='bold', pad=15)


    row_labels_raw = list(datos_hist_tabla.keys()) + ["Meta"]

    row_labels = [f"■  {k}" for k in row_labels_raw]

    cell_text = [[f"{v:.1%}" if pd.notna(v) else "—" for v in vals] for vals in datos_hist_tabla.values()] + [[f"{obj_h:.0%}"] * len(meses_h)]
    

    margen_dinamico = 0.12 + (len(row_labels) * 0.045)

    plt.subplots_adjust(bottom=margen_dinamico)

    

    tbl = ax_h.table(cellText=cell_text, rowLabels=row_labels, colLabels=meses_h, loc='bottom', cellLoc='center')

    tbl.auto_set_font_size(False); tbl.set_fontsize(8.5); tbl.scale(1, 1.8)

    

    for (r, c), cell in tbl.get_celld().items():

        if r == 0: 

            cell.set_facecolor("#1A1A2E"); cell.get_text().set_color("white"); cell.get_text().set_weight("bold")

        elif c == -1: 

            lbl_raw = row_labels_raw[r-1]

            cell.get_text().set_weight("bold")

            if lbl_raw in color_map: cell.get_text().set_color(color_map[lbl_raw])

            if "TOTAL" in str(lbl_raw): cell.set_facecolor("#E8E8E8")

            if "Meta" in str(lbl_raw): cell.set_facecolor("#FFF3E0")

    buf = io.BytesIO()

    fig_h.savefig(buf, format="jpeg", facecolor="white", bbox_inches="tight", dpi=150)

    plt.close(fig_h)

    buf.seek(0)

    return buf.getvalue()


def render_atributos():

    st.markdown("<div class='seccion-titulo'>📋 Resumen de Atributos</div>",unsafe_allow_html=True)

    c1,c2,c3,c4,c5,c6 = st.columns([1.5, 1.5, 1.5, 1.5, 1, 1])

    with c1: fytd_sel=st.selectbox("Periodo:",todos_fytd,key="a_fytd")

    with c2:

        meses_a=meses_de(D["atributos"],fytd_sel,CIUDAD)

        mes_sel=st.selectbox("Mes/Año:", ["TODOS"] + meses_a if meses_a else ["TODOS"], key="a_mes")

    with c3: dealer_sel=st.selectbox("Dealer:",dealers_para_ciudad(CIUDAD),key="a_dlr")

    with c4:

        aps_opts=aps_para_filtro(CIUDAD,dealer_sel,fytd_sel)

        aps_sel=st.selectbox("APS:",aps_opts,key="a_aps")

        

    if "atr_acum" not in st.session_state: st.session_state.atr_acum = False

    with c5:

        st.markdown("<div style='margin-top:28px'></div>",unsafe_allow_html=True)

        if st.button("Cargar",key="a_btn",use_container_width=True,type="primary"):

            st.session_state.atr_acum = False; st.rerun()

    with c6:

        st.markdown("<div style='margin-top:28px'></div>",unsafe_allow_html=True)

        if st.button("ACUM",key="a_btn_acum",use_container_width=True):

            st.session_state.atr_acum = True; st.rerun()



    if D["atributos"].empty or (mes_sel=="TODOS" and not meses_a): st.info("Sin datos."); return



    usar_aps_nivel=(aps_sel!="TODOS")


    df_all_city = aplicar_filtro_ciudad(D["atributos"], CIUDAD)

    if df_all_city.empty: st.warning("Sin datos."); return    

    df_all_city = df_all_city.sort_values("orden_mes")

    todos_meses_cron = df_all_city.drop_duplicates("mes_anio")["mes_anio"].tolist()

    meses_cron_fytd = meses_de(D["atributos"], fytd_sel, CIUDAD)


    if mes_sel == 'TODOS':

        meses_act_eval = meses_cron_fytd

        meses_prev_eval = []

    elif st.session_state.atr_acum:

        if mes_sel in meses_cron_fytd:

            idx = meses_cron_fytd.index(mes_sel)

            meses_act_eval = meses_cron_fytd[:idx+1]

        else:

            meses_act_eval = []

        meses_prev_eval = []

    else:

        meses_act_eval = [mes_sel]

        idx_actual = todos_meses_cron.index(mes_sel) if mes_sel in todos_meses_cron else -1

        meses_prev_eval = [todos_meses_cron[idx_actual - 1]] if idx_actual > 0 else []



    if usar_aps_nivel:

        df_act = filtrar(D["atrib_aps"], fytd=None, ciudad=CIUDAD, dealer=dealer_sel if dealer_sel!="GENERAL" else None, aps=aps_sel)

    else:

        df_act = filtrar(D["atributos"] if dealer_sel == "GENERAL" else D["atrib_aps"], fytd=None, ciudad=CIUDAD, dealer=dealer_sel if dealer_sel!="GENERAL" else None)



    df_act = df_act[~df_act["dealer"].astype(str).str.upper().str.contains("SIN DEALER", na=False)]

    if "aps_nombre" in df_act.columns:

        df_act = df_act[~df_act["aps_nombre"].astype(str).str.upper().str.contains("SIN ASESOR", na=False)]



    if df_act.empty: st.warning("Sin datos para la selección."); return



    score_col="pct_score" if "pct_score" in df_act.columns else "pct_top2box"

    obj_col = "obj_atributo"



    def calc_atr_vals(meses_filtro, df_fuente):

        df_proc = df_fuente[df_fuente["mes_anio"].isin(meses_filtro)]

        if df_proc.empty: return {}

        res = {}

        for attr, g in df_proc.groupby("atributo"):

            if "n_respuestas" in g.columns and g["n_respuestas"].sum() > 0:

                res[attr] = (g[score_col]*g["n_respuestas"]).sum()/g["n_respuestas"].sum()

            else:

                res[attr] = g[score_col].mean()

        return res



    vals_act_main = calc_atr_vals(meses_act_eval, df_act)

    vals_prev_main = calc_atr_vals(meses_prev_eval, df_act)

    df_act_current = df_act[df_act["mes_anio"].isin(meses_act_eval)]


    st.markdown("<div class='chart-box'>",unsafe_allow_html=True)


    if not usar_aps_nivel and dealer_sel == "GENERAL":

        dealers_en_datos = sorted([d for d in df_act_current["dealer"].unique() if d != "SIN DEALER"])

        vals_dealers = {d: calc_atr_vals(meses_act_eval, df_act[df_act["dealer"] == d]) for d in dealers_en_datos}

        data_tbl = []

        todos_atributos = sorted([a for a in set(vals_act_main.keys()).union(set(vals_prev_main.keys())) if "bien a la primera h1" not in a.lower()])

        for a in todos_atributos:

            row = {'Atributo': a}

            for d in dealers_en_datos: row[d] = vals_dealers[d].get(a, np.nan)

            val_gen = vals_act_main.get(a, np.nan); val_prev = vals_prev_main.get(a, np.nan)

            row['GENERAL'] = val_gen; row['GAP'] = val_gen - val_prev if pd.notna(val_gen) and pd.notna(val_prev) else np.nan

            obj_raw = df_act_current[df_act_current['atributo'] == a][obj_col].mean()

            row['Objetivo'] = obj_raw / 100.0 if pd.notna(obj_raw) and obj_raw > 2 else obj_raw

            data_tbl.append(row)

        df_resumen = pd.DataFrame(data_tbl)

        if not df_resumen.empty:

            df_resumen = df_resumen.sort_values(by='GENERAL', ascending=True).reset_index(drop=True)

            cols_orden = ['Atributo'] + dealers_en_datos + ['GENERAL', 'Objetivo', 'GAP']

            df_resumen = df_resumen[[c for c in cols_orden if c in df_resumen.columns]]

        fmt_dict = {'GENERAL': '{:.1%}', 'Objetivo': '{:.1%}', 'GAP': '{:+.1%}'}

        for d in dealers_en_datos: fmt_dict[d] = '{:.1%}'

        cols_cache = tuple(dealers_en_datos + ["GENERAL"])

        styler_res = df_resumen.style.apply(lambda row: ["color:#D32F2F;font-weight:700" if c == "GAP" and (pd.notna(row[c]) and row[c]<0) else ("color:#388E3C;font-weight:700" if c == "GAP" else (color_obj(row[c], row.get("Objetivo", np.nan)) if c in dealers_en_datos + ["GENERAL"] else "")) for c in row.index], axis=1).format(fmt_dict, na_rep="—").set_table_attributes('class="tabla-voc"').hide(axis="index")



    elif not usar_aps_nivel and dealer_sel != "GENERAL":

        aps_en_datos = sorted([a for a in df_act_current["aps_nombre"].unique() if a != "SIN ASESOR"])

        vals_aps = {a: calc_atr_vals(meses_act_eval, df_act[df_act["aps_nombre"] == a]) for a in aps_en_datos}

        data_tbl = []

        todos_atributos = sorted([a for a in set(vals_act_main.keys()).union(set(vals_prev_main.keys())) if "bien a la primera h1" not in a.lower()])

        for a in todos_atributos:

            row = {'Atributo': a}

            for aps in aps_en_datos: row[aps] = vals_aps[aps].get(a, np.nan)

            val_gen = vals_act_main.get(a, np.nan); val_prev = vals_prev_main.get(a, np.nan)

            row['GENERAL'] = val_gen; row['GAP'] = val_gen - val_prev if pd.notna(val_gen) and pd.notna(val_prev) else np.nan

            obj_raw = df_act_current[df_act_current['atributo'] == a][obj_col].mean()

            row['Objetivo'] = obj_raw / 100.0 if pd.notna(obj_raw) and obj_raw > 2 else obj_raw

            data_tbl.append(row)

        df_resumen = pd.DataFrame(data_tbl)

        if not df_resumen.empty:

            df_resumen = df_resumen.sort_values(by='GENERAL', ascending=True).reset_index(drop=True)

            cols_orden = ['Atributo'] + aps_en_datos + ['GENERAL', 'Objetivo', 'GAP']

            df_resumen = df_resumen[[c for c in cols_orden if c in df_resumen.columns]]

        fmt_dict = {'GENERAL': '{:.1%}', 'Objetivo': '{:.1%}', 'GAP': '{:+.1%}'}

        for aps in aps_en_datos: fmt_dict[aps] = '{:.1%}'

        cols_cache = tuple(aps_en_datos + ["GENERAL"])

        styler_res = df_resumen.style.apply(lambda row: ["color:#D32F2F;font-weight:700" if c == "GAP" and (pd.notna(row[c]) and row[c]<0) else ("color:#388E3C;font-weight:700" if c == "GAP" else (color_obj(row[c], row.get("Objetivo", np.nan)) if c in aps_en_datos + ["GENERAL"] else "")) for c in row.index], axis=1).format(fmt_dict, na_rep="—").set_table_attributes('class="tabla-voc"').hide(axis="index")



    else:

        data_tbl = []

        todos_atributos = sorted([a for a in set(vals_act_main.keys()).union(set(vals_prev_main.keys())) if "bien a la primera h1" not in a.lower()])

        for a in todos_atributos:

            val_a = vals_act_main.get(a, np.nan); val_p = vals_prev_main.get(a, np.nan)

            obj_raw = df_act_current[df_act_current['atributo'] == a][obj_col].mean()

            obj_v = obj_raw / 100.0 if pd.notna(obj_raw) and obj_raw > 2 else obj_raw

            data_tbl.append({'Atributo': a, '%': val_a, 'Objetivo': obj_v, 'GAP': val_a - val_p if pd.notna(val_a) and pd.notna(val_p) else np.nan})

        df_resumen = pd.DataFrame(data_tbl)

        if not df_resumen.empty:

            df_resumen = df_resumen.sort_values(by='%', ascending=True).reset_index(drop=True)

            df_resumen = df_resumen[['Atributo', '%', 'Objetivo', 'GAP']]

        cols_cache = tuple(["%"])

        styler_res = df_resumen.style.apply(lambda row: ["color:#D32F2F;font-weight:700" if c == "GAP" and (pd.notna(row[c]) and row[c]<0) else ("color:#388E3C;font-weight:700" if c == "GAP" else (color_obj(row[c], row.get("Objetivo", np.nan)) if c == "%" else "")) for c in row.index], axis=1).format({'%': '{:.1%}', 'Objetivo': '{:.1%}', 'GAP': '{:+.1%}'}, na_rep="—").set_table_attributes('class="tabla-voc"').hide(axis="index")



    styler_res.map(lambda v: 'background-color: #A6A6A6; color: black;', subset=['Atributo'])



    col_rs_izq, col_rs_esp, col_rs_der = st.columns([1.4, 0.2, 1.4])

    with col_rs_izq: 

        st.markdown(tabla_html(styler_res), unsafe_allow_html=True)

        st.download_button("📥 Descargar Tabla", data=cached_jpg_attr_table(df_resumen, cols_cache), file_name="Atributos.jpg", mime="image/jpeg", key="dl_attr")


    with col_rs_der:

        st.markdown("<div style='margin-bottom:15px; font-weight:900; color:#1A1A1A; font-size:22px;'>Atributos en Alerta</div>", unsafe_allow_html=True)

        col_eval = 'GENERAL' if not usar_aps_nivel else '%'

        if df_resumen.empty: st.info("Sin datos.")

        else:

            alertas = df_resumen[df_resumen[col_eval] < df_resumen['Objetivo']]

            if alertas.empty: st.success("✅ Todo sobre el objetivo.")

            else:

                for index, row in alertas.iterrows():

                    atr_n = row['Atributo']; gap_val = row['GAP']; obj_req = row['Objetivo']

                    gap_str = "N/A" if pd.isna(gap_val) else (f"📉 {gap_val:+.1%}" if gap_val < 0 else f"📈 {gap_val:+.1%}")

                    st.markdown(f"<div style='background:rgba(211,47,47,0.1); border-left:5px solid #D32F2F; padding:10px; margin-bottom:10px;'><span style='color: black;'><b>{atr_n}</b> (Meta: {obj_req:.0%})</span><br><span style='color:#D32F2F; font-size:18px; font-weight:900;'>{row[col_eval]:.1%}</span> <small style='color:#555;'>GAP: {gap_str}</small></div>", unsafe_allow_html=True)

    st.markdown("</div>",unsafe_allow_html=True)


    # =========================================================================

    # ANÁLISIS HISTÓRICO: GRÁFICA + TABLA LEYENDA

    # =========================================================================

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    st.markdown("<div class='seccion-titulo'>📊 Análisis Histórico de Atributos</div>", unsafe_allow_html=True)


    df_h_base = aplicar_filtro_ciudad(D["atributos"], CIUDAD)

    df_h_base = df_h_base[df_h_base["fytd"] == fytd_sel]

    df_h_aps = aplicar_filtro_ciudad(D["atrib_aps"], CIUDAD)

    if not df_h_aps.empty: df_h_aps = df_h_aps[df_h_aps["fytd"] == fytd_sel]



    lista_atr_hist = sorted([a for a in df_h_base["atributo"].unique() if "bien a la primera h1" not in a.lower()])

    if not lista_atr_hist: return

    atr_hist_sel = st.selectbox("Selecciona un Atributo:", lista_atr_hist, key="h_atr_sel")

    meses_h = meses_de(D["atributos"], fytd_sel, CIUDAD)

    

    df_h_b_sel = df_h_base[df_h_base["atributo"] == atr_hist_sel]

    df_h_a_sel = df_h_aps[df_h_aps["atributo"] == atr_hist_sel] if not df_h_aps.empty else pd.DataFrame()



    def get_trend(df_sub):

        vals = []

        for m in meses_h:

            g = df_sub[df_sub["mes_anio"] == m]

            if g.empty: vals.append(np.nan)

            else:

                if "n_respuestas" in g.columns and g["n_respuestas"].sum() > 0:

                    vals.append((g[score_col]*g["n_respuestas"]).sum()/g["n_respuestas"].sum())

                else: vals.append(g[score_col].mean())

        return vals



    datos_hist_tabla = {}

    color_map = {}

    obj_h_raw = df_h_b_sel[obj_col].iloc[0] if not df_h_b_sel.empty else 0.85

    obj_h = obj_h_raw / 100.0 if obj_h_raw > 2 else obj_h_raw

    PALETA = ["#1976D2", "#D32F2F", "#388E3C", "#9C27B0", "#FF9800", "#00BCD4", "#E91E63", "#795548", "#607D8B"]



    if dealer_sel == "GENERAL":

        datos_hist_tabla["TOTAL GENERAL"] = get_trend(df_h_b_sel)

        color_map["TOTAL GENERAL"] = "#000000"

        for i, d in enumerate(sorted(df_h_b_sel["dealer"].unique())):

            datos_hist_tabla[d] = get_trend(df_h_b_sel[df_h_b_sel["dealer"] == d])

            color_map[d] = PALETA[i % len(PALETA)]

                

    elif dealer_sel != "GENERAL" and aps_sel == "TODOS":

        datos_hist_tabla[f"TOTAL {dealer_sel}"] = get_trend(df_h_b_sel[df_h_b_sel["dealer"] == dealer_sel])

        color_map[f"TOTAL {dealer_sel}"] = "#000000"

        aps_list = sorted(df_h_a_sel[df_h_a_sel["dealer"] == dealer_sel]["aps_nombre"].unique())

        for i, a in enumerate(aps_list):

            datos_hist_tabla[a] = get_trend(df_h_a_sel[(df_h_a_sel["dealer"] == dealer_sel) & (df_h_a_sel["aps_nombre"] == a)])

            color_map[a] = PALETA[i % len(PALETA)]

    else:

        datos_hist_tabla[aps_sel] = get_trend(df_h_a_sel[(df_h_a_sel["dealer"] == dealer_sel) & (df_h_a_sel["aps_nombre"] == aps_sel)])

        color_map[aps_sel] = "#1976D2"



    color_map["Meta"] = COLOR_OBJETIVO



    img_bytes = cached_hist_plot_attr(datos_hist_tabla, color_map, meses_h, obj_h, atr_hist_sel, dealer_sel, aps_sel)

    

    st.markdown("<div class='chart-box'>", unsafe_allow_html=True)

    st.image(img_bytes, use_container_width=True)

    st.download_button("📥 Descargar Gráfica Completa", data=img_bytes, file_name=f"Tendencia_{atr_hist_sel}.jpg", mime="image/jpeg", key="dl_hist_full")

    st.markdown("</div>", unsafe_allow_html=True)


# ==============================================================================
# SECCIÓN 4: RADIAL
# ==============================================================================

import matplotlib.patheffects as path_effects

def render_radial():
    st.markdown("<div class='seccion-titulo'>Comparativo Radial de Talleres</div>",unsafe_allow_html=True)
    
    c1,c2,c3,c4,c5=st.columns([2,2,2,1,1])
    with c1: fytd_sel=st.selectbox("Periodo:",todos_fytd,key="r_fytd")
    with c2:
        meses_r=meses_de(D["atributos"],fytd_sel,CIUDAD)
        mes_sel=st.selectbox("Mes/Año:", ["TODOS"] + meses_r if meses_r else ["TODOS"], key="r_mes")
    with c3:
        dlrs_r=dealers_para_ciudad(CIUDAD)
        dealer_sel=st.selectbox("Dealer:",dlrs_r,key="r_dlr")
        
    if "rad_acum" not in st.session_state: st.session_state.rad_acum = False
    with c4:
        st.markdown("<div style='margin-top:28px'></div>",unsafe_allow_html=True)
        if st.button("Cargar",key="r_btn",use_container_width=True,type="primary"):
            st.session_state.rad_acum = False; st.rerun()
    with c5:
        st.markdown("<div style='margin-top:28px'></div>",unsafe_allow_html=True)
        if st.button("ACUM",key="r_btn_acum",use_container_width=True):
            st.session_state.rad_acum = True; st.rerun()

    if D["atributos"].empty or (mes_sel=="TODOS" and not meses_r): st.info("Sin datos."); return

    if mes_sel == 'TODOS': meses_r_eval = meses_r
    elif st.session_state.rad_acum:
        if mes_sel in meses_r:
            idx = meses_r.index(mes_sel)
            meses_r_eval = meses_r[:idx+1]
        else: meses_r_eval = []
    else:
        meses_r_eval = [mes_sel]

    if dealer_sel == "GENERAL":
        df_a = filtrar(D["atributos"], fytd=fytd_sel, ciudad=CIUDAD)
    else:
        df_a = filtrar(D["atrib_aps"], fytd=fytd_sel, ciudad=CIUDAD, dealer=dealer_sel)
        
    df_a = df_a[df_a["mes_anio"].isin(meses_r_eval)]
    df_a = df_a[df_a["dealer"] != "SIN DEALER"]
    if "aps_nombre" in df_a.columns:
        df_a = df_a[~df_a["aps_nombre"].astype(str).str.upper().str.contains("SIN ASESOR", na=False)]
        
    if df_a.empty: st.warning("Sin datos para la selección."); return

    if dealer_sel == "GENERAL":
        entidades = sorted(df_a["dealer"].unique())
        col_nombre = "dealer"
        prefijo = "Taller: "
        PALETA_RADIAL = ["#1976D2", "#D32F2F", "#388E3C", "#9C27B0", "#FF9800", "#00BCD4", "#E91E63", "#795548", "#607D8B"]
    else:
        entidades = sorted(df_a["aps_nombre"].unique())
        col_nombre = "aps_nombre"
        prefijo = "APS: "
        PALETA_RADIAL = ["#E91E63","#9C27B0","#3F51B5","#00BCD4","#4CAF50","#FF9800","#795548","#607D8B","#F44336","#009688","#CDDC39","#FF5722"]

    attrs_disponibles=sorted(df_a["atributo"].unique())
    if not attrs_disponibles: st.warning("Sin atributos."); return

    score_col="pct_score" if "pct_score" in df_a.columns else "pct_top2box"

    def gral_dict(df_sub):
        res={}
        for attr in attrs_disponibles:
            sub=df_sub[df_sub["atributo"]==attr]
            if sub.empty: continue
            res[attr] = (sub[score_col]*sub["n_respuestas"]).sum()/sub["n_respuestas"].sum() if "n_respuestas" in sub.columns and sub["n_respuestas"].sum()>0 else sub[score_col].mean()
        return res

    gral=gral_dict(df_a); obj_isc_r=get_obj(D["objetivos"],fytd_sel,"obj_isc")
    N=len(attrs_disponibles); angles=np.linspace(0,2*np.pi,N,endpoint=False).tolist(); angles+=angles[:1]

    fig,ax=plt.subplots(figsize=(11,11),subplot_kw=dict(polar=True),facecolor="white"); ax.set_facecolor("#FAFAFA")
    efecto = [path_effects.withStroke(linewidth=2, foreground="white", alpha=0.9)]

    vg=[gral.get(a,0) for a in attrs_disponibles]+[gral.get(attrs_disponibles[0],0)]
    ax.fill(angles,vg,alpha=0.15,color="#1A1A2E"); ax.plot(angles,vg,color="#1A1A2E",linewidth=3,linestyle="-",label="PROM. GENERAL")
    ax.plot(angles,[obj_isc_r]*(N+1),color=COLOR_OBJETIVO,linewidth=2,linestyle="--",label=f"Objetivo ({obj_isc_r:.0%})")

    for i, ent in enumerate(entidades):
        sub_d=df_a[df_a[col_nombre]==ent]; dd=gral_dict(sub_d)
        vd=[dd.get(a,0) for a in attrs_disponibles]+[dd.get(attrs_disponibles[0],0)]
        color_d = PALETA_RADIAL[i % len(PALETA_RADIAL)]
        ax.plot(angles,vd,color=color_d,linewidth=1.8,linestyle="-",marker='o',markersize=5,label=f"{prefijo}{ent}")
        
        for j, val in enumerate(vd[:-1]):
            if pd.notna(val) and val > 0:
                ax.text(angles[j], val + 0.05, f"{val:.0%}", color=color_d, fontsize=8, fontweight='bold', ha='center', va='center', path_effects=efecto, zorder=5)

    for i,(angle,attr) in enumerate(zip(angles[:-1],attrs_disponibles)):
        val_g=gral.get(attr,0); ha="left" if angle<np.pi else "right"
        if abs(angle-np.pi/2)<0.1 or abs(angle-3*np.pi/2)<0.1: ha="center"
        ax.text(angle,1.25,f"{textwrap.fill(attr,14)}\n{val_g:.1%}", ha=ha,va="center",fontsize=8,color="#1A1A1A",fontweight="bold",linespacing=1.3)

    ax.set_xticks([]); ax.set_yticks([0.2,0.4,0.6,0.8,1.0]); ax.set_yticklabels(["20%","40%","60%","80%","100%"],fontsize=7,color="#888")
    ax.set_ylim(0,1.25); ax.spines["polar"].set_color("#DDD"); ax.grid(color="#DDD",linewidth=0.7)
    
    str_acum = " (ACUM)" if st.session_state.rad_acum and mes_sel != "TODOS" else ""
    ax.set_title(f"Comparativo Radial ({mes_sel}{str_acum}){f' — {dealer_sel}' if dealer_sel!='GENERAL' else ''}",fontsize=16,fontweight="bold",pad=40,color="#1A1A1A")
    
    ax.legend(loc="lower center",bbox_to_anchor=(0.5,-0.15),fontsize=9,frameon=True,facecolor="white",edgecolor="#CCC", ncol=3)

    st.markdown("<div class='chart-box'>",unsafe_allow_html=True)
    _,col_c,_=st.columns([0.1,2.8,0.1]); col_c.pyplot(fig,transparent=False)
    st.download_button("Descargar Radial", data=fig_to_buf(fig), file_name="Radial.jpg", mime="image/jpeg", key="dl_rad")
    st.markdown("</div>",unsafe_allow_html=True)

# ==============================================================================
# SECCIÓN 5: PENDIENTES
# ==============================================================================

@st.cache_data(show_spinner=False, max_entries=10)
def cached_jpg_export(df, table_type):
    if table_type == 'pendientes':
        styler = df.style.apply(lambda row: ["color:#D32F2F;font-weight:bold" if c == "Estado" and row["Estado"] == "Expirado" else ("color:#FF8C00;font-weight:bold" if c == "Estado" and row["Estado"] == "Contacto en uso" else "") for c in row.index], axis=1).set_table_attributes('class="tabla-voc"').hide(axis="index")
    elif table_type == 'resumen':
        styler = df.style.apply(lambda row: ["background-color:#ffcccc;color:black;font-weight:bold" if c == "Vencidas" and row["Vencidas"] > 0 else ("background-color:#E8E8E8;font-weight:bold" if row["Asesor"] == "TOTAL GENERAL" else "") for c in row.index], axis=1).format({"Total Enviadas":"{:.0f}", "Completadas":"{:.0f}", "Vencidas":"{:.0f}", "Pendientes":"{:.0f}"}).set_table_attributes('class="tabla-voc"').hide(axis="index")
    return styler_to_jpg_buf(styler).getvalue()

@st.cache_data(show_spinner=False)
def procesar_fechas_pendientes(df_raw, ciudad):
    if df_raw.empty: return df_raw
    df_p = aplicar_filtro_ciudad(df_raw.copy(), ciudad)
    df_p = df_p.drop_duplicates(subset=["mes_anio", "dealer", "aps_nombre", "cliente_nombre", "cliente_celular"], keep="last")
    df_p['fecha_validez_dt'] = pd.to_datetime(df_p['fecha_validez'], format='mixed', dayfirst=True, errors='coerce')
    return df_p

def render_pendientes():
    st.markdown("<div class='seccion-titulo'>Encuestas Pendientes</div>",unsafe_allow_html=True)
    if D["pendientes"].empty: st.info("Sin datos. Ejecuta el preprocesador."); return
    
    df_p = procesar_fechas_pendientes(D["pendientes"], CIUDAD)

    c1,c2,c3,c4 = st.columns(4)
    with c1: fytd_p = st.selectbox("Periodo:", sorted(df_p["fytd"].unique(), reverse=True), key="p_fytd")
    with c2: mes_p = st.selectbox("Mes:", ["TODOS"] + sorted(df_p[df_p["fytd"]==fytd_p]["mes_anio"].unique()), key="p_mes")
    with c3: dealer_p = st.selectbox("Dealer:", ["LISTADO GENERAL"] + sorted(df_p["dealer"].unique()), key="p_dlr")

    df_f = df_p[df_p["fytd"] == fytd_p].copy()
    if mes_p != "TODOS": df_f = df_f[df_f["mes_anio"] == mes_p]
    if dealer_p != "LISTADO GENERAL": df_f = df_f[df_f["dealer"] == dealer_p]

    with c4:
        aps_list = ["TODOS"] + sorted([str(x) for x in df_f["aps_nombre"].unique() if pd.notna(x) and str(x).strip() != ""])
        aps_p = st.selectbox("Asesor (APS):", aps_list, key="p_aps")
    
    if aps_p != "TODOS": df_f = df_f[df_f["aps_nombre"] == aps_p]

    if "filtro_estado_p" not in st.session_state: st.session_state.filtro_estado_p = "TODOS"

    # =========================================================================
    # EXTRACCIÓN DE TOTALES MAESTROS
    # =========================================================================
    df_tre = aplicar_filtro_ciudad(D["tre_aps"].copy(), CIUDAD)
    if not df_tre.empty:
        df_tre = df_tre[df_tre["fytd"] == fytd_p]
        if mes_p != "TODOS": df_tre = df_tre[df_tre["mes_anio"] == mes_p]
        if dealer_p != "LISTADO GENERAL": df_tre = df_tre[df_tre["dealer"] == dealer_p]
        if aps_p != "TODOS": df_tre = df_tre[df_tre["aps_nombre"] == aps_p]
        
        tot_env = df_tre['E'].sum()
        tot_comp = df_tre['C'].sum()
    else:
        tot_env = 0
        tot_comp = 0

    # =========================================================================
    # LÓGICA DE CADUCIDAD ROBUSTA
    # =========================================================================
    hoy = pd.Timestamp.today().normalize()
    
    df_f['status_calc'] = df_f['status']
    df_f.loc[(df_f['status_calc'] == 'Contacto en uso') & (df_f['fecha_validez_dt'] < hoy), 'status_calc'] = 'Expirado'

    tot_exp = (df_f["status_calc"] == "Expirado").sum()
    tot_pen = (df_f["status_calc"] == "Contacto en uso").sum()

    k1,k2,k3 = st.columns(3)
    with k1:
        st.markdown(kpi_html("Total Encuestas", tot_env, "{:.0f}"), unsafe_allow_html=True)
        if st.button("Listar Todos", key="btn_ptod", use_container_width=True): st.session_state.filtro_estado_p = "TODOS"; st.rerun()
    with k2:
        st.markdown(kpi_html("Vencidas", tot_exp, "{:.0f}", color="#D32F2F"), unsafe_allow_html=True)
        if st.button("Listar Vencidas", key="btn_pexp", use_container_width=True): st.session_state.filtro_estado_p = "Expirado"; st.rerun()
    with k3:
        st.markdown(kpi_html("Pendientes", tot_pen, "{:.0f}", color="#FF8C00"), unsafe_allow_html=True)
        if st.button("Listar Pendientes", key="btn_puso", use_container_width=True): st.session_state.filtro_estado_p = "Contacto en uso"; st.rerun()

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    df_mostrar = df_f[df_f["status_calc"] == st.session_state.filtro_estado_p] if st.session_state.filtro_estado_p != "TODOS" else df_f.copy()

    # --- TABLA 1: DETALLE DE PENDIENTES ---
    st.markdown("<div class='chart-box'>",unsafe_allow_html=True)
    titulo_estado = "" if st.session_state.filtro_estado_p == 'TODOS' else f" ({st.session_state.filtro_estado_p.upper()})"
    st.markdown(f"**DETALLE DE PENDIENTES — {dealer_p} · {CIUDAD}{titulo_estado}**")
    
    ds_p = df_mostrar[["aps_nombre","cliente_nombre","cliente_celular","cliente_mail","fecha_validez", "status_calc"]].copy()
    ds_p.columns = ["Asesor","Nombre del Cliente","Celular","Mail","Fecha Validez", "Estado"]
    ds_p = ds_p.sort_values("Asesor").reset_index(drop=True)
    ds_p["Celular"] = ds_p["Celular"].astype(str).str.replace(".0","",regex=False)
    
    st_p = ds_p.style.apply(lambda row: ["color:#D32F2F;font-weight:bold" if c == "Estado" and row["Estado"] == "Expirado" else ("color:#FF8C00;font-weight:bold" if c == "Estado" and row["Estado"] == "Contacto en uso" else "") for c in row.index], axis=1).set_table_attributes('class="tabla-voc"').hide(axis="index")
    st.markdown(tabla_html(st_p), unsafe_allow_html=True)
    
    if not ds_p.empty:
        jpg_bytes_pend = cached_jpg_export(ds_p, 'pendientes')
        st.download_button("Descargar Pendientes", data=jpg_bytes_pend, file_name="Pendientes.jpg", mime="image/jpeg", key="dl_pend")
    st.markdown("</div>",unsafe_allow_html=True)

    # --- TABLA 2: RESUMEN POR ASESOR ---
    st.markdown("<div class='chart-box'>",unsafe_allow_html=True)
    st.markdown("**Resumen por Asesor**")
    
    if not df_tre.empty:
        df_tre_aps = df_tre.groupby("aps_nombre").agg(Total_Enviadas=("E", "sum"), Completadas=("C", "sum")).reset_index()
        df_tre_aps.columns = ["Asesor", "Total Enviadas", "Completadas"]
    else:
        df_tre_aps = pd.DataFrame(columns=["Asesor", "Total Enviadas", "Completadas"])

    df_f['is_exp'] = (df_f["status_calc"] == "Expirado").astype(int)
    df_f['is_uso'] = (df_f["status_calc"] == "Contacto en uso").astype(int)
    res_a = df_f.groupby("aps_nombre").agg(Vencidas=("is_exp", "sum"), Pendientes=("is_uso", "sum")).reset_index()
    res_a.rename(columns={"aps_nombre": "Asesor"}, inplace=True)

    if not df_tre_aps.empty:
        res_a = pd.merge(df_tre_aps, res_a, on="Asesor", how="outer").fillna(0)
        res_a = res_a.sort_values("Total Enviadas", ascending=False)
    
    if not res_a.empty:
        tot_ra = pd.DataFrame([{
            "Asesor": "TOTAL GENERAL", 
            "Total Enviadas": res_a["Total Enviadas"].sum(), 
            "Completadas": res_a["Completadas"].sum(), 
            "Vencidas": res_a["Vencidas"].sum(), 
            "Pendientes": res_a["Pendientes"].sum()
        }])
        res_a = pd.concat([res_a, tot_ra], ignore_index=True)
        for col in ["Total Enviadas", "Completadas", "Vencidas", "Pendientes"]:
            if col in res_a.columns: res_a[col] = res_a[col].astype(int)

    st_ra = res_a.style.apply(lambda row: ["background-color:#ffcccc;color:black;font-weight:bold" if c == "Vencidas" and row["Vencidas"] > 0 else ("background-color:#E8E8E8;font-weight:bold" if row["Asesor"] == "TOTAL GENERAL" else "") for c in row.index], axis=1).format({"Total Enviadas":"{:.0f}", "Completadas":"{:.0f}", "Vencidas":"{:.0f}", "Pendientes":"{:.0f}"}).set_table_attributes('class="tabla-voc"').hide(axis="index")
    st.markdown(tabla_html(st_ra), unsafe_allow_html=True)
    
    if not res_a.empty:
        jpg_bytes_res = cached_jpg_export(res_a, 'resumen')
        st.download_button("Descargar Resumen", data=jpg_bytes_res, file_name="Resumen_Asesor.jpg", mime="image/jpeg", key="dl_res_asesor")
    st.markdown("</div>",unsafe_allow_html=True)

# ==============================================================================
# SECCIÓN 6: VERBALIZACIONES
# ==============================================================================
def render_verbalizaciones():
    st.markdown("<div class='seccion-titulo'>Análisis de Verbalizaciones del Cliente</div>",unsafe_allow_html=True)

    if D["verbaliz"].empty:
        st.info("No hay datos de verbalizaciones cargados. Ve al preprocesador para subirlos."); return

    # Pre-filtrar por ciudad antes de calcular meses disponibles
    df_base = aplicar_filtro_ciudad(D["verbaliz"].copy(), CIUDAD)

    if df_base.empty:
        st.info(f"Sin verbalizaciones para {CIUDAD}."); return

    # mes_anio y orden_mes ya vienen reconstruidos desde cargar_datos()
    # Verificación defensiva por si algún registro escapa la reconstrucción
    if "mes_anio" not in df_base.columns:
        df_base["mes_anio"] = np.nan
    if "orden_mes" not in df_base.columns:
        df_base["orden_mes"] = 0
    df_base = _reconstruir_mes_anio(df_base)

    c1, c2, c3, c4 = st.columns([1.5, 1.5, 1, 1])
    with c1:
        fytd_sel = st.selectbox("Periodo:", todos_fytd, key="v_fytd_dash")

    # Meses disponibles directamente desde verbalizaciones, no desde atributos
    mm = meses_de(df_base, fytd_sel)

    with c2:
        mes_sel = st.selectbox("Mes/Año:", ["TODOS"] + mm if mm else ["TODOS"], key="v_mes_dash")

    # Resetear acum si cambia el FYTD
    if st.session_state.get("v_fytd_prev") != fytd_sel:
        st.session_state.ver_acum = False
        st.session_state.v_fytd_prev = fytd_sel

    if "ver_acum" not in st.session_state:
        st.session_state.ver_acum = False

    with c3:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        if st.button("Cargar", key="v_btn", use_container_width=True, type="primary"):
            st.session_state.ver_acum = False
            st.rerun()
    with c4:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        if st.button("ACUM", key="v_btn_acum", use_container_width=True):
            st.session_state.ver_acum = True
            st.rerun()

    # Determinar meses a incluir
    if mes_sel == "TODOS":
        meses_proc = mm
    elif st.session_state.ver_acum:
        meses_proc = mm[:mm.index(mes_sel) + 1] if mes_sel in mm else []
    else:
        meses_proc = [mes_sel]

    if not meses_proc:
        st.warning("Sin datos para esta selección."); return

    # Filtrar por FYTD y meses (usando mes_anio)
    df = df_base[
        (df_base["fytd"] == fytd_sel) &
        (df_base["mes_anio"].isin(meses_proc))
    ].copy()

    if df.empty:
        st.warning("Sin datos para esta selección."); return

    label_periodo = f"ACUM hasta {mes_sel}" if st.session_state.ver_acum and mes_sel != "TODOS" else mes_sel

    def clean_pct(x):
        try:
            if pd.isna(x) or str(x).strip() in ["-", ""]: return 0.0
            return float(str(x).split('%')[0].strip())
        except: return 0.0

    df["sat_neta"]      = df["SATISFACCIÓN NETA"].apply(clean_pct)
    df["menciones_pct"] = df["Comentarios relacionados"].apply(clean_pct)

    df_plot = df.groupby("Sub-Categoría").agg(
        sat_neta=("sat_neta", "mean"),
        menciones_pct=("menciones_pct", "sum"),
        Categoría=("Categoría", "first")
    ).reset_index()
    df_graficos = df_plot[df_plot["menciones_pct"] > 0].reset_index(drop=True)

    if df_graficos.empty:
        st.info("No hay menciones registradas (mayores a 0%) en este periodo."); return

    def titulo_grafica(texto):
        return (
            "<div style='text-align:center;margin-bottom:15px'>"
            f"<div style='font-family:\"Barlow Condensed\",sans-serif;font-size:22px;"
            f"font-weight:900;color:#1A1A2E;text-transform:uppercase;letter-spacing:1px'>{texto}</div>"
            "<div style='width:50px;height:3px;background:#FFD600;margin:4px auto 0'></div></div>"
        )

    # =========================================================================
    # GRÁFICA 1: MAPA DE IMPACTO
    # =========================================================================
    st.markdown("<div class='chart-box'>", unsafe_allow_html=True)
    st.markdown(titulo_grafica("Impacto Estratégico de Verbalizaciones"), unsafe_allow_html=True)

    df_s1 = df_graficos.sort_values("menciones_pct", ascending=True)
    n_items = len(df_s1)
    fig1, ax1 = plt.subplots(figsize=(8, max(3.5, n_items * 0.38)), facecolor="white")
    y_pos = np.arange(n_items)

    ax1.hlines(y=y_pos, xmin=0, xmax=100, color='#EEEEEE', linewidth=1, zorder=1)
    ax1.axvline(75, color=COLOR_OBJETIVO, linestyle='--', linewidth=1.5, zorder=2, alpha=0.6)

    colors  = ['#D32F2F' if s < 60 else '#FF9800' if s < 85 else '#388E3C' for s in df_s1["sat_neta"]]
    max_pct = df_s1["menciones_pct"].max() or 1
    sizes   = (df_s1["menciones_pct"] / max_pct) * 500 + 80
    ax1.scatter(df_s1["sat_neta"], y_pos, s=sizes, c=colors, alpha=0.9,
                edgecolors="white", linewidth=1.0, zorder=3)
    for i, s in enumerate(df_s1["sat_neta"]):
        ax1.text(s, i, f"{s:.0f}%", ha='center', va='center',
                 fontsize=8, color="white", fontweight='bold', zorder=4)

    ax1.set_yticks(y_pos)
    ax1.set_yticklabels([textwrap.fill(x, 30) for x in df_s1["Sub-Categoría"]],
                        fontsize=9, fontweight='bold', color="#1A1A2E")
    ax1.set_xticks([0, 20, 40, 60, 80, 100])
    ax1.set_xlabel("Satisfacción Neta (%)", fontsize=10, fontweight='bold', labelpad=8)
    ax1.set_xlim(-5, 105)
    ax1.xaxis.set_major_formatter(mtick.PercentFormatter(100.0))
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)
    ax1.spines['left'].set_visible(False)
    ax1.tick_params(axis='y', length=0)
    plt.tight_layout()

    buf1 = fig_to_buf(fig1)
    st.image(buf1, use_container_width=True)
    st.download_button("Descargar Mapa de Impacto",
                       data=buf1.getvalue(),
                       file_name=f"Impacto_Verbalizaciones_{label_periodo}.jpg",
                       mime="image/jpeg", key="dl_verb_1")
    st.markdown("</div>", unsafe_allow_html=True)

    # =========================================================================
    # GRÁFICA 2: RANKING DE TEMAS
    # =========================================================================
    st.markdown("<div class='chart-box'>", unsafe_allow_html=True)
    st.markdown(titulo_grafica("Ranking de Temas más comentados"), unsafe_allow_html=True)

    df_s2 = df_graficos.sort_values("menciones_pct", ascending=True).tail(12)
    fig2, ax2 = plt.subplots(figsize=(10, 8), facecolor="white")
    bar_colors = ['#D32F2F' if s < 75 else '#388E3C' for s in df_s2["sat_neta"]]
    bars = ax2.barh(df_s2["Sub-Categoría"], df_s2["menciones_pct"], color=bar_colors, alpha=0.8)

    for bar in bars:
        ax2.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                 f'{bar.get_width():.1f}%', va='center', fontsize=11,
                 fontweight='bold', color="#1A1A2E")

    max_val = df_s2["menciones_pct"].max() or 1
    ax2.set_xlim(0, max_val * 1.18)
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.set_yticks(np.arange(len(df_s2)))
    ax2.set_yticklabels([textwrap.fill(x, 28) for x in df_s2["Sub-Categoría"]],
                        fontsize=11, fontweight='bold', color="#1A1A2E")
    plt.tight_layout()

    buf2 = fig_to_buf(fig2)
    st.image(buf2, use_container_width=True)
    st.download_button("Descargar Ranking de Temas",
                       data=buf2.getvalue(),
                       file_name=f"Ranking_Verbalizaciones_{label_periodo}.jpg",
                       mime="image/jpeg", key="dl_verb_2")
    st.markdown("</div>", unsafe_allow_html=True)


# ==============================================================================
# SECCIÓN 7: PERFIL APS
# ==============================================================================
def render_vista_aps():

    st.markdown("""
        <style>
            .stSelectbox label p { font-size: 18px !important; font-weight: bold !important; color: #1A1A2E !important; }
            .tabla-voc { border: 2px solid #1A1A2E !important; border-collapse: collapse !important; width: 100% !important; }
            .tabla-voc td { font-size: 18px !important; color: #000000 !important; font-weight: bold !important; border: 1px solid #CFD8DC !important; padding: 8px !important; }
            .tabla-voc th { font-size: 18px !important; color: #FFFFFF !important; background-color: #1A1A2E !important; font-weight: bold !important; border: 1px solid #1A1A2E !important; padding: 10px !important; text-align: center !important; }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("<div class='seccion-titulo' style='color:#1A1A2E; font-size:28px;'>Perfil Individual del Asesor de Servicio</div>",unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns([1.5, 1.5, 3, 1])
    with c1: fytd_sel = st.selectbox("Periodo:", todos_fytd, key="aps_fytd")
    with c2:
        mm = meses_de(D["isc_aps"], fytd_sel, CIUDAD)
        mes_sel = st.selectbox("Mes/Año:", ["TODOS"] + mm if mm else ["TODOS"], key="aps_mes")
    with c3:
        aps_opts = aps_para_filtro(CIUDAD, "GENERAL", fytd_sel)
        aps_opts = [a for a in aps_opts if a != "TODOS"]
        aps_sel = st.selectbox("Selecciona tu nombre (APS):", aps_opts if aps_opts else ["Sin asesores"], key="aps_sel")
    
    if "aps_acum" not in st.session_state: st.session_state.aps_acum = False
    with c4:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        if st.button("ACUM", key="btn_aps_acum", use_container_width=True, type="primary" if st.session_state.aps_acum else "secondary"):
            st.session_state.aps_acum = not st.session_state.aps_acum
            st.rerun()

    if not aps_opts or aps_sel == "Sin asesores": 
        st.info("No hay asesores registrados para esta selección."); return

    acum = st.session_state.aps_acum
    meses_proc = mm if mes_sel == "TODOS" else [mes_sel]
    
    df_tre_all = filtrar(D["tre_aps"], fytd=fytd_sel, ciudad=CIUDAD, aps=aps_sel)
    df_isc_all = filtrar(D["isc_aps"], fytd=fytd_sel, ciudad=CIUDAD, aps=aps_sel)
    
    df_tre_sel = df_tre_all[df_tre_all["mes_anio"].isin(meses_proc)]
    df_isc_sel = df_isc_all[df_isc_all["mes_anio"].isin(meses_proc)]
    
    obj_tre = get_obj(D["objetivos"], fytd_sel, "obj_tre")
    obj_isc = get_obj(D["objetivos"], fytd_sel, "obj_isc")
    
    e_tot = df_tre_sel["E"].sum(); c_tot = df_tre_sel["C"].sum()
    tre_val = c_tot / e_tot if e_tot > 0 else 0
    enc_tot = df_isc_sel["total_encuestas"].sum()
    isc_val = (enc_tot - df_isc_sel["I16"].sum()*2 - df_isc_sel["I78"].sum()) / enc_tot if enc_tot > 0 else 0

    def color_estado(val, obj):
        if val >= obj: return "#388E3C"
        elif val >= obj - 0.03: return "#F57F17"
        else: return "#D32F2F"

    # =========================================================================
    # 1. KPIs Y RANKING
    # =========================================================================
    k1, k2, k3 = st.columns(3)
    with k1: st.markdown(kpi_html("Encuestas Enviadas", e_tot, "{:.0f}", f"Objetivo TRE: {obj_tre:.0%}"), unsafe_allow_html=True)
    with k2: st.markdown(kpi_html("Resultado TRE", tre_val, "{:.1%}", "Eficiencia", color=color_estado(tre_val, obj_tre)), unsafe_allow_html=True)
    with k3: st.markdown(kpi_html("Resultado ISC", isc_val, "{:.1%}", f"Meta ISC: {obj_isc:.0%}", color=color_estado(isc_val, obj_isc)), unsafe_allow_html=True)

    st.markdown("<div class='chart-box'>", unsafe_allow_html=True)
    c_rank, c_kpi = st.columns([2, 1.2])
    
    df_rank_base = filtrar(D["isc_aps"], fytd=fytd_sel, ciudad=CIUDAD, dealer=None)
    df_rank_base = df_rank_base[df_rank_base["mes_anio"].isin(meses_proc)]
    rank_list = []
    for a_n, g in df_rank_base.groupby("aps_nombre"):
        if "SIN ASESOR" in str(a_n).upper(): continue
        et = g["total_encuestas"].sum()
        rank_list.append({"aps": a_n, "isc": (et - g["I16"].sum()*2 - g["I78"].sum())/et if et > 0 else 0})
    df_r = pd.DataFrame(rank_list).sort_values("isc", ascending=True)
    df_rd = df_r.sort_values("isc", ascending=False).reset_index(drop=True)
    my_p = df_rd[df_rd["aps"] == aps_sel].index[0] + 1 if aps_sel in df_rd["aps"].values else "-"
    
    with c_rank:
        st.markdown("<h6 style='color:#1A1A2E; font-weight:bold; font-size:18px;'>Ranking ISC de Asesores</h6>", unsafe_allow_html=True)
        fig_r, ax_r = plt.subplots(figsize=(8, max(4, len(df_r)*0.45)), facecolor="white")
        bars = ax_r.barh(df_r["aps"], df_r["isc"], color=[color_estado(v, obj_isc) for v in df_r["isc"]], height=0.6)
        for i, bar in enumerate(bars):
            is_me = (df_r["aps"].iloc[i] == aps_sel)
            bar.set_alpha(1.0 if is_me else 0.4); bar.set_edgecolor('#000' if is_me else 'none'); bar.set_linewidth(2 if is_me else 0)
            ax_r.text(bar.get_width()+0.01, bar.get_y()+bar.get_height()/2, f"{bar.get_width():.1%}", va='center', fontsize=11 if is_me else 9, fontweight='bold')
        ax_r.axvline(obj_isc, color=COLOR_OBJETIVO, linestyle="--", alpha=0.4)
        ax_r.set_xlim(0, 1.15); ax_r.tick_params(axis='y', length=0)
        st.pyplot(fig_r)

    with c_kpi:
        penalties = df_isc_sel["I16"].sum()*2 + df_isc_sel["I78"].sum()
        vivas = len(aplicar_filtro_ciudad(D["pendientes"], CIUDAD).query(f"aps_nombre=='{aps_sel}' and status=='Contacto en uso'"))
        req = (penalties / (1 - obj_isc)) - enc_tot if isc_val < obj_isc else 0
        fal = max(1, int(np.ceil(req))) if req > 0 else 0
        
        if isc_val >= obj_isc:
            msg_txt = f"¡Felicidades! Ya superaste la meta del <b>{obj_isc:.0%}</b>. Mantén la calidad."
            color_msg, bg_msg = "#2E7D32", "#E8F5E9"
        else:
            if fal <= vivas:
                msg_txt = f"<b>Proyección:</b> Necesitas que <b>{fal}</b> de tus <b>{vivas}</b> encuestas pendientes cierren con calificación perfecta (10) para llegar al <b>{obj_isc:.0%}</b>."
                color_msg, bg_msg = "#F57F17", "#FFF8E1"
            else:
                msg_txt = f"<b>Alerta:</b> Necesitas <b>{fal}</b> encuestas perfectas, pero solo tienes <b>{vivas}</b> encuestas disponibles por llamar."
                color_msg, bg_msg = "#D32F2F", "#FFEBEE"
                
        st.markdown(f"""
        <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; padding-top:20px;">
            <div style="width: 190px; height: 190px; border-radius: 50%; background-color: #FFFFFF; display: flex; align-items: center; justify-content: center; box-shadow: 0 8px 16px rgba(0,0,0,0.15); border: 6px solid #FFD600; margin-bottom: 25px;">
                <div style="text-align: center;">
                    <span style="color: #444; font-size: 18px; font-weight: bold; text-transform: uppercase;">Puesto</span><br>
                    <span style="color: #1A1A2E; font-size: 54px; font-weight: 900; font-family: 'Barlow Condensed'; line-height: 1;">#{my_p}</span><br>
                    <span style="color: #1A1A2E; font-size: 18px; font-weight: bold;">de {len(df_rd)}</span>
                </div>
            </div>
            <div style="background:{bg_msg}; border: 1px solid {color_msg}; border-radius: 10px; padding: 15px; text-align: center; width: 100%;">
                <span style="color:{color_msg}; font-size: 15px; line-height: 1.4;">{msg_txt}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # =========================================================================
    # 2. GESTIÓN Y AGENDA
    # =========================================================================
    st.markdown("<h5 style='color:#1A1A2E; margin-top:25px; font-weight:bold;'>Gestión de Encuestas y Agenda</h5>", unsafe_allow_html=True)
    exp = len(aplicar_filtro_ciudad(D["pendientes"], CIUDAD).query(f"aps_nombre=='{aps_sel}' and status=='Contacto en uso' and fecha_validez < '{pd.Timestamp.today().normalize()}'"))
    i1, i2, i3 = st.columns(3)
    with i1: st.markdown(kpi_html("Llenadas", c_tot, "{:.0f}", "Éxito", color="#388E3C"), unsafe_allow_html=True)
    with i2: st.markdown(kpi_html("Vencidas", exp, "{:.0f}", "Pérdida", color="#D32F2F"), unsafe_allow_html=True)
    with i3: st.markdown(kpi_html("Pendientes", vivas, "{:.0f}", "Acción hoy", color="#F57F17"), unsafe_allow_html=True)

    st.markdown("<div class='chart-box'>", unsafe_allow_html=True)
    st.markdown("<h4 style='color:#1A1A2E; font-weight:bold;'>Agenda Activa: Clientes a contactar ahora</h4>", unsafe_allow_html=True)
    df_p_show = aplicar_filtro_ciudad(D["pendientes"], CIUDAD).query(f"aps_nombre=='{aps_sel}' and status=='Contacto en uso'")[["cliente_nombre", "cliente_celular", "fecha_validez"]]
    if not df_p_show.empty:
        df_p_show.columns = ["Nombre del Cliente", "Celular de Contacto", "Vence en"]
        st.markdown(tabla_html(df_p_show.style.hide(axis="index").set_table_attributes('class="tabla-voc"')), unsafe_allow_html=True)
    else: st.success("¡Sin pendientes!")
    st.markdown("</div>", unsafe_allow_html=True)

    # =========================================================================
    # 3. ATRIBUTOS TOP 3
    # =========================================================================
    df_at = filtrar(D["atrib_aps"], fytd=fytd_sel, ciudad=CIUDAD, aps=aps_sel)
    df_at = df_at[df_at["mes_anio"].isin(meses_proc)]
    fort, aler = [], []
    if not df_at.empty:
        for attr, g in df_at.groupby("atributo"):
            if "bien a la primera" in attr.lower(): continue
            v = (g["pct_score"]*g["n_respuestas"]).sum()/g["n_respuestas"].sum() if "n_respuestas" in g.columns else g["pct_score"].mean()
            m = g["obj_atributo"].mean() / 100.0 if g["obj_atributo"].mean() > 2 else g["obj_atributo"].mean()
            if v >= m: fort.append({"attr": attr, "val": v, "meta": m})
            else: aler.append({"attr": attr, "val": v, "meta": m})
    fort = sorted(fort, key=lambda x: x["val"], reverse=True)[:3]
    aler = sorted(aler, key=lambda x: x["val"])[:3]

    st.markdown("<div class='chart-box'>", unsafe_allow_html=True)
    st.markdown("<h6 style='color:#1A1A2E; font-weight:bold; font-size:18px;'>Foco de Mejora (Top 3 Atributos)</h6>", unsafe_allow_html=True)
    c_f, c_a = st.columns(2)
    for col, lista, tit, c_hex in zip([c_f, c_a], [fort, aler], ["Fortalezas", "Alertas"], ["#E8F5E9", "#FFEBEE"]):
        with col:
            st.markdown(f"<h6 style='color:{'#2E7D32' if 'Fort' in tit else '#C62828'};'>{tit}</h6>", unsafe_allow_html=True)
            for x in lista:
                st.markdown(f"<div style='background:{c_hex}; border-left:5px solid {'#4CAF50' if 'Fort' in tit else '#F44336'}; padding:10px; margin-bottom:5px; border-radius:4px;'><span style='color:#000; font-weight:bold; font-size:14px;'>{x['attr']}</span><br><span style='font-size:19px; font-weight:900; color:#000;'>{x['val']:.1%}</span> <span style='font-size:12px; color:#444; font-weight:bold;'>(Meta: {x['meta']:.0%})</span></div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # =========================================================================
    # 4. EVOLUCIÓN HISTÓRICA (ISC Y TRE)
    # =========================================================================
    st.markdown("<h5 style='color:#1A1A2E; margin-top:25px; font-weight:bold;'>Evolución de Mis Indicadores</h5>", unsafe_allow_html=True)
    
    halo = [path_effects.withStroke(linewidth=3.5, foreground="white", alpha=0.9)]
    
    def get_hist(df_base, tipo="isc"):
        hist = []
        for m in mm:
            if m not in df_base["mes_anio"].values: hist.append(np.nan); continue
            if acum: df_m = df_base[df_base["orden_mes"] <= df_base[df_base["mes_anio"]==m]["orden_mes"].values[0]]
            else: df_m = df_base[df_base["mes_anio"] == m]
            
            if tipo == "isc":
                et = df_m["total_encuestas"].sum()
                hist.append((et - df_m["I16"].sum()*2 - df_m["I78"].sum())/et if et > 0 else np.nan)
            else:
                et = df_m["E"].sum()
                hist.append(df_m["C"].sum()/et if et > 0 else np.nan)
        return hist

    vals_isc = get_hist(df_isc_all, "isc")
    vals_tre = get_hist(df_tre_all, "tre")
    
    c_ev1, c_ev2 = st.columns(2)
    
    with c_ev1:
        st.markdown("<div class='chart-box'>", unsafe_allow_html=True)
        st.markdown("<h6 style='text-align:center; color:#1A1A2E; font-size:18px;'>Evolución Mensual ISC</h6>", unsafe_allow_html=True)
        
        fig_e1, ax_e1 = plt.subplots(figsize=(8, 5.5), facecolor="white")
        ax_e1.plot(mm, vals_isc, marker='o', color='#1976D2', linewidth=3, markersize=8, label="Mi ISC")
        ax_e1.fill_between(mm, vals_isc, color='#1976D2', alpha=0.1)
        ax_e1.axhline(obj_isc, color='#D32F2F', linestyle='--', label=f"Meta ({obj_isc:.0%})")
        
        for i, v in enumerate(vals_isc):
            if pd.notna(v): 
                ax_e1.text(i, v+0.02, f"{v:.1%}", ha='center', fontweight='900', fontsize=13, color="#1A1A2E", path_effects=halo)
                
        ax_e1.set_ylim(min(0.4, min([x for x in vals_isc if pd.notna(x)] or [0.4])-0.1), 1.05)
        ax_e1.yaxis.set_major_formatter(mtick.PercentFormatter(1.0))
        ax_e1.tick_params(axis='y', labelsize=12)
        ax_e1.spines['top'].set_visible(False); ax_e1.spines['right'].set_visible(False)
        plt.xticks(rotation=45, fontsize=12); plt.tight_layout()
        st.pyplot(fig_e1)
        st.markdown("</div>", unsafe_allow_html=True)

    with c_ev2:
        st.markdown("<div class='chart-box'>", unsafe_allow_html=True)
        st.markdown("<h6 style='text-align:center; color:#1A1A2E; font-size:18px;'>Evolución Mensual TRE</h6>", unsafe_allow_html=True)
        
        fig_e2, ax_e2 = plt.subplots(figsize=(8, 5.5), facecolor="white")
        ax_e2.plot(mm, vals_tre, marker='s', color='#388E3C', linewidth=3, markersize=8, label="Mi TRE")
        ax_e2.fill_between(mm, vals_tre, color='#388E3C', alpha=0.1)
        ax_e2.axhline(obj_tre, color='#1A1A2E', linestyle='--', alpha=0.5, label=f"Meta ({obj_tre:.0%})")
        
        for i, v in enumerate(vals_tre):
            if pd.notna(v): 
                ax_e2.text(i, v+0.02, f"{v:.1%}", ha='center', fontweight='900', fontsize=13, color="#1A1A2E", path_effects=halo)
                
        ax_e2.set_ylim(min(0.2, min([x for x in vals_tre if pd.notna(x)] or [0.2])-0.1), 1.05)
        ax_e2.yaxis.set_major_formatter(mtick.PercentFormatter(1.0))
        ax_e2.tick_params(axis='y', labelsize=12)
        ax_e2.spines['top'].set_visible(False); ax_e2.spines['right'].set_visible(False)
        plt.xticks(rotation=45, fontsize=12); plt.tight_layout()
        st.pyplot(fig_e2)
        st.markdown("</div>", unsafe_allow_html=True)

# ==============================================================================
# SECCIÓN 8: MOTOR DE EXPORTACIÓN EJECUTIVA TAIYO MOTORS
# ==============================================================================
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.patches as patches

COLOR_TAIYO = "#C62828" 

def preparar_diapositiva(title_slide=None):
    """Genera el lienzo 16:9 con el encabezado premium (Texto Blanco y Grande)."""
    fig = plt.figure(figsize=(16, 9), facecolor='white')
    
    rect = patches.Rectangle((0, 0.92), 1, 0.08, transform=fig.transFigure, color='#1A1A2E', zorder=0)
    fig.patches.append(rect)
    
    line = patches.Rectangle((0, 0.915), 1, 0.005, transform=fig.transFigure, color=COLOR_TAIYO, zorder=1)
    fig.patches.append(line)
    
    fig.text(0.04, 0.945, "VOC INSIGHTS ANALYTICS", color='white', fontsize=16, fontweight='900', fontfamily='Barlow Condensed', transform=fig.transFigure)
    
    fig.text(0.96, 0.945, "TAIYO MOTORS", color='white', fontsize=20, fontweight='bold', ha='right', transform=fig.transFigure)
    
    if title_slide:
        fig.text(0.04, 0.85, title_slide.upper(), color='#1A1A2E', fontsize=20, fontweight='bold', transform=fig.transFigure)
    
    ax = fig.add_axes([0.03, 0.02, 0.94, 0.78])
    ax.axis('off')
    
    return fig, ax

def construir_pdf(fytd_sel, mes_sel, dealer_sel, aps_sel, acum, ciudad, mm):
    pdf_buf = io.BytesIO()
    meses_proc = mm if mes_sel == "TODOS" else (mm[:mm.index(mes_sel)+1] if acum else [mes_sel])

    PALETA_APS = ["#E91E63","#9C27B0","#3F51B5","#00BCD4","#4CAF50","#FF9800","#795548","#607D8B","#F44336","#009688","#CDDC39","#FF5722"]
    PALETA_ATR = ["#1976D2", "#D32F2F", "#388E3C", "#9C27B0", "#FF9800", "#00BCD4", "#E91E63", "#795548", "#607D8B"]

    with PdfPages(pdf_buf) as pdf:
        # 1. CARÁTULA
        fig_c, ax_c = plt.subplots(figsize=(16, 9), facecolor='white')
        ax_c.axis('off')
        ax_c.add_patch(patches.Rectangle((0, 0), 1, 1, color='#1A1A2E', transform=ax_c.transAxes))
        ax_c.add_patch(patches.Rectangle((0, 0), 0.02, 1, color=COLOR_TAIYO, transform=ax_c.transAxes))
        txt_m = mes_sel.upper() if mes_sel != "TODOS" else "PERIODO COMPLETO"
        txt_a = "\n(ACUMULADO)" if acum and mes_sel != "TODOS" else ""
        ax_c.text(0.1, 0.6, f"RESULTADOS VoC\nCORTE: {txt_m}{txt_a}", color='white', fontsize=48, fontweight='bold', ha='left', transform=ax_c.transAxes)
        ax_c.text(0.1, 0.4, f"GESTIÓN: {fytd_sel.upper()} | SEDE: {ciudad.upper()}", color='#FFD600', fontsize=22, ha='left', transform=ax_c.transAxes)
        det = f"UNIDAD: {dealer_sel.upper()}" if aps_sel == "TODOS" else f"ASESOR: {aps_sel.upper()}"
        ax_c.text(0.1, 0.32, det, color='#E0E0E0', fontsize=18, ha='left', transform=ax_c.transAxes)
        pdf.savefig(fig_c, bbox_inches='tight', dpi=300); plt.close(fig_c)

        # 2. TRE E ISC
        df_im = filtrar(D["isc_mensual"][D["isc_mensual"]["mes_anio"].isin(meses_proc)], fytd=fytd_sel, ciudad=ciudad)
        df_tm = filtrar(D["tre_mensual"][D["tre_mensual"]["mes_anio"].isin(meses_proc)], fytd=fytd_sel, ciudad=ciudad)
        obj_t = get_obj(D["objetivos"], fytd_sel, "obj_tre"); obj_i = get_obj(D["objetivos"], fytd_sel, "obj_isc")
        agg_i = df_im[~df_im["dealer"].astype(str).str.upper().str.contains("SIN DEALER", na=False)].groupby("dealer").agg(enc=("total_encuestas","sum"),i16=("I16","sum"),i78=("I78","sum")).reset_index()
        agg_t = df_tm[~df_tm["dealer"].astype(str).str.upper().str.contains("SIN DEALER", na=False)].groupby("dealer").agg(E=("E","sum"),C=("C","sum"),F=("F","sum")).reset_index()
        df_d = agg_t.merge(agg_i,on="dealer",how="outer").fillna(0)
        df_d["prom_tre"] = np.where(df_d["E"]>0, df_d["C"]/df_d["E"], 0)
        df_d["prom_isc"] = np.where(df_d["enc"]>0, (df_d["enc"]-df_d["i16"]*2-df_d["i78"])/df_d["enc"], 0)
        tot = {"dealer": "TOTAL GENERAL", "E": df_d["E"].sum(), "C": df_d["C"].sum(), "F": df_d["F"].sum(), "enc": df_d["enc"].sum(), "i16": df_d["i16"].sum(), "i78": df_d["i78"].sum(), "prom_tre": df_d["C"].sum()/df_d["E"].sum() if df_d["E"].sum()>0 else 0, "prom_isc": (df_d["enc"].sum()-df_d["i16"].sum()*2-df_d["i78"].sum())/df_d["enc"].sum() if df_d["enc"].sum()>0 else 0}
        ds_d = pd.concat([df_d, pd.DataFrame([tot])], ignore_index=True)[["dealer","E","C","F","prom_tre","i16","i78","prom_isc"]]; ds_d.columns = ["Nombre","Env.","Comp.","Falta","TRE %","1-6","7-8","%ISC"]
        
        fig_dual, _ = preparar_diapositiva("Desempeño Operativo: TRE e ISC")
        ax_l = fig_dual.add_axes([0.04, 0.1, 0.44, 0.70]); ax_l.axis('off'); ax_l.set_title("RESUMEN POR TALLER", fontsize=14, fontweight='bold', color=COLOR_TAIYO, pad=10)
        ax_l.imshow(Image.open(io.BytesIO(cached_jpg_gen(ds_d.fillna(0), 'gral', obj_t, obj_i))))
        
        df_ia = filtrar(D["isc_aps"][D["isc_aps"]["mes_anio"].isin(meses_proc)], fytd=fytd_sel, ciudad=ciudad, dealer=dealer_sel if dealer_sel!="GENERAL" else None)
        df_ta = filtrar(D["tre_aps"][D["tre_aps"]["mes_anio"].isin(meses_proc)], fytd=fytd_sel, ciudad=ciudad, dealer=dealer_sel if dealer_sel!="GENERAL" else None)
        if not df_ia.empty:
            agg_ii = df_ia.groupby("aps_nombre").agg(enc=("total_encuestas","sum"),i16=("I16","sum"),i78=("I78","sum")).reset_index()
            agg_ti = df_ta.groupby("aps_nombre").agg(E=("E","sum"),C=("C","sum")).reset_index()
            df_ap = agg_ti.merge(agg_ii, on="aps_nombre", how="outer").fillna(0)
            df_ap["prom_tre"] = np.where(df_ap["E"]>0, df_ap["C"]/df_ap["E"], 0); df_ap["prom_isc"] = np.where(df_ap["enc"]>0, (df_ap["enc"]-df_ap["i16"]*2-df_ap["i78"])/df_ap["enc"], 0)
            tot_ap = {"aps_nombre": "TOTAL GENERAL", "E": df_ap["E"].sum(), "C": df_ap["C"].sum(), "enc": df_ap["enc"].sum(), "i16": df_ap["i16"].sum(), "i78": df_ap["i78"].sum()}
            tot_ap["prom_tre"] = tot_ap["C"]/tot_ap["E"] if tot_ap["E"]>0 else 0; tot_ap["prom_isc"] = (tot_ap["enc"] - tot_ap["i16"]*2 - tot_ap["i78"])/tot_ap["enc"] if tot_ap["enc"]>0 else 0
            ds_ap = pd.concat([df_ap, pd.DataFrame([tot_ap])], ignore_index=True)[["aps_nombre","E","C","prom_tre","i16","i78","prom_isc"]]; ds_ap.columns = ["Nombre","Enviadas","Completadas","TRE %","1-6","7-8","% ISC"]
            ax_r = fig_dual.add_axes([0.52, 0.1, 0.44, 0.70]); ax_r.axis('off'); ax_r.set_title("DETALLE POR ASESOR (APS)", fontsize=14, fontweight='bold', color=COLOR_TAIYO, pad=10)
            ax_r.imshow(Image.open(io.BytesIO(cached_jpg_gen(ds_ap.fillna(0), 'aps', obj_t, obj_i))))
        pdf.savefig(fig_dual, bbox_inches='tight', dpi=300); plt.close(fig_dual)

        # 3. ATRIBUTOS ESPECIALES
        df_esp_pdf = D["especiales"].copy() if not D["especiales"].empty else pd.DataFrame()
        if not df_esp_pdf.empty:
            df_esp_pdf['fytd'] = df_esp_pdf['fytd'].astype(str).str.strip().str.upper()
            df_esp_pdf['mes']  = df_esp_pdf['mes'].astype(str).str.strip().str.capitalize()
            if 'ciudad' in df_esp_pdf.columns:
                df_esp_pdf['ciudad'] = df_esp_pdf['ciudad'].astype(str).str.strip().str.upper()
        if not df_esp_pdf.empty:
            f3, _ = preparar_diapositiva("Indicadores Especiales de Servicio")
            meses_cortos = [str(m).split()[0].strip().capitalize() for m in meses_proc]
            mask_pdf = (df_esp_pdf['fytd'] == fytd_sel.strip().upper()) & (df_esp_pdf['mes'].isin(meses_cortos))
            if 'ciudad' in df_esp_pdf.columns and CIUDAD != "TODAS":
                mask_pdf = mask_pdf & (df_esp_pdf['ciudad'] == CIUDAD.upper())
            row_e = df_esp_pdf[mask_pdf]
            names = ["Bien a la primera H1", "Customer Expectations CES", "Alertas atendidas en 24 Hrs"]
            import unicodedata
            def n_t(t): return unicodedata.normalize('NFKD', str(t).strip().lower()).encode('ASCII', 'ignore').decode('utf-8') if pd.notna(t) else ""
            for i, name in enumerate(names):
                val = float(row_e[name].mean()) if name in row_e.columns and pd.notna(row_e[name].mean()) else 0.0
                obj_v = 0.85
                for col in D["objetivos"].columns:
                    if n_t(col) == n_t(name):
                        r_o = D["objetivos"][D["objetivos"]["fytd"] == fytd_sel]
                        if not r_o.empty: obj_v = float(r_o[col].values[0]) / (100.0 if float(r_o[col].values[0]) > 2 else 1.0)
                        break
                c_c = "#388E3C" if val >= obj_v else "#D32F2F"; c_b = "#F2F9F2" if val >= obj_v else "#FFEBEE"
                ax = f3.add_axes([0.08 + (i*0.31), 0.15, 0.25, 0.65]); ax.axis('off')
                card = patches.FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.02,rounding_size=0.05", linewidth=2.5, edgecolor=c_c, facecolor=c_b, zorder=0); ax.add_patch(card)
                ax.add_patch(patches.Ellipse((0.5, 0.72), width=0.40, height=0.2735, color=c_c, zorder=1))
                ax.add_patch(patches.Ellipse((0.5, 0.72), width=0.40, height=0.2735, edgecolor='white', facecolor='none', linewidth=5, zorder=2))
                ax.text(0.5, 0.72, f"{val:.0%}", color='white', fontsize=36, fontweight='900', ha='center', va='center', zorder=3)
                ax.text(0.5, 0.40, textwrap.fill(name.upper(), 18), fontsize=15, fontweight='900', color='#1A1A2E', ha='center', va='center')
                ax.text(0.5, 0.22, f"Objetivo: {obj_v:.0%}", fontsize=12, fontweight='bold', color='#555555', ha='center', va='center', bbox=dict(facecolor='white', edgecolor='#CCCCCC', boxstyle='round,pad=0.5', linewidth=1))
                ax.text(0.5, 0.08, "¡CUMPLIDO!" if val >= obj_v else "NO CUMPLIDO", fontsize=16, fontweight='900', color=c_c, ha='center', va='center')
            pdf.savefig(f3, bbox_inches='tight', dpi=300); plt.close(f3)

        # 4. TENDENCIA ISC
        f4, ax_img4 = preparar_diapositiva("Evolución Histórica: Índice de Satisfacción")
        df_base_t_full = filtrar(D["isc_aps"] if dealer_sel != "GENERAL" else D["isc_mensual"], fytd=fytd_sel, ciudad=ciudad)
        if dealer_sel != "GENERAL": df_base_t_full = df_base_t_full[df_base_t_full["dealer"] == dealer_sel]
        df_prom_t = filtrar(D["isc_mensual"] if aps_sel == "TODOS" else D["isc_aps"], fytd=fytd_sel, ciudad=ciudad)
        if dealer_sel != "GENERAL": df_prom_t = df_prom_t[df_prom_t["dealer"] == dealer_sel]
        if aps_sel != "TODOS": df_prom_t = df_prom_t[df_prom_t["aps_nombre"] == aps_sel]
        m_ord = df_base_t_full.drop_duplicates("mes_anio").sort_values("orden_mes")["mes_anio"].tolist()
        if m_ord:
            def serie_t(sub_df):
                pts = []
                for m in m_ord:
                    if m not in sub_df["mes_anio"].values: pts.append(np.nan); continue
                    g = sub_df[sub_df["orden_mes"] <= sub_df[sub_df["mes_anio"]==m]["orden_mes"].values[0]] if acum else sub_df[sub_df["mes_anio"] == m]
                    pts.append((g["total_encuestas"].sum() - g["I16"].sum()*2 - g["I78"].sum())/g["total_encuestas"].sum() if g["total_encuestas"].sum()>0 else np.nan)
                return pts
            prom_vals = serie_t(df_prom_t); tots = [df_prom_t[df_prom_t["mes_anio"]==m]["total_encuestas"].sum() if m in df_prom_t["mes_anio"].values else 0 for m in m_ord]
            s_sec, c_sec, m_sec = {}, {}, {}
            if dealer_sel == "GENERAL":
                for i, d in enumerate(sorted([x for x in df_base_t_full["dealer"].unique() if x != "SIN DEALER"])):
                    s_sec[d] = serie_t(df_base_t_full[df_base_t_full["dealer"] == d]); c_sec[d] = COLORES_DEALERS.get(d, PALETA_ATR[i % len(PALETA_ATR)]); m_sec[d] = MARKERS_DEALERS.get(d, "o")
            else:
                for i, an in enumerate(sorted([x for x in df_base_t_full["aps_nombre"].unique() if str(x).strip().upper() != "SIN ASESOR"])):
                    s_sec[an] = serie_t(df_base_t_full[df_base_t_full["aps_nombre"] == an]); c_sec[an] = COLOR_TAIYO if an == aps_sel else PALETA_APS[i % len(PALETA_APS)]; m_sec[an] = "o"
            img_ten = cached_plot_tendencia(tuple(m_ord), tuple(tots), tuple(prom_vals), s_sec, c_sec, m_sec, obj_i, "SELECCIÓN" if aps_sel != "TODOS" else "PROM. GENERAL", "", ""); ax_img4.imshow(Image.open(io.BytesIO(img_ten))); pdf.savefig(f4, bbox_inches='tight', dpi=300); plt.close(f4)

        # 5. ATRIBUTOS (RADIAL + TABLA)
        f5, _ = preparar_diapositiva("Diagnóstico de Atributos: Radial y Tabla")
        df_at_full = filtrar(D["atrib_aps"] if dealer_sel!="GENERAL" else D["atributos"], fytd=fytd_sel, ciudad=ciudad)
        if dealer_sel != "GENERAL": df_at_full = df_at_full[df_at_full["dealer"]==dealer_sel]
        df_at_p_full = df_at_full[df_at_full["mes_anio"].isin(meses_proc)]
        if not df_at_p_full.empty:
            sc_col="pct_score" if "pct_score" in df_at_p_full.columns else "pct_top2box"
            attrs = sorted([a for a in df_at_p_full["atributo"].unique() if "bien a la primera" not in a.lower()])
            def get_attr_avg(s_df):
                r = {}
                for a in attrs:
                    g = s_df[s_df["atributo"]==a]
                    r[a] = (g[sc_col]*g["n_respuestas"]).sum()/g["n_respuestas"].sum() if "n_respuestas" in g.columns and g["n_respuestas"].sum()>0 else (g[sc_col].mean() if not g.empty else 0)
                return r
            res_gen = get_attr_avg(df_at_p_full[df_at_p_full["aps_nombre"]==aps_sel] if aps_sel != "TODOS" else df_at_p_full)
            ax_rad = f5.add_axes([0.02, 0.12, 0.38, 0.65], projection='polar'); angles = np.linspace(0, 2*np.pi, len(attrs), endpoint=False).tolist(); angles += angles[:1]
            ax_rad.fill(angles, [res_gen.get(a,0) for a in attrs]+[res_gen.get(attrs[0],0)], color='#1A1A2E', alpha=0.1); ax_rad.plot(angles, [res_gen.get(a,0) for a in attrs]+[res_gen.get(attrs[0],0)], color='#1A1A2E', linewidth=2.5, label="SELECCIÓN" if aps_sel != "TODOS" else "PROM. GENERAL")
            ax_rad.plot(angles, [obj_i]*(len(attrs)+1), color=COLOR_OBJETIVO, linewidth=1.5, linestyle="--")
            if dealer_sel == "GENERAL":
                for i, d in enumerate(sorted([x for x in df_at_p_full["dealer"].unique() if x != "SIN DEALER"])):
                    rg = get_attr_avg(df_at_p_full[df_at_p_full["dealer"] == d]); ax_rad.plot(angles, [rg.get(a,0) for a in attrs]+[rg.get(attrs[0],0)], color=PALETA_ATR[i % len(PALETA_ATR)], linewidth=1.5, marker='o', markersize=4, alpha=0.8, label=f"Taller: {d}")
            else:
                for i, an in enumerate(sorted([x for x in df_at_p_full["aps_nombre"].unique() if str(x).strip().upper() != "SIN ASESOR"])):
                    rg = get_attr_avg(df_at_p_full[df_at_p_full["aps_nombre"] == an]); ax_rad.plot(angles, [rg.get(a,0) for a in attrs]+[rg.get(attrs[0],0)], color=COLOR_TAIYO if an == aps_sel else PALETA_APS[i % len(PALETA_APS)], linewidth=2.0 if an==aps_sel else 1.2, marker='o', markersize=3, alpha=0.8, label=f"APS: {an}")
            ax_rad.spines['polar'].set_visible(False); ax_rad.set_xticks([]); ax_rad.set_yticks([0.2,0.4,0.6,0.8,1.0]); ax_rad.set_yticklabels([]); ax_rad.set_ylim(0,1.35)
            for i,(angle,attr) in enumerate(zip(angles[:-1],attrs)): ax_rad.text(angle, 1.30, f"{textwrap.fill(attr,14)}\n{res_gen.get(attr,0):.1%}", ha="left" if angle<np.pi else "right", va="center", fontsize=7.5, fontweight='bold', color='#333')
            ax_rad.legend(loc="lower center", bbox_to_anchor=(0.5, -0.20), fontsize=7, frameon=False, ncol=2)
            ax_tbl = f5.add_axes([0.48, 0.05, 0.50, 0.82]); ax_tbl.axis('off'); data_t = []
            if dealer_sel == "GENERAL":
                d_en = sorted([d for d in df_at_p_full["dealer"].unique() if d != "SIN DEALER"])
                for a in attrs:
                    row = {"Atributo": a}
                    for d in d_en: g_d = df_at_p_full[(df_at_p_full['atributo']==a)&(df_at_p_full['dealer']==d)]; row[d] = (g_d[sc_col]*g_d["n_respuestas"]).sum()/g_d["n_respuestas"].sum() if "n_respuestas" in g_d.columns and g_d["n_respuestas"].sum()>0 else (g_d[sc_col].mean() if not g_d.empty else np.nan)
                    row["GENERAL"] = res_gen.get(a, np.nan); row["Objetivo"] = 0.9; row["GAP"] = row["GENERAL"]-0.9; data_t.append(row)
                df_t = pd.DataFrame(data_t).sort_values("GENERAL", ascending=True); cols_ev = tuple(d_en + ["GENERAL"])
            else:
                aps_en = sorted([an for an in df_at_p_full["aps_nombre"].unique() if str(an).strip().upper() != "SIN ASESOR"])
                for a in attrs:
                    row = {"Atributo": a}
                    for an in aps_en: g_a = df_at_p_full[(df_at_p_full['atributo']==a)&(df_at_p_full['aps_nombre']==an)]; row[an] = (g_a[sc_col]*g_a["n_respuestas"]).sum()/g_a["n_respuestas"].sum() if "n_respuestas" in g_a.columns and g_a["n_respuestas"].sum()>0 else (g_a[sc_col].mean() if not g_a.empty else np.nan)
                    row["GENERAL"] = res_gen.get(a, np.nan); row["Objetivo"] = 0.9; row["GAP"] = row["GENERAL"]-0.9; data_t.append(row)
                df_t = pd.DataFrame(data_t).sort_values("GENERAL", ascending=True); cols_ev = tuple(aps_en + ["GENERAL"])
            ax_tbl.imshow(Image.open(io.BytesIO(cached_jpg_attr_table(df_t.fillna(np.nan), cols_ev)))); pdf.savefig(f5, bbox_inches='tight', dpi=300); plt.close(f5)

            # HISTÓRICOS ATRIBUTOS
            sorted_attrs = sorted(attrs, key=lambda x: res_gen.get(x,0))
            for a in sorted_attrs:
                f_a, ax_a = preparar_diapositiva(f"Evolución Atributo: {a}")
                def get_tr(s_df):
                    v_l = []
                    for m in m_ord:
                        g = s_df[s_df["orden_mes"] <= s_df[s_df["mes_anio"]==m]["orden_mes"].values[0]] if acum else s_df[s_df["mes_anio"] == m]
                        if g.empty: v_l.append(np.nan)
                        else: v_l.append((g[sc_col]*g["n_respuestas"]).sum()/g["n_respuestas"].sum() if "n_respuestas" in g.columns and g["n_respuestas"].sum()>0 else g[sc_col].mean())
                    return v_l
                d_h, c_m = {}, {}
                if dealer_sel == "GENERAL":
                    d_h["TOTAL GENERAL"] = get_tr(D["atributos"][D["atributos"]["atributo"]==a]); c_m["TOTAL GENERAL"] = "#000"
                    for i, d in enumerate(sorted([x for x in D["atributos"]["dealer"].unique() if x != "SIN DEALER"])): d_h[d] = get_tr(D["atributos"][(D["atributos"]["atributo"]==a)&(D["atributos"]["dealer"]==d)]); c_m[d] = PALETA_ATR[i % len(PALETA_ATR)]
                else:
                    lbl_tot = f"PROM {aps_sel}" if aps_sel != "TODOS" else f"TOTAL {dealer_sel}"
                    d_h[lbl_tot] = get_tr(D["atrib_aps"][(D["atrib_aps"]["atributo"]==a)&(D["atrib_aps"]["dealer"]==dealer_sel)&(D["atrib_aps"]["aps_nombre"]==aps_sel)] if aps_sel!="TODOS" else D["atributos"][(D["atributos"]["atributo"]==a)&(D["atributos"]["dealer"]==dealer_sel)])
                    c_m[lbl_tot] = "#000"
                    for i, an in enumerate(sorted([x for x in D["atrib_aps"][(D["atrib_aps"]["dealer"]==dealer_sel)]["aps_nombre"].unique() if str(x).strip().upper() != "SIN ASESOR"])): 
                        d_h[an] = get_tr(D["atrib_aps"][(D["atrib_aps"]["atributo"]==a)&(D["atrib_aps"]["dealer"]==dealer_sel)&(D["atrib_aps"]["aps_nombre"]==an)]); c_m[an] = COLOR_TAIYO if an == aps_sel else PALETA_APS[i % len(PALETA_APS)]
                ax_a.imshow(Image.open(io.BytesIO(cached_hist_plot_attr(d_h, c_m, tuple(m_ord), 0.9, "", dealer_sel, aps_sel)))); pdf.savefig(f_a, bbox_inches='tight', dpi=300); plt.close(f_a)

        # 6. VERBALIZACIONES
        df_v = D["verbaliz"].copy()
        if not df_v.empty:
            meses_cortos = [str(m).split()[0].strip().capitalize() for m in meses_proc]
            df_v = df_v[(df_v["fytd"] == fytd_sel.strip().upper()) & (df_v["mes"].isin(meses_cortos)) & (df_v["ciudad"] == ciudad)]
            if not df_v.empty:
                def cl_p(x): return float(str(x).split('%')[0].strip()) if pd.notna(x) and str(x).strip() not in ["-",""] else 0.0
                df_v["sat_neta"] = df_v["SATISFACCIÓN NETA"].apply(cl_p); df_v["menciones_pct"] = df_v["Comentarios relacionados"].apply(cl_p)
                df_g = df_v.groupby("Sub-Categoría").agg({"sat_neta":"mean", "menciones_pct":"sum"}).reset_index().query("menciones_pct > 0").sort_values("menciones_pct")
                if not df_g.empty:
                    # SLIDE A: MAPA
                    f_v1, ax_main1 = preparar_diapositiva("Impacto Estratégico de Verbalizaciones"); ax_main1.remove(); ax_v1 = f_v1.add_axes([0.25, 0.08, 0.70, 0.70])
                    yp = np.arange(len(df_g)); ax_v1.hlines(y=yp, xmin=0, xmax=100, color='#EEEEEE'); ax_v1.axvline(75, color=COLOR_OBJETIVO, linestyle='--')
                    ax_v1.scatter(df_g["sat_neta"], yp, s=df_g["menciones_pct"]*110+200, c=['#D32F2F' if s<60 else '#FF9800' if s<85 else '#388E3C' for s in df_g["sat_neta"]], edgecolors="white")
                    for i, s in enumerate(df_g["sat_neta"]): ax_v1.text(s, i, f"{s:.0f}%", ha='center', va='center', fontsize=9.5, color="white", fontweight='bold')
                    ax_v1.set_yticks(yp); ax_v1.set_yticklabels([textwrap.fill(x, 28) for x in df_g["Sub-Categoría"]], fontsize=11, fontweight='bold', color="#1A1A2E")
                    ax_v1.set_xlim(-5, 105); ax_v1.xaxis.set_major_formatter(mtick.PercentFormatter(100.0)); pdf.savefig(f_v1, bbox_inches='tight', dpi=300); plt.close(f_v1)
                    
                    # SLIDE B: RANKING
                    f_v2, ax_main2 = preparar_diapositiva("Ranking de Temas más comentados"); ax_main2.remove(); ax_v2 = f_v2.add_axes([0.25, 0.08, 0.70, 0.70])
                    df_top = df_g.sort_values("menciones_pct", ascending=True).tail(12)
                    bars = ax_v2.barh(df_top["Sub-Categoría"], df_top["menciones_pct"], color=['#D32F2F' if s<75 else '#388E3C' for s in df_top["sat_neta"]], alpha=0.8)
                    for bar in bars: ax_v2.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height()/2, f'{bar.get_width():.1f}%', va='center', fontsize=11, fontweight='bold', color="#1A1A2E")
                    ax_v2.set_xlim(0, df_top["menciones_pct"].max() * 1.2); ax_v2.set_yticks(np.arange(len(df_top))); ax_v2.set_yticklabels([textwrap.fill(x, 28) for x in df_top["Sub-Categoría"]], fontsize=11, fontweight='bold', color="#1A1A2E")
                    pdf.savefig(f_v2, bbox_inches='tight', dpi=300); plt.close(f_v2)

    pdf_buf.seek(0); return pdf_buf.getvalue()

def render_descargas():
    st.markdown("<div class='seccion-titulo'>Generador de Informes Ejecutivos PDF</div>", unsafe_allow_html=True)
    st.markdown("""<div style='background:white; padding:20px; border-radius:10px; border-left:5px solid #C62828; margin-bottom:20px; box-shadow: 0 2px 10px rgba(0,0,0,0.05);'><p style='color:#1A1A2E; font-size:16px; margin:0;'>Módulo de alta resolución (300 DPI) con diseño institucional <b>Taiyo Motors</b>.</p></div>""", unsafe_allow_html=True)
    
    st.markdown("""
    <style>
        div[data-testid="stCheckbox"] label p {
            font-weight: 900 !important; 
            font-size: 14px !important; 
            color: #1A1A2E !important; 
            text-transform: uppercase; 
            letter-spacing: 0.5px;
        }
    </style>
    """, unsafe_allow_html=True)

    c1, c2, c3, c4, c5 = st.columns([1.5, 1.5, 2, 2, 1])
    with c1: fytd_pdf = st.selectbox("Periodo FYTD:", todos_fytd, key="pdf_fytd")
    with c2: mm = meses_de(D["isc_mensual"], fytd_pdf, CIUDAD); mes_pdf = st.selectbox("Corte Mensual:", ["TODOS"] + mm if mm else ["TODOS"], key="pdf_mes")
    with c3: dlrs = dealers_para_ciudad(CIUDAD); dealer_pdf = st.selectbox("Filtrar Taller:", dlrs, key="pdf_dlr")
    with c4: aps_opts = aps_para_filtro(CIUDAD, dealer_pdf, fytd_pdf); aps_pdf = st.selectbox("Filtrar Asesor:", aps_opts, key="pdf_aps")
    with c5: 
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        acum_pdf = st.checkbox("ACUM", value=False, key="pdf_acum")
        
    if st.button("GENERAR INFORME TAIYO MOTORS (PDF)", type="primary", use_container_width=True):
        with st.spinner("Compilando arquitectura corporativa y diapositivas de alta definición..."):
            pdf_bytes = construir_pdf(fytd_pdf, mes_pdf, dealer_pdf, aps_pdf, acum_pdf, CIUDAD, mm); st.session_state.pdf_ready = pdf_bytes
            
    if st.session_state.get("pdf_ready"):
        st.download_button(label="DESCARGAR REPORTE PDF", data=st.session_state.pdf_ready, file_name=f"Reporte_VoC_Taiyo_{CIUDAD}_{mes_pdf if mes_pdf!='TODOS' else fytd_pdf}.pdf", mime="application/pdf", use_container_width=True)

# ==============================================================================
# SECCIÓN DE INICIO
# ==============================================================================
def render_caratula():

    st.markdown("""
<style>
    [data-testid="stSidebar"], [data-testid="collapsedControl"], [data-testid="stHeader"], footer { display: none !important; }
    html body section[data-testid="stMain"], html body .stApp, html body [data-testid="stAppViewContainer"] { background-color: #1A1A2E !important; background: #1A1A2E !important; overflow: hidden !important; height: 100vh !important; margin: 0 !important; padding: 0 !important; }
    html body .block-container { display: flex !important; flex-direction: column !important; justify-content: center !important; align-items: center !important; height: 100vh !important; max-width: 100% !important; padding: 0 !important; background-color: #1A1A2E !important; }
    div[data-testid="stSelectbox"] label p { color: #FFFFFF !important; font-size: 15px !important; letter-spacing: 2px !important; text-transform: uppercase; font-weight: 900 !important; text-align: center !important; width: 100%; }
</style>
""", unsafe_allow_html=True)

    st.markdown('<div style="position:fixed; top:0; left:0; width:15px; height:100vh; background-color:#C62828; z-index:9999;"></div><div style="text-align:center; width:100%; padding:0 40px; display:flex; flex-direction:column; align-items:center; margin-bottom: 40px; margin-top: -30px;"><h2 style="color:#FFFFFF; font-family:\'Barlow\',sans-serif; font-size:clamp(18px, 2.5vw, 35px); font-weight:500; letter-spacing:15px; text-transform:uppercase; margin:0 0 25px 0;">TAIYO MOTORS</h2><div style="display:flex; flex-direction:column; align-items:center;"><h1 style="background:linear-gradient(90deg, #E0E0E0 0%, #9E9E9E 50%, #616161 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; font-family:\'Barlow Condensed\',sans-serif; font-size:clamp(60px, 13vw, 220px); font-weight:900; letter-spacing:1px; line-height:0.9; margin:0; text-transform:uppercase; white-space:nowrap;">VOC INSIGHTS</h1><h1 style="background:linear-gradient(90deg, #E0E0E0 0%, #9E9E9E 50%, #616161 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; font-family:\'Barlow Condensed\',sans-serif; font-size:clamp(60px, 13vw, 220px); font-weight:900; letter-spacing:1px; line-height:0.9; margin:0; text-transform:uppercase; white-space:nowrap;">ANALYTICS</h1></div></div>', unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1, 1.5, 1])
    with c2:
        vista = st.selectbox("CREDENCIALES DE ACCESO:", ["-- Seleccione su Perfil --", "VISTA GENERAL (Reporte Ejecutivo)", "VISTA APS (Gestión Operativa)"], key="vista_caratula")
        
        if vista == "VISTA GENERAL (Reporte Ejecutivo)":
            st.session_state.perfil = "GENERAL"
            st.session_state.seccion = "general"
            st.rerun()
        elif vista == "VISTA APS (Gestión Operativa)":
            st.session_state.perfil = "APS"
            st.session_state.seccion = "vista_aps"
            st.rerun()

# ==============================================================================
# ENRUTADOR ACTUALIZADO
# ==============================================================================
sec = st.session_state.get("seccion", "caratula")
if   sec == "caratula":       render_caratula()
elif sec == "general":        render_general()
elif sec == "tendencia":      render_tendencia()
elif sec == "atributos":      render_atributos()
elif sec == "radial":         render_radial()
elif sec == "pendientes":     render_pendientes()
elif sec == "verbalizaciones": render_verbalizaciones()
elif sec == "vista_aps":       render_vista_aps() 
elif sec == "descargar_pdf":   render_descargas()
else: render_caratula()