# ==============================================================================
# PREPROCESADOR VoC — App 1  (v2)
# ==============================================================================

import streamlit as st
import pandas as pd
import numpy as np
import os, re
import unicodedata
from pathlib import Path

from google.cloud import storage
from google.oauth2 import service_account

# --- CONFIGURACIÓN ---
BUCKET_NAME = "bk_voc"

# --- AUTENTICACIÓN GCP vía st.secrets ---
credentials_dict = st.secrets["gcp_service_account"]
credentials = service_account.Credentials.from_service_account_info(credentials_dict)

def upload_to_gcs(file_path, filename, folder):
    client = storage.Client(credentials=credentials)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(f"{folder}{filename}")
    blob.upload_from_filename(file_path)

st.set_page_config(page_title="VoC Preprocesador", page_icon="⚙️", layout="wide")

CARPETA_SALIDA  = Path("datos_procesados")
CARPETA_SALIDA.mkdir(exist_ok=True)

RUTA_ISC_BASE    = CARPETA_SALIDA / "01_isc_base.csv"
RUTA_TRE_BASE    = CARPETA_SALIDA / "02_tre_base.csv"
RUTA_ISC_MENSUAL = CARPETA_SALIDA / "03_isc_mensual_dealer.csv"
RUTA_TRE_MENSUAL = CARPETA_SALIDA / "04_tre_mensual_dealer.csv"
RUTA_ATRIBUTOS   = CARPETA_SALIDA / "05_atributos_resumen.csv"
RUTA_PENDIENTES  = CARPETA_SALIDA / "06_pendientes.csv"
RUTA_OBJETIVOS   = CARPETA_SALIDA / "07_objetivos.csv"
RUTA_ISC_APS     = CARPETA_SALIDA / "08_isc_mensual_aps.csv"
RUTA_TRE_APS     = CARPETA_SALIDA / "09_tre_mensual_aps.csv"
RUTA_ATRIB_APS   = CARPETA_SALIDA / "10_atributos_aps.csv"
RUTA_ESPECIALES  = CARPETA_SALIDA / "11_atributos_especiales.csv"
RUTA_VERBALIZACIONES = CARPETA_SALIDA / "12_verbalizaciones.csv"
ATTR_ESCALA = {
    31:'Recepcion del vehiculo', 33:'Facilidad para realizar la cita',
    34:'Rapidez en recepcion', 35:'Disposicion del asesor',
    36:'Explicacion del trabajo a realizar', 39:'Instalaciones',
    40:'Entrega del vehiculo', 41:'Tiempo de espera en entrega',
    42:'Limpieza del vehiculo', 43:'Explicacion del trabajo realizado',
    44:'Explicacion costo total', 45:'Calidad de los trabajos',
    46:'Valor pagado por el servicio', 47:'Puntualidad en la fecha de entrega',
    48:'Puntualidad en la hora de entrega',
    50:'Tiempo que permanecio el vehiculo en la agencia',
    52:'Atencion del personal en la visita', 53:'Amabilidad del personal',
    55:'Seguimiento del concesionario - asesor',
}
ATTR_BINARIO = {
    27:'Bien a la primera H1',
    37:'Mencion del tiempo aproximado de entrega',
    38:'Explicacion del precio antes de ingresar',
    49:'Evidencias de trabajos realizados / repuestos',
}
TODOS_ATTR = {**ATTR_ESCALA, **ATTR_BINARIO}

MESES_NUM = {'Ene':1,'Feb':2,'Mar':3,'Abr':4,'May':5,'Jun':6,
             'Jul':7,'Ago':8,'Sep':9,'Oct':10,'Nov':11,'Dic':12}

def extraer_mes_anio(nombre):
    m = re.search(r"export_(\d{4})_([A-Za-z]+)_", nombre)
    if m:
        anio, mes = m.groups()
        mes = mes.capitalize()
        if mes in MESES_NUM: return mes, anio
    return None, None

def calcular_fytd(mes, anio):
    return f"FYTD {anio}" if MESES_NUM.get(mes,0) >= 4 else f"FYTD {int(anio)-1}"

def calcular_orden_mes(mes, anio):
    return int(anio)*100 + MESES_NUM.get(mes,0)

def cargar_mapeo_aps(path):
    df = None
    for engine in ['calamine', 'openpyxl']:
        try:
            df = pd.read_excel(path, sheet_name="APS", engine=engine, header=None)
            break
        except Exception:
            continue
    if df is None:
        raise ValueError("No se pudo leer INFO_BASE.xlsx con ningún engine disponible")

    if str(df.iloc[0, 0]).strip().lower() in ('periodo', 'period'):
        df.columns = [str(c).strip() for c in df.iloc[0]]
        df = df[1:].reset_index(drop=True)
    else:
        df.columns = [str(c).strip() for c in df.columns]

    if len(df.columns) == 3:
        df.columns = ['Periodo','Dealer','APS']
        ciudad_default = {
            'Centro Nacional de Servicio': 'LA PAZ',
            'El Alto'                    : 'LA PAZ',
            'Express'                    : 'LA PAZ',
        }
        df['Ciudad'] = df['Dealer'].map(ciudad_default).fillna('SIN CIUDAD')
    elif len(df.columns) == 4:
        df.columns = ['Periodo','Dealer','Ciudad','APS']
    else:
        raise ValueError(f"Hoja APS tiene {len(df.columns)} columnas inesperadas")

    df['APS']     = df['APS'].astype(str).str.strip().str.upper()
    df['Periodo'] = df['Periodo'].astype(str).str.strip()
    df['Ciudad']  = df['Ciudad'].astype(str).str.strip().str.upper()
    df['Dealer']  = df['Dealer'].astype(str).str.strip()
    df = df[df['Periodo'].str.match(r'\d{4}\s+\w+', na=False)].reset_index(drop=True)
    return df

def cargar_objetivos(path):
    for engine in ['calamine', 'openpyxl']:
        try:
            df = pd.read_excel(path, sheet_name="OBJ GEN", engine=engine)
            break
        except Exception:
            continue
    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(columns={'FYTD':'fytd','TRE':'obj_tre','ISC':'obj_isc'})
    df['fytd'] = df['fytd'].astype(str).str.strip()
    return df

def obtener_mapeo_completo(df_aps, mes, anio):
    periodo = f"{anio} {mes}"
    sub = df_aps[df_aps['Periodo'] == periodo]
    if sub.empty:
        sub = df_aps[df_aps['Periodo'] == df_aps['Periodo'].dropna().iloc[-1]]
    return sub[['APS','Dealer','Ciudad']].drop_duplicates()

def append_csv(ruta, df_nuevo, claves_dedup):
    if ruta.exists():
        df_e = pd.read_csv(ruta)
        df_c = pd.concat([df_e, df_nuevo], ignore_index=True)
        df_c = df_c.drop_duplicates(subset=claves_dedup, keep='last')
    else:
        df_c = df_nuevo.copy()
    df_c.to_csv(ruta, index=False)
    
    upload_to_gcs(str(ruta), ruta.name, "datos_procesados/")
    
    return len(df_c)

def calc_attr_score(vals, es_binario):
    datos = vals.dropna()
    tot = len(datos)
    if tot == 0: return np.nan
    if es_binario:
        return (datos == 1).sum() / tot
    else:
        return ((datos >= 9).sum() - (datos < 7).sum()) / tot

def isc_pct(g):
    c = len(g); i16 = g['I16'].sum(); i78 = g['I78'].sum()
    return (c - i16*2 - i78)/c if c > 0 else np.nan

def normalizar_texto(texto):
    """Elimina tildes, espacios extra y pasa a minúsculas para un cruce perfecto"""
    if pd.isna(texto): return ""
    t = str(texto).strip().lower()
    return unicodedata.normalize('NFKD', t).encode('ASCII', 'ignore').decode('utf-8')

def calc_attr_rows(base, obj_row, group_keys, mes_anio, fytd, orden_mes):
    rows = []
    
    cols_obj_norm = {normalizar_texto(col): col for col in obj_row.columns} if not obj_row.empty else {}

    for keys_vals, grp in base.groupby(group_keys):
        if not isinstance(keys_vals, tuple): keys_vals = (keys_vals,)
        extra = dict(zip(group_keys, keys_vals))
        extra.update({'mes_anio':mes_anio,'fytd':fytd,'orden_mes':orden_mes})
        
        for col_idx, nombre_attr in TODOS_ATTR.items():
            es_bin = col_idx in ATTR_BINARIO
            vals   = pd.to_numeric(grp[nombre_attr], errors='coerce')
            score  = calc_attr_score(vals, es_bin)
            if np.isnan(score): continue
            
            nombre_norm = normalizar_texto(nombre_attr)
            obj_val = np.nan
            if nombre_norm in cols_obj_norm:
                col_real = cols_obj_norm[nombre_norm]
                obj_val = float(obj_row[col_real].values[0])
                
            rows.append({**extra, 'atributo':nombre_attr, 'es_binario':es_bin,
                         'pct_score':round(score,4), 'n_respuestas':int(vals.dropna().__len__()),
                         'obj_atributo':obj_val,
                         'gap':round(score-obj_val,4) if not np.isnan(obj_val) else np.nan})
    return rows

def procesar_isc(arch, nombre, df_aps, df_obj):
    mes, anio  = extraer_mes_anio(nombre)
    if not mes: raise ValueError(f"No se pudo extraer mes/anio: {nombre}")
    mes_anio   = f"{mes} {anio}"
    fytd       = calcular_fytd(mes, anio)
    orden_mes  = calcular_orden_mes(mes, anio)
    mapeo_df   = obtener_mapeo_completo(df_aps, mes, anio)
    m_dlr      = dict(zip(mapeo_df['APS'], mapeo_df['Dealer']))
    m_ciu      = dict(zip(mapeo_df['APS'], mapeo_df['Ciudad']))
    df         = pd.read_excel(arch, engine="xlrd")
    voc01      = pd.to_numeric(df.iloc[:,22], errors='coerce')
    aps        = df.iloc[:,17].fillna('SIN ASESOR').astype(str).str.strip().str.upper()
    base = pd.DataFrame({
        'mes_anio':mes_anio,'fytd':fytd,'orden_mes':orden_mes,
        'ciudad':aps.map(m_ciu).fillna('Sin Ciudad'),
        'dealer':aps.map(m_dlr).fillna('SIN DEALER'),
        'aps_nombre':aps,
        'voc01':voc01,
        'I16':np.where(voc01<=6,1,0),
        'I78':np.where((voc01>=7)&(voc01<=8),1,0),
    })
    for ci, na in TODOS_ATTR.items():
        base[na] = pd.to_numeric(df.iloc[:,ci], errors='coerce')
    obj_row = df_obj[df_obj['fytd']==fytd]
    def agg_isc_fn(g):
        return pd.Series({'total_encuestas':len(g),'prom_isc':isc_pct(g),
                          'I16':int(g['I16'].sum()),'I78':int(g['I78'].sum())})
    m_dlr_df  = base.groupby(['mes_anio','fytd','orden_mes','ciudad','dealer']).apply(agg_isc_fn).reset_index()
    m_aps_df  = base.groupby(['mes_anio','fytd','orden_mes','ciudad','dealer','aps_nombre']).apply(agg_isc_fn).reset_index()
    at_dlr    = calc_attr_rows(base, obj_row, ['ciudad','dealer'], mes_anio, fytd, orden_mes)
    at_aps    = calc_attr_rows(base, obj_row, ['ciudad','dealer','aps_nombre'], mes_anio, fytd, orden_mes)
    return {'isc_base':base,'isc_mensual':m_dlr_df,'isc_aps':m_aps_df,
            'atributos_dlr':pd.DataFrame(at_dlr),'atributos_aps':pd.DataFrame(at_aps),
            'mes_anio':mes_anio,'fytd':fytd}

def procesar_tre(arch, nombre, df_aps):
    mes, anio  = extraer_mes_anio(nombre)
    if not mes: raise ValueError(f"No se pudo extraer mes/anio: {nombre}")
    mes_anio   = f"{mes} {anio}"
    fytd       = calcular_fytd(mes, anio)
    orden_mes  = calcular_orden_mes(mes, anio)
    mapeo_df   = obtener_mapeo_completo(df_aps, mes, anio)
    m_dlr      = dict(zip(mapeo_df['APS'], mapeo_df['Dealer']))
    m_ciu      = dict(zip(mapeo_df['APS'], mapeo_df['Ciudad']))
    
    df = pd.read_excel(arch, engine="xlrd")
    
    df = df.drop_duplicates(subset=[df.columns[3], df.columns[4]], keep='last').copy()
    
    aps        = df.iloc[:,20].fillna('SIN ASESOR').astype(str).str.strip().str.upper()
    status     = df.iloc[:,7].fillna('').astype(str).str.strip()
    
    fechas_validas = pd.to_datetime(df.iloc[:,6], format='mixed', dayfirst=True, errors='coerce')
    hoy = pd.Timestamp.today().normalize()
    
    status = np.where((status == 'Contacto en uso') & (fechas_validas < hoy), 'Expirado', status)

    base = pd.DataFrame({
        'mes_anio':mes_anio,'fytd':fytd,'orden_mes':orden_mes,
        'ciudad':aps.map(m_ciu).fillna('Sin Ciudad'),
        'dealer':aps.map(m_dlr).fillna('SIN DEALER'),
        'aps_nombre':aps,
        'status':status,
        'E': np.where((status != '') & (status != 'nan'), 1, 0),
        'C': np.where(status=='Entrevista completa',1,0),
        'F': np.where(status=='Contacto en uso',1,0),
    })
    
    def agg_tre(g): return pd.Series({'E':g['E'].sum(),'C':g['C'].sum(),'F':g['F'].sum()})
    m_dlr_df = base.groupby(['mes_anio','fytd','orden_mes','ciudad','dealer']).apply(agg_tre).reset_index()
    m_dlr_df['prom_tre'] = np.where(m_dlr_df['E']>0, m_dlr_df['C']/m_dlr_df['E'], np.nan)
    m_aps_df = base.groupby(['mes_anio','fytd','orden_mes','ciudad','dealer','aps_nombre']).apply(agg_tre).reset_index()
    m_aps_df['prom_tre'] = np.where(m_aps_df['E']>0, m_aps_df['C']/m_aps_df['E'], np.nan)
    
    m = (status != '') & (status != 'nan')
    pend = pd.DataFrame({
        'mes_anio':mes_anio,'fytd':fytd,
        'ciudad':base.loc[m,'ciudad'].values,
        'dealer':base.loc[m,'dealer'].values,
        'aps_nombre':base.loc[m,'aps_nombre'].values,
        'cliente_nombre':df.loc[m].iloc[:,3].astype(str).values,
        'cliente_celular':df.loc[m].iloc[:,4].astype(str).values,
        'cliente_mail':df.loc[m].iloc[:,5].astype(str).values,
        'fecha_validez':df.loc[m].iloc[:,6].astype(str).values,
        'status':status[m],
    })
    
    return {'tre_base':base,'tre_mensual':m_dlr_df,'tre_aps':m_aps_df,
            'pendientes':pend,'mes_anio':mes_anio,'fytd':fytd}

# ── UI ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;900&family=Barlow:wght@400;600&display=swap');
html,[class*="css"]{font-family:'Barlow',sans-serif!important}
.block-container{max-width:960px;padding-top:2rem}
.success-box{background:#e8f5e9;border-radius:8px;padding:1rem 1.5rem;border-left:4px solid #388E3C;font-family:monospace;font-size:13px}
.error-box{background:#ffebee;border-radius:8px;padding:1rem 1.5rem;border-left:4px solid #D32F2F}
</style>""", unsafe_allow_html=True)

st.title("⚙️ VoC Preprocesador  v2")
st.markdown("**Genera CSVs intermedios acumulados. Sube los 2 archivos del mes.**")

with st.expander("📁 Estado actual de datos procesados", expanded=False):
    csvs_info = [
        ("01_isc_base.csv","ISC detalle"),("02_tre_base.csv","TRE detalle"),
        ("03_isc_mensual_dealer.csv","ISC×Dealer"),("04_tre_mensual_dealer.csv","TRE×Dealer"),
        ("05_atributos_resumen.csv","Atrib×Dealer"),("06_pendientes.csv","Pendientes"),
        ("07_objetivos.csv","Objetivos"),("08_isc_mensual_aps.csv","ISC×APS"),
        ("09_tre_mensual_aps.csv","TRE×APS"),("10_atributos_aps.csv","Atrib×APS"),
    ]
    cols_e = st.columns(3)
    for i,(archivo,desc) in enumerate(csvs_info):
        ruta = CARPETA_SALIDA/archivo
        with cols_e[i%3]:
            if ruta.exists():
                dt = pd.read_csv(ruta)
                pp = sorted(dt['mes_anio'].unique()) if 'mes_anio' in dt.columns else []
                st.success(f"✅ **{archivo}**\n\n{len(dt):,} filas · {len(pp)} periodos")
            else:
                st.warning(f"⬜ **{archivo}**\n\n{desc} — no existe")

st.markdown("---")
st.subheader("📂 Paso 1 — Archivos del mes")
ruta_info_local = CARPETA_SALIDA/"INFO_BASE.xlsx"
col_i,col_isc,col_tre = st.columns(3)
with col_i:
    st.markdown("**INFO_BASE.xlsx**")
    st.caption("Hoja APS: Periodo, Ciudad*, Dealer, APS  (*opcional aún)")
    arch_info = st.file_uploader("INFO_BASE",type=["xlsx"],key="up_info",label_visibility="collapsed")
    if arch_info:
        ruta_info_local.write_bytes(arch_info.read())
        # --- GATILLO GCP ---
        upload_to_gcs(str(ruta_info_local), "INFO_BASE.xlsx", "datos_procesados/")
        st.success("✅ Guardado")
    if ruta_info_local.exists(): st.info("📌 Guardado localmente")
with col_isc:
    st.markdown("**ISC** (`d413a5.xls`)")
    arch_isc = st.file_uploader("ISC",type=["xls","xlsx"],key="up_isc",label_visibility="collapsed")
    if arch_isc: st.caption(f"📎 {arch_isc.name}")
with col_tre:
    st.markdown("**TRE** (`21014a.xls`)")
    arch_tre = st.file_uploader("TRE",type=["xls","xlsx"],key="up_tre",label_visibility="collapsed")
    if arch_tre: st.caption(f"📎 {arch_tre.name}")

st.markdown("---")
st.subheader("📝 Carga Manual — Atributos Especiales")

c_fy, c_mo, c_ciu = st.columns(3)
with c_fy:
    opciones_fytd = [f"FYTD {y}" for y in range(2023, 2031)]
    fytd_esp = st.selectbox("Selecciona FYTD:", opciones_fytd, key="esp_fytd")
with c_mo:
    meses_fytd_orden = ['Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic', 'Ene', 'Feb', 'Mar']
    mes_esp = st.selectbox("Selecciona Mes:", meses_fytd_orden, key="esp_mes")
with c_ciu:
    opciones_ciudad = ["LA PAZ", "SANTA CRUZ", "COCHABAMBA", "TARIJA", "SUCRE", "NACIONAL"]
    ciudad_esp = st.selectbox("Selecciona Ciudad:", opciones_ciudad, key="esp_ciu")

cz, caa, cab, cbtn = st.columns([1,1,1,1])
with cz:  val_z  = st.number_input("Bien a la primera H1 (%)", min_value=0.0, max_value=100.0, value=0.0, step=1.0)
with caa: val_aa = st.number_input("Customer Expectations CES (%)", min_value=0.0, max_value=100.0, value=0.0, step=1.0)
with cab: val_ab = st.number_input("Alertas atendidas 24 Hrs (%)", min_value=0.0, max_value=100.0, value=0.0, step=1.0)

with cbtn:
    st.markdown("<div style='margin-top:28px'></div>",unsafe_allow_html=True)
    if st.button("💾 Guardar Especiales", use_container_width=True):
        df_nuevo = pd.DataFrame([{
            'fytd': fytd_esp, 'mes': mes_esp, 'ciudad': ciudad_esp,
            'Bien a la primera H1': val_z / 100.0, 
            'Customer Expectations CES': val_aa / 100.0, 
            'Alertas atendidas en 24 Hrs': val_ab / 100.0
        }])
        
        if RUTA_ESPECIALES.exists():
            df_e = pd.read_csv(RUTA_ESPECIALES)
            
            if 'ciudad' not in df_e.columns:
                df_e['ciudad'] = "LA PAZ" 
                
            df_e = df_e[~((df_e['fytd'] == fytd_esp) & (df_e['mes'] == mes_esp) & (df_e['ciudad'] == ciudad_esp))]
            df_c = pd.concat([df_e, df_nuevo], ignore_index=True)
        else:
            df_c = df_nuevo
            
        df_c.to_csv(RUTA_ESPECIALES, index=False)

        upload_to_gcs(str(RUTA_ESPECIALES), "11_atributos_especiales.csv", "datos_procesados/")
        
        st.success(f"✅ ¡Atributos de {ciudad_esp} ({mes_esp} {fytd_esp}) guardados con éxito!")
# =========================================================================
st.markdown("---")
st.subheader("🗣️ Carga Manual — Verbalizaciones del Cliente")

c_fy_v, c_mo_v, c_ciu_v = st.columns(3)
with c_fy_v:
    fytd_verb = st.selectbox("Selecciona FYTD:", opciones_fytd, key="v_fytd")
with c_mo_v:
    mes_verb = st.selectbox("Selecciona Mes:", meses_fytd_orden, key="v_mes")
with c_ciu_v:
    ciudad_verb = st.selectbox("Selecciona Ciudad:", opciones_ciudad, key="v_ciu")

st.markdown("Pega tu tabla directamente en la celda vacía de abajo (puedes usar **Ctrl+V** desde Excel):")

columnas_verb = [
    "Categoría", "Sub-Categoría", "Variación", 
    "Comentarios relacionados", "SATISFACCIÓN NETA", "Por SATISFACCIÓN NETA", "Detalle 1", "Detalle 2"
]
df_verb_vacio = pd.DataFrame(columns=columnas_verb)

df_verb_editado = st.data_editor(df_verb_vacio, num_rows="dynamic", use_container_width=True, key="editor_verb")

with st.columns([1,2,1])[1]:
    st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
    if st.button("💾 Guardar Verbalizaciones", use_container_width=True):
        if not df_verb_editado.empty:
            df_verb_editado['fytd'] = fytd_verb
            df_verb_editado['mes'] = mes_verb
            df_verb_editado['ciudad'] = ciudad_verb
            
            if RUTA_VERBALIZACIONES.exists():
                df_v = pd.read_csv(RUTA_VERBALIZACIONES)
                if 'ciudad' not in df_v.columns: df_v['ciudad'] = "LA PAZ"
                df_v = df_v[~((df_v['fytd'] == fytd_verb) & (df_v['mes'] == mes_verb) & (df_v['ciudad'] == ciudad_verb))]
                df_c_v = pd.concat([df_v, df_verb_editado], ignore_index=True)
            else:
                df_c_v = df_verb_editado
                
            df_c_v.to_csv(RUTA_VERBALIZACIONES, index=False)
            # --- GATILLO GCP ---
            upload_to_gcs(str(RUTA_VERBALIZACIONES), "12_verbalizaciones.csv", "datos_procesados/")
            
            st.success(f"✅ ¡Verbalizaciones de {ciudad_verb} ({mes_verb} {fytd_verb}) guardadas con éxito!")
        else:
            st.warning("⚠️ La tabla está vacía. Pega los datos antes de guardar.")

st.markdown("---")
st.subheader("⚡ Paso 2 — Procesar y acumular")
cb,cn = st.columns([1,3])
with cb: btn = st.button("🚀 Procesar",type="primary",use_container_width=True)
with cn: st.caption("Append + dedup por periodo. Reprocesar el mismo mes sobrescribe solo ese periodo.")

if btn:
    errs = []
    if not ruta_info_local.exists() and arch_info is None: errs.append("❌ Falta INFO_BASE")
    if arch_isc is None: errs.append("❌ Falta ISC")
    if arch_tre is None: errs.append("❌ Falta TRE")
    if errs:
        for e in errs: st.error(e)
    else:
        prog = st.progress(0,"Iniciando...")
        log  = []
        try:
            prog.progress(5,"Cargando INFO_BASE...")
            df_aps = cargar_mapeo_aps(str(ruta_info_local))
            df_obj = cargar_objetivos(str(ruta_info_local))
            ciu = sorted(df_aps['Ciudad'].unique())
            log.append(f"✅ INFO_BASE — {len(df_aps)} APS · Ciudades: {', '.join(ciu)}")

            append_csv(RUTA_OBJETIVOS, df_obj, ['fytd'])
            log.append("✅ 07_objetivos.csv")

            prog.progress(20,f"ISC: {arch_isc.name}...")
            arch_isc.seek(0)
            ri = procesar_isc(arch_isc, arch_isc.name, df_aps, df_obj)
            log.append(f"✅ ISC — {len(ri['isc_base'])} encuestas · {ri['mes_anio']} · {ri['fytd']}")

            prog.progress(40,"Guardando ISC...")
            append_csv(RUTA_ISC_BASE,    ri['isc_base'],     ['mes_anio','ciudad','dealer','aps_nombre','voc01'])
            append_csv(RUTA_ISC_MENSUAL, ri['isc_mensual'],  ['mes_anio','ciudad','dealer'])
            append_csv(RUTA_ISC_APS,     ri['isc_aps'],      ['mes_anio','ciudad','dealer','aps_nombre'])
            append_csv(RUTA_ATRIBUTOS,   ri['atributos_dlr'],['mes_anio','ciudad','dealer','atributo'])
            append_csv(RUTA_ATRIB_APS,   ri['atributos_aps'],['mes_anio','ciudad','dealer','aps_nombre','atributo'])
            log.append("   → 01,03,05,08,10 guardados")

            prog.progress(65,f"TRE: {arch_tre.name}...")
            arch_tre.seek(0)
            rt = procesar_tre(arch_tre, arch_tre.name, df_aps)
            log.append(f"✅ TRE — {len(rt['tre_base'])} contactos · {rt['mes_anio']}")

            prog.progress(85,"Guardando TRE...")
            append_csv(RUTA_TRE_BASE,    rt['tre_base'],    ['mes_anio','ciudad','dealer','aps_nombre','status','E'])
            append_csv(RUTA_TRE_MENSUAL, rt['tre_mensual'], ['mes_anio','ciudad','dealer'])
            append_csv(RUTA_TRE_APS,     rt['tre_aps'],     ['mes_anio','ciudad','dealer','aps_nombre'])
            append_csv(RUTA_PENDIENTES,  rt['pendientes'],  ['mes_anio','ciudad','dealer','aps_nombre','cliente_nombre','cliente_celular'])
            log.append("   → 02,04,06,09 guardados")

            prog.progress(100,"✅ Listo")
            st.markdown("<div class='success-box'>"+"<br>".join(log)+"</div>",unsafe_allow_html=True)
            st.balloons()
        except Exception as e:
            prog.progress(100,"❌ Error")
            st.markdown(f"<div class='error-box'>❌ {e}</div>",unsafe_allow_html=True)
            import traceback; st.code(traceback.format_exc())

st.markdown("---")
st.subheader("🔍 Paso 3 — Verificar")
opciones = {
    "01-ISC base":RUTA_ISC_BASE,"02-TRE base":RUTA_TRE_BASE,
    "03-ISC×Dealer":RUTA_ISC_MENSUAL,"04-TRE×Dealer":RUTA_TRE_MENSUAL,
    "05-Atrib×Dealer":RUTA_ATRIBUTOS,"06-Pendientes":RUTA_PENDIENTES,
    "07-Objetivos":RUTA_OBJETIVOS,"08-ISC×APS":RUTA_ISC_APS,
    "09-TRE×APS":RUTA_TRE_APS,"10-Atrib×APS":RUTA_ATRIB_APS,
}
sel = st.selectbox("CSV:",list(opciones.keys()))
rsel = opciones[sel]

if rsel.exists():
    dv = pd.read_csv(rsel)
    
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Filas", f"{len(dv):,}")
    c2.metric("Cols", len(dv.columns))
    
    if 'mes_anio' in dv.columns:
        periodos_unicos = dv['mes_anio'].unique()
        c3.metric("Periodos", len(periodos_unicos))
        
        if 'orden_mes' in dv.columns:
            ultimo_periodo = dv.sort_values('orden_mes')['mes_anio'].iloc[-1]
            c4.metric("Último", ultimo_periodo)
        else:
            pp = sorted(periodos_unicos)
            c4.metric("Último", pp[-1] if len(pp)>0 else "-")
            
    if 'ciudad' in dv.columns:
        st.info(f"Ciudades detectadas: {', '.join(sorted(dv['ciudad'].unique().astype(str)))}")
        
    st.dataframe(dv.head(100), use_container_width=True)
    
    st.download_button(
        label=f"Descargar {rsel.name}",
        data=dv.to_csv(index=False).encode('utf-8'),
        file_name=rsel.name,
        mime="text/csv",
        key="btn_dl_verificar"
    )
else:
    st.info("El archivo seleccionado no existe aún. Procesa los datos en el Paso 2.")