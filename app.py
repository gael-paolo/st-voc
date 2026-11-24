# ==============================================================================
# DASHBOARD VoC
# ==============================================================================

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import io, textwrap
import os
from pathlib import Path
from PIL import Image
from ui_theme import aplicar_estilos, C, COLORWAY, plotly_layout, mostrar_plotly, kpi_card, delta_chip

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
        # 01_isc_base y 02_tre_base (una fila por encuesta/contacto) NO se cargan: el portal no los usa
        # y ocupaban la mayor parte de la memoria y del tiempo de carga.
        "03_isc_mensual_dealer.csv"  : "isc_mensual",
        "04_tre_mensual_dealer.csv"  : "tre_mensual",
        "05_atributos_resumen.csv"   : "atributos",
        "06_pendientes.csv"          : "pendientes",
        "08_isc_mensual_aps.csv"     : "isc_aps",
        "09_tre_mensual_aps.csv"     : "tre_aps",
        "10_atributos_aps.csv"       : "atrib_aps",
        "12_verbalizaciones.csv"     : "verbaliz",
        "13_alertas.csv"             : "alertas",
        "14_drivers.csv"             : "drivers",
        "15_comentarios.csv"         : "comentarios",
        "16_embudo.csv"              : "embudo",
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

    # Recorrer los blobs y descargar en paralelo solo los archivos que el portal usa
    trabajos = []   # (clave, nombre_blob)
    for blob in bucket.list_blobs(prefix="datos_procesados/"):
        partes = blob.name.split("/")
        n = len(partes)
        if n == 4 and partes[3] in TODOS_ARCHIVOS:          # ciudad/periodo/archivo
            trabajos.append((TODOS_ARCHIVOS[partes[3]], blob.name))
        elif n == 3 and partes[2] in ARCHIVOS_CIUDAD:        # ciudad/archivo
            trabajos.append((ARCHIVOS_CIUDAD[partes[2]], blob.name))
        elif n == 2 and partes[1] in TODOS_ARCHIVOS:         # estructura antigua (raíz)
            trabajos.append((TODOS_ARCHIVOS[partes[1]], blob.name))

    def _bajar(job):
        clave, nombre = job
        return clave, pd.read_csv(io.BytesIO(bucket.blob(nombre).download_as_bytes()), low_memory=False)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        for clave, df_leido in pool.map(_bajar, trabajos):
            acum[clave].append(df_leido)

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
            elif clave == "embudo":
                df_concat = df_concat.drop_duplicates(subset=["mes_anio", "ciudad", "dealer", "aps_nombre"], keep="last")
            elif clave == "comentarios":
                df_concat = df_concat.drop_duplicates(subset=["mes_anio", "id_contacto", "pregunta"], keep="last")
            elif clave == "drivers":
                df_concat = df_concat.drop_duplicates(subset=["mes_anio", "ciudad", "dealer", "atributo"], keep="last")
            elif clave == "alertas":
                df_concat = df_concat.drop_duplicates(subset=["mes_anio", "id_contacto"], keep="last")
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

# Límites para exportar tablas como imagen
JPG_MAX_FILAS = 150       # más filas que esto → descarga en Excel
JPG_DPI = 120             # resolución de las tablas exportadas como JPG
JPG_MAX_ALTO_PX = 12000   # alto máximo de la imagen en píxeles

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

    # dpi adaptativo: limita el alto de la imagen para no exceder la memoria del servidor
    dpi_final = max(40, min(JPG_DPI, int(JPG_MAX_ALTO_PX / max(fig_h, 1))))
    buf = io.BytesIO()
    fig.savefig(buf, format="jpeg", facecolor="white", bbox_inches="tight", dpi=dpi_final)
    plt.close(fig); buf.seek(0); return buf

# ==============================================================================
# DESCARGA DE TABLAS: JPG para tablas chicas, Excel para tablas grandes
# ==============================================================================
@st.cache_data(show_spinner=False, max_entries=20)
def df_to_xlsx_bytes(df, hoja="Datos"):
    """Convierte un DataFrame a bytes .xlsx (liviano en memoria)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=hoja[:31])
    return buf.getvalue()

def boton_descarga_tabla(contenedor, label, df, generar_jpg, nombre_archivo, key):
    """
    Muestra un botón de descarga.
    - Hasta JPG_MAX_FILAS filas: genera la imagen JPG con generar_jpg().
    - Más filas: ofrece la tabla en Excel para evitar que la app se quede sin memoria.
    """
    if len(df) <= JPG_MAX_FILAS:
        contenedor.download_button(label, data=generar_jpg(), file_name=f"{nombre_archivo}.jpg",
                                   mime="image/jpeg", key=key)
    else:
        contenedor.download_button(f"{label} (Excel)", data=df_to_xlsx_bytes(df),
                                   file_name=f"{nombre_archivo}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   key=key)
        contenedor.caption(f"La tabla tiene {len(df)} filas; se descarga en Excel en lugar de imagen.")

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
    return d.drop_duplicates("mes_anio").sort_values("orden_mes")["mes_anio"].tolist()

def tabla_html(styled): return f"<div class='table-scroll'>{styled.to_html()}</div>"

def kpi_html(label,val,fmt="{:.1%}",sub="",color="#1A1A2E"):
    v=fmt.format(val) if val is not None else "—"
    return f"""<div class='kpi-card'><div class='kpi-label'>{label}</div>
    <div class='kpi-value' style='color:{color}'>{v}</div>
    <div class='kpi-sub'>{sub}</div></div>"""
# ==============================================================================
# CSS
# ==============================================================================

aplicar_estilos()

# ==============================================================================
# ACCESO CON CONTRASEÑA (definida en st.secrets["app_password"])
# ==============================================================================
import hmac

def verificar_acceso():
    """Muestra la pantalla de acceso y detiene la app hasta ingresar la contraseña correcta."""
    if st.session_state.get("acceso_ok"):
        return
    try:
        clave_app = st.secrets.get("app_password")
    except Exception:
        clave_app = None
    if not clave_app:
        st.error("Falta configurar la contraseña de la app (app_password) en los secrets.")
        st.stop()

    st.markdown("""
<style>
    [data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none !important; }
</style>
""", unsafe_allow_html=True)
    _, c_login, _ = st.columns([1, 1.2, 1])
    with c_login:
        st.markdown("<div style='height:12vh'></div>", unsafe_allow_html=True)
        st.markdown("<div class='seccion-titulo'>Portal VoC Taiyo</div>", unsafe_allow_html=True)
        with st.form("form_acceso"):
            clave = st.text_input("Contraseña:", type="password")
            entrar = st.form_submit_button("Ingresar", type="primary", width="stretch")
        if entrar:
            if hmac.compare_digest(clave.encode("utf-8"), str(clave_app).encode("utf-8")):
                st.session_state["acceso_ok"] = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
    st.stop()

verificar_acceso()

# ==============================================================================
# SIDEBAR + SESSION STATE
# ==============================================================================
if "seccion" not in st.session_state: st.session_state.seccion = "caratula"
if "perfil" not in st.session_state: st.session_state.perfil = None
if "ciudad_sel" not in st.session_state: st.session_state.ciudad_sel = "TODAS"

import time as _time
_t0 = _time.perf_counter()
with st.spinner("Cargando datos desde GCS (puede tardar en la primera carga)..."):
    D = cargar_datos()
_dt = _time.perf_counter() - _t0
if _dt > 0.5: st.session_state["t_carga"] = _dt   # solo cuando realmente descargó (no desde caché)

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

# ==============================================================================
# SECCIÓN KAIZEN: ALERTAS POR CORREO A LOS APS
# ==============================================================================
import html as _html
import smtplib
import streamlit.components.v1 as components
import unicodedata as _ud
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

RUTA_DIRECTORIO_APS = "config/directorio_aps.csv"   # columnas: aps_nombre, email
RUTA_LOG_ENVIOS     = "config/log_envios_kaizen.csv"

def _norm_nombre(txt):
    """Normaliza nombres para cruzar el directorio con los datos (mayúsculas, sin tildes ni espacios extra)."""
    t = _ud.normalize("NFKD", str(txt)).encode("ascii", "ignore").decode("ascii")
    return " ".join(t.upper().split())

def _bucket_gcs():
    credentials = service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
    return storage.Client(credentials=credentials).bucket("bk_voc")

@st.cache_data(ttl=600, show_spinner=False)
def cargar_directorio_aps():
    """Lee el directorio de correos de los APS desde GCS. Devuelve {nombre_normalizado: email}."""
    try:
        blob = _bucket_gcs().blob(RUTA_DIRECTORIO_APS)
        if not blob.exists():
            return {}, f"No se encontró gs://bk_voc/{RUTA_DIRECTORIO_APS}"
        df = pd.read_csv(io.BytesIO(blob.download_as_bytes()), dtype=str)
    except Exception as e:
        return {}, f"No se pudo leer el directorio: {e}"
    cols = {c.strip().lower(): c for c in df.columns}
    if "aps_nombre" not in cols or "email" not in cols:
        return {}, "El directorio debe tener las columnas: aps_nombre, email"
    df = df.rename(columns={cols["aps_nombre"]: "aps_nombre", cols["email"]: "email"})
    df = df.dropna(subset=["aps_nombre", "email"])
    df["email"] = df["email"].str.strip()
    df = df[df["email"].str.contains("@", na=False)]
    return {_norm_nombre(n): e for n, e in zip(df["aps_nombre"], df["email"])}, ""

def registrar_envios_gcs(registros):
    """Agrega los envíos al log en GCS. Si falla, no interrumpe el envío."""
    try:
        blob = _bucket_gcs().blob(RUTA_LOG_ENVIOS)
        df_new = pd.DataFrame(registros)
        if blob.exists():
            df_old = pd.read_csv(io.BytesIO(blob.download_as_bytes()), dtype=str)
            df_new = pd.concat([df_old, df_new], ignore_index=True)
        blob.upload_from_string(df_new.to_csv(index=False), content_type="text/csv")
        return True
    except Exception:
        return False

@st.cache_data(ttl=300, show_spinner=False)
def cargar_log_envios():
    """Lee el log de envíos de Kaizen (solo lectura). DataFrame vacío si no existe o falla."""
    try:
        blob = _bucket_gcs().blob(RUTA_LOG_ENVIOS)
        if not blob.exists(): return pd.DataFrame()
        return pd.read_csv(io.BytesIO(blob.download_as_bytes()), dtype=str)
    except Exception:
        return pd.DataFrame()

def periodo_mes_actual(ciudad):
    """Último FYTD y último mes con datos de APS."""
    if not todos_fytd: return None, None
    fytd = todos_fytd[0]
    meses = meses_de(D["isc_aps"], fytd, ciudad)
    return fytd, (meses[-1] if meses else None)

_ORDEN_ALERTA = {"Pendiente": 0, "Vencida": 1, "Gestionada": 2}

# CES = % de clientes que califican 5 (de 5) cuánto la experiencia cumplió sus expectativas (VOC09).
# SUPUESTO: el reporte nuevo no trae la fórmula del proveedor; cambiar a 4 para contar también la nota 4.
CES_NOTA_MINIMA = 5

def alertas_cliente(aps=None, mes=None, ciudad="TODAS", fytd=None):
    """Alertas de clientes insatisfechos (casos graves a atender en 24 h), pendientes primero."""
    df = D["alertas"]
    if df.empty: return df
    df = aplicar_filtro_ciudad(df, ciudad)
    if fytd and "fytd" in df.columns: df = df[df["fytd"] == fytd]
    if mes and mes != "TODOS": df = df[df["mes_anio"] == mes]
    if aps and aps != "TODOS": df = df[df["aps_nombre"] == aps]
    return (df.assign(_o=df["estado"].map(_ORDEN_ALERTA).fillna(3))
              .sort_values(["_o", "dias_para_expirar"]).drop(columns="_o").reset_index(drop=True))

def _txt_estado_alerta(estado, dias):
    if estado == "Pendiente":
        if pd.notna(dias): return f"Pendiente · vence en {int(dias)} día{'s' if int(dias) != 1 else ''}"
        return "Pendiente"
    return str(estado)

def _col_txt(df, col):
    """Columna como texto para mostrar; '—' si no existe (datos de meses anteriores) o está vacía."""
    if col not in df.columns: return ["—"] * len(df)
    return df[col].astype(str).str.strip().replace({"nan": "—", "None": "—", "NaN": "—", "": "—", "-": "—"}).values

def _dedup_contactos(df):
    """Un contacto = un ID (reporte nuevo). Filas de meses antiguos (sin ID): nombre + celular."""
    if "id_contacto" not in df.columns:
        return df.drop_duplicates(subset=[c for c in ["cliente_nombre", "cliente_celular"] if c in df.columns])
    con_id = df[df["id_contacto"].notna()].drop_duplicates(subset=["mes_anio", "id_contacto"], keep="last")
    sin_id = df[df["id_contacto"].isna()]
    sin_id = sin_id.drop_duplicates(subset=[c for c in ["mes_anio", "cliente_nombre", "cliente_celular"] if c in sin_id.columns])
    return pd.concat([sin_id, con_id], ignore_index=True)

def resumen_aps(aps, fytd, mes, ciudad):
    """Calcula los datos del correo de un APS con la misma lógica que Vista APS."""
    meses_proc = [mes]
    df_tre = filtrar(D["tre_aps"], fytd=fytd, ciudad=ciudad, aps=aps)
    df_isc = filtrar(D["isc_aps"], fytd=fytd, ciudad=ciudad, aps=aps)
    df_tre = df_tre[df_tre["mes_anio"].isin(meses_proc)] if not df_tre.empty else df_tre
    df_isc = df_isc[df_isc["mes_anio"].isin(meses_proc)] if not df_isc.empty else df_isc

    obj_tre = get_obj(D["objetivos"], fytd, "obj_tre")
    obj_isc = get_obj(D["objetivos"], fytd, "obj_isc")

    e_tot = df_tre["E"].sum() if not df_tre.empty else 0
    c_tot = df_tre["C"].sum() if not df_tre.empty else 0
    tre_val = c_tot / e_tot if e_tot > 0 else 0
    enc_tot = df_isc["total_encuestas"].sum() if not df_isc.empty else 0
    pen = (df_isc["I16"].sum()*2 + df_isc["I78"].sum()) if not df_isc.empty else 0
    isc_val = (enc_tot - pen) / enc_tot if enc_tot > 0 else 0

    # Contactos en uso (vigentes y vencidos)
    df_p = aplicar_filtro_ciudad(D["pendientes"], ciudad)
    if {"aps_nombre", "status", "fecha_validez"}.issubset(df_p.columns):
        df_p = df_p[(df_p["aps_nombre"] == aps) & (df_p["status"] == "Contacto en uso")]
        df_p = _dedup_contactos(df_p)
    else:
        df_p = pd.DataFrame(columns=["cliente_nombre", "vehiculo", "vin", "fecha_validez"])
    fv = pd.to_datetime(df_p["fecha_validez"], format='mixed', dayfirst=True, errors='coerce')
    vencido = fv < pd.Timestamp.today().normalize()
    contactos = pd.DataFrame({
        "cliente": df_p["cliente_nombre"].astype(str).values if "cliente_nombre" in df_p.columns else [],
        "vehiculo": _col_txt(df_p, "vehiculo"),
        "vin":      _col_txt(df_p, "vin"),
        "vence":   (fv - pd.Timedelta(days=1)).dt.strftime('%d/%m/%Y').fillna("—").values,
        "vencido": vencido.values,
        "_orden":  fv.values,
    }).sort_values(["vencido", "_orden"]).drop(columns="_orden")
    vivas = int((~vencido).sum()); vencidas = int(vencido.sum())

    alertas = alertas_metas(isc_val, obj_isc, enc_tot, tre_val, obj_tre, e_tot)

    # Foco de mejora (Top 3), misma regla que Vista APS
    df_at = filtrar(D["atrib_aps"], fytd=fytd, ciudad=ciudad, aps=aps)
    df_at = df_at[df_at["mes_anio"].isin(meses_proc)] if not df_at.empty else df_at
    fort, aler = [], []
    if not df_at.empty:
        for attr, g in df_at.groupby("atributo"):
            if "bien a la primera" in str(attr).lower(): continue
            v = (g["pct_score"]*g["n_respuestas"]).sum()/g["n_respuestas"].sum() if ("n_respuestas" in g.columns and g["n_respuestas"].sum() > 0) else g["pct_score"].mean()
            m = g["obj_atributo"].mean() / 100.0 if g["obj_atributo"].mean() > 2 else g["obj_atributo"].mean()
            (fort if v >= m else aler).append({"attr": attr, "val": v, "meta": m})
    fort = sorted(fort, key=lambda x: x["val"], reverse=True)[:3]
    aler = sorted(aler, key=lambda x: x["val"])[:3]

    al_cli = alertas_cliente(aps, mes, ciudad, fytd)
    return {"aps": aps, "fytd": fytd, "mes": mes, "e_tot": int(e_tot), "c_tot": int(c_tot),
            "alertas_cli": al_cli,
            "alertas_pend": int((al_cli["estado"] == "Pendiente").sum()) if not al_cli.empty else 0,
            "alertas_venc": int((al_cli["estado"] == "Vencida").sum()) if not al_cli.empty else 0,
            "tre": tre_val, "obj_tre": obj_tre, "enc_tot": int(enc_tot), "isc": isc_val, "obj_isc": obj_isc,
            "vivas": vivas, "vencidas": vencidas, "contactos": contactos,
            "alertas": alertas, "fort": fort, "aler": aler}

def _color_kpi(val, obj):
    if val >= obj: return "#388E3C"
    if val >= obj - 0.03: return "#F57F17"
    return "#D32F2F"

def alertas_metas(isc_val, obj_isc, enc_tot, tre_val, obj_tre, e_tot):
    """Alertas de cumplimiento de meta (ISC y TRE). Fuente única para el correo Kaizen y Vista APS.
    Devuelve [(mensaje, color), (mensaje, color)]."""
    def _alerta(nombre, val, obj, hay_datos):
        if not hay_datos:
            return f"{nombre}: aún no tienes resultados registrados este mes.", "#555555"
        if val >= obj:
            return (f"¡Felicidades! Alcanzaste la meta de {nombre}: lograste {val:.1%} frente a una meta de {obj:.0%}. "
                    f"Sigue así."), "#2E7D32"
        return (f"No alcanzaste la meta de {nombre}: lograste {val:.1%} frente a una meta de {obj:.0%} "
                f"({(obj - val) * 100:.1f} puntos por debajo). Enfoquémonos en mejorar este indicador."), _color_kpi(val, obj)
    return [_alerta("ISC", isc_val, obj_isc, enc_tot > 0), _alerta("TRE", tre_val, obj_tre, e_tot > 0)]

FONDO_ALERTA = {"#2E7D32": "#E8F5E9", "#F57F17": "#FFF8E1", "#D32F2F": "#FFEBEE", "#555555": "#F5F5F5"}

def html_correo_aps(r):
    """Arma el correo HTML (estilos en línea, compatible con Gmail/Outlook)."""
    e = _html.escape
    kpi = lambda tit, val, sub, color: f"""
      <td width="33%" style="padding:6px;"><table width="100%" cellpadding="0" cellspacing="0" style="background:#FFFFFF;border-top:4px solid #1A1A2E;border-radius:8px;">
        <tr><td align="center" style="padding:12px 6px 2px;font:700 11px Arial,sans-serif;color:#666;text-transform:uppercase;letter-spacing:1px;">{tit}</td></tr>
        <tr><td align="center" style="font:900 28px Arial,sans-serif;color:{color};">{val}</td></tr>
        <tr><td align="center" style="padding:2px 6px 12px;font:12px Arial,sans-serif;color:#999;">{sub}</td></tr></table></td>"""
    filas = ""
    for _, c in r["contactos"].iterrows():
        est = ("<span style='color:#D32F2F;font-weight:bold;'>Vencido</span>" if c["vencido"]
               else "<span style='color:#F57F17;font-weight:bold;'>Pendiente</span>")
        filas += (f"<tr><td style='padding:6px 8px;border-bottom:1px solid #EEE;font:13px Arial,sans-serif;'>{e(c['cliente'])}</td>"
                  f"<td style='padding:6px 8px;border-bottom:1px solid #EEE;font:13px Arial,sans-serif;'>{e(c['vehiculo'])}</td>"
                  f"<td style='padding:6px 8px;border-bottom:1px solid #EEE;font:12px Arial,sans-serif;'>{e(c['vin'])}</td>"
                  f"<td align='center' style='padding:6px 8px;border-bottom:1px solid #EEE;font:13px Arial,sans-serif;'>{e(c['vence'])}</td>"
                  f"<td align='center' style='padding:6px 8px;border-bottom:1px solid #EEE;font:13px Arial,sans-serif;'>{est}</td></tr>")
    if not filas:
        tabla_contactos = "<p style='font:14px Arial,sans-serif;color:#2E7D32;'>¡Sin contactos en uso! Buen trabajo.</p>"
    else:
        tabla_contactos = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#FFFFFF;">
          <tr style="background:#1A1A2E;"><th align="left" style="padding:8px;font:700 12px Arial,sans-serif;color:#FFF;">Cliente</th>
          <th align="left" style="padding:8px;font:700 12px Arial,sans-serif;color:#FFF;">Vehículo</th>
          <th align="left" style="padding:8px;font:700 12px Arial,sans-serif;color:#FFF;">VIN</th>
          <th style="padding:8px;font:700 12px Arial,sans-serif;color:#FFF;">Vence</th>
          <th style="padding:8px;font:700 12px Arial,sans-serif;color:#FFF;">Estado</th></tr>{filas}</table>"""

    # Alertas de clientes insatisfechos: solo aparece si el asesor tiene casos en el periodo
    al = r["alertas_cli"]
    if al.empty:
        bloque_alertas_cli = ""
    else:
        filas_al = ""
        for _, a_ in al.iterrows():
            pend_ = a_["estado"] == "Pendiente"
            col_ = "#C62828" if pend_ else ("#757575" if a_["estado"] == "Vencida" else "#2E7D32")
            motivo = "" if pd.isna(a_.get("verbalizacion")) else str(a_["verbalizacion"])
            motivo = motivo if len(motivo) <= 180 else motivo[:177] + "..."
            calif = "" if pd.isna(a_.get("satisfaccion")) else f"{int(a_['satisfaccion'])}"
            fondo_ = "#FFF3F3" if pend_ else "#FFFFFF"
            td = f"padding:6px 8px;border-bottom:1px solid #EEE;font:13px Arial,sans-serif;background:{fondo_};"
            filas_al += (f"<tr><td style='{td}'><b>{e(str(a_['cliente_nombre']))}</b></td>"
                         f"<td style='{td}'>{e(str(a_['vehiculo']))}<br><span style='font-size:11px;color:#777;'>{e(str(a_['vin']))}</span></td>"
                         f"<td align='center' style='{td}'>{e(calif)}</td>"
                         f"<td style='{td}'>{e(motivo)}</td>"
                         f"<td align='center' style='{td}color:{col_};font-weight:bold;'>{e(_txt_estado_alerta(a_['estado'], a_.get('dias_para_expirar')))}</td></tr>")
        aviso_al = ("Casos graves: el cliente debe ser contactado dentro de las primeras 24 horas. Búscalo en el sistema por nombre o VIN."
                    if r["alertas_pend"] else "Estas alertas ya superaron el plazo de atención; se informan para tu seguimiento.")
        bloque_alertas_cli = f"""
  <tr><td style="padding:14px 10px 2px;font:900 16px Arial,sans-serif;color:#C62828;">ALERTAS DE CLIENTES INSATISFECHOS ({r['alertas_pend']} por atender · {r['alertas_venc']} vencidas)</td></tr>
  <tr><td style="padding:0 10px 6px;font:13px Arial,sans-serif;color:#555;">{aviso_al}</td></tr>
  <tr><td style="padding:0 10px;"><table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#FFFFFF;border:1px solid #F5C6C6;">
    <tr style="background:#C62828;"><th align="left" style="padding:8px;font:700 12px Arial,sans-serif;color:#FFF;">Cliente</th>
    <th align="left" style="padding:8px;font:700 12px Arial,sans-serif;color:#FFF;">Vehículo / VIN</th>
    <th style="padding:8px;font:700 12px Arial,sans-serif;color:#FFF;">Calif.</th>
    <th align="left" style="padding:8px;font:700 12px Arial,sans-serif;color:#FFF;">Motivo</th>
    <th style="padding:8px;font:700 12px Arial,sans-serif;color:#FFF;">Estado</th></tr>{filas_al}</table></td></tr>"""

    def bloque_focos(lista, titulo, color, fondo, borde):
        if not lista:
            return f"<p style='font:13px Arial,sans-serif;color:#777;margin:4px 0;'>Sin datos este mes.</p>" if titulo else ""
        return "".join(f"""<div style="background:{fondo};border-left:5px solid {borde};padding:8px 10px;margin-bottom:6px;border-radius:4px;">
            <span style="font:700 13px Arial,sans-serif;color:#000;">{e(str(x['attr']))}</span><br>
            <span style="font:900 18px Arial,sans-serif;color:#000;">{x['val']:.1%}</span>
            <span style="font:700 12px Arial,sans-serif;color:#444;">(Meta: {x['meta']:.0%})</span></div>""" for x in lista)

    return f"""<!doctype html><html><body style="margin:0;background:#F5F6FA;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F5F6FA;"><tr><td align="center" style="padding:16px;">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">
  <tr><td style="background:#1A1A2E;padding:16px 20px;border-bottom:4px solid #FFD600;">
    <div style="font:900 20px Arial,sans-serif;color:#FFFFFF;letter-spacing:1px;">VOC INSIGHTS · TAIYO MOTORS</div>
    <div style="font:14px Arial,sans-serif;color:#E0E0E0;margin-top:4px;">Resultados de <b>{e(r['aps'])}</b> — {e(str(r['mes']))} ({e(str(r['fytd']))})</div></td></tr>
  <tr><td style="padding:14px 4px 0;"><table width="100%" cellpadding="0" cellspacing="0"><tr>
    {kpi("Encuestas enviadas", f"{r['e_tot']}", f"Meta TRE: {r['obj_tre']:.0%}", "#1A1A2E")}
    {kpi("TRE", f"{r['tre']:.1%}", f"Meta: {r['obj_tre']:.0%}", _color_kpi(r['tre'], r['obj_tre']))}
    {kpi("ISC", f"{r['isc']:.1%}", f"Meta: {r['obj_isc']:.0%}", _color_kpi(r['isc'], r['obj_isc']))}
  </tr></table></td></tr>
  {"".join(f"""<tr><td style="padding:4px 10px;"><div style="background:#FFFFFF;border-left:5px solid {c};padding:10px 12px;font:14px Arial,sans-serif;color:{c};">{e(m)}</div></td></tr>""" for m, c in r['alertas'])}
  {bloque_alertas_cli}
  <tr><td style="padding:12px 10px 4px;font:900 16px Arial,sans-serif;color:#1A1A2E;">Contactos en uso ({r['vivas']} pendientes · {r['vencidas']} vencidos)</td></tr>
  <tr><td style="padding:0 10px;">{tabla_contactos}</td></tr>
  <tr><td style="padding:16px 10px 4px;font:900 16px Arial,sans-serif;color:#1A1A2E;">Foco de mejora (Top 3 atributos)</td></tr>
  <tr><td style="padding:0 10px;"><table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td width="50%" valign="top" style="padding-right:6px;"><div style="font:700 14px Arial,sans-serif;color:#2E7D32;margin-bottom:6px;">Fortalezas</div>{bloque_focos(r['fort'], 'f', '#2E7D32', '#E8F5E9', '#4CAF50')}</td>
    <td width="50%" valign="top" style="padding-left:6px;"><div style="font:700 14px Arial,sans-serif;color:#C62828;margin-bottom:6px;">Por mejorar</div>{bloque_focos(r['aler'], 'a', '#C62828', '#FFEBEE', '#F44336')}</td>
  </tr></table></td></tr>
  <tr><td style="padding:18px 10px;font:12px Arial,sans-serif;color:#888;">Correo enviado por el área de Calidad (Kaizen). Información confidencial: contiene datos de clientes solo para tu gestión.</td></tr>
</table></td></tr></table></body></html>"""

def texto_correo_aps(r):
    lineas = [f"Resultados de {r['aps']} — {r['mes']} ({r['fytd']})", "",
              f"Encuestas enviadas: {r['e_tot']}", f"TRE: {r['tre']:.1%} (meta {r['obj_tre']:.0%})",
              f"ISC: {r['isc']:.1%} (meta {r['obj_isc']:.0%})", ""] + [m for m, _ in r["alertas"]] + ["",
              f"Contactos en uso: {r['vivas']} pendientes, {r['vencidas']} vencidos"]
    if not r["alertas_cli"].empty:
        lineas += ["", f"ALERTAS DE CLIENTES INSATISFECHOS ({r['alertas_pend']} por atender, {r['alertas_venc']} vencidas)" + (" — contactar en las primeras 24 h:" if r["alertas_pend"] else " — plazo de atención superado:")]
        for _, a_ in r["alertas_cli"].iterrows():
            lineas.append(f"- {a_['cliente_nombre']} | {a_['vehiculo']} | {a_['vin']} | calif. {a_['satisfaccion']:.0f} | {_txt_estado_alerta(a_['estado'], a_.get('dias_para_expirar'))}")
        lineas.append("")
    for _, c in r["contactos"].iterrows():
        lineas.append(f"- {c['cliente']} | {c['vehiculo']} | {c['vin']} | vence {c['vence']}{' (VENCIDO)' if c['vencido'] else ''}")
    lineas += ["", "Fortalezas:"] + [f"- {x['attr']}: {x['val']:.1%} (meta {x['meta']:.0%})" for x in r["fort"]]
    lineas += ["", "Por mejorar:"] + [f"- {x['attr']}: {x['val']:.1%} (meta {x['meta']:.0%})" for x in r["aler"]]
    return "\n".join(lineas)

def asunto_correo(tpl, mes, aps, r):
    """Asunto del correo; se marca como ALERTA cuando el asesor tiene casos por atender."""
    a = tpl.format(mes=mes, aps=aps)
    return f"[ALERTA] {a} · {r['alertas_pend']} cliente(s) por contactar" if r["alertas_pend"] else a

def config_gmail():
    try:
        g = st.secrets["gmail"]
        user, pwd = g.get("user"), g.get("app_password")
        cc = g.get("cc", user); nombre = g.get("nombre", "Calidad VoC Taiyo")
    except Exception:
        return None
    if not user or not pwd: return None
    return {"user": user, "pwd": str(pwd).replace(" ", ""), "cc": cc, "nombre": nombre}

def enviar_correos(lote, cfg):
    """lote: lista de (destinatario, cc, asunto, html, texto). Devuelve lista de (destinatario, ok, detalle)."""
    resultados = []
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as srv:
        srv.login(cfg["user"], cfg["pwd"])
        for dest, cc, asunto, cuerpo_html, cuerpo_txt in lote:
            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = asunto
                msg["From"] = formataddr((cfg["nombre"], cfg["user"]))
                msg["To"] = dest
                if cc and cc.lower() != dest.lower(): msg["Cc"] = cc
                msg.attach(MIMEText(cuerpo_txt, "plain", "utf-8"))
                msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))
                rcpts = [dest] + ([cc] if cc and cc.lower() != dest.lower() else [])
                srv.sendmail(cfg["user"], rcpts, msg.as_string())
                resultados.append((dest, True, "Enviado"))
            except Exception as ex:
                resultados.append((dest, False, str(ex)[:120]))
    return resultados

def render_kaizen_alertas():
    st.markdown("<div class='seccion-titulo'>Kaizen — Alertas por correo a los APS</div>", unsafe_allow_html=True)

    cfg = config_gmail()
    if cfg is None:
        st.error("Falta configurar la cuenta de Gmail en los secrets (sección [gmail] con user y app_password).")
        return

    fytd, mes = periodo_mes_actual(CIUDAD)
    if not mes:
        st.info("No hay datos de APS para el periodo actual."); return

    directorio, err_dir = cargar_directorio_aps()
    if err_dir: st.warning(err_dir)

    aps_lista = [a for a in aps_para_filtro(CIUDAD, "GENERAL", fytd) if a != "TODOS" and "SIN ASESOR" not in str(a).upper()]
    df_dir = pd.DataFrame({"APS": aps_lista})
    df_dir["Correo"] = [directorio.get(_norm_nombre(a), "") for a in aps_lista]
    con_correo = df_dir[df_dir["Correo"] != ""]["APS"].tolist()
    sin_correo = df_dir[df_dir["Correo"] == ""]["APS"].tolist()

    k1, k2, k3, k4 = st.columns(4)
    _al_mes = alertas_cliente(None, mes, CIUDAD, fytd)
    _n_pend = int((_al_mes["estado"] == "Pendiente").sum()) if not _al_mes.empty else 0
    with k4: st.markdown(kpi_html("Alertas por atender", _n_pend, "{:.0f}", "Se incluyen en los correos", color="#D32F2F" if _n_pend else "#1A1A2E"), unsafe_allow_html=True)
    with k1: st.markdown(kpi_html("Periodo del correo", mes, "{}", f"Último mes · {fytd}"), unsafe_allow_html=True)
    with k2: st.markdown(kpi_html("APS con correo", len(con_correo), "{:.0f}", f"de {len(aps_lista)} asesores", color="#388E3C"), unsafe_allow_html=True)
    with k3: st.markdown(kpi_html("APS sin correo", len(sin_correo), "{:.0f}", "Agrégalos al directorio", color="#D32F2F" if sin_correo else "#1A1A2E"), unsafe_allow_html=True)
    st.caption(f"Copia (CC) de cada correo: {cfg['cc']} · Directorio: gs://bk_voc/{RUTA_DIRECTORIO_APS}")
    if sin_correo:
        with st.expander(f"Ver {len(sin_correo)} APS sin correo en el directorio"):
            st.write(", ".join(sin_correo))

    # --- Panel de cobertura y prioridades (solo lectura; no envía nada) ---
    log_df = cargar_log_envios()
    ultimo = {}
    if not log_df.empty and {"aps", "fecha"} <= set(log_df.columns):
        ultimo = log_df.groupby("aps")["fecha"].max().to_dict()
    res_kz = {a: resumen_aps(a, fytd, mes, CIUDAD) for a in aps_lista}
    filas_kz = [{"Asesor": a, "Correo": "✓" if a in con_correo else "—",
                 "Alertas por atender": res_kz[a]["alertas_pend"], "Alertas vencidas": res_kz[a]["alertas_venc"],
                 "Contactos vigentes": res_kz[a]["vivas"],
                 "ISC": res_kz[a]["isc"] if res_kz[a]["enc_tot"] > 0 else np.nan,
                 "TRE": res_kz[a]["tre"] if res_kz[a]["e_tot"] > 0 else np.nan,
                 "Último envío": ultimo.get(a, "—")} for a in aps_lista]
    df_kz = pd.DataFrame(filas_kz).sort_values(["Alertas por atender", "Contactos vigentes"], ascending=False).reset_index(drop=True)
    obj_isc_kz = get_obj(D["objetivos"], fytd, "obj_isc"); obj_tre_kz = get_obj(D["objetivos"], fytd, "obj_tre")
    with st.container(key="card_kz_cobertura"):
        st.markdown("<div class='card-titulo'>Cobertura y prioridades</div>", unsafe_allow_html=True)
        n_alerta_aps = int((df_kz["Alertas por atender"] > 0).sum())
        if n_alerta_aps:
            st.markdown(f"<div class='banner-alerta'>{n_alerta_aps} asesor(es) tienen alertas de clientes por atender: su correo saldrá con <b>[ALERTA]</b> en el asunto.</div>", unsafe_allow_html=True)
        def _pinta(v, obj):
            return "" if pd.isna(v) else ("color:#2E7D32;font-weight:700" if v >= obj else ("color:#F57F17;font-weight:700" if v >= obj - 0.03 else "color:#C62828;font-weight:700"))
        st_kz = (df_kz.style.format({"ISC": "{:.1%}", "TRE": "{:.1%}"}, na_rep="—")
                 .map(lambda v: "background:#FFEBEE;color:#C62828;font-weight:700" if v and v > 0 else "", subset=["Alertas por atender"])
                 .map(lambda v: _pinta(v, obj_isc_kz), subset=["ISC"]).map(lambda v: _pinta(v, obj_tre_kz), subset=["TRE"])
                 .map(lambda v: "color:#C62828;font-weight:700" if v == "—" else "", subset=["Correo"]))
        st.dataframe(st_kz, hide_index=True, width="stretch")
        st.caption(f"ISC y TRE de {mes} (colores contra la meta). \"Último envío\" sale del registro de envíos.")
        con_alerta = [a for a in con_correo if res_kz[a]["alertas_pend"] > 0]
        b1, b2, _b3 = st.columns([1.3, 1, 2])
        b1.button(f"Solo con alertas por atender ({len(con_alerta)})", key="kz_b_al", disabled=not con_alerta,
                  on_click=lambda: st.session_state.__setitem__("kz_sel", con_alerta))
        b2.button("Seleccionar todos", key="kz_b_all", on_click=lambda: st.session_state.__setitem__("kz_sel", con_correo))

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    seleccion = st.multiselect("Asesores a notificar:", con_correo, default=con_correo, key="kz_sel")

    # --- Vista previa ---
    st.markdown("<h5 style='color:#1A1A2E; margin-top:15px; font-weight:bold;'>Vista previa</h5>", unsafe_allow_html=True)
    aps_prev = st.selectbox("Ver el correo de:", seleccion or aps_lista, key="kz_prev")
    r_prev = resumen_aps(aps_prev, fytd, mes, CIUDAD)
    if hasattr(st, "iframe"):
        st.iframe(html_correo_aps(r_prev), height=900)
    else:
        components.html(html_correo_aps(r_prev), height=900, scrolling=True)

    asunto_tpl = "VoC Taiyo · Tus resultados {mes} — {aps}"

    # --- Envío de prueba ---
    c_p, c_e = st.columns(2)
    with c_p:
        if st.button(f"Enviar prueba a {cfg['user']}", key="kz_test", width="stretch"):
            try:
                res = enviar_correos([(cfg["user"], None, "[PRUEBA] " + asunto_correo(asunto_tpl, mes, aps_prev, r_prev),
                                       html_correo_aps(r_prev), texto_correo_aps(r_prev))], cfg)
                st.success("Prueba enviada.") if res[0][1] else st.error(f"Error: {res[0][2]}")
            except Exception as ex:
                st.error(f"No se pudo conectar con Gmail: {ex}")

    # --- Envío real ---
    with c_e:
        if st.session_state.get("kz_ultimo_envio") == f"{mes}|{fytd}":
            st.warning(f"Ya enviaste los correos de {mes} en esta sesión.")
        confirmar = st.checkbox(f"Confirmo el envío a {len(seleccion)} asesores ({mes})",
                                key=f"kz_conf_{st.session_state.get('kz_n_envios', 0)}")
        enviar = st.button(f"Enviar a {len(seleccion)} asesores", key="kz_send", type="primary",
                           width="stretch", disabled=not (confirmar and seleccion))
    if enviar:
        lote = []
        for a in seleccion:
            r = resumen_aps(a, fytd, mes, CIUDAD)
            lote.append((directorio[_norm_nombre(a)], cfg["cc"], asunto_correo(asunto_tpl, mes, a, r),
                         html_correo_aps(r), texto_correo_aps(r)))
        with st.spinner(f"Enviando {len(lote)} correos..."):
            try:
                res = enviar_correos(lote, cfg)
            except Exception as ex:
                st.error(f"No se pudo conectar con Gmail: {ex}"); return
        ahora = pd.Timestamp.now(tz="America/La_Paz").strftime("%Y-%m-%d %H:%M")
        df_res = pd.DataFrame([{"APS": a, "Correo": d, "Resultado": det} for a, (d, ok, det) in zip(seleccion, res)])
        ok_n = sum(1 for _, ok, _ in res if ok)
        (st.success if ok_n == len(res) else st.warning)(f"Enviados {ok_n} de {len(res)} correos.")
        st.markdown(tabla_html(df_res.style.hide(axis="index").set_table_attributes('class="tabla-voc"')), unsafe_allow_html=True)
        registrar_envios_gcs([{"fecha": ahora, "periodo": f"{mes} {fytd}", "aps": a, "email": d, "resultado": det}
                              for a, (d, ok, det) in zip(seleccion, res)])
        st.session_state["kz_ultimo_envio"] = f"{mes}|{fytd}"
        st.session_state["kz_n_envios"] = st.session_state.get("kz_n_envios", 0) + 1

def verificar_acceso_kaizen():
    """Segunda contraseña para el perfil Kaizen (st.secrets['kaizen_password'])."""
    if st.session_state.get("acceso_kaizen"):
        return
    try:
        clave_kz = st.secrets.get("kaizen_password")
    except Exception:
        clave_kz = None
    st.markdown("<style>[data-testid='stSidebar'],[data-testid='collapsedControl']{display:none!important}</style>", unsafe_allow_html=True)
    _, c_login, _ = st.columns([1, 1.2, 1])
    with c_login:
        st.markdown("<div style='height:12vh'></div>", unsafe_allow_html=True)
        st.markdown("<div class='seccion-titulo'>Vista Kaizen</div>", unsafe_allow_html=True)
        if not clave_kz:
            st.error("Falta configurar kaizen_password en los secrets.")
        else:
            with st.form("form_kaizen"):
                clave = st.text_input("Contraseña Kaizen:", type="password")
                entrar = st.form_submit_button("Ingresar", type="primary", width="stretch")
            if entrar:
                if hmac.compare_digest(clave.encode("utf-8"), str(clave_kz).encode("utf-8")):
                    st.session_state["acceso_kaizen"] = True
                    st.rerun()
                else:
                    st.error("Contraseña incorrecta.")
        if st.button("Volver al inicio", key="kz_volver"):
            st.session_state.perfil = None; st.session_state.seccion = "caratula"; st.rerun()
    st.stop()

sec = st.session_state.seccion
perfil = st.session_state.perfil
if perfil == "KAIZEN":
    verificar_acceso_kaizen()

if sec != "caratula":
    with st.sidebar:
        st.markdown('<div style="text-align:center; margin-bottom:1.5cm; margin-top:-25px;"><div style="color:#FFFFFF; font-family:\'Barlow\',sans-serif; font-size:13px; font-weight:500; letter-spacing:6px; text-transform:uppercase; margin-bottom:4px;">TAIYO MOTORS</div><div style="display:flex; flex-direction:column; align-items:center; line-height:0.9;"><div style="background:linear-gradient(90deg, #E0E0E0 0%, #9E9E9E 50%, #616161 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; font-family:\'Barlow Condensed\',sans-serif; font-size:26px; font-weight:900; letter-spacing:1px; text-transform:uppercase; margin-bottom:2px;">VOC INSIGHTS</div><div style="background:linear-gradient(90deg, #E0E0E0 0%, #9E9E9E 50%, #616161 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; font-family:\'Barlow Condensed\',sans-serif; font-size:26px; font-weight:900; letter-spacing:2px; text-transform:uppercase;">ANALYTICS</div></div></div>', unsafe_allow_html=True)
        
        if st.button("INICIO", key="nav_ini"):  
            st.session_state.seccion = "caratula"
            st.session_state.perfil = None
            st.session_state.acceso_kaizen = False
            st.rerun()
        st.markdown("<hr>", unsafe_allow_html=True)
        
        if perfil in ("GENERAL", "KAIZEN"):
            st.markdown("<div style='padding:8px 16px;font-size:11px;color:#666;letter-spacing:1px;font-weight:700'>MEDICIÓN</div>", unsafe_allow_html=True)
            if st.button("GENERAL",         key="nav_gen"):  st.session_state.seccion="general"
            if st.button("ATRIBUTOS",       key="nav_atr"):  st.session_state.seccion="atributos"
            if st.button("VERBALIZACIONES", key="nav_ver"):  st.session_state.seccion="verbalizaciones"
            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown("<div style='padding:8px 16px;font-size:11px;color:#666;letter-spacing:1px;font-weight:700'>OPERATIVO</div>", unsafe_allow_html=True)
            if st.button("EMBUDO DE ENVÍO", key="nav_emb"): st.session_state.seccion="embudo"
            if st.button("ALERTAS 24 H",  key="nav_alr"):  st.session_state.seccion="alertas"
            if st.button("PENDIENTES",    key="nav_pen"):  st.session_state.seccion="pendientes"
            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown("<div style='padding:8px 16px;font-size:11px;color:#666;letter-spacing:1px;font-weight:700'>MI PERFIL</div>", unsafe_allow_html=True)
            if st.button("VISTA APS", key="nav_aps"): st.session_state.seccion="vista_aps"
            if perfil == "KAIZEN":
                st.markdown("<hr>", unsafe_allow_html=True)
                st.markdown("<div style='padding:8px 16px;font-size:11px;color:#666;letter-spacing:1px;font-weight:700'>KAIZEN</div>", unsafe_allow_html=True)
                if st.button("ALERTAS POR CORREO", key="nav_kz"): st.session_state.seccion="kaizen_alertas"
                with st.expander("Diagnóstico"):
                    _mb = {k: v.memory_usage(deep=True).sum() / 1e6 for k, v in D.items() if hasattr(v, "memory_usage") and not v.empty}
                    st.caption(f"Datos en memoria: {sum(_mb.values()):.1f} MB")
                    st.caption(" · ".join(f"{k} {v:.1f}" for k, v in sorted(_mb.items(), key=lambda x: -x[1])[:6]))
                    if "t_carga" in st.session_state: st.caption(f"Última descarga desde GCS: {st.session_state['t_carga']:.1f} s")
                    try:
                        import psutil; st.caption(f"Memoria del proceso: {psutil.Process().memory_info().rss / 1e6:.0f} MB")
                    except Exception:
                        try:
                            _rss = [l for l in open("/proc/self/status") if l.startswith("VmRSS")][0].split()[1]
                            st.caption(f"Memoria del proceso: {int(_rss) / 1e3:.0f} MB")
                        except Exception: pass
            st.markdown("<hr>", unsafe_allow_html=True)
            # Solo los perfiles General y Kaizen pueden recargar los datos.
            # Se limpia únicamente la caché de cargar_datos (no la de imágenes ni gráficos).
            if st.button("Recargar datos", key="nav_rel"):
                cargar_datos.clear()
                st.session_state["datos_recargados"] = True
                st.rerun()
        
        elif perfil == "APS":
            st.markdown("<div style='padding:8px 16px;font-size:11px;color:#666;letter-spacing:1px;font-weight:700'>MI PERFIL</div>", unsafe_allow_html=True)
            if st.button("VISTA APS", key="nav_aps"): st.session_state.seccion="vista_aps"

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

# ==============================================================================
# SECCIÓN 1: GENERAL (incluye la tendencia del ISC)
# ==============================================================================
def _sin_dealer(df):
    if df.empty or "dealer" not in df.columns: return df
    return df[~df["dealer"].astype(str).str.upper().str.contains("SIN DEALER", na=False)]

def kpis_periodo(meses, fytd, ciudad):
    """Totales de la ciudad (todos los dealers) para una lista de meses: enviadas, respondidas, TRE e ISC."""
    im = _sin_dealer(filtrar(D["isc_mensual"], fytd=fytd, ciudad=ciudad))
    tm = _sin_dealer(filtrar(D["tre_mensual"], fytd=fytd, ciudad=ciudad))
    im = im[im["mes_anio"].isin(meses)] if not im.empty else im
    tm = tm[tm["mes_anio"].isin(meses)] if not tm.empty else tm
    enc = im["total_encuestas"].sum() if not im.empty else 0
    i16 = im["I16"].sum() if not im.empty else 0; i78 = im["I78"].sum() if not im.empty else 0
    E = tm["E"].sum() if not tm.empty else 0; Cc = tm["C"].sum() if not tm.empty else 0
    return {"E": E, "C": Cc, "enc": enc,
            "tre": Cc / E if E > 0 else np.nan,
            "isc": (enc - 2 * i16 - i78) / enc if enc > 0 else np.nan}

def datos_tendencia(fytd, ciudad, dealer, acumulado):
    """Series mensuales (o acumuladas) de ISC: promedio + una serie por dealer (o por asesor si hay dealer)."""
    if dealer != "GENERAL":
        df_base = filtrar(D["isc_aps"], fytd=fytd, ciudad=ciudad, dealer=dealer)
        df_prom = filtrar(D["isc_mensual"], fytd=fytd, ciudad=ciudad, dealer=dealer)
        clave = "aps_nombre"
    else:
        df_base = _sin_dealer(filtrar(D["isc_mensual"], fytd=fytd, ciudad=ciudad)); df_prom = df_base; clave = "dealer"
    if df_base.empty: return None
    orden = dict(zip(df_base["mes_anio"], df_base["orden_mes"]))
    meses = sorted(orden, key=orden.get); ords = [orden[m] for m in meses]

    def serie(sub, acum):
        g = sub.groupby("orden_mes")[["total_encuestas", "I16", "I78"]].sum().reindex(ords).fillna(0)
        n_mes = g["total_encuestas"].tolist()
        if acum: g = g.cumsum()
        n = g["total_encuestas"]
        isc = ((n - 2 * g["I16"] - g["I78"]) / n.where(n > 0)).tolist()
        return isc, n_mes

    prom, n_prom = serie(df_prom, acumulado)
    series = {}
    for nombre in sorted(x for x in df_base[clave].unique() if str(x).strip().upper() != "SIN DEALER"):
        series[nombre] = serie(df_base[df_base[clave] == nombre], acumulado)
    return {"meses": meses, "prom": prom, "n_prom": n_prom, "series": series, "por": clave}

def fig_tendencia_isc(d, obj_isc, titulo_prom, resaltar=None):
    """Línea del ISC con meta, barras de encuestas y una serie por dealer/asesor (interactiva)."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    meses = d["meses"]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if resaltar in meses:
        i = meses.index(resaltar)
        fig.add_vrect(x0=i - 0.5, x1=i + 0.5, fillcolor=C["amarillo"], opacity=0.16, line_width=0, layer="below")
    fig.add_trace(go.Bar(x=meses, y=d["n_prom"], name="Encuestas", marker_color="#DDE1EA", opacity=0.9,
                         hovertemplate="%{y} encuestas<extra></extra>"), secondary_y=True)
    colores_dealer = {"Centro Nacional de Servicio": "#1976D2", "El Alto": "#D32F2F", "Express": "#388E3C"}
    muchas = len(d["series"]) > 6
    for k, (nombre, (vals, ns)) in enumerate(d["series"].items()):
        color = colores_dealer.get(nombre, COLORWAY[k % len(COLORWAY)])
        fig.add_trace(go.Scatter(x=meses, y=vals, mode="lines+markers", name=nombre, customdata=ns,
                                 line=dict(color=color, width=2), marker=dict(size=6),
                                 visible="legendonly" if muchas else True,
                                 hovertemplate=nombre + ": %{y:.1%} (%{customdata} enc. del mes)<extra></extra>"),
                      secondary_y=False)
    fig.add_trace(go.Scatter(x=meses, y=d["prom"], mode="lines+markers+text", name=titulo_prom,
                             line=dict(color=C["navy"], width=4), marker=dict(size=10, color=C["amarillo"], line=dict(color=C["navy"], width=2)),
                             text=[("" if v != v else f"{v:.1%}") for v in d["prom"]], textposition="top center",
                             textfont=dict(size=12, color=C["navy"]),
                             hovertemplate=titulo_prom + ": %{y:.1%}<extra></extra>"), secondary_y=False)
    fig.add_hline(y=obj_isc, line_dash="dash", line_color=C["naranja"], secondary_y=False,
                  annotation_text=f"Meta {obj_isc:.0%}", annotation_position="top left", annotation_font_color=C["naranja"])
    vals = [v for v in d["prom"] if v == v] + [v for _, (vs, _) in d["series"].items() for v in vs if v == v] + [obj_isc]
    lo = max(0.0, min(vals) - 0.06); hi = min(1.03, max(vals) + 0.06)
    fig.update_yaxes(tickformat=".0%", range=[lo, hi], secondary_y=False)
    fig.update_yaxes(showgrid=False, range=[0, max(max(d["n_prom"]), 1) * 4], showticklabels=False, secondary_y=True)
    plotly_layout(fig, alto=420)
    fig.update_layout(hovermode="x unified", bargap=0.45)
    return fig

def fig_ranking_aps(df_ap, obj_isc, resaltar=None):
    """Barras horizontales: ISC por asesor contra la meta (color por cumplimiento)."""
    import plotly.graph_objects as go
    d = df_ap[df_ap["enc"] > 0].sort_values("prom_isc")
    if d.empty: return None
    cols = [C["ok"] if v >= obj_isc else (C["alerta"] if v >= obj_isc - 0.03 else C["mal"]) for v in d["prom_isc"]]
    es_yo = [(n == resaltar) for n in d["aps_nombre"]] if resaltar else [True] * len(d)
    fig = go.Figure(go.Bar(x=d["prom_isc"], y=d["aps_nombre"], orientation="h",
                           marker=dict(color=cols, opacity=[1.0 if y else 0.4 for y in es_yo],
                                       line=dict(color=C["navy"], width=[3 if (resaltar and y) else 0 for y in es_yo])),
                           text=[f"{v:.0%} · n={int(n)}" for v, n in zip(d["prom_isc"], d["enc"])], textposition="outside",
                           customdata=d[["enc", "E", "C"]].values,
                           hovertemplate="<b>%{y}</b><br>ISC %{x:.1%}<br>%{customdata[0]:.0f} respondidas<br>TRE: %{customdata[2]:.0f} de %{customdata[1]:.0f} enviadas<extra></extra>"))
    fig.add_vline(x=obj_isc, line_dash="dash", line_color=C["naranja"], annotation_text=f"Meta {obj_isc:.0%}", annotation_position="top")
    plotly_layout(fig, alto=max(240, 34 * len(d) + 70), leyenda_abajo=False)
    fig.update_xaxes(tickformat=".0%", range=[0, 1.22], showgrid=True, gridcolor=C["linea"])
    fig.update_yaxes(showgrid=False)
    return fig

def render_general():
    st.markdown("<div class='seccion-titulo'>Medición General — TRE & ISC</div>",unsafe_allow_html=True)
    
    col1,col2,col3,col4 = st.columns([2,2,2,1.6])
    with col1: fytd_sel=st.selectbox("Periodo:",todos_fytd,key="g_fytd")
    with col2:
        mm=meses_de(D["isc_mensual"],fytd_sel,CIUDAD)
        mes_sel=st.selectbox("Mes/Año:",["TODOS"]+mm,index=len(mm),key="g_mes")   # por defecto: el último mes
    with col3:
        dlrs=dealers_para_ciudad(CIUDAD)
        dealer_sel=st.selectbox("Dealer:",dlrs,key="g_dlr")
    with col4:
        st.markdown("<div style='margin-top:30px'></div>",unsafe_allow_html=True)
        acum_sel = st.toggle("Acumulado hasta el mes", key="g_acum")
    st.session_state.gen_acum = acum_sel

    if mes_sel == "TODOS": meses_a_procesar = mm
    elif acum_sel:
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

    str_acum = " (ACUM)" if acum_sel and mes_sel != "TODOS" else ""

    # --- Aviso de alertas pendientes (casos graves a atender en 24 h) ---
    _al_pend = alertas_cliente(None, None, CIUDAD, fytd_sel)
    _n_al = int((_al_pend["estado"] == "Pendiente").sum()) if not _al_pend.empty else 0
    if _n_al:
        st.markdown(f"<div class='banner-alerta'>{_n_al} alerta(s) de clientes insatisfechos por atender — "
                    f"revisa la sección <b>ALERTAS 24 H</b>.</div>", unsafe_allow_html=True)

    # --- KPIs con variación contra el periodo anterior ---
    prev = None; ref_txt = ""
    if mes_sel != "TODOS":
        if acum_sel and len(meses_a_procesar) > 1:
            prev = kpis_periodo(meses_a_procesar[:-1], fytd_sel, CIUDAD); ref_txt = f"vs acum. a {meses_a_procesar[-2]}"
        elif not acum_sel and mm.index(mes_sel) > 0:
            _mp = mm[mm.index(mes_sel) - 1]
            prev = kpis_periodo([_mp], fytd_sel, CIUDAD); ref_txt = f"vs {_mp}"
    def _dpp(k):   # variación en puntos porcentuales
        return None if prev is None or prev[k] != prev[k] else (tot["prom_" + k] - prev[k]) * 100
    ks = st.columns(4)
    tarjetas = [
        kpi_card("Encuestas enviadas", f"{tot['E']:,.0f}", f"FYTD {fytd_sel.replace('FYTD ','')}{str_acum}",
                 delta_chip(None if prev is None else tot["E"] - prev["E"], "abs", ref_txt)),
        kpi_card("Respondidas", f"{tot['C']:,.0f}", f"{tot['enc']:,.0f} con ISC",
                 delta_chip(None if prev is None else tot["C"] - prev["C"], "abs", ref_txt)),
        kpi_card("TRE", f"{tot['prom_tre']:.1%}", f"Meta {obj_tre:.0%}", delta_chip(_dpp("tre"), "pp", ref_txt),
                 color=_color_kpi(tot["prom_tre"], obj_tre)),
        kpi_card("ISC", f"{tot['prom_isc']:.1%}", f"Meta {obj_isc:.0%}", delta_chip(_dpp("isc"), "pp", ref_txt),
                 color=_color_kpi(tot["prom_isc"], obj_isc)),
    ]
    for col_k, html_k in zip(ks, tarjetas): col_k.markdown(html_k, unsafe_allow_html=True)
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # --- Tendencia del ISC (antes era una sección aparte) ---
    with st.container(key="card_tendencia"):
        tc1, tc2 = st.columns([3, 1.6])
        tc1.markdown(f"<div class='card-titulo'>Tendencia del ISC — {fytd_sel} · {CIUDAD}"
                     f"{'' if dealer_sel == 'GENERAL' else ' · ' + dealer_sel}</div>", unsafe_allow_html=True)
        modo_t = tc2.radio("Serie", ["Mensual", "Acumulada"], horizontal=True, key="g_trend_modo", label_visibility="collapsed")
        dt = datos_tendencia(fytd_sel, CIUDAD, dealer_sel, modo_t == "Acumulada")
        if dt is None:
            st.info("Sin datos para la tendencia.")
        else:
            lbl_prom = "Promedio general" if dealer_sel == "GENERAL" else f"Promedio {dealer_sel}"
            mostrar_plotly(fig_tendencia_isc(dt, obj_isc, lbl_prom, resaltar=None if mes_sel == "TODOS" else mes_sel), key="pl_tend")
            st.caption("Clic en la leyenda para ocultar o mostrar series. Las barras son las encuestas respondidas del mes."
                       + (" Líneas por asesor." if dealer_sel != "GENERAL" else " Líneas por dealer."))

    cl,cr=st.columns([1,1.4])
    with cl, st.container(key="card_dealer"):
        st.markdown(f"<div class='card-titulo'>Dealer{str_acum}</div>", unsafe_allow_html=True)
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
        
        boton_descarga_tabla(st, "Tabla General", ds, lambda: cached_jpg_gen(ds, 'gral', obj_tre, obj_isc), "Tabla_General", "dl_gd")

    with cr, st.container(key="card_aps"):
        st.markdown(f"<div class='card-titulo'>Detalle por APS — {dealer_sel} · {CIUDAD}{str_acum}</div>", unsafe_allow_html=True)
        
        if not df_ia.empty and not df_ta.empty:
            agg_ti = df_ta.groupby("aps_nombre").agg(E=("E","sum"), C=("C","sum")).reset_index()
            agg_ii = df_ia.groupby("aps_nombre").agg(enc=("total_encuestas","sum"), i16=("I16","sum"), i78=("I78","sum")).reset_index()
            df_ap = agg_ti.merge(agg_ii, on="aps_nombre", how="outer").fillna(0)
            df_ap["prom_tre"] = np.where(df_ap["E"]>0, df_ap["C"]/df_ap["E"], 0)
            df_ap["prom_isc"] = np.where(df_ap["enc"]>0, (df_ap["enc"]-df_ap["i16"]*2-df_ap["i78"])/df_ap["enc"], 0)
            
            _fig_rk = fig_ranking_aps(df_ap, obj_isc)
            if _fig_rk is not None: mostrar_plotly(_fig_rk, key="pl_rank")

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
            
            boton_descarga_tabla(st, "Descargar Tabla APS", ds_ap, lambda: cached_jpg_gen(ds_ap, 'aps', obj_tre, obj_isc), "Tabla_APS", "dl_aps_gen")
            
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

        # H1 y CES: se calculan del RawData (conteos guardados en isc_mensual); si el mes no los tiene, queda el valor manual
        nota_auto = ""
        if attr_name in ("Bien a la primera H1", "Customer Expectations CES") and "h1_n" in D["isc_mensual"].columns:
            _b = aplicar_filtro_ciudad(D["isc_mensual"], CIUDAD)
            _b = _b[(_b["fytd"].astype(str).str.upper() == fytd_str) & (_b["mes_anio"].isin(meses_a_procesar))]
            if attr_name == "Bien a la primera H1":
                _n, _si = _b["h1_n"].sum(), _b["h1_si"].sum()
                if _n > 0:
                    val_actual = float(_si / _n)
                    nota_auto = f"Calculado del RawData (VOC06): {int(_si)} de {int(_n)} respondieron Sí"
            else:
                _col_c = "ces_c5" if CES_NOTA_MINIMA >= 5 else "ces_c4"
                _n, _si = _b["ces_n"].sum(), _b[_col_c].sum()
                if _n > 0:
                    val_actual = float(_si / _n)
                    nota_auto = f"Calculado del RawData (VOC09 ≥ {CES_NOTA_MINIMA}): {int(_si)} de {int(_n)}"

        # "Alertas atendidas en 24 Hrs": se calcula del reporte de alertas; si no hay, queda el valor manual
        if attr_name == "Alertas atendidas en 24 Hrs" and not D["alertas"].empty:
            _al_e = aplicar_filtro_ciudad(D["alertas"], CIUDAD)
            _al_e = _al_e[(_al_e["fytd"].astype(str).str.upper() == fytd_str) & (_al_e["mes_anio"].isin(meses_a_procesar))]
            _al_e = _al_e[_al_e["en_tiempo_24h"].isin(["Sí", "No"])]
            if not _al_e.empty:
                val_actual = float((_al_e["en_tiempo_24h"] == "Sí").mean())
                nota_auto = f"Calculado del reporte de alertas: {int((_al_e['en_tiempo_24h'] == 'Sí').sum())} de {len(_al_e)} atendidas en 24 h"

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
        if nota_auto: cols_redondos[i].caption(nota_auto)

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


# ------------------------------------------------------------------------------
# Vistas de Atributos (Plotly: se dibujan en el navegador)
# ------------------------------------------------------------------------------
def _meta_attr(v):
    return v / 100.0 if pd.notna(v) and v > 2 else v

def vista_brechas(df_resumen, cols_cache, styler_res):
    """Mapa de calor atributo x dealer/asesor coloreado por la distancia a la meta + cambio vs periodo anterior."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    if df_resumen.empty: st.info("Sin datos."); return
    val_cols = [c for c in df_resumen.columns if c not in ("Atributo", "Objetivo", "GAP")]
    main = "GENERAL" if "GENERAL" in val_cols else "%"
    obj = df_resumen["Objetivo"].fillna(0.85)
    brecha_main = df_resumen[main] - obj
    n_bajo = int((brecha_main < 0).sum()); n_tot = int(brecha_main.notna().sum())

    k1, k2, k3 = st.columns(3)
    peor = brecha_main.idxmin() if brecha_main.notna().any() else None
    k1.markdown(kpi_card("Atributos bajo la meta", f"{n_bajo} de {n_tot}", "en la selección actual",
                         color=C["mal"] if n_bajo else C["ok"]), unsafe_allow_html=True)
    k2.markdown(kpi_card("Mayor brecha", f"{brecha_main[peor] * 100:+.1f} pp" if peor is not None else "—",
                         df_resumen.loc[peor, "Atributo"] if peor is not None else "", color=C["mal"]), unsafe_allow_html=True)
    gap = df_resumen["GAP"]
    caida = gap.idxmin() if gap.notna().any() and gap.min() < 0 else None
    k3.markdown(kpi_card("Mayor caída vs periodo anterior", f"{gap[caida] * 100:+.1f} pp" if caida is not None else "—",
                         df_resumen.loc[caida, "Atributo"] if caida is not None else "Sin caídas o sin periodo previo",
                         color=C["alerta"] if caida is not None else C["ok"]), unsafe_allow_html=True)
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    ylabels = [f"{a}  ·  meta {o:.0%}" for a, o in zip(df_resumen["Atributo"], obj)]
    z = df_resumen[val_cols].sub(obj, axis=0)
    txt = df_resumen[val_cols].map(lambda v: "" if pd.isna(v) else f"{v:.0%}")
    hay_gap = gap.notna().any()
    fig = make_subplots(rows=1, cols=2 if hay_gap else 1, shared_yaxes=True, horizontal_spacing=0.02,
                        column_widths=[0.78, 0.22] if hay_gap else [1.0])
    fig.add_trace(go.Heatmap(z=z.values, x=val_cols, y=ylabels, text=txt.values, texttemplate="%{text}", showscale=False,
                             colorscale=[[0, "#D32F2F"], [0.5, "#FFFFFF"], [1, "#388E3C"]], zmin=-0.15, zmax=0.15, xgap=2, ygap=2,
                             hovertemplate="<b>%{y}</b><br>%{x}: %{text}<br>Distancia a la meta: %{z:+.1%}<extra></extra>"), row=1, col=1)
    if hay_gap:
        cols_g = [C["ok"] if (v == v and v >= 0) else C["mal"] for v in gap]
        fig.add_trace(go.Bar(x=(gap * 100), y=ylabels, orientation="h", marker_color=cols_g,
                             text=[("" if v != v else f"{v * 100:+.1f}") for v in gap], textposition="outside",
                             hovertemplate="<b>%{y}</b><br>%{x:+.1f} pp<extra></extra>"), row=1, col=2)
        fig.update_xaxes(zeroline=True, zerolinecolor=C["gris"], showgrid=False, showticklabels=False, row=1, col=2)
    plotly_layout(fig, alto=max(380, 30 * len(df_resumen) + 120), leyenda_abajo=False)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(side="top", row=1, col=1)
    fig.update_layout(showlegend=False, margin=dict(l=10, r=30, t=40, b=10))
    mostrar_plotly(fig, key="pl_brechas")
    st.caption("Izquierda: desempeño por columna, coloreado por su distancia a la meta (verde = sobre la meta, rojo = bajo la meta, intensidad = distancia). "
               "Derecha: cambio contra el periodo anterior en puntos porcentuales. Atributos ordenados del peor al mejor desempeño general.")

    with st.expander("Tabla detallada y descarga"):
        st.markdown(tabla_html(styler_res), unsafe_allow_html=True)
        boton_descarga_tabla(st, "📥 Descargar Tabla", df_resumen, lambda: cached_jpg_attr_table(df_resumen, cols_cache), "Atributos", "dl_attr")

def vista_evolucion(fytd_sel, dealer_sel, aps_sel, score_col, obj_col):
    """Evolución mensual de un atributo (total + dealers o asesores) contra su meta."""
    import plotly.graph_objects as go
    df_h_base = aplicar_filtro_ciudad(D["atributos"], CIUDAD)
    df_h_base = df_h_base[df_h_base["fytd"] == fytd_sel]
    df_h_aps = aplicar_filtro_ciudad(D["atrib_aps"], CIUDAD)
    if not df_h_aps.empty: df_h_aps = df_h_aps[df_h_aps["fytd"] == fytd_sel]
    lista = sorted([a for a in df_h_base["atributo"].unique() if "bien a la primera h1" not in a.lower()])
    if not lista: st.info("Sin atributos."); return
    atr = st.selectbox("Atributo:", lista, key="h_atr_sel")
    meses_h = meses_de(D["atributos"], fytd_sel, CIUDAD)
    df_b = df_h_base[df_h_base["atributo"] == atr]
    df_a = df_h_aps[df_h_aps["atributo"] == atr] if not df_h_aps.empty else pd.DataFrame()

    def tendencia(df_sub):
        vals = []
        for m in meses_h:
            g = df_sub[df_sub["mes_anio"] == m] if not df_sub.empty else df_sub
            if g.empty: vals.append(np.nan)
            elif "n_respuestas" in g.columns and g["n_respuestas"].sum() > 0:
                vals.append((g[score_col] * g["n_respuestas"]).sum() / g["n_respuestas"].sum())
            else: vals.append(g[score_col].mean())
        return vals

    series = {}; grueso = None
    if dealer_sel == "GENERAL":
        grueso = "TOTAL GENERAL"; series[grueso] = tendencia(df_b)
        for d in sorted(df_b["dealer"].unique()):
            if d != "SIN DEALER": series[d] = tendencia(df_b[df_b["dealer"] == d])
    elif aps_sel == "TODOS":
        grueso = f"TOTAL {dealer_sel}"; series[grueso] = tendencia(df_b[df_b["dealer"] == dealer_sel])
        if not df_a.empty:
            for a in sorted(df_a[df_a["dealer"] == dealer_sel]["aps_nombre"].unique()):
                if "SIN ASESOR" not in str(a).upper():
                    series[a] = tendencia(df_a[(df_a["dealer"] == dealer_sel) & (df_a["aps_nombre"] == a)])
    else:
        series[aps_sel] = tendencia(df_a[(df_a["dealer"] == dealer_sel) & (df_a["aps_nombre"] == aps_sel)] if not df_a.empty else df_a)
    obj_raw = df_b[obj_col].iloc[0] if not df_b.empty else 0.85
    obj_h = _meta_attr(obj_raw)

    fig = go.Figure()
    for k, (nombre, vals) in enumerate(series.items()):
        es_total = nombre == grueso
        fig.add_trace(go.Scatter(x=meses_h, y=vals, mode="lines+markers+text" if es_total else "lines+markers", name=nombre,
                                 line=dict(color=C["navy"] if es_total else COLORWAY[k % len(COLORWAY)], width=4 if es_total else 2),
                                 text=[("" if v != v else f"{v:.0%}") for v in vals] if es_total else None, textposition="top center",
                                 hovertemplate=nombre + ": %{y:.1%}<extra></extra>"))
    fig.add_hline(y=obj_h, line_dash="dash", line_color=C["naranja"], annotation_text=f"Meta {obj_h:.0%}", annotation_position="top left")
    todos = [v for vs in series.values() for v in vs if v == v] + [obj_h]
    plotly_layout(fig, alto=430)
    fig.update_layout(hovermode="x unified")
    fig.update_yaxes(tickformat=".0%", range=[max(0, min(todos) - 0.06), min(1.03, max(todos) + 0.06)])
    mostrar_plotly(fig, key="pl_evol")
    tabla = pd.DataFrame(series, index=meses_h).T
    st.dataframe(tabla.style.format("{:.1%}", na_rep="—"), width="stretch")

def vista_palancas(df_resumen, meses_eval, dealer_sel, aps_sel):
    """Qué atributos se relacionan más con la satisfacción general y cuáles están bajo la meta (prioridades)."""
    import plotly.graph_objects as go
    dr = D["drivers"]
    if dr.empty:
        st.info("Aún no hay datos de palancas. Reprocesa los meses en el preprocesador actualizado (genera el archivo 14)."); return
    dr = aplicar_filtro_ciudad(dr, CIUDAD)
    dr = _sin_dealer(dr[dr["mes_anio"].isin(meses_eval)])
    if dealer_sel != "GENERAL": dr = dr[dr["dealer"] == dealer_sel]
    if dr.empty: st.info("Sin datos de palancas para la selección."); return
    g = dr.groupby("atributo")[["n", "sx", "sy", "sxx", "syy", "sxy"]].sum()
    den = np.sqrt((g["n"] * g["sxx"] - g["sx"] ** 2) * (g["n"] * g["syy"] - g["sy"] ** 2))
    g["r"] = (g["n"] * g["sxy"] - g["sx"] * g["sy"]) / den.where(den > 0)
    main = "GENERAL" if "GENERAL" in df_resumen.columns else "%"
    base = df_resumen[["Atributo", main, "Objetivo"]].rename(columns={main: "desempeno", "Objetivo": "meta"}).copy()
    base["meta"] = base["meta"].fillna(0.85)
    d = base.merge(g[["n", "r"]], left_on="Atributo", right_index=True, how="inner")
    d = d[(d["n"] >= 15) & d["r"].notna() & d["desempeno"].notna()].copy()
    if d.empty: st.info("Muestra insuficiente (menos de 15 respuestas por atributo). Prueba con el modo acumulado."); return
    d["brecha"] = (d["desempeno"] - d["meta"]) * 100
    umbral = float(d["r"].mean())
    d["prioridad"] = np.where((d["brecha"] < 0) & (d["r"] >= umbral), "Prioridad alta",
                      np.where(d["brecha"] < 0, "Bajo la meta", np.where(d["r"] >= umbral, "Mantener (importante)", "Sobre la meta")))
    colores = {"Prioridad alta": C["mal"], "Bajo la meta": C["alerta"], "Mantener (importante)": C["ok"], "Sobre la meta": "#9E9E9E"}

    if aps_sel != "TODOS": st.caption("Las palancas se calculan a nivel dealer (no por asesor individual).")
    n_resp = int(g["n"].max())
    if n_resp < 50: st.warning(f"Muestra pequeña ({n_resp} respuestas): las relaciones son orientativas. Usa el modo acumulado para más precisión.")

    fig = go.Figure()
    for nombre, grp in d.groupby("prioridad"):
        fig.add_trace(go.Scatter(x=grp["brecha"], y=grp["r"], mode="markers+text", name=nombre, text=grp["Atributo"],
                                 textposition="top center", textfont=dict(size=10), marker=dict(size=13, color=colores[nombre], line=dict(color="white", width=1)),
                                 customdata=np.c_[grp["desempeno"] * 100, grp["meta"] * 100, grp["n"]],
                                 hovertemplate="<b>%{text}</b><br>Desempeño %{customdata[0]:.1f}% (meta %{customdata[1]:.0f}%)<br>"
                                               "Relación con la satisfacción: %{y:.2f}<br>%{customdata[2]:.0f} respuestas<extra></extra>"))
    fig.add_vline(x=0, line_color=C["gris"], line_width=1)
    fig.add_hline(y=umbral, line_dash="dot", line_color=C["gris"])
    x0 = min(d["brecha"].min() - 3, -5); x1 = max(d["brecha"].max() + 3, 5)
    fig.add_shape(type="rect", x0=x0, x1=0, y0=umbral, y1=d["r"].max() + 0.05, fillcolor=C["mal"], opacity=0.06, line_width=0, layer="below")
    plotly_layout(fig, alto=520)
    fig.update_xaxes(title="Distancia a la meta (puntos porcentuales)  ←  bajo la meta  |  sobre la meta  →", range=[x0, x1], showgrid=True, gridcolor=C["linea"])
    fig.update_yaxes(title="Relación con la satisfacción general (correlación)", range=[max(0, d["r"].min() - 0.08), d["r"].max() + 0.08])
    mostrar_plotly(fig, key="pl_palancas")
    st.caption("Zona roja = atributos importantes (alta relación con la nota de satisfacción) y bajo la meta: es donde más conviene actuar. "
               "La correlación muestra asociación, no prueba causalidad.")

    top = d[d["prioridad"] == "Prioridad alta"].assign(indice=lambda x: -x["brecha"] * x["r"]).sort_values("indice", ascending=False).head(5)
    st.markdown("<div class='card-titulo'>Prioridades sugeridas</div>", unsafe_allow_html=True)
    if top.empty: st.success("Ningún atributo importante está bajo la meta en esta selección.")
    else:
        for _, r_ in top.iterrows():
            st.markdown(f"<div style='background:rgba(211,47,47,0.08);border-left:5px solid #D32F2F;padding:8px 12px;margin-bottom:8px;border-radius:4px'>"
                        f"<b>{r_['Atributo']}</b> — desempeño {r_['desempeno']:.0%} (meta {r_['meta']:.0%}, {r_['brecha']:+.1f} pp) · relación con la satisfacción {r_['r']:.2f}</div>",
                        unsafe_allow_html=True)

def vista_radar(df_act, meses_eval, dealer_sel, aps_sel, score_col):
    """Radar interactivo: dealers (o asesores) contra el promedio y la meta de cada atributo."""
    import plotly.graph_objects as go
    sub = df_act[df_act["mes_anio"].isin(meses_eval)].copy()
    if sub.empty: st.info("Sin datos."); return
    col_ent = "dealer" if (dealer_sel == "GENERAL" and aps_sel == "TODOS") else "aps_nombre"
    if col_ent not in sub.columns: st.info("Sin datos para comparar."); return
    sub = sub[~sub["atributo"].str.lower().str.contains("bien a la primera h1")]
    w = sub[score_col] * sub["n_respuestas"]
    sub = sub.assign(_w=w)
    def ponderar(g): return g["_w"].sum() / g["n_respuestas"].sum() if g["n_respuestas"].sum() > 0 else np.nan
    prom = sub.groupby("atributo").apply(ponderar)
    atributos = list(prom.sort_values().index)          # del peor al mejor: lectura más clara
    if len(atributos) < 3: st.info("Se necesitan al menos 3 atributos para el radar."); return
    metas = sub.groupby("atributo")["obj_atributo"].mean().map(_meta_attr)
    etiquetas = [textwrap.fill(a, 18).replace("\n", "<br>") for a in atributos]
    cierre = lambda vals: list(vals) + [vals[0]]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=cierre([metas.get(a, np.nan) for a in atributos]), theta=cierre(etiquetas), name="Meta",
                                  line=dict(color=C["naranja"], dash="dash", width=2), hovertemplate="Meta %{r:.0%}<extra></extra>"))
    fig.add_trace(go.Scatterpolar(r=cierre([prom.get(a, np.nan) for a in atributos]), theta=cierre(etiquetas), name="Promedio",
                                  line=dict(color=C["navy"], width=4), fill="toself", fillcolor="rgba(26,26,46,0.08)",
                                  hovertemplate="Promedio %{r:.1%}<extra></extra>"))
    if aps_sel == "TODOS" or col_ent == "dealer":
        entidades = sorted(e for e in sub[col_ent].unique() if str(e).upper() not in ("SIN DEALER", "SIN ASESOR"))
        for k, ent in enumerate(entidades):
            sc = sub[sub[col_ent] == ent].groupby("atributo").apply(ponderar)
            fig.add_trace(go.Scatterpolar(r=cierre([sc.get(a, np.nan) for a in atributos]), theta=cierre(etiquetas), name=str(ent),
                                          line=dict(color=COLORWAY[k % len(COLORWAY)], width=2), marker=dict(size=5),
                                          hovertemplate=str(ent) + " %{r:.1%}<extra></extra>"))
    piso = max(0.0, np.floor((np.nanmin(prom.values) - 0.1) * 10) / 10)
    plotly_layout(fig, alto=680)
    fig.update_layout(polar=dict(radialaxis=dict(range=[piso, 1.0], tickformat=".0%", gridcolor=C["linea"], angle=90),
                                 angularaxis=dict(tickfont=dict(size=10), direction="clockwise")),
                      legend=dict(orientation="h", y=-0.08))
    mostrar_plotly(fig, key="pl_radar")
    st.caption("Los atributos van del más débil al más fuerte (promedio). Clic en la leyenda para ocultar o mostrar series.")

def render_atributos():
    st.markdown("<div class='seccion-titulo'>Atributos — brechas y palancas</div>",unsafe_allow_html=True)

    c1,c2,c3,c4,c5 = st.columns([1.5, 1.5, 1.5, 1.5, 1.3])
    with c1: fytd_sel=st.selectbox("Periodo:",todos_fytd,key="a_fytd")
    with c2:
        meses_a=meses_de(D["atributos"],fytd_sel,CIUDAD)
        mes_sel=st.selectbox("Mes/Año:", ["TODOS"] + meses_a if meses_a else ["TODOS"], index=len(meses_a) if meses_a else 0, key="a_mes")
    with c3: dealer_sel=st.selectbox("Dealer:",dealers_para_ciudad(CIUDAD),key="a_dlr")
    with c4:
        aps_opts=aps_para_filtro(CIUDAD,dealer_sel,fytd_sel)
        aps_sel=st.selectbox("APS:",aps_opts,key="a_aps")
    with c5:
        st.markdown("<div style='margin-top:30px'></div>",unsafe_allow_html=True)
        acum_sel = st.toggle("Acumulado hasta el mes", key="a_acum")
    st.session_state.atr_acum = acum_sel

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

    elif acum_sel:

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



    vistas = ["Mapa de brechas", "Evolución", "Palancas", "Radar"]
    vista = st.segmented_control("Vista", vistas, default=vistas[0], key="a_vista", label_visibility="collapsed") or vistas[0]
    if vista == vistas[0]:   vista_brechas(df_resumen, cols_cache, styler_res)
    elif vista == vistas[1]: vista_evolucion(fytd_sel, dealer_sel, aps_sel, score_col, obj_col)
    elif vista == vistas[2]: vista_palancas(df_resumen, meses_act_eval, dealer_sel, aps_sel)
    else:                    vista_radar(df_act, meses_act_eval, dealer_sel, aps_sel, score_col)

# ==============================================================================
# SECCIÓN 5: PENDIENTES
# ==============================================================================

@st.cache_data(show_spinner=False, max_entries=10)
def cached_jpg_export(df, table_type):
    if table_type == 'pendientes':
        styler = df.style.apply(lambda row: ["color:#D32F2F;font-weight:bold" if c == "Estado" and row["Estado"] == "Expirado" else ("color:#FF8C00;font-weight:bold" if c == "Estado" and row["Estado"] == "Contacto en uso" else "") for c in row.index], axis=1).set_table_attributes('class="tabla-voc"').hide(axis="index")
    elif table_type == 'alertas':
        styler = df.style.apply(lambda row: ["color:#D32F2F;font-weight:bold" if c == "Estado" and str(row["Estado"]).startswith("Pendiente") else ("color:#757575;font-weight:bold" if c == "Estado" else "") for c in row.index], axis=1).set_table_attributes('class="tabla-voc"').hide(axis="index")
    elif table_type == 'resumen':
        styler = df.style.apply(lambda row: ["background-color:#ffcccc;color:black;font-weight:bold" if c == "Vencidas" and row["Vencidas"] > 0 else ("background-color:#E8E8E8;font-weight:bold" if row["Asesor"] == "TOTAL GENERAL" else "") for c in row.index], axis=1).format({"Total Enviadas":"{:.0f}", "Completadas":"{:.0f}", "Vencidas":"{:.0f}", "Pendientes":"{:.0f}"}).set_table_attributes('class="tabla-voc"').hide(axis="index")
    return styler_to_jpg_buf(styler).getvalue()

def tabla_alertas_detalle(df):
    """Tabla legible de alertas (para la sección Alertas y Vista APS)."""
    t = pd.DataFrame({
        "Asesor": df["aps_nombre"].values,
        "Cliente": df["cliente_nombre"].values,
        "Vehículo": df["vehiculo"].values,
        "VIN": df["vin"].values,
        "Calif.": df["satisfaccion"].map(lambda v: "—" if pd.isna(v) else f"{int(v)}").values,
        "Motivo": df["verbalizacion"].fillna("—").astype(str).values,
        "Estado": [_txt_estado_alerta(e_, d_) for e_, d_ in zip(df["estado"], df["dias_para_expirar"])],
        "¿Atendida en 24 h?": df["en_tiempo_24h"].replace({"": "—"}).fillna("—").values,
    })
    return t

def _estilo_alertas(t):
    return t.style.apply(lambda row: ["color:#D32F2F;font-weight:bold" if c == "Estado" and str(row["Estado"]).startswith("Pendiente")
                                      else ("color:#757575;font-weight:bold" if c == "Estado" and row["Estado"] == "Vencida"
                                            else ("color:#388E3C;font-weight:bold" if c == "Estado" else "")) for c in row.index],
                         axis=1).set_table_attributes('class="tabla-voc"').hide(axis="index")

# ==============================================================================
# EMBUDO DE ENVÍO (del link enviado a la encuesta respondida)
# ==============================================================================
def tarjetas_alertas(pend, max_n=6):
    """Tarjetas con cuenta regresiva de las alertas pendientes (casos graves a atender en 24 h)."""
    if pend.empty: return
    cols = st.columns(min(3, len(pend)))
    for i_, (_, r_) in enumerate(pend.head(max_n).iterrows()):
        d_ = r_["dias_para_expirar"]
        urg = "#C62828" if (pd.notna(d_) and d_ <= 1) else "#F57F17"
        txt_d = "sin fecha" if pd.isna(d_) else (f"vence en {int(d_)} día{'s' if int(d_) != 1 else ''}")
        mot = "" if pd.isna(r_["verbalizacion"]) else str(r_["verbalizacion"])
        mot = mot if len(mot) <= 140 else mot[:137] + "..."
        cols[i_ % len(cols)].markdown(
            f"<div style='background:#fff;border-top:5px solid {urg};border-radius:10px;padding:12px 14px;margin-bottom:10px;box-shadow:0 2px 10px rgba(26,26,46,.08)'>"
            f"<div style='font-weight:700;color:{urg};font-size:13px'>{txt_d.upper()} · NOTA {'—' if pd.isna(r_['satisfaccion']) else int(r_['satisfaccion'])}</div>"
            f"<div style='font-size:16px;font-weight:700;color:#1A1A2E;margin-top:2px'>{_html.escape(str(r_['cliente_nombre']))}</div>"
            f"<div style='font-size:12px;color:#777'>{_html.escape(str(r_['vehiculo']))} · {_html.escape(str(r_['vin']))}<br>Asesor: {_html.escape(str(r_['aps_nombre']))}</div>"
            f"<div style='font-size:13px;color:#444;margin-top:6px'>{_html.escape(mot)}</div></div>", unsafe_allow_html=True)
    if len(pend) > max_n: st.caption(f"Y {len(pend) - max_n} más en el detalle.")

_DIAS_SEM = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

def render_embudo():
    import plotly.graph_objects as go
    st.markdown("<div class='seccion-titulo'>Embudo de envío — del link a la respuesta</div>", unsafe_allow_html=True)
    if D["embudo"].empty:
        st.info("Aún no hay datos del embudo. Reprocesa los meses en el preprocesador actualizado (genera el archivo 16)."); return
    df_all = aplicar_filtro_ciudad(D["embudo"], CIUDAD)
    if df_all.empty: st.info("Sin datos para la ciudad seleccionada."); return

    c1, c2, c3, c4, c5 = st.columns([1.4, 1.4, 1.6, 1.6, 1.3])
    with c1: fytd_e = st.selectbox("Periodo:", sorted(df_all["fytd"].unique(), reverse=True), key="em_fytd")
    mm = meses_de(df_all, fytd_e)
    with c2: mes_e = st.selectbox("Mes/Año:", ["TODOS"] + mm, index=len(mm), key="em_mes")
    with c3: dealer_e = st.selectbox("Dealer:", ["GENERAL"] + sorted(x for x in df_all["dealer"].unique() if x != "SIN DEALER"), key="em_dlr")
    base_a = df_all if dealer_e == "GENERAL" else df_all[df_all["dealer"] == dealer_e]
    with c4: aps_e = st.selectbox("Asesor:", ["TODOS"] + sorted(x for x in base_a["aps_nombre"].unique() if "SIN ASESOR" not in str(x).upper()), key="em_aps")
    with c5:
        st.markdown("<div style='margin-top:30px'></div>", unsafe_allow_html=True)
        acum_e = st.toggle("Acumulado hasta el mes", key="em_acum")
    if mes_e == "TODOS": meses_e = mm
    elif acum_e: meses_e = mm[:mm.index(mes_e) + 1]
    else: meses_e = [mes_e]

    df = df_all[(df_all["fytd"] == fytd_e) & (df_all["mes_anio"].isin(meses_e))]
    if dealer_e != "GENERAL": df = df[df["dealer"] == dealer_e]
    if aps_e != "TODOS": df = df[df["aps_nombre"] == aps_e]
    if df.empty or df["enviadas"].sum() == 0: st.info("Sin envíos para la selección."); return
    S = df.sum(numeric_only=True)
    div = lambda a, b: (a / b) if b else np.nan

    # --- Embudo ---
    pasos = [("Enviadas", S["enviadas"]), ("Entregadas", S["entregadas"]), ("Abiertas", S["abiertas"]),
             ("Interactuaron con el link", S["interactuaron"]), ("Respondieron", S["realizadas"])]
    obj_tre = get_obj(D["objetivos"], fytd_e, "obj_tre")
    tre = div(S["realizadas"], S["enviadas"])
    k = st.columns(4)
    k[0].markdown(kpi_card("TRE (respondieron / enviadas)", f"{tre:.1%}", f"Meta {obj_tre:.0%} · {S['realizadas']:.0f} de {S['enviadas']:.0f}",
                           color=_color_kpi(tre, obj_tre)), unsafe_allow_html=True)
    k[1].markdown(kpi_card("Apertura", f"{div(S['abiertas'], S['entregadas']):.0%}", "abrieron el mensaje / entregadas"), unsafe_allow_html=True)
    k[2].markdown(kpi_card("Conversión del link", f"{div(S['realizadas'], S['interactuaron']):.0%}", "respondieron / entraron al link"), unsafe_allow_html=True)
    t_med = div(S["t_sum"], S["t_n"])
    k[3].markdown(kpi_card("Tiempo medio de respuesta", "—" if t_med != t_med else f"{t_med:.1f} días", f"{S['t_n']:.0f} respuestas medidas"), unsafe_allow_html=True)
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # mayor pérdida relativa entre pasos consecutivos
    perd = [(pasos[i][0], pasos[i + 1][0], div(pasos[i + 1][1], pasos[i][1])) for i in range(len(pasos) - 1) if pasos[i][1] > 0]
    if perd:
        a_, b_, r_ = min(perd, key=lambda x: x[2])
        st.markdown(f"<div class='banner-alerta' style='background:#FFF8E1;border-left-color:#F57F17;color:#5D4037'>Mayor pérdida del embudo: de "
                    f"<b>{a_}</b> a <b>{b_}</b> solo pasa el <b>{r_:.0%}</b>.</div>", unsafe_allow_html=True)

    col_f, col_d = st.columns([1.3, 1])
    with col_f, st.container(key="card_emb_funnel"):
        st.markdown("<div class='card-titulo'>Embudo</div>", unsafe_allow_html=True)
        fig = go.Figure(go.Funnel(y=[n for n, _ in pasos], x=[v for _, v in pasos], textposition="inside", textinfo="value+percent initial",
                                  marker=dict(color=[C["navy"], "#303052", "#4A4A78", "#388E3C", "#2E7D32"]),
                                  hovertemplate="<b>%{y}</b><br>%{x:.0f} (%{percentInitial:.0%} de las enviadas)<extra></extra>"))
        plotly_layout(fig, alto=360, leyenda_abajo=False)
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10))
        mostrar_plotly(fig, key="pl_emb_funnel")
    with col_d, st.container(key="card_emb_canal"):
        st.markdown("<div class='card-titulo'>Cómo y cuándo responden</div>", unsafe_allow_html=True)
        tot_r = S["realizadas"]
        fig2 = go.Figure()
        buckets = [("Mismo día", S["t_0"]), ("1 día", S["t_1"]), ("2-3 días", S["t_2_3"]), ("4+ días", S["t_4mas"])]
        fig2.add_trace(go.Bar(x=[b[0] for b in buckets], y=[b[1] for b in buckets], marker_color=C["navy"],
                              text=[f"{div(b[1], S['t_n']):.0%}" if S["t_n"] else "" for b in buckets], textposition="outside",
                              hovertemplate="%{x}: %{y:.0f} respuestas<extra></extra>"))
        plotly_layout(fig2, alto=230, leyenda_abajo=False)
        fig2.update_layout(margin=dict(l=10, r=10, t=10, b=10)); fig2.update_yaxes(visible=False, range=[0, max(b[1] for b in buckets) * 1.3 + 1])
        mostrar_plotly(fig2, key="pl_emb_tiempo")
        st.caption("Tiempo entre el envío del link y la respuesta.")
        m1, m2, m3 = st.columns(3)
        m1.metric("Por WhatsApp", f"{div(S['resp_whatsapp'], tot_r):.0%}" if tot_r else "—")
        m2.metric("Por email", f"{div(S['resp_email'], tot_r):.0%}" if tot_r else "—")
        m3.metric("Opcionales completas", f"{div(S['opcionales'], tot_r):.0%}" if tot_r else "—")

    # --- Día de envío ---
    dias = [(_DIAS_SEM[i], S[f"env_d{i}"], S[f"real_d{i}"]) for i in range(7) if S[f"env_d{i}"] > 0]
    with st.container(key="card_emb_dia"):
        st.markdown("<div class='card-titulo'>Tasa de respuesta según el día del envío</div>", unsafe_allow_html=True)
        figd = go.Figure(go.Bar(x=[d[0] for d in dias], y=[div(d[2], d[1]) for d in dias], marker_color=C["amarillo"], marker_line_color=C["navy"], marker_line_width=1.5,
                                text=[f"{div(d[2], d[1]):.0%}<br>n={d[1]:.0f}" for d in dias], textposition="outside", customdata=[[d[1], d[2]] for d in dias],
                                hovertemplate="<b>%{x}</b><br>%{customdata[1]:.0f} respuestas de %{customdata[0]:.0f} enviadas<extra></extra>"))
        figd.add_hline(y=tre, line_dash="dash", line_color=C["naranja"], annotation_text=f"Promedio {tre:.0%}", annotation_position="top left")
        plotly_layout(figd, alto=300, leyenda_abajo=False)
        figd.update_yaxes(tickformat=".0%", range=[0, max(div(d[2], d[1]) for d in dias) * 1.35 + 0.02])
        mostrar_plotly(figd, key="pl_emb_dia")
        mejor = max(dias, key=lambda d: div(d[2], d[1]))
        st.caption(f"Mejor día de envío en esta selección: **{mejor[0]}** ({div(mejor[2], mejor[1]):.0%}, {mejor[1]:.0f} envíos). Con pocos envíos por día la lectura es orientativa.")

    # --- Por asesor ---
    with st.container(key="card_emb_aps"):
        st.markdown("<div class='card-titulo'>Embudo por asesor</div>", unsafe_allow_html=True)
        g = df.groupby("aps_nombre")[["enviadas", "entregadas", "abiertas", "interactuaron", "realizadas"]].sum()
        g = g[g.index.astype(str).str.upper() != "SIN ASESOR"]
        t = pd.DataFrame({"Enviadas": g["enviadas"], "Abiertas": g["abiertas"] / g["entregadas"].where(g["entregadas"] > 0),
                          "Interactuaron": g["interactuaron"] / g["enviadas"], "Respondieron": g["realizadas"],
                          "TRE": g["realizadas"] / g["enviadas"]}).sort_values("TRE")
        t.index.name = "Asesor"
        st.dataframe(t.style.format({"Enviadas": "{:.0f}", "Abiertas": "{:.0%}", "Interactuaron": "{:.0%}", "Respondieron": "{:.0f}", "TRE": "{:.1%}"}, na_rep="—")
                      .background_gradient(subset=["TRE"], cmap="RdYlGn", vmin=0.2, vmax=0.8), width="stretch")
        st.caption("Abiertas = % de los mensajes entregados que se abrieron; Interactuaron = % de las enviadas cuyo link fue abierto.")

def render_alertas():
    st.markdown("<div class='seccion-titulo'>Alertas de Clientes — atención en 24 h</div>", unsafe_allow_html=True)
    if D["alertas"].empty:
        st.info("Sin datos de alertas. Carga el reporte AcompanamientoDeAlertas en el preprocesador."); return
    df_a = aplicar_filtro_ciudad(D["alertas"], CIUDAD)
    if df_a.empty:
        st.info("No hay alertas para la ciudad seleccionada."); return

    c1, c2, c3, c4 = st.columns(4)
    with c1: fytd_a = st.selectbox("Periodo:", sorted(df_a["fytd"].unique(), reverse=True), key="al_fytd")
    with c2: mes_a = st.selectbox("Mes:", ["TODOS"] + sorted(df_a[df_a["fytd"] == fytd_a]["mes_anio"].unique()), key="al_mes")
    with c3: dealer_a = st.selectbox("Dealer:", ["LISTADO GENERAL"] + sorted(df_a["dealer"].unique()), key="al_dlr")
    df_f = df_a[df_a["fytd"] == fytd_a].copy()
    if mes_a != "TODOS": df_f = df_f[df_f["mes_anio"] == mes_a]
    if dealer_a != "LISTADO GENERAL": df_f = df_f[df_f["dealer"] == dealer_a]
    with c4:
        aps_a = st.selectbox("Asesor (APS):", ["TODOS"] + sorted([str(x) for x in df_f["aps_nombre"].unique()]), key="al_aps")
    if aps_a != "TODOS": df_f = df_f[df_f["aps_nombre"] == aps_a]

    tot = len(df_f)
    n_pen = int((df_f["estado"] == "Pendiente").sum())
    n_ven = int((df_f["estado"] == "Vencida").sum())
    n_ges = int((df_f["estado"] == "Gestionada").sum())
    n_24 = int((df_f["en_tiempo_24h"] == "Sí").sum())

    k1, k2, k3, k4 = st.columns(4)
    with k1: st.markdown(kpi_html("Total alertas", tot, "{:.0f}", "Clientes insatisfechos"), unsafe_allow_html=True)
    with k2: st.markdown(kpi_html("Por atender", n_pen, "{:.0f}", "Acción hoy", color="#D32F2F" if n_pen else "#388E3C"), unsafe_allow_html=True)
    with k3: st.markdown(kpi_html("Vencidas", n_ven, "{:.0f}", "Plazo cumplido sin gestión", color="#757575"), unsafe_allow_html=True)
    with k4: st.markdown(kpi_html("Atendidas en 24 h", n_24 / tot if tot else 0, "{:.0%}", f"{n_24} de {tot}", color="#388E3C" if tot and n_24 == tot else "#F57F17"), unsafe_allow_html=True)

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    # --- Por atender ahora: tarjetas con cuenta regresiva ---
    _pend = df_f[df_f["estado"] == "Pendiente"].sort_values("dias_para_expirar")
    if not _pend.empty:
        st.markdown("<div class='card-titulo' style='color:#C62828'>Por atender ahora</div>", unsafe_allow_html=True)
        tarjetas_alertas(_pend)

    # --- Evolución mensual: alertas y % atendidas en 24 h contra la meta ---
    _hist = aplicar_filtro_ciudad(D["alertas"], CIUDAD)
    _hist = _hist[_hist["fytd"] == fytd_a]
    if dealer_a != "LISTADO GENERAL": _hist = _hist[_hist["dealer"] == dealer_a]
    if aps_a != "TODOS": _hist = _hist[_hist["aps_nombre"] == aps_a]
    if not _hist.empty:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        # el archivo de alertas no trae orden_mes: se deriva de "Mes AAAA"
        _hist = _hist.assign(orden_mes=_hist["mes_anio"].map(lambda m: int(str(m).split()[1]) * 100 + _MESES_NUM.get(str(m).split()[0], 0)))
        _g = _hist.groupby(["orden_mes", "mes_anio"]).apply(lambda x: pd.Series({
            "Pendiente": (x["estado"] == "Pendiente").sum(), "Vencida": (x["estado"] == "Vencida").sum(), "Gestionada": (x["estado"] == "Gestionada").sum(),
            "pct24": (x["en_tiempo_24h"] == "Sí").sum() / max(1, x["en_tiempo_24h"].isin(["Sí", "No"]).sum())})).reset_index().sort_values("orden_mes")
        _meta24 = get_obj(D["objetivos"], fytd_a, "Alertas atendidas en 24 Hrs")
        _meta24 = _meta24 / 100.0 if _meta24 > 2 else _meta24
        with st.container(key="card_al_evol"):
            st.markdown("<div class='card-titulo'>Evolución de alertas y cumplimiento en 24 h</div>", unsafe_allow_html=True)
            _f = make_subplots(specs=[[{"secondary_y": True}]])
            for est_, col_ in (("Gestionada", C["ok"]), ("Vencida", C["gris"]), ("Pendiente", C["mal"])):
                _f.add_trace(go.Bar(x=_g["mes_anio"], y=_g[est_], name=est_, marker_color=col_,
                                    hovertemplate=est_ + ": %{y:.0f}<extra></extra>"), secondary_y=False)
            _f.add_trace(go.Scatter(x=_g["mes_anio"], y=_g["pct24"], name="% atendidas en 24 h", mode="lines+markers+text",
                                    line=dict(color=C["navy"], width=3), marker=dict(size=9, color=C["amarillo"], line=dict(color=C["navy"], width=2)),
                                    text=[f"{v:.0%}" for v in _g["pct24"]], textposition="top center",
                                    hovertemplate="%{y:.0%} atendidas en 24 h<extra></extra>"), secondary_y=True)
            if _meta24 > 0:
                _f.add_hline(y=_meta24, line_dash="dash", line_color=C["naranja"], secondary_y=True,
                             annotation_text=f"Meta {_meta24:.0%}", annotation_position="top left")
            plotly_layout(_f, alto=320)
            _f.update_layout(barmode="stack")
            _f.update_yaxes(title="Alertas", secondary_y=False, rangemode="tozero")
            _f.update_yaxes(tickformat=".0%", range=[0, 1.1], showgrid=False, secondary_y=True)
            mostrar_plotly(_f, key="pl_al_evol")

    filtro = st.radio("Mostrar:", ["Todas", "Pendiente", "Vencida", "Gestionada"], horizontal=True, key="al_estado")
    df_m = df_f if filtro == "Todas" else df_f[df_f["estado"] == filtro]
    df_m = df_m.assign(_o=df_m["estado"].map(_ORDEN_ALERTA).fillna(3)).sort_values(["_o", "dias_para_expirar", "aps_nombre"]).drop(columns="_o")

    st.markdown("<div class='chart-box'>", unsafe_allow_html=True)
    st.markdown(f"**DETALLE DE ALERTAS — {dealer_a} · {CIUDAD}**")
    st.caption("Busca al cliente en el sistema por nombre o VIN para contactarlo.")
    if df_m.empty:
        st.success("Sin alertas para este filtro.")
    else:
        t = tabla_alertas_detalle(df_m)
        st.markdown(tabla_html(_estilo_alertas(t)), unsafe_allow_html=True)
        boton_descarga_tabla(st, "Descargar Alertas", t, lambda: cached_jpg_export(t, 'alertas'), "Alertas", "dl_alertas")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("**Resumen por Asesor**")
    res = (df_f.assign(Pendientes=(df_f["estado"] == "Pendiente").astype(int), Vencidas=(df_f["estado"] == "Vencida").astype(int),
                       Gestionadas=(df_f["estado"] == "Gestionada").astype(int), En24=(df_f["en_tiempo_24h"] == "Sí").astype(int))
           .groupby("aps_nombre").agg(Alertas=("id_contacto", "count"), Pendientes=("Pendientes", "sum"), Vencidas=("Vencidas", "sum"),
                                      Gestionadas=("Gestionadas", "sum"), En24=("En24", "sum")).reset_index()
           .rename(columns={"aps_nombre": "Asesor", "En24": "Atendidas en 24 h"}).sort_values("Alertas", ascending=False))
    tot_r = pd.DataFrame([{"Asesor": "TOTAL GENERAL", **{c: int(res[c].sum()) for c in res.columns if c != "Asesor"}}])
    res = pd.concat([res, tot_r], ignore_index=True)
    st_r = res.style.apply(lambda row: ["background-color:#ffcccc;font-weight:bold" if c == "Pendientes" and row["Pendientes"] > 0
                                        else ("background-color:#E8E8E8;font-weight:bold" if row["Asesor"] == "TOTAL GENERAL" else "") for c in row.index],
                           axis=1).set_table_attributes('class="tabla-voc"').hide(axis="index")
    _rb = res[res["Asesor"] != "TOTAL GENERAL"]
    if not _rb.empty:
        import plotly.graph_objects as go
        _fa = go.Figure()
        for est_, col_ in (("Gestionadas", C["ok"]), ("Vencidas", C["gris"]), ("Pendientes", C["mal"])):
            _fa.add_trace(go.Bar(y=_rb["Asesor"], x=_rb[est_], name=est_, orientation="h", marker_color=col_,
                                 hovertemplate="<b>%{y}</b><br>" + est_ + ": %{x}<extra></extra>"))
        plotly_layout(_fa, alto=max(220, 36 * len(_rb) + 90))
        _fa.update_layout(barmode="stack"); _fa.update_yaxes(autorange="reversed", showgrid=False); _fa.update_xaxes(dtick=1)
        mostrar_plotly(_fa, key="pl_al_aps")
    with st.expander("Ver tabla"):
        st.markdown(tabla_html(st_r), unsafe_allow_html=True)

@st.cache_data(show_spinner=False)
def procesar_fechas_pendientes(df_raw, ciudad):
    if df_raw.empty: return df_raw
    df_p = aplicar_filtro_ciudad(df_raw.copy(), ciudad)
    df_p = _dedup_contactos(df_p)
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

    # Solo se listan pendientes o vencidas (no se muestran las completadas)
    if st.session_state.get("filtro_estado_p") not in ("Contacto en uso", "Expirado"):
        st.session_state.filtro_estado_p = "Contacto en uso"

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
    with k2:
        st.markdown(kpi_html("Vencidas", tot_exp, "{:.0f}", color="#D32F2F"), unsafe_allow_html=True)
        if st.button("Listar Vencidas", key="btn_pexp", width="stretch",
                     type="primary" if st.session_state.filtro_estado_p == "Expirado" else "secondary"):
            st.session_state.filtro_estado_p = "Expirado"; st.rerun()
    with k3:
        st.markdown(kpi_html("Pendientes", tot_pen, "{:.0f}", color="#FF8C00"), unsafe_allow_html=True)
        if st.button("Listar Pendientes", key="btn_puso", width="stretch",
                     type="primary" if st.session_state.filtro_estado_p == "Contacto en uso" else "secondary"):
            st.session_state.filtro_estado_p = "Contacto en uso"; st.rerun()

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    df_mostrar = df_f[df_f["status_calc"] == st.session_state.filtro_estado_p].copy()

    # --- TABLA 1: DETALLE DE PENDIENTES ---
    st.markdown("<div class='chart-box'>",unsafe_allow_html=True)
    titulo_estado = "VENCIDAS" if st.session_state.filtro_estado_p == "Expirado" else "PENDIENTES"
    st.markdown(f"**DETALLE DE {titulo_estado} — {dealer_p} · {CIUDAD}**")
    
    # El reporte del proveedor ya no trae celular/mail: se muestran vehículo y VIN para ubicar al cliente en el sistema
    ds_p = df_mostrar.reindex(columns=["aps_nombre","cliente_nombre","vehiculo","vin","fecha_envio","fecha_validez","status_calc"]).copy()
    ds_p.columns = ["Asesor","Nombre del Cliente","Vehículo","VIN","Fecha Envío","Fecha Validez", "Estado"]
    ds_p = ds_p.sort_values("Asesor").reset_index(drop=True)
    # Vacíos ("nan", "None", "") se muestran como guion
    for _col in ("Vehículo", "VIN", "Fecha Envío"):
        ds_p[_col] = ds_p[_col].astype(str).str.strip().replace({"nan": "—", "None": "—", "NaN": "—", "": "—"})
    _fv_dt = pd.to_datetime(ds_p["Fecha Validez"], format='mixed', dayfirst=True, errors='coerce')
    _mask_uso = ds_p["Estado"].isin(["Contacto en uso", "Expirado"])
    ds_p.loc[_mask_uso, "Fecha Validez"] = (_fv_dt[_mask_uso] - pd.Timedelta(days=1)).dt.strftime('%d/%m/%Y')
    
    st_p = ds_p.style.apply(lambda row: ["color:#D32F2F;font-weight:bold" if c == "Estado" and row["Estado"] == "Expirado" else ("color:#FF8C00;font-weight:bold" if c == "Estado" and row["Estado"] == "Contacto en uso" else "") for c in row.index], axis=1).set_table_attributes('class="tabla-voc"').hide(axis="index")
    st.markdown(tabla_html(st_p), unsafe_allow_html=True)
    
    if not ds_p.empty:
        boton_descarga_tabla(st, "Descargar Pendientes", ds_p, lambda: cached_jpg_export(ds_p, 'pendientes'), "Pendientes", "dl_pend")
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

    _rb = res_a[res_a["Asesor"] != "TOTAL GENERAL"] if not res_a.empty else res_a
    if not _rb.empty and {"Completadas", "Pendientes", "Vencidas"} <= set(_rb.columns):
        import plotly.graph_objects as go
        _fp = go.Figure()
        for nom_, col_ in (("Completadas", C["ok"]), ("Pendientes", C["alerta"]), ("Vencidas", C["mal"])):
            _fp.add_trace(go.Bar(y=_rb["Asesor"], x=_rb[nom_], name=nom_, orientation="h", marker_color=col_,
                                 hovertemplate="<b>%{y}</b><br>" + nom_ + ": %{x}<extra></extra>"))
        plotly_layout(_fp, alto=max(240, 36 * len(_rb) + 90))
        _fp.update_layout(barmode="stack"); _fp.update_yaxes(autorange="reversed", showgrid=False)
        mostrar_plotly(_fp, key="pl_pend_aps")
        st.caption("Cada barra suma las encuestas enviadas del asesor: completadas, pendientes (aún vigentes) y vencidas.")

    st_ra = res_a.style.apply(lambda row: ["background-color:#ffcccc;color:black;font-weight:bold" if c == "Vencidas" and row["Vencidas"] > 0 else ("background-color:#E8E8E8;font-weight:bold" if row["Asesor"] == "TOTAL GENERAL" else "") for c in row.index], axis=1).format({"Total Enviadas":"{:.0f}", "Completadas":"{:.0f}", "Vencidas":"{:.0f}", "Pendientes":"{:.0f}"}).set_table_attributes('class="tabla-voc"').hide(axis="index")
    st.markdown(tabla_html(st_ra), unsafe_allow_html=True)
    
    if not res_a.empty:
        boton_descarga_tabla(st, "Descargar Resumen", res_a, lambda: cached_jpg_export(res_a, 'resumen'), "Resumen_Asesor", "dl_res_asesor")
    st.markdown("</div>",unsafe_allow_html=True)

# ==============================================================================
# SECCIÓN 6: VERBALIZACIONES
# ==============================================================================
def _clean_pct(x):
    try:
        if pd.isna(x) or str(x).strip() in ["-", ""]: return 0.0
        return float(str(x).split('%')[0].strip())
    except Exception: return 0.0

def agregar_temas(df):
    """Una fila por tema para los meses elegidos: % de menciones, satisfacción neta y conteos positivo/negativo.
    Meses reconstruidos del RawData (traen conteos) se ponderan; los antiguos (tabla pegada a mano) se promedian."""
    d = df.copy()
    d["sat_neta"] = d["SATISFACCIÓN NETA"].apply(_clean_pct)
    d["menciones_pct"] = d["Comentarios relacionados"].apply(_clean_pct)
    for c in ("n_menciones", "n_base", "n_i16", "n_i78", "n_pos", "n_neg"):
        if c not in d.columns: d[c] = np.nan
    cols = ["Sub-Categoría", "Categoría", "sat_neta", "menciones_pct", "n_menciones", "n_pos", "n_neg"]
    partes = []
    nuevo = d[d["n_menciones"].notna()]; viejo = d[d["n_menciones"].isna()]
    if not nuevo.empty:
        base_tot = nuevo.drop_duplicates(["mes_anio", "ciudad"])["n_base"].sum() or 1
        g = nuevo.groupby("Sub-Categoría").agg(n=("n_menciones", "sum"), i16=("n_i16", "sum"), i78=("n_i78", "sum"),
                                               n_pos=("n_pos", "sum"), n_neg=("n_neg", "sum"), Categoría=("Categoría", "first")).reset_index()
        g["sat_neta"] = (g["n"] - 2 * g["i16"] - g["i78"]) / g["n"] * 100
        g["menciones_pct"] = g["n"] / base_tot * 100
        g["n_menciones"] = g["n"]
        # categoría = signo predominante cuando hay conteos
        g["Categoría"] = np.where(g["n_pos"] > g["n_neg"], "Positivo", np.where(g["n_neg"] > g["n_pos"], "Negativo", "Mixto"))
        partes.append(g[cols])
    if not viejo.empty:
        partes.append(viejo[cols])
    if not partes: return pd.DataFrame(columns=cols)
    out = pd.concat(partes, ignore_index=True)
    out = out.groupby("Sub-Categoría").agg(Categoría=("Categoría", "first"), sat_neta=("sat_neta", "mean"),
                                           menciones_pct=("menciones_pct", "sum"), n_menciones=("n_menciones", "sum"),
                                           n_pos=("n_pos", "sum"), n_neg=("n_neg", "sum")).reset_index()
    return out[out["menciones_pct"] > 0].sort_values("menciones_pct", ascending=False).reset_index(drop=True)

_COL_SENT = {"Positivo": "#388E3C", "Negativo": "#D32F2F", "Mixto": "#F57F17", "Sin tema": "#9E9E9E"}

def vista_mapa_temas(agg):
    """Burbujas (menciones vs satisfacción neta) y balance positivo/negativo por tema."""
    import plotly.graph_objects as go
    c_izq, c_der = st.columns([1.35, 1])
    with c_izq, st.container(key="card_vb_mapa"):
        st.markdown("<div class='card-titulo'>Mapa de impacto de los temas</div>", unsafe_allow_html=True)
        fig = go.Figure()
        rotulados = set(agg.head(7)["Sub-Categoría"])      # solo los más mencionados llevan etiqueta (el resto, al pasar el mouse)
        for cat, g in agg.groupby("Categoría"):
            fig.add_trace(go.Scatter(
                x=g["sat_neta"], y=g["menciones_pct"], mode="markers+text", name=cat,
                text=[t if t in rotulados else "" for t in g["Sub-Categoría"]],
                textposition="top center", textfont=dict(size=10),
                marker=dict(size=(g["menciones_pct"].clip(lower=1) ** 0.5) * 5 + 8, color=_COL_SENT.get(cat, C["gris"]),
                            opacity=0.85, line=dict(color="white", width=1.5)),
                customdata=np.c_[g["n_menciones"].fillna(0), g["Sub-Categoría"]],
                hovertemplate="<b>%{customdata[1]}</b><br>Menciones: %{y:.1f}% de los clientes (%{customdata[0]:.0f})<br>Satisfacción neta: %{x:.0f}%<extra></extra>"))
        fig.add_vline(x=75, line_dash="dash", line_color=C["naranja"], annotation_text="Ref. 75%", annotation_position="top")
        x0 = min(-5, agg["sat_neta"].min() - 10)
        fig.add_shape(type="rect", x0=x0, x1=75, y0=0, y1=agg["menciones_pct"].max() * 1.2, fillcolor=C["mal"], opacity=0.05, line_width=0, layer="below")
        plotly_layout(fig, alto=520)
        fig.update_xaxes(title="Satisfacción neta de quienes mencionan el tema (%)", range=[x0, 118], showgrid=True, gridcolor=C["linea"])
        fig.update_yaxes(title="% de clientes que lo mencionan", range=[0, agg["menciones_pct"].max() * 1.25])
        mostrar_plotly(fig, key="pl_vb_mapa")
        st.caption("Tamaño = menciones. Zona roja a la izquierda: temas que restan satisfacción; mientras más arriba, más clientes lo viven.")
    with c_der, st.container(key="card_vb_bal"):
        st.markdown("<div class='card-titulo'>Positivo vs negativo por tema</div>", unsafe_allow_html=True)
        d = agg[(agg["n_pos"] + agg["n_neg"]) > 0].copy()
        if d.empty:
            st.info("El balance positivo/negativo está disponible para los meses procesados desde el RawData.")
        else:
            d["tot"] = d["n_pos"] + d["n_neg"]
            d = d.sort_values("tot").tail(12)
            fig2 = go.Figure()
            fig2.add_trace(go.Bar(y=d["Sub-Categoría"], x=d["n_pos"], orientation="h", name="Positivo", marker_color=C["ok"],
                                  text=d["n_pos"].map(lambda v: f"{v:.0f}" if v else ""), textposition="outside",
                                  hovertemplate="<b>%{y}</b><br>%{x:.0f} positivos<extra></extra>"))
            fig2.add_trace(go.Bar(y=d["Sub-Categoría"], x=-d["n_neg"], orientation="h", name="Negativo", marker_color=C["mal"],
                                  text=d["n_neg"].map(lambda v: f"{v:.0f}" if v else ""), textposition="outside", customdata=d["n_neg"],
                                  hovertemplate="<b>%{y}</b><br>%{customdata:.0f} negativos<extra></extra>"))
            plotly_layout(fig2, alto=max(320, 34 * len(d) + 80))
            fig2.update_layout(barmode="relative", margin=dict(l=10, r=30, t=10, b=10))
            fig2.update_xaxes(showticklabels=False, zeroline=True, zerolinecolor=C["gris"], showgrid=False,
                              range=[-(d["n_neg"].max() * 1.35 + 1), d["n_pos"].max() * 1.3 + 1])
            fig2.update_yaxes(showgrid=False)
            mostrar_plotly(fig2, key="pl_vb_bal")
            st.caption("Clientes que mencionan cada tema, a la derecha en positivo y a la izquierda en negativo.")

def vista_evolucion_temas(df_fytd):
    """Mapa de calor tema x mes (menciones o satisfacción neta) con los temas más frecuentes del periodo."""
    import plotly.graph_objects as go
    metrica = st.radio("Métrica", ["% de menciones", "Satisfacción neta"], horizontal=True, key="vb_metrica", label_visibility="collapsed")
    d = df_fytd.copy()
    d["sat_neta"] = d["SATISFACCIÓN NETA"].apply(_clean_pct); d["menciones_pct"] = d["Comentarios relacionados"].apply(_clean_pct)
    col = "menciones_pct" if metrica == "% de menciones" else "sat_neta"
    top = d.groupby("Sub-Categoría")["menciones_pct"].sum().nlargest(14).index
    d = d[d["Sub-Categoría"].isin(top) & (d["menciones_pct"] > 0)]
    if d.empty: st.info("Sin datos para la evolución."); return
    orden = d.drop_duplicates("mes_anio").sort_values("orden_mes")["mes_anio"].tolist()
    piv = d.pivot_table(index="Sub-Categoría", columns="mes_anio", values=col, aggfunc="mean").reindex(columns=orden)
    piv = piv.loc[piv.mean(axis=1).sort_values(ascending=(col == "sat_neta")).index]
    if col == "menciones_pct":
        esc = dict(colorscale=[[0, "#FFFFFF"], [1, C["navy"]]], zmin=0, zmax=float(np.nanmax(piv.values)) or 1)
    else:
        esc = dict(colorscale=[[0, "#D32F2F"], [0.5, "#FFFFFF"], [1, "#388E3C"]], zmin=50, zmax=100)
    fig = go.Figure(go.Heatmap(z=piv.values, x=piv.columns, y=piv.index, text=piv.map(lambda v: "" if pd.isna(v) else f"{v:.0f}").values,
                               texttemplate="%{text}%", showscale=False, xgap=2, ygap=2, **esc,
                               hovertemplate="<b>%{y}</b><br>%{x}: %{z:.1f}%<extra></extra>"))
    plotly_layout(fig, alto=max(360, 34 * len(piv) + 100), leyenda_abajo=False)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(side="top")
    mostrar_plotly(fig, key="pl_vb_evol")
    st.caption("Muestra todos los meses del periodo (no solo el mes elegido). Con un solo mes procesado se verá una sola columna.")

def vista_comentarios(meses_proc, fytd_sel):
    """Comentarios reales de los clientes con filtros por tema, sentimiento, nota, dealer y asesor."""
    dc = D["comentarios"]
    if dc.empty:
        st.info("Aún no hay comentarios cargados. Reprocesa los meses en el preprocesador actualizado (genera el archivo 15)."); return
    dc = aplicar_filtro_ciudad(dc, CIUDAD)
    dc = dc[(dc["fytd"] == fytd_sel) & (dc["mes_anio"].isin(meses_proc))].copy()
    if dc.empty: st.info("Sin comentarios para la selección."); return
    dc["temas"] = dc["temas"].fillna("")
    temas = sorted({t.split(" - ", 1)[1].strip() for sx in dc["temas"] for t in sx.split(";")
                    if " - " in t and not t.strip().lower().startswith("no verbalizado")})
    f1, f2, f3, f4 = st.columns([1.4, 1.2, 1.3, 1.3])
    tema_sel = f1.selectbox("Tema:", ["Todos"] + temas, key="vc_tema")
    sent_sel = f2.multiselect("Sentimiento:", ["Positivo", "Negativo", "Mixto", "Sin tema"], default=["Positivo", "Negativo", "Mixto", "Sin tema"], key="vc_sent")
    dealer_sel = f3.selectbox("Dealer:", ["Todos"] + sorted(x for x in dc["dealer"].unique() if x != "SIN DEALER"), key="vc_dlr")
    base_aps = dc if dealer_sel == "Todos" else dc[dc["dealer"] == dealer_sel]
    aps_sel = f4.selectbox("Asesor:", ["Todos"] + sorted(x for x in base_aps["aps_nombre"].unique() if "SIN ASESOR" not in str(x).upper()), key="vc_aps")
    g1, g2, g3 = st.columns([1.6, 1.6, 1.6])
    preguntas = sorted(dc["pregunta"].unique())
    preg_sel = g1.multiselect("Pregunta:", preguntas, default=preguntas, key="vc_preg")
    nota_lo, nota_hi = g2.slider("Nota de satisfacción:", 1, 10, (1, 10), key="vc_nota")
    texto = g3.text_input("Buscar en el comentario:", key="vc_txt")

    f = dc[dc["sentimiento"].isin(sent_sel) & dc["pregunta"].isin(preg_sel) & dc["satisfaccion"].between(nota_lo, nota_hi)]
    if tema_sel != "Todos": f = f[f["temas"].str.contains(" - " + tema_sel, regex=False)]
    if dealer_sel != "Todos": f = f[f["dealer"] == dealer_sel]
    if aps_sel != "Todos": f = f[f["aps_nombre"] == aps_sel]
    if texto.strip(): f = f[f["comentario"].str.contains(texto.strip(), case=False, regex=False)]
    f = f.sort_values(["satisfaccion", "orden_mes"], ascending=[True, False])

    h1, h2 = st.columns([3, 1])
    h1.markdown(f"**{len(f)} comentarios** (ordenados de menor a mayor nota)")
    if not f.empty:
        h2.download_button("Descargar Excel", data=df_to_xlsx_bytes(f[["mes_anio", "dealer", "aps_nombre", "satisfaccion", "pregunta", "sentimiento", "temas", "comentario"]], "Comentarios"),
                           file_name="Comentarios.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="vc_dl")
    limite = st.session_state.get("vc_lim", 20)
    for _, r in f.head(limite).iterrows():
        col = _COL_SENT.get(r["sentimiento"], C["gris"])
        chips = "".join(f"<span style='display:inline-block;background:#F0F1F5;border-radius:10px;padding:1px 9px;margin:2px 4px 0 0;font-size:11px;color:#444'>{_html.escape(t.strip())}</span>"
                        for t in str(r["temas"]).split(";") if t.strip() and not t.strip().lower().startswith("no verbalizado"))
        nota = "—" if pd.isna(r["satisfaccion"]) else f"{int(r['satisfaccion'])}"
        st.markdown(
            f"<div style='background:#fff;border-left:5px solid {col};border-radius:8px;padding:10px 14px;margin-bottom:8px;box-shadow:0 1px 6px rgba(26,26,46,.06)'>"
            f"<div style='font-size:12px;color:#777;margin-bottom:4px'><b style='color:{col}'>Nota {nota}</b> · {_html.escape(str(r['pregunta']))} · "
            f"{_html.escape(str(r['dealer']))} · {_html.escape(str(r['aps_nombre']))} · {_html.escape(str(r['mes_anio']))}</div>"
            f"<div style='font-size:15px;color:#1A1A2E'>{_html.escape(str(r['comentario']))}</div>{chips}</div>", unsafe_allow_html=True)
    if len(f) > limite:
        if st.button(f"Mostrar más ({len(f) - limite} restantes)", key="vc_mas"):
            st.session_state["vc_lim"] = limite + 20; st.rerun()

def render_verbalizaciones():
    st.markdown("<div class='seccion-titulo'>Verbalizaciones del cliente</div>", unsafe_allow_html=True)
    if D["verbaliz"].empty:
        st.info("No hay verbalizaciones cargadas. Se generan al procesar el RawData en el preprocesador."); return
    df_base = aplicar_filtro_ciudad(D["verbaliz"].copy(), CIUDAD)
    if df_base.empty:
        st.info(f"Sin verbalizaciones para {CIUDAD}."); return
    if "mes_anio" not in df_base.columns: df_base["mes_anio"] = np.nan
    if "orden_mes" not in df_base.columns: df_base["orden_mes"] = 0
    df_base = _reconstruir_mes_anio(df_base)

    c1, c2, c3 = st.columns([1.5, 1.5, 1.4])
    with c1: fytd_sel = st.selectbox("Periodo:", todos_fytd, key="v_fytd_dash")
    mm = meses_de(df_base, fytd_sel)
    with c2: mes_sel = st.selectbox("Mes/Año:", ["TODOS"] + mm if mm else ["TODOS"], index=len(mm) if mm else 0, key="v_mes_dash")
    with c3:
        st.markdown("<div style='margin-top:30px'></div>", unsafe_allow_html=True)
        acum_v = st.toggle("Acumulado hasta el mes", key="v_acum")
    if mes_sel == "TODOS": meses_proc = mm
    elif acum_v: meses_proc = mm[:mm.index(mes_sel) + 1] if mes_sel in mm else []
    else: meses_proc = [mes_sel]
    if not meses_proc: st.warning("Sin datos para esta selección."); return

    df_fytd = df_base[df_base["fytd"] == fytd_sel]
    df = df_fytd[df_fytd["mes_anio"].isin(meses_proc)]
    if df.empty: st.warning("Sin datos para esta selección."); return
    agg = agregar_temas(df)
    if agg.empty: st.info("No hay menciones registradas en este periodo."); return

    # --- KPIs ---
    top = agg.iloc[0]
    pos = agg[agg["Categoría"] == "Positivo"]; neg = agg[agg["Categoría"] == "Negativo"]
    peq = lambda t: f"<span style='font-size:20px;line-height:1.2;display:block'>{_html.escape(str(t))}</span>"
    k = st.columns(4)
    k[0].markdown(kpi_card("Temas mencionados", f"{len(agg)}", f"en {len(meses_proc)} mes(es)"), unsafe_allow_html=True)
    k[1].markdown(kpi_card("Más mencionado", peq(top["Sub-Categoría"]), f"{top['menciones_pct']:.0f}% de los clientes · satisfacción neta {top['sat_neta']:.0f}%"), unsafe_allow_html=True)
    k[2].markdown(kpi_card("Mayor fortaleza", peq(pos.iloc[0]["Sub-Categoría"]) if not pos.empty else "—",
                           f"{pos.iloc[0]['menciones_pct']:.0f}% de menciones" if not pos.empty else "Sin temas positivos", color=C["ok"]), unsafe_allow_html=True)
    k[3].markdown(kpi_card("Mayor alerta", peq(neg.iloc[0]["Sub-Categoría"]) if not neg.empty else "—",
                           f"{neg.iloc[0]['menciones_pct']:.0f}% de menciones" if not neg.empty else "Sin temas negativos", color=C["mal"] if not neg.empty else C["ok"]), unsafe_allow_html=True)
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    vistas = ["Mapa de temas", "Evolución", "Comentarios"]
    vista = st.segmented_control("Vista", vistas, default=vistas[0], key="vb_vista", label_visibility="collapsed") or vistas[0]
    if vista == vistas[0]: vista_mapa_temas(agg)
    elif vista == vistas[1]: vista_evolucion_temas(df_fytd)
    else: vista_comentarios(meses_proc, fytd_sel)

# ==============================================================================
# SECCIÓN 7: PERFIL APS
# ==============================================================================
def _kpis_aps(aps, meses, fytd, ciudad):
    """TRE e ISC de un asesor para una lista de meses."""
    t = filtrar(D["tre_aps"], fytd=fytd, ciudad=ciudad, aps=aps); i = filtrar(D["isc_aps"], fytd=fytd, ciudad=ciudad, aps=aps)
    t = t[t["mes_anio"].isin(meses)] if not t.empty else t; i = i[i["mes_anio"].isin(meses)] if not i.empty else i
    E = t["E"].sum() if not t.empty else 0; Cc = t["C"].sum() if not t.empty else 0
    enc = i["total_encuestas"].sum() if not i.empty else 0
    pen = (i["I16"].sum() * 2 + i["I78"].sum()) if not i.empty else 0
    return {"E": E, "C": Cc, "enc": enc, "tre": Cc / E if E > 0 else np.nan, "isc": (enc - pen) / enc if enc > 0 else np.nan}

def fig_evolucion_simple(meses, vals, meta, color, nombre):
    """Línea mensual de un indicador contra su meta (interactiva)."""
    import plotly.graph_objects as go
    fig = go.Figure(go.Scatter(x=meses, y=vals, mode="lines+markers+text", name=nombre, line=dict(color=color, width=4),
                               marker=dict(size=10, color="white", line=dict(color=color, width=3)),
                               text=[("" if v != v else f"{v:.0%}") for v in vals], textposition="top center", textfont=dict(size=13),
                               hovertemplate="%{x}: %{y:.1%}<extra></extra>"))
    fig.add_hline(y=meta, line_dash="dash", line_color=C["naranja"], annotation_text=f"Meta {meta:.0%}", annotation_position="top left")
    todos = [v for v in vals if v == v] + [meta]
    plotly_layout(fig, alto=300, leyenda_abajo=False)
    fig.update_yaxes(tickformat=".0%", range=[max(0, min(todos) - 0.08), min(1.05, max(todos) + 0.08)])
    return fig

def render_vista_aps():
    st.markdown("""
        <style>
            .stSelectbox label p { font-size: 18px !important; font-weight: bold !important; color: #1A1A2E !important; }
            .tabla-voc { border: 2px solid #1A1A2E !important; border-collapse: collapse !important; width: 100% !important; }
            .tabla-voc td { font-size: 16px !important; color: #000000 !important; font-weight: bold !important; border: 1px solid #CFD8DC !important; padding: 8px !important; }
            .tabla-voc th { font-size: 16px !important; color: #FFFFFF !important; background-color: #1A1A2E !important; font-weight: bold !important; border: 1px solid #CFD8DC !important; }
        </style>
    """, unsafe_allow_html=True)
    st.markdown("<div class='seccion-titulo'>Mi tablero — Asesor de servicio</div>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([1.3, 1.3, 3, 1.4])
    with c1: fytd_sel = st.selectbox("Periodo:", todos_fytd, key="aps_fytd")
    with c2:
        mm = meses_de(D["isc_aps"], fytd_sel, CIUDAD)
        mes_sel = st.selectbox("Mes/Año:", ["TODOS"] + mm if mm else ["TODOS"], index=len(mm) if mm else 0, key="aps_mes")
    with c3:
        aps_opts = [a for a in aps_para_filtro(CIUDAD, "GENERAL", fytd_sel) if a != "TODOS"]
        aps_sel = st.selectbox("Selecciona tu nombre (APS):", aps_opts if aps_opts else ["Sin asesores"], key="aps_sel")
    with c4:
        st.markdown("<div style='margin-top:30px'></div>", unsafe_allow_html=True)
        acum = st.toggle("Acumulado hasta el mes", key="aps_acum_t")
    st.session_state.aps_acum = acum
    if not aps_opts or aps_sel == "Sin asesores":
        st.info("No hay asesores registrados para esta selección."); return

    if mes_sel == "TODOS": meses_proc = mm
    elif acum: meses_proc = mm[:mm.index(mes_sel) + 1] if mes_sel in mm else []
    else: meses_proc = [mes_sel]
    if not meses_proc: st.warning("Sin datos para esta selección."); return

    df_tre_all = filtrar(D["tre_aps"], fytd=fytd_sel, ciudad=CIUDAD, aps=aps_sel)
    df_isc_all = filtrar(D["isc_aps"], fytd=fytd_sel, ciudad=CIUDAD, aps=aps_sel)
    obj_tre = get_obj(D["objetivos"], fytd_sel, "obj_tre"); obj_isc = get_obj(D["objetivos"], fytd_sel, "obj_isc")
    K = _kpis_aps(aps_sel, meses_proc, fytd_sel, CIUDAD)
    e_tot, c_tot, enc_tot = K["E"], K["C"], K["enc"]
    tre_val = K["tre"] if K["tre"] == K["tre"] else 0
    isc_val = K["isc"] if K["isc"] == K["isc"] else 0
    color_estado = _color_kpi

    # variación contra el periodo anterior
    prev = None; ref_txt = ""
    if mes_sel != "TODOS":
        if acum and len(meses_proc) > 1: prev = _kpis_aps(aps_sel, meses_proc[:-1], fytd_sel, CIUDAD); ref_txt = f"vs acum. a {meses_proc[-2]}"
        elif not acum and mm.index(mes_sel) > 0:
            prev = _kpis_aps(aps_sel, [mm[mm.index(mes_sel) - 1]], fytd_sel, CIUDAD); ref_txt = f"vs {mm[mm.index(mes_sel) - 1]}"
    dpp = lambda now, k: None if prev is None or prev[k] != prev[k] or now != now else (now - prev[k]) * 100

    # ranking de asesores de la ciudad
    rb = filtrar(D["isc_aps"], fytd=fytd_sel, ciudad=CIUDAD); rb = rb[rb["mes_anio"].isin(meses_proc)]
    rt_ = filtrar(D["tre_aps"], fytd=fytd_sel, ciudad=CIUDAD); rt_ = rt_[rt_["mes_anio"].isin(meses_proc)] if not rt_.empty else rt_
    rk = rb.groupby("aps_nombre").agg(enc=("total_encuestas", "sum"), i16=("I16", "sum"), i78=("I78", "sum")).reset_index()
    rk = rk[~rk["aps_nombre"].astype(str).str.upper().str.contains("SIN ASESOR", na=False)]
    rk["prom_isc"] = np.where(rk["enc"] > 0, (rk["enc"] - rk["i16"] * 2 - rk["i78"]) / rk["enc"].where(rk["enc"] > 0), 0)
    rk = rk.merge(rt_.groupby("aps_nombre").agg(E=("E", "sum"), C=("C", "sum")).reset_index() if not rt_.empty else pd.DataFrame({"aps_nombre": [], "E": [], "C": []}),
                  on="aps_nombre", how="left").fillna({"E": 0, "C": 0})
    df_rd = rk.sort_values("prom_isc", ascending=False).reset_index(drop=True)
    my_p = int(df_rd[df_rd["aps_nombre"] == aps_sel].index[0]) + 1 if aps_sel in df_rd["aps_nombre"].values else None

    # pendientes del asesor (contactos vigentes) y alertas de clientes
    _pend_aps = aplicar_filtro_ciudad(D["pendientes"], CIUDAD)
    if {"aps_nombre", "status", "fecha_validez"}.issubset(_pend_aps.columns):
        _pend_aps = _pend_aps[(_pend_aps["aps_nombre"] == aps_sel) & (_pend_aps["status"] == "Contacto en uso")]
    else:
        _pend_aps = pd.DataFrame(columns=["cliente_nombre", "vehiculo", "vin", "fecha_validez"])
    _venc_aps = pd.to_datetime(_pend_aps["fecha_validez"], format='mixed', dayfirst=True, errors='coerce') < pd.Timestamp.today().normalize()
    vivas = int((~_venc_aps).sum()); exp = int(_venc_aps.sum())
    al_todas = alertas_cliente(aps_sel, None, CIUDAD, fytd_sel)
    al_pend = al_todas[al_todas["estado"] == "Pendiente"] if not al_todas.empty else al_todas

    # ===================== 1. LO URGENTE HOY =====================
    if not al_pend.empty:
        st.markdown(f"<div class='card-titulo' style='color:#C62828'>Urgente: {len(al_pend)} cliente(s) insatisfecho(s) por contactar en 24 h</div>", unsafe_allow_html=True)
        tarjetas_alertas(al_pend)
    if vivas:
        st.markdown(f"<div class='banner-alerta' style='background:#FFF8E1;border-left-color:#F57F17;color:#5D4037'>Tienes <b>{vivas}</b> encuesta(s) pendiente(s) de respuesta: "
                    f"contacta a esos clientes antes de que venza el plazo (ver Agenda más abajo).</div>", unsafe_allow_html=True)
    if al_pend.empty and not vivas:
        st.markdown("<div class='banner-alerta' style='background:#E8F5E9;border-left-color:#388E3C;color:#1B5E20'>Sin urgencias: no tienes alertas ni contactos pendientes. ¡Buen trabajo!</div>", unsafe_allow_html=True)

    # ===================== 2. MIS NÚMEROS =====================
    k = st.columns(4)
    k[0].markdown(kpi_card("Encuestas enviadas", f"{e_tot:,.0f}", f"Meta TRE {obj_tre:.0%}",
                           delta_chip(None if prev is None else e_tot - prev["E"], "abs", ref_txt)), unsafe_allow_html=True)
    k[1].markdown(kpi_card("Mi TRE", f"{tre_val:.1%}", f"{c_tot:.0f} respondidas de {e_tot:.0f}", delta_chip(dpp(K["tre"], "tre"), "pp", ref_txt),
                           color=color_estado(tre_val, obj_tre)), unsafe_allow_html=True)
    k[2].markdown(kpi_card("Mi ISC", f"{isc_val:.1%}", f"Meta {obj_isc:.0%} · {enc_tot:.0f} encuestas", delta_chip(dpp(K["isc"], "isc"), "pp", ref_txt),
                           color=color_estado(isc_val, obj_isc)), unsafe_allow_html=True)
    k[3].markdown(kpi_card("Mi puesto", f"#{my_p}" if my_p else "—", f"de {len(df_rd)} asesores (por ISC)"), unsafe_allow_html=True)
    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    col_rk, col_msg = st.columns([2, 1.2])
    with col_rk, st.container(key="card_aps_rank"):
        st.markdown("<div class='card-titulo'>Ranking ISC de asesores</div>", unsafe_allow_html=True)
        fr = fig_ranking_aps(df_rd, obj_isc, resaltar=aps_sel)
        if fr is not None: mostrar_plotly(fr, key="pl_aps_rank")
        else: st.info("Sin datos para el ranking.")
    with col_msg:
        for m, c in alertas_metas(isc_val, obj_isc, enc_tot, tre_val, obj_tre, e_tot):
            st.markdown(f"<div style='background:{FONDO_ALERTA.get(c, '#F5F5F5')};border:1px solid {c};border-radius:10px;padding:12px 15px;margin-bottom:10px;color:{c};font-size:15px;line-height:1.4'>{_html.escape(m)}</div>",
                        unsafe_allow_html=True)

    # ===================== 3. AGENDA =====================
    st.markdown("<div class='card-titulo'>Gestión de encuestas y agenda</div>", unsafe_allow_html=True)
    i1, i2, i3 = st.columns(3)
    i1.markdown(kpi_card("Llenadas", f"{c_tot:.0f}", "Éxito", color=C["ok"]), unsafe_allow_html=True)
    i2.markdown(kpi_card("Vencidas", f"{exp}", "Pérdida", color=C["mal"]), unsafe_allow_html=True)
    i3.markdown(kpi_card("Pendientes", f"{vivas}", "Acción hoy", color=C["alerta"]), unsafe_allow_html=True)
    with st.container(key="card_aps_agenda"):
        st.markdown("<div class='card-titulo'>Agenda activa: clientes a contactar ahora</div>", unsafe_allow_html=True)
        df_p_show = _pend_aps.loc[~_venc_aps].reindex(columns=["cliente_nombre", "vehiculo", "vin", "fecha_validez"]).copy()
        if not df_p_show.empty:
            df_p_show["vehiculo"] = _col_txt(df_p_show, "vehiculo"); df_p_show["vin"] = _col_txt(df_p_show, "vin")
            df_p_show.columns = ["Nombre del Cliente", "Vehículo", "VIN", "Vence en"]
            _fv_agenda = pd.to_datetime(df_p_show["Vence en"], format='mixed', dayfirst=True, errors='coerce')
            df_p_show["Vence en"] = (_fv_agenda - pd.Timedelta(days=1)).dt.strftime('%d/%m/%Y')
            st.markdown(tabla_html(df_p_show.style.hide(axis="index").set_table_attributes('class="tabla-voc"')), unsafe_allow_html=True)
        else: st.success("¡Sin pendientes!")

    # historial de alertas del periodo (si hay)
    al_per = al_todas[al_todas["mes_anio"].isin(meses_proc)] if not al_todas.empty else al_todas
    if not al_per.empty:
        with st.expander(f"Mis alertas del periodo ({len(al_per)})"):
            st.markdown(tabla_html(_estilo_alertas(tabla_alertas_detalle(al_per).drop(columns=["Asesor"]))), unsafe_allow_html=True)

    # ===================== 4. FOCO DE MEJORA =====================
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

    # ===================== 5. EVOLUCIÓN =====================
    st.markdown("<div class='card-titulo'>Evolución de mis indicadores</div>", unsafe_allow_html=True)
    def get_hist(df_base, tipo="isc"):
        hist = []
        for m_ in mm:
            if m_ not in df_base["mes_anio"].values: hist.append(np.nan); continue
            if acum: df_m = df_base[df_base["orden_mes"] <= df_base[df_base["mes_anio"] == m_]["orden_mes"].values[0]]
            else: df_m = df_base[df_base["mes_anio"] == m_]
            if tipo == "isc":
                et = df_m["total_encuestas"].sum()
                hist.append((et - df_m["I16"].sum() * 2 - df_m["I78"].sum()) / et if et > 0 else np.nan)
            else:
                et = df_m["E"].sum()
                hist.append(df_m["C"].sum() / et if et > 0 else np.nan)
        return hist
    ev1, ev2 = st.columns(2)
    with ev1, st.container(key="card_aps_ev_isc"):
        st.markdown("<div class='card-titulo'>Mi ISC por mes</div>", unsafe_allow_html=True)
        mostrar_plotly(fig_evolucion_simple(mm, get_hist(df_isc_all, "isc"), obj_isc, "#1976D2", "Mi ISC"), key="pl_aps_isc")
    with ev2, st.container(key="card_aps_ev_tre"):
        st.markdown("<div class='card-titulo'>Mi TRE por mes</div>", unsafe_allow_html=True)
        mostrar_plotly(fig_evolucion_simple(mm, get_hist(df_tre_all, "tre"), obj_tre, "#388E3C", "Mi TRE"), key="pl_aps_tre")

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
        vista = st.selectbox("CREDENCIALES DE ACCESO:", ["-- Seleccione su Perfil --", "VISTA GENERAL (Reporte Ejecutivo)", "VISTA APS (Gestión Operativa)", "VISTA KAIZEN (Gestión de Calidad)"], key="vista_caratula")
        
        if vista == "VISTA GENERAL (Reporte Ejecutivo)":
            st.session_state.perfil = "GENERAL"
            st.session_state.seccion = "general"
            st.rerun()
        elif vista == "VISTA KAIZEN (Gestión de Calidad)":
            st.session_state.perfil = "KAIZEN"
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
elif sec in ("general", "tendencia"): render_general()
elif sec in ("atributos", "radial"): render_atributos()
elif sec == "embudo":         render_embudo()
elif sec == "alertas":        render_alertas()
elif sec == "pendientes":     render_pendientes()
elif sec == "verbalizaciones": render_verbalizaciones()
elif sec == "vista_aps":       render_vista_aps() 
elif sec == "kaizen_alertas" and perfil == "KAIZEN": render_kaizen_alertas()
else: render_caratula()