# ==============================================================================
# PREPROCESADOR VoC — App 1  (v3)
# Estructura GCS: datos_procesados/{dealer_slug}/{YYYY}_{Mes}/archivo.csv
#                 datos_procesados/{dealer_slug}/11_atributos_especiales.csv
#                 datos_procesados/{dealer_slug}/12_verbalizaciones.csv
#                 datos_procesados/INFO_BASE.xlsx   (global)
#                 datos_procesados/07_objetivos.csv (global)
# ==============================================================================

import streamlit as st
import pandas as pd
import numpy as np
import os, re, io
import unicodedata
from pathlib import Path

from google.cloud import storage
from google.oauth2 import service_account

# --- CONFIGURACIÓN ---
BUCKET_NAME = "bk_voc"

CIUDADES = {
    "LA PAZ"      : "la_paz",
    "SANTA CRUZ"  : "santa_cruz",
    "COCHABAMBA"  : "cochabamba",
    "TARIJA"      : "tarija",
    "SUCRE"       : "sucre",
    "NACIONAL"    : "nacional",
}

# --- AUTENTICACIÓN GCP vía st.secrets ---
credentials_dict = st.secrets["gcp_service_account"]
credentials = service_account.Credentials.from_service_account_info(credentials_dict)

# ---------------------------------------------------------------------------
# Funciones GCS
# ---------------------------------------------------------------------------
def _gcs_client():
    return storage.Client(credentials=credentials)

def upload_to_gcs(local_path, blob_path):
    """Sube un archivo local a la ruta exacta de blob en el bucket."""
    bucket = _gcs_client().bucket(BUCKET_NAME)
    bucket.blob(blob_path).upload_from_filename(local_path)

def upload_periodo(local_path, filename, dealer_slug, mes, anio):
    """Sube a datos_procesados/{dealer_slug}/{YYYY}_{Mes}/filename."""
    blob_path = f"datos_procesados/{dealer_slug}/{anio}_{mes}/{filename}"
    upload_to_gcs(local_path, blob_path)

def upload_dealer(local_path, filename, dealer_slug):
    """Sube a datos_procesados/{dealer_slug}/filename (acumulado de dealer)."""
    blob_path = f"datos_procesados/{dealer_slug}/{filename}"
    upload_to_gcs(local_path, blob_path)

def download_df_from_gcs(blob_path):
    """Descarga un blob CSV y retorna DataFrame; None si no existe."""
    bucket = _gcs_client().bucket(BUCKET_NAME)
    blob = bucket.blob(blob_path)
    if blob.exists():
        data = blob.download_as_bytes()
        return pd.read_csv(io.BytesIO(data), low_memory=False)
    return None

def listar_periodos_dealer(dealer_slug):
    """Lista los subdirectorios de período disponibles en GCS para un dealer."""
    client = _gcs_client()
    prefix = f"datos_procesados/{dealer_slug}/"
    blobs = client.list_blobs(BUCKET_NAME, prefix=prefix, delimiter="/")
    # consumir blobs para que se pueblen los prefixes
    list(blobs)
    periodos = []
    for p in blobs.prefixes:
        parte = p.rstrip("/").split("/")[-1]
        periodos.append(parte)
    return sorted(periodos)

def estado_gcs_todas_ciudades():
    """Retorna dict {ciudad_slug: [periodos]} con lo que hay en GCS."""
    resumen = {}
    for slug in CIUDADES.values():
        periodos = listar_periodos_dealer(slug)
        resumen[slug] = periodos
    return resumen

# ---------------------------------------------------------------------------
st.set_page_config(page_title="VoC Preprocesador", page_icon="⚙️", layout="wide")

CARPETA_SALIDA = Path("datos_procesados")
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
RUTA_ALERTAS     = CARPETA_SALIDA / "13_alertas.csv"
RUTA_DRIVERS     = CARPETA_SALIDA / "14_drivers.csv"
RUTA_COMENTARIOS = CARPETA_SALIDA / "15_comentarios.csv"
RUTA_EMBUDO      = CARPETA_SALIDA / "16_embudo.csv"

# Atributos identificados por CODIGO de pregunta (encabezado del RawData), no por posicion.
# Atributos identificados por CODIGO de pregunta (encabezado del RawData), no por posicion.
ATTR_ESCALA = {
    'P02'  :'Recepcion del vehiculo', 'P04':'Facilidad para realizar la cita',
    'P05.1':'Rapidez en recepcion', 'P05.2':'Disposicion del asesor',
    'P05.3':'Explicacion del trabajo a realizar', 'P08':'Instalaciones',
    'P09'  :'Entrega del vehiculo', 'P10.1':'Tiempo de espera en entrega',
    'P10.2':'Limpieza del vehiculo', 'P10.3':'Explicacion del trabajo realizado',
    'P10.4':'Explicacion costo total', 'P10.5':'Calidad de los trabajos',
    'P10.6':'Valor pagado por el servicio', 'P10.7':'Puntualidad en la fecha de entrega',
    'P10.8':'Puntualidad en la hora de entrega',
    'P12'  :'Tiempo que permanecio el vehiculo en la agencia',
    'P14'  :'Atencion del personal en la visita', 'P15':'Amabilidad del personal',
    'P17'  :'Seguimiento del concesionario - asesor',
}
ATTR_BINARIO = {
    'VOC06':'Bien a la primera H1',
    'P06'  :'Mencion del tiempo aproximado de entrega',
    'P07'  :'Explicacion del precio antes de ingresar',
    'P11'  :'Evidencias de trabajos realizados / repuestos',
}
TODOS_ATTR = {**ATTR_ESCALA, **ATTR_BINARIO}

# Vigencia del link de encuesta: el reporte nuevo ya no trae "Validez".
# SUPUESTO (confirmar con el proveedor): el link sirve hasta N dias despues del envio.
DIAS_VIGENCIA_LINK = 4

ESTADO_REALIZADA = 'encuesta realizada'
ESTADO_EXPIRADA  = 'encuesta expirada'
ESTADOS_PENDIENTES = {'registro disponible para la encuesta', 'encuesta interrumpida'}

MESES_NUM = {'Ene':1,'Feb':2,'Mar':3,'Abr':4,'May':5,'Jun':6,
             'Jul':7,'Ago':8,'Sep':9,'Oct':10,'Nov':11,'Dic':12}

MESES_ABR = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
MESES_ES  = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto',
             'septiembre','octubre','noviembre','diciembre']

MESES_ABR = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
MESES_ES  = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto',
             'septiembre','octubre','noviembre','diciembre']

def extraer_mes_anio(nombre):
    """Compatibilidad: formato antiguo export_YYYY_Mes_... (ya no se usa con los reportes nuevos)."""
    m = re.search(r"export_(\d{4})_([A-Za-z]+)_", nombre)
    if m:
        anio, mes = m.groups()
        mes = mes.capitalize()
        if mes in MESES_NUM: return mes, anio
    return None, None

def _norm(x):
    """Texto en minusculas, sin acentos/nbsp, espacios colapsados ('' si es nulo)."""
    if x is None or (isinstance(x, float) and np.isnan(x)): return ""
    t = unicodedata.normalize('NFKD', str(x).replace('\xa0', ' ')).encode('ASCII', 'ignore').decode()
    return re.sub(r'\s+', ' ', t).strip().lower()

def _id_txt(x):
    """ID de contacto como texto estable ('910220', no '910220.0')."""
    t = str(x).strip()
    return t[:-2] if t.endswith('.0') else t

def norm_aps(x):
    """Nombre de asesor comparable con INFO_BASE: sin acentos/enie, mayusculas."""
    t = _norm(x).upper()
    return t if t not in ("", "NAN", "-") else "SIN ASESOR"

def _leer_hoja(arch):
    """Lee la primera hoja de un reporte nuevo (.xlsx) sin encabezado."""
    if hasattr(arch, "seek"): arch.seek(0)
    return pd.read_excel(arch, header=None, engine="openpyxl")

def _buscar_col(headers, patron, desc):
    """Indice de la primera columna cuyo encabezado normalizado cumple el regex."""
    for i, h in enumerate(headers):
        if re.search(patron, h): return i
    raise ValueError(f"No se encontro la columna '{desc}' en el reporte (patron: {patron})")

def _periodo_desde_titulo(texto):
    """'... - NOVIEMBRE/2026 - ...' -> ('Nov', '2026')."""
    m = re.search(r'([a-z]+)\s*/\s*(\d{4})', _norm(texto))
    if m and m.group(1) in MESES_ES:
        return MESES_ABR[MESES_ES.index(m.group(1))], m.group(2)
    return None, None

def calcular_fytd(mes, anio):
    return f"FYTD {anio}" if MESES_NUM.get(mes,0) >= 4 else f"FYTD {int(anio)-1}"

def anio_desde_fytd_mes(fytd_str, mes_str):
    """Reconstruye el año calendario a partir de FYTD y mes (inverso de calcular_fytd)."""
    fytd_year = int(fytd_str.replace("FYTD ", "").strip())
    return str(fytd_year) if MESES_NUM.get(mes_str, 0) >= 4 else str(fytd_year + 1)

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

    df['APS']     = df['APS'].map(norm_aps)
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

def guardar_csv(ruta, df):
    """Guarda DataFrame en CSV local."""
    df.to_csv(ruta, index=False)

def save_global_csv(ruta, df_nuevo, clave_dedup):
    """Para 07_objetivos: acumula localmente y sube a raíz global."""
    if ruta.exists():
        df_e = pd.read_csv(ruta)
        df_c = pd.concat([df_e, df_nuevo], ignore_index=True)
        df_c = df_c.drop_duplicates(subset=clave_dedup, keep='last')
    else:
        df_c = df_nuevo.copy()
    df_c.to_csv(ruta, index=False)
    upload_to_gcs(str(ruta), f"datos_procesados/{ruta.name}")

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

def construir_verbalizaciones(base, cods, mes_anio, fytd, mes, orden_mes):
    """Verbalizaciones por tema, a partir de VOC02/VOC04 'Codificacion' del RawData.

    Cada codificacion es 'Positivo - Tema; Negativo - Otro tema' (o 'No verbalizado - ...').
    Definiciones (supuestos, ya no existe la tabla agregada del proveedor):
      - Un cliente 'menciona' un tema si aparece en VOC02 o VOC04 (se cuenta una vez por cliente).
      - Base = clientes con al menos un tema codificado, por ciudad y mes.
      - Comentarios relacionados = clientes que mencionan el tema / base (en %).
      - Satisfaccion neta = ISC (VOC01) solo de los clientes que mencionan el tema (en %).
    """
    temas = []
    for i in range(len(base)):
        conj = set()
        for c in cods:
            v = c.iat[i]
            if pd.isna(v): continue
            for tok in str(v).split(';'):
                tok = tok.strip()
                if not tok or _norm(tok).startswith('no verbalizado') or ' - ' not in tok: continue
                signo, tema = tok.split(' - ', 1)
                conj.add((signo.strip().capitalize(), tema.strip()))
        temas.append(conj)

    filas = []
    for ciudad, g in base.groupby('ciudad'):
        if ciudad == 'Sin Ciudad': continue
        idx = [i for i in g.index if temas[i]]
        if not idx: continue
        por_tema = {}
        for i in idx:
            for signo, tema in temas[i]:
                por_tema.setdefault(tema, {})[i] = por_tema.setdefault(tema, {}).get(i, set()) | {signo}
        for tema, clientes in por_tema.items():
            n = len(clientes)
            i16 = int(sum(base.at[i, 'I16'] for i in clientes))
            i78 = int(sum(base.at[i, 'I78'] for i in clientes))
            pos = sum('Positivo' in sg for sg in clientes.values())
            neg = sum('Negativo' in sg for sg in clientes.values())
            filas.append({
                'Categoría': 'Positivo' if pos > neg else ('Negativo' if neg > pos else 'Mixto'),
                'Sub-Categoría': tema,
                'Variación': f"{pos} positivos / {neg} negativos",
                'n_pos': pos, 'n_neg': neg,
                'Comentarios relacionados': round(n / len(idx) * 100, 1),
                'SATISFACCIÓN NETA': round((n - 2 * i16 - i78) / n * 100, 1),
                'n_menciones': n, 'n_base': len(idx), 'n_i16': i16, 'n_i78': i78,
                'fytd': fytd, 'mes': mes, 'ciudad': ciudad,
                'mes_anio': mes_anio, 'orden_mes': orden_mes,
            })
    cols = ['Categoría', 'Sub-Categoría', 'Variación', 'Comentarios relacionados', 'SATISFACCIÓN NETA',
            'n_menciones', 'n_base', 'n_i16', 'n_i78', 'n_pos', 'n_neg', 'fytd', 'mes', 'ciudad', 'mes_anio', 'orden_mes']
    return pd.DataFrame(filas, columns=cols)

def _sentimiento(cod):
    """'Positivo - X; Negativo - Y' -> Positivo / Negativo / Mixto / Sin tema."""
    if cod is None or (isinstance(cod, float) and np.isnan(cod)): return "Sin tema"
    toks = [_norm(t) for t in str(cod).split(';') if t.strip() and not _norm(t).startswith('no verbalizado')]
    pos = any(t.startswith('positivo') for t in toks); neg = any(t.startswith('negativo') for t in toks)
    return "Mixto" if pos and neg else ("Positivo" if pos else ("Negativo" if neg else "Sin tema"))

COLS_COMENTARIOS = ['mes_anio', 'fytd', 'orden_mes', 'ciudad', 'dealer', 'aps_nombre', 'id_contacto', 'satisfaccion',
                    'pregunta', 'comentario', 'temas', 'sentimiento']

def construir_comentarios(base, df, headers, mes_anio, fytd, orden_mes, ids):
    """Comentarios reales del cliente (VOC02, VOC04 y VOC07) con sus temas codificados.
    No incluye nombre del cliente ni VIN: solo asesor, dealer, nota y texto."""
    pregs = [("Motivo de la calificación", r'^voc02 (?!codificacion)', r'^voc02 codificacion'),
             ("Qué mejorar", r'^voc04 (?!codificacion)', r'^voc04 codificacion'),
             ("Por qué no quedó bien a la primera", r'^voc07', None)]
    filas = []
    for nombre, pat_t, pat_c in pregs:
        try: ct = _buscar_col(headers, pat_t, nombre)
        except ValueError: continue
        cc = None
        if pat_c:
            try: cc = _buscar_col(headers, pat_c, nombre + ' codificacion')
            except ValueError: cc = None
        for i in range(len(df)):
            txt = df.iat[i, ct]
            if pd.isna(txt) or not str(txt).strip() or str(txt).strip() == '-': continue
            cod = df.iat[i, cc] if cc is not None else None
            filas.append({
                'mes_anio': mes_anio, 'fytd': fytd, 'orden_mes': orden_mes,
                'ciudad': base.at[i, 'ciudad'], 'dealer': base.at[i, 'dealer'], 'aps_nombre': base.at[i, 'aps_nombre'],
                'id_contacto': ids[i] if ids is not None else '', 'satisfaccion': base.at[i, 'voc01'],
                'pregunta': nombre, 'comentario': re.sub(r'\s+', ' ', str(txt)).strip(),
                'temas': '' if (cod is None or pd.isna(cod)) else str(cod).strip(),
                'sentimiento': _sentimiento(cod) if cc is not None else 'Negativo'})
    return pd.DataFrame(filas, columns=COLS_COMENTARIOS)

def calcular_drivers(base, mes_anio, fytd, orden_mes):
    """Sumas para medir cuanto se relaciona cada atributo con la satisfaccion general (VOC01).

    Por ciudad y dealer guarda n, Σx, Σy, Σx², Σy², Σxy (x = nota del atributo, y = VOC01, solo pares completos).
    Con esas sumas el portal calcula la correlacion de Pearson de cualquier combinacion de meses y dealers
    sin cargar las encuestas una por una. En atributos Si/No, x vale 1 (Si) o 0 (No)."""
    filas = []
    for (ciudad, dealer), g in base.groupby(['ciudad', 'dealer']):
        y = pd.to_numeric(g['voc01'], errors='coerce')
        for codigo, nombre in TODOS_ATTR.items():
            x = g[nombre]
            es_bin = codigo in ATTR_BINARIO
            if es_bin: x = (x == 1).astype(float).where(x.notna())
            m = x.notna() & y.notna()
            n = int(m.sum())
            if n < 2: continue
            xv = x[m].astype(float); yv = y[m].astype(float)
            filas.append({'mes_anio': mes_anio, 'fytd': fytd, 'orden_mes': orden_mes, 'ciudad': ciudad, 'dealer': dealer,
                          'atributo': nombre, 'es_binario': es_bin, 'n': n,
                          'sx': float(xv.sum()), 'sy': float(yv.sum()), 'sxx': float((xv**2).sum()),
                          'syy': float((yv**2).sum()), 'sxy': float((xv * yv).sum())})
    return pd.DataFrame(filas, columns=['mes_anio', 'fytd', 'orden_mes', 'ciudad', 'dealer', 'atributo', 'es_binario',
                                        'n', 'sx', 'sy', 'sxx', 'syy', 'sxy'])

def _a_binario(serie):
    """'Si'/'No' (o 1/2) -> 1/2, que es la codificacion que usa calc_attr_score (1 = Si)."""
    def f(v):
        t = _norm(v)
        if t in ('si', '1', '1.0'): return 1
        if t in ('no', '2', '2.0', '0'): return 2
        return np.nan
    return serie.map(f)

def procesar_isc(arch, nombre, df_aps, df_obj):
    """RawData_Servicios (.xlsx): una fila por encuesta respondida."""
    raw = _leer_hoja(arch)
    # fila de encabezado: la que dice 'Periodo' en la primera columna
    hdr_rows = [i for i in range(min(len(raw), 40)) if _norm(raw.iat[i, 0]) == 'periodo']
    if not hdr_rows: raise ValueError("RawData: no se encontro la fila de encabezado ('Periodo').")
    h = hdr_rows[0]
    headers = [_norm(x) for x in raw.iloc[h]]
    df = raw.iloc[h+1:].reset_index(drop=True)
    # quitar la fila 'Base' (conteos) y cualquier fila sin periodo AAAAMM
    df = df[df.iloc[:, 0].astype(str).str.strip().str.fullmatch(r'\d{6}')].reset_index(drop=True)
    if df.empty: raise ValueError("RawData: sin filas de datos.")

    periodos = sorted(df.iloc[:, 0].astype(str).str.strip().unique())
    if len(periodos) > 1:
        raise ValueError(f"RawData trae varios periodos {periodos}; carga un archivo por mes.")
    anio, mes = periodos[0][:4], MESES_ABR[int(periodos[0][4:]) - 1]
    mes_anio   = f"{mes} {anio}"
    fytd       = calcular_fytd(mes, anio)
    orden_mes  = calcular_orden_mes(mes, anio)
    mapeo_df   = obtener_mapeo_completo(df_aps, mes, anio)
    m_dlr      = dict(zip(mapeo_df['APS'], mapeo_df['Dealer']))
    m_ciu      = dict(zip(mapeo_df['APS'], mapeo_df['Ciudad']))

    def col_pregunta(codigo):
        return _buscar_col(headers, rf'^{re.escape(codigo.lower())}(?![\w.])', codigo)

    voc01 = pd.to_numeric(df.iloc[:, col_pregunta('VOC01')], errors='coerce')
    i_aps = _buscar_col(headers, r'^nombre completo\s+aps', 'Nombre completo APS')
    aps   = df.iloc[:, i_aps].map(norm_aps)
    base = pd.DataFrame({
        'mes_anio':mes_anio,'fytd':fytd,'orden_mes':orden_mes,
        'ciudad':aps.map(m_ciu).fillna('Sin Ciudad'),
        'dealer':aps.map(m_dlr).fillna('SIN DEALER'),
        'aps_nombre':aps,
        'voc01':voc01,
        'I16':np.where(voc01<=6,1,0),
        'I78':np.where((voc01>=7)&(voc01<=8),1,0),
    })
    # Un VOC01 vacio no es una encuesta valida
    valido = voc01.notna().values
    base = base[valido].reset_index(drop=True)
    df   = df[valido].reset_index(drop=True)
    # ID de cliente -> asesor (sirve para asignar asesor a las alertas)
    try:
        c_idc = _buscar_col(headers, r'^id cliente$', 'ID Cliente')
        id_aps = dict(zip(df.iloc[:, c_idc].map(_id_txt), base['aps_nombre']))
    except ValueError:
        id_aps = {}

    for codigo, na in TODOS_ATTR.items():
        col = df.iloc[:, col_pregunta(codigo)]
        base[na] = _a_binario(col) if codigo in ATTR_BINARIO else pd.to_numeric(col, errors='coerce')

    # VOC09 (expectativa 1-5): base del indicador CES
    try:    base['voc09'] = pd.to_numeric(df.iloc[:, col_pregunta('VOC09')], errors='coerce')
    except ValueError: base['voc09'] = np.nan

    # Verbalizaciones codificadas por tema (VOC02 y VOC04)
    cods = []
    for patron in (r'^voc02 codificacion', r'^voc04 codificacion'):
        try: cods.append(df.iloc[:, _buscar_col(headers, patron, patron)].reset_index(drop=True))
        except ValueError: pass
    verb = construir_verbalizaciones(base, cods, mes_anio, fytd, mes, orden_mes)
    drivers = calcular_drivers(base, mes_anio, fytd, orden_mes)
    _ids = df.iloc[:, c_idc].map(_id_txt).tolist() if 'c_idc' in locals() else None
    comentarios = construir_comentarios(base, df, headers, mes_anio, fytd, orden_mes, _ids)

    obj_row = df_obj[df_obj['fytd']==fytd]
    def agg_isc_fn(g):
        # Conteos de H1 (VOC06) y CES (VOC09): el portal los suma, asi no necesita cargar las encuestas una por una
        h1 = g['Bien a la primera H1'].dropna(); v9 = g['voc09'].dropna()
        return pd.Series({'total_encuestas':len(g),'prom_isc':isc_pct(g),
                          'I16':int(g['I16'].sum()),'I78':int(g['I78'].sum()),
                          'h1_n':len(h1),'h1_si':int((h1==1).sum()),
                          'ces_n':len(v9),'ces_c4':int((v9>=4).sum()),'ces_c5':int((v9>=5).sum())})
    m_dlr_df  = base.groupby(['mes_anio','fytd','orden_mes','ciudad','dealer']).apply(agg_isc_fn).reset_index()
    m_aps_df  = base.groupby(['mes_anio','fytd','orden_mes','ciudad','dealer','aps_nombre']).apply(agg_isc_fn).reset_index()
    at_dlr    = calc_attr_rows(base, obj_row, ['ciudad','dealer'], mes_anio, fytd, orden_mes)
    at_aps    = calc_attr_rows(base, obj_row, ['ciudad','dealer','aps_nombre'], mes_anio, fytd, orden_mes)
    return {'isc_base':base,'isc_mensual':m_dlr_df,'isc_aps':m_aps_df,
            'atributos_dlr':pd.DataFrame(at_dlr),'atributos_aps':pd.DataFrame(at_aps),
            'id_aps':id_aps, 'verbalizaciones':verb, 'drivers':drivers, 'comentarios':comentarios,
            'mes_anio':mes_anio,'fytd':fytd,'mes':mes,'anio':anio}

def construir_embudo(df, base, headers, envio, est_n, mes_anio, fytd, orden_mes):
    """Embudo de envio por ciudad / dealer / asesor: enviadas -> entregadas -> abiertas -> interactuaron -> respondieron,
    mas canal de respuesta, tiempo de respuesta y dia de la semana del envio. Solo conteos (sin datos del cliente)."""
    def col(patron):
        try: return _buscar_col(headers, patron, patron)
        except ValueError: return None
    def serie(idx, f=lambda x: x):
        return df.iloc[:, idx].map(_norm) if idx is not None else pd.Series('', index=df.index)
    c_int, c_em, c_wa = col(r'^hubo interaccion'), col(r'^status del envio \(e-mail\)'), col(r'^status del envio \(whatsapp\)')
    c_eo, c_wo = col(r'^e-mail fue abierto'), col(r'^whatsapp fue abierto')
    c_resp, c_med, c_niv = col(r'^fecha de respuesta'), col(r'^medio por lo cual'), col(r'^nivel de respuesta')

    entregada = serie(c_em).str.startswith('e-mail recibido') | serie(c_wa).str.startswith('whatsapp recibido')
    abierta = (serie(c_eo) == 'si') | (serie(c_wo) == 'si')
    interactuo = serie(c_int) == 'si'
    realizada = est_n == ESTADO_REALIZADA
    medio = serie(c_med)
    resp = pd.to_datetime(df.iloc[:, c_resp], format='mixed', dayfirst=True, errors='coerce') if c_resp is not None else pd.Series(pd.NaT, index=df.index)
    dias = (resp.dt.normalize() - envio.dt.normalize()).dt.days.where(realizada)
    dow = envio.dt.dayofweek.fillna(-1).astype(int)

    e = pd.DataFrame({
        'ciudad': base['ciudad'].values, 'dealer': base['dealer'].values, 'aps_nombre': base['aps_nombre'].values,
        'enviadas': 1, 'entregadas': entregada.astype(int).values, 'abiertas': abierta.astype(int).values,
        'interactuaron': interactuo.astype(int).values, 'interrumpidas': (est_n == 'encuesta interrumpida').astype(int).values,
        'realizadas': realizada.astype(int).values,
        'opcionales': (realizada & serie(c_niv).str.startswith('opcionales')).astype(int).values,
        'resp_whatsapp': (realizada & medio.str.contains('whats')).astype(int).values,
        'resp_email': (realizada & medio.str.contains('mail')).astype(int).values,
        't_n': dias.notna().astype(int).values, 't_sum': dias.clip(lower=0).fillna(0).values,
        't_0': (dias <= 0).astype(int).values, 't_1': (dias == 1).astype(int).values,
        't_2_3': dias.between(2, 3).astype(int).values, 't_4mas': (dias >= 4).astype(int).values,
    })
    for d_ in range(7):
        e[f'env_d{d_}'] = (dow == d_).astype(int).values
        e[f'real_d{d_}'] = ((dow == d_) & realizada).astype(int).values
    out = e.groupby(['ciudad', 'dealer', 'aps_nombre']).sum().reset_index()
    out.insert(0, 'orden_mes', orden_mes); out.insert(0, 'fytd', fytd); out.insert(0, 'mes_anio', mes_anio)
    return out

def procesar_tre(arch, nombre, df_aps):
    """ExtractoVOCEncuestas (.xlsx): una fila por link enviado (realizada / expirada / pendiente)."""
    raw = _leer_hoja(arch)
    # encabezado de 2 niveles: la fila con 'CENSO' es la de columnas; la anterior trae ID y Status
    fila = [i for i in range(min(len(raw), 40)) if any(_norm(x) == 'censo' for x in raw.iloc[i])]
    if not fila: raise ValueError("Extracto: no se encontro la fila de encabezado ('CENSO').")
    h = fila[0]
    headers = [_norm(raw.iat[h, j]) or _norm(raw.iat[h-1, j]) for j in range(raw.shape[1])]
    # periodo desde el titulo ('... - NOVIEMBRE/2026 - ...')
    mes = anio = None
    for i in range(h):
        for x in raw.iloc[i]:
            if not mes and isinstance(x, str) and 'sucursal' in _norm(x):
                mes, anio = _periodo_desde_titulo(x)
    if not mes: raise ValueError("Extracto: no se pudo leer el mes/anio del titulo del reporte.")
    df = raw.iloc[h+1:].reset_index(drop=True)
    df = df[pd.to_numeric(df.iloc[:, 0], errors='coerce').notna()].reset_index(drop=True)

    mes_anio   = f"{mes} {anio}"
    fytd       = calcular_fytd(mes, anio)
    orden_mes  = calcular_orden_mes(mes, anio)
    mapeo_df   = obtener_mapeo_completo(df_aps, mes, anio)
    m_dlr      = dict(zip(mapeo_df['APS'], mapeo_df['Dealer']))
    m_ciu      = dict(zip(mapeo_df['APS'], mapeo_df['Ciudad']))

    c_id   = _buscar_col(headers, r'^id$', 'ID')
    c_st   = _buscar_col(headers, r'^status$', 'Status')
    c_env  = _buscar_col(headers, r'^fecha del envio del link', 'Fecha del envio del link')
    c_cli  = _buscar_col(headers, r'^nombre del cliente', 'Nombre del cliente')
    c_veh  = _buscar_col(headers, r'^vehiculo', 'Vehiculo')
    c_vin  = _buscar_col(headers, r'^vin', 'VIN')
    c_aps  = _buscar_col(headers, r'^nombre vendedor/asesor', 'Nombre Vendedor/Asesor')

    df = df.drop_duplicates(subset=[df.columns[c_id]], keep='last').reset_index(drop=True)
    aps      = df.iloc[:, c_aps].map(norm_aps)
    estado_p = df.iloc[:, c_st].fillna('').astype(str).str.replace('\xa0', ' ').str.strip()
    est_n    = estado_p.map(_norm)

    desconocidos = sorted(set(est_n) - {ESTADO_REALIZADA, ESTADO_EXPIRADA} - ESTADOS_PENDIENTES - {''})

    envio = pd.to_datetime(df.iloc[:, c_env], format='mixed', dayfirst=True, errors='coerce')
    # El reporte nuevo no trae 'Validez': se estima desde la fecha de envio (ver DIAS_VIGENCIA_LINK).
    # Convencion heredada de la app: vigente mientras hoy <= fecha_validez; se muestra fecha_validez-1.
    validez = envio + pd.Timedelta(days=DIAS_VIGENCIA_LINK + 1)

    # Vocabulario heredado (la app filtra por estos textos)
    status = np.where(est_n == ESTADO_REALIZADA, 'Entrevista completa',
              np.where(est_n == ESTADO_EXPIRADA, 'Expirado',
              np.where(est_n == '', '', 'Contacto en uso')))
    hoy = pd.Timestamp.today().normalize()
    status = np.where((status == 'Contacto en uso') & (validez < hoy), 'Expirado', status)

    base = pd.DataFrame({
        'mes_anio':mes_anio,'fytd':fytd,'orden_mes':orden_mes,
        'ciudad':aps.map(m_ciu).fillna('Sin Ciudad'),
        'dealer':aps.map(m_dlr).fillna('SIN DEALER'),
        'aps_nombre':aps,
        'status':status,
        'E': np.where(status != '', 1, 0),
        'C': np.where(status=='Entrevista completa',1,0),
        'F': np.where(status=='Contacto en uso',1,0),
    })

    def agg_tre(g): return pd.Series({'E':g['E'].sum(),'C':g['C'].sum(),'F':g['F'].sum()})
    m_dlr_df = base.groupby(['mes_anio','fytd','orden_mes','ciudad','dealer']).apply(agg_tre).reset_index()
    m_dlr_df['prom_tre'] = np.where(m_dlr_df['E']>0, m_dlr_df['C']/m_dlr_df['E'], np.nan)
    m_aps_df = base.groupby(['mes_anio','fytd','orden_mes','ciudad','dealer','aps_nombre']).apply(agg_tre).reset_index()
    m_aps_df['prom_tre'] = np.where(m_aps_df['E']>0, m_aps_df['C']/m_aps_df['E'], np.nan)

    m = (status != '')
    pend = pd.DataFrame({
        'mes_anio':mes_anio,'fytd':fytd,
        'ciudad':base.loc[m,'ciudad'].values,
        'dealer':base.loc[m,'dealer'].values,
        'aps_nombre':base.loc[m,'aps_nombre'].values,
        'id_contacto':df.loc[m].iloc[:,c_id].astype(str).values,
        'cliente_nombre':df.loc[m].iloc[:,c_cli].astype(str).str.strip().values,
        'vehiculo':df.loc[m].iloc[:,c_veh].astype(str).str.strip().values,
        'vin':df.loc[m].iloc[:,c_vin].astype(str).str.strip().values,
        'fecha_envio':envio[m].dt.strftime('%d/%m/%Y').values,
        'fecha_validez':validez[m].dt.strftime('%d/%m/%Y').values,
        'status':status[m],
        'estado_proveedor':estado_p[m].values,
    })

    embudo = construir_embudo(df, base, headers, envio, est_n, mes_anio, fytd, orden_mes)

    return {'tre_base':base,'tre_mensual':m_dlr_df,'tre_aps':m_aps_df,
            'pendientes':pend,'embudo':embudo,'mes_anio':mes_anio,'fytd':fytd,'mes':mes,'anio':anio,
            'id_aps':dict(zip(df.iloc[:, c_id].map(_id_txt), aps)),
            'estados_desconocidos':desconocidos}

COLS_ALERTAS = ['mes_anio','fytd','ciudad','dealer','aps_nombre','id_contacto','cliente_nombre','vehiculo','vin',
                'satisfaccion','verbalizacion','estado','estado_proveedor','en_tiempo_24h','dias_para_expirar',
                'cl_fecha_envio','cl_fecha_respuesta','cl_satisfecho','cl_calificacion','cl_verbalizacion']

def _vacio_a_nan(serie):
    return serie.map(lambda v: np.nan if _norm(v) in ('', '-', 'nan') else v)

def procesar_alertas(arch, nombre, df_aps, id_aps):
    """AcompanamientoDeAlertas (.xlsx): casos graves (insatisfaccion) que se deben atender en 24 h.
    El reporte no trae asesor: se asigna por ID de contacto con el RawData/Extracto del mismo mes."""
    raw = _leer_hoja(arch)
    fila = [i for i in range(min(len(raw), 40)) if any(_norm(x) == 'censo' for x in raw.iloc[i])]
    if not fila: raise ValueError("Alertas: no se encontro la fila de encabezado ('CENSO').")
    h = fila[0]
    headers = [_norm(raw.iat[h, j]) or _norm(raw.iat[h-1, j]) for j in range(raw.shape[1])]
    mes = anio = None
    for i in range(h):
        for x in raw.iloc[i]:
            if not mes and isinstance(x, str) and 'sucursal' in _norm(x):
                mes, anio = _periodo_desde_titulo(x)
    if not mes: raise ValueError("Alertas: no se pudo leer el mes/anio del titulo del reporte.")
    df = raw.iloc[h+1:].reset_index(drop=True)
    df = df[pd.to_numeric(df.iloc[:, 0], errors='coerce').notna()].reset_index(drop=True)

    mes_anio  = f"{mes} {anio}"
    fytd      = calcular_fytd(mes, anio)
    mapeo_df  = obtener_mapeo_completo(df_aps, mes, anio)
    m_dlr     = dict(zip(mapeo_df['APS'], mapeo_df['Dealer']))
    m_ciu     = dict(zip(mapeo_df['APS'], mapeo_df['Ciudad']))

    if df.empty:
        return {'alertas': pd.DataFrame(columns=COLS_ALERTAS), 'mes_anio': mes_anio, 'fytd': fytd,
                'mes': mes, 'anio': anio, 'sin_asesor': [], 'estados_desconocidos': []}

    col = lambda patron, desc: _buscar_col(headers, patron, desc)
    c_id, c_sat = col(r'^id$', 'ID'), col(r'^satisfaccion con la experiencia', 'Satisfaccion')
    c_st, c_24  = col(r'^status$', 'STATUS'), col(r'^gestion de alerta en tiempo', 'Gestion de alerta en tiempo (24 hs)')
    c_dias      = col(r'^dias para expirar', 'Dias para expirar el plazo')
    verbs       = [i for i, t in enumerate(headers) if t.startswith('verbalizacion')]
    if len(verbs) < 2: raise ValueError("Alertas: se esperaban 2 columnas 'Verbalizacion' (cuestionario y close loop).")
    c_fe, c_fr  = col(r'^fecha de envio', 'Fecha de envio'), col(r'^fecha de respuesta', 'Fecha de respuesta')
    c_ssol, c_cal = col(r'^satisfecho con solucion', 'Satisfecho con solucion'), col(r'^calificacion del alerta', 'Calificacion del Alerta')
    c_veh, c_cli, c_vin = col(r'^vehiculo', 'Vehiculo'), col(r'^nombre del cliente', 'Nombre del cliente'), col(r'^vin', 'VIN')

    ids = df.iloc[:, c_id].map(_id_txt)
    aps = ids.map(id_aps).fillna('SIN ASESOR')
    sin_asesor = sorted(set(ids[aps == 'SIN ASESOR']))

    est_p = df.iloc[:, c_st].fillna('').astype(str).str.replace('\xa0', ' ').str.strip()
    est_n = est_p.map(_norm)
    estado = np.where(est_n.str.startswith('a realizar'), 'Pendiente',
              np.where(est_n.str.startswith('expirado'), 'Vencida', 'Gestionada'))
    conocidos = est_n.str.startswith('a realizar') | est_n.str.startswith('expirado')
    desconocidos = sorted(set(est_p[~conocidos]))

    en24 = df.iloc[:, c_24].map(lambda v: 'Sí' if _norm(v) == 'si' else ('No' if _norm(v) == 'no' else ''))
    out = pd.DataFrame({
        'mes_anio': mes_anio, 'fytd': fytd,
        'ciudad': aps.map(m_ciu).fillna('Sin Ciudad').values,
        'dealer': aps.map(m_dlr).fillna('SIN DEALER').values,
        'aps_nombre': aps.values,
        'id_contacto': ids.values,
        'cliente_nombre': df.iloc[:, c_cli].astype(str).str.strip().values,
        'vehiculo': df.iloc[:, c_veh].astype(str).str.strip().values,
        'vin': df.iloc[:, c_vin].astype(str).str.strip().values,
        'satisfaccion': pd.to_numeric(df.iloc[:, c_sat], errors='coerce').values,
        'verbalizacion': _vacio_a_nan(df.iloc[:, verbs[0]]).values,
        'estado': estado, 'estado_proveedor': est_p.values,
        'en_tiempo_24h': en24.values,
        'dias_para_expirar': pd.to_numeric(df.iloc[:, c_dias], errors='coerce').values,
        'cl_fecha_envio': _vacio_a_nan(df.iloc[:, c_fe]).values,
        'cl_fecha_respuesta': _vacio_a_nan(df.iloc[:, c_fr]).values,
        'cl_satisfecho': _vacio_a_nan(df.iloc[:, c_ssol]).values,
        'cl_calificacion': pd.to_numeric(df.iloc[:, c_cal], errors='coerce').values,
        'cl_verbalizacion': _vacio_a_nan(df.iloc[:, verbs[-1]]).values,
    })[COLS_ALERTAS]
    out = out.drop_duplicates(subset=['id_contacto'], keep='last').reset_index(drop=True)
    return {'alertas': out, 'mes_anio': mes_anio, 'fytd': fytd, 'mes': mes, 'anio': anio,
            'sin_asesor': sin_asesor, 'estados_desconocidos': desconocidos}

# ==============================================================================
# UI
# ==============================================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;900&family=Barlow:wght@400;600&display=swap');
html,[class*="css"]{font-family:'Barlow',sans-serif!important}
.block-container{max-width:960px;padding-top:2rem}
.success-box{background:#e8f5e9;border-radius:8px;padding:1rem 1.5rem;border-left:4px solid #388E3C;font-family:monospace;font-size:13px}
.error-box{background:#ffebee;border-radius:8px;padding:1rem 1.5rem;border-left:4px solid #D32F2F}
</style>""", unsafe_allow_html=True)

st.title("⚙️ VoC Preprocesador  v3")
st.markdown("**Estructura GCS:** `datos_procesados/{dealer}/{YYYY}_{Mes}/archivo.csv`")

# ---------------------------------------------------------------------------
# Estado actual en GCS
# ---------------------------------------------------------------------------
with st.expander("📁 Estado actual en GCS (todas las ciudades)", expanded=False):
    try:
        resumen = estado_gcs_todas_ciudades()
        cols_e = st.columns(len(CIUDADES))
        for i, (nombre_ciudad, slug) in enumerate(CIUDADES.items()):
            with cols_e[i]:
                periodos = resumen.get(slug, [])
                if periodos:
                    st.success(f"**{nombre_ciudad}**\n\n{len(periodos)} período(s):\n" +
                               "\n".join(f"- {p}" for p in periodos))
                else:
                    st.warning(f"**{nombre_ciudad}**\n\nSin datos aún")
    except Exception as ex:
        st.warning(f"No se pudo consultar GCS: {ex}")

st.markdown("---")

# ---------------------------------------------------------------------------
# Paso 0 — Selección de Ciudad
# ---------------------------------------------------------------------------
st.subheader("🏙️ Paso 0 — Selecciona la Ciudad")
opciones_ciudad_up = ["-- Selecciona Ciudad --"] + list(CIUDADES.keys())
ciudad_seleccionada = st.selectbox("Ciudad que está cargando datos:", opciones_ciudad_up, key="sel_ciudad")

if ciudad_seleccionada != "-- Selecciona Ciudad --":
    ciudad_slug = CIUDADES[ciudad_seleccionada]
    st.info(f"📂 Los datos se guardarán en: `datos_procesados/{ciudad_slug}/{{YYYY}}_{{Mes}}/`")

st.markdown("---")

# ---------------------------------------------------------------------------
# Paso 1 — Archivos del mes
# ---------------------------------------------------------------------------
st.subheader("📂 Paso 1 — Archivos del mes")
ruta_info_local = CARPETA_SALIDA / "INFO_BASE.xlsx"
col_i, col_isc, col_tre, col_alt = st.columns(4)

with col_i:
    st.markdown("**INFO_BASE.xlsx** *(global, único)*")
    st.caption("Hoja APS: Periodo, Ciudad*, Dealer, APS")
    arch_info = st.file_uploader("INFO_BASE", type=["xlsx"], key="up_info", label_visibility="collapsed")
    if arch_info:
        ruta_info_local.write_bytes(arch_info.read())
        upload_to_gcs(str(ruta_info_local), "datos_procesados/INFO_BASE.xlsx")
        st.success("✅ INFO_BASE guardado (global)")
    if ruta_info_local.exists(): st.info("📌 Guardado localmente")

with col_isc:
    st.markdown("**ISC** (`RawData_Servicios ... .xlsx`)")
    arch_isc = st.file_uploader("ISC", type=["xlsx"], key="up_isc", label_visibility="collapsed")
    if arch_isc: st.caption(f"📎 {arch_isc.name}")

with col_tre:
    st.markdown("**TRE** (`ExtractoVOCEncuestas ... .xlsx`)")
    arch_tre = st.file_uploader("TRE", type=["xlsx"], key="up_tre", label_visibility="collapsed")
    if arch_tre: st.caption(f"📎 {arch_tre.name}")

with col_alt:
    st.markdown("**Alertas** (`AcompanamientoDeAlertas ... .xlsx`)")
    arch_alt = st.file_uploader("Alertas", type=["xlsx"], key="up_alt", label_visibility="collapsed")
    if arch_alt: st.caption(f"📎 {arch_alt.name}")
    else: st.caption("Opcional")

st.markdown("---")

# ---------------------------------------------------------------------------
# Carga Manual — Atributos Especiales
# ---------------------------------------------------------------------------
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
with cz:  val_z  = st.number_input("Bien a la primera H1 (%) — solo meses sin RawData", min_value=0.0, max_value=100.0, value=0.0, step=1.0)
with caa: val_aa = st.number_input("Customer Expectations CES (%) — solo meses sin RawData", min_value=0.0, max_value=100.0, value=0.0, step=1.0)
with cab: val_ab = st.number_input("Alertas 24 Hrs (%) — solo si no hay reporte de alertas", min_value=0.0, max_value=100.0, value=0.0, step=1.0)

with cbtn:
    st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
    if st.button("💾 Guardar Especiales", use_container_width=True):
        if ciudad_seleccionada == "-- Selecciona Ciudad --":
            st.error("❌ Selecciona la Ciudad (Paso 0) antes de guardar.")
        else:
            ciudad_slug = CIUDADES[ciudad_seleccionada]
            df_nuevo_esp = pd.DataFrame([{
                'fytd': fytd_esp, 'mes': mes_esp, 'ciudad': ciudad_esp,
                'Bien a la primera H1': val_z / 100.0,
                'Customer Expectations CES': val_aa / 100.0,
                'Alertas atendidas en 24 Hrs': val_ab / 100.0
            }])
            # Descargar acumulado actual de GCS para esta ciudad
            blob_esp = f"datos_procesados/{ciudad_slug}/11_atributos_especiales.csv"
            df_existente = download_df_from_gcs(blob_esp)
            if df_existente is not None:
                if 'ciudad' not in df_existente.columns:
                    df_existente['ciudad'] = ciudad_seleccionada
                df_existente = df_existente[~(
                    (df_existente['fytd'] == fytd_esp) &
                    (df_existente['mes'] == mes_esp) &
                    (df_existente['ciudad'] == ciudad_esp)
                )]
                df_c_esp = pd.concat([df_existente, df_nuevo_esp], ignore_index=True)
            else:
                df_c_esp = df_nuevo_esp

            df_c_esp.to_csv(RUTA_ESPECIALES, index=False)
            upload_dealer(str(RUTA_ESPECIALES), "11_atributos_especiales.csv", ciudad_slug)
            st.success(f"✅ Especiales de {ciudad_seleccionada} ({mes_esp} {fytd_esp}) guardados.")

st.markdown("---")

# ---------------------------------------------------------------------------
# Paso 2 — Procesar y subir a GCS
# ---------------------------------------------------------------------------
st.subheader("⚡ Paso 2 — Procesar y subir a GCS")
cb, cn = st.columns([1,3])
with cb: btn = st.button("🚀 Procesar", type="primary", use_container_width=True)
with cn: st.caption("Cada mes/dealer se guarda en su propia carpeta en GCS. Reprocesar el mismo mes/dealer reemplaza solo ese período.")

if btn:
    errs = []
    if ciudad_seleccionada == "-- Selecciona Ciudad --": errs.append("❌ Debes seleccionar la Ciudad (Paso 0)")
    if not ruta_info_local.exists() and arch_info is None: errs.append("❌ Falta INFO_BASE")
    if arch_isc is None: errs.append("❌ Falta archivo ISC")
    if arch_tre is None: errs.append("❌ Falta archivo TRE")

    if errs:
        for e in errs: st.error(e)
    else:
        ciudad_slug = CIUDADES[ciudad_seleccionada]
        prog = st.progress(0, "Iniciando...")
        log  = []
        try:
            prog.progress(5, "Cargando INFO_BASE...")
            df_aps = cargar_mapeo_aps(str(ruta_info_local))
            df_obj = cargar_objetivos(str(ruta_info_local))
            ciu = sorted(df_aps['Ciudad'].unique())
            log.append(f"✅ INFO_BASE — {len(df_aps)} APS · Ciudades: {', '.join(ciu)}")

            # Objetivos: global en raíz
            save_global_csv(RUTA_OBJETIVOS, df_obj, ['fytd'])
            log.append("✅ 07_objetivos.csv (global)")

            prog.progress(20, f"ISC: {arch_isc.name}...")
            arch_isc.seek(0)
            ri = procesar_isc(arch_isc, arch_isc.name, df_aps, df_obj)
            mes, anio = ri['mes'], ri['anio']
            log.append(f"✅ ISC — {len(ri['isc_base'])} encuestas · {ri['mes_anio']} · {ri['fytd']}")

            prog.progress(40, f"Subiendo ISC → {ciudad_slug}/{anio}_{mes}/...")
            for ruta, df in [
                (RUTA_ISC_BASE,    ri['isc_base']),
                (RUTA_ISC_MENSUAL, ri['isc_mensual']),
                (RUTA_ISC_APS,     ri['isc_aps']),
                (RUTA_ATRIBUTOS,   ri['atributos_dlr']),
                (RUTA_ATRIB_APS,   ri['atributos_aps']),
                (RUTA_DRIVERS,     ri['drivers']),
                (RUTA_COMENTARIOS, ri['comentarios']),
            ]:
                guardar_csv(ruta, df)
                upload_periodo(str(ruta), ruta.name, ciudad_slug, mes, anio)
            log.append(f"   → 01,03,05,08,10,14,15 subidos a {ciudad_slug}/{anio}_{mes}/ ({len(ri['comentarios'])} comentarios)")
            guardar_csv(RUTA_VERBALIZACIONES, ri['verbalizaciones'])
            upload_periodo(str(RUTA_VERBALIZACIONES), RUTA_VERBALIZACIONES.name, ciudad_slug, mes, anio)
            log.append(f"✅ Verbalizaciones — {ri['verbalizaciones']['Sub-Categoría'].nunique()} temas (12 subido, calculado del RawData)")

            prog.progress(65, f"TRE: {arch_tre.name}...")
            arch_tre.seek(0)
            rt = procesar_tre(arch_tre, arch_tre.name, df_aps)
            log.append(f"✅ TRE — {len(rt['tre_base'])} contactos · {rt['mes_anio']}")
            if rt['mes_anio'] != ri['mes_anio']:
                raise ValueError(f"El ISC es de {ri['mes_anio']} pero el TRE es de {rt['mes_anio']}. Carga archivos del mismo mes.")
            if rt['estados_desconocidos']:
                log.append(f"⚠️ Estados no reconocidos (tratados como pendientes): {', '.join(rt['estados_desconocidos'])}")

            prog.progress(85, f"Subiendo TRE → {ciudad_slug}/{anio}_{mes}/...")
            for ruta, df in [
                (RUTA_TRE_BASE,    rt['tre_base']),
                (RUTA_TRE_MENSUAL, rt['tre_mensual']),
                (RUTA_TRE_APS,     rt['tre_aps']),
                (RUTA_PENDIENTES,  rt['pendientes']),
                (RUTA_EMBUDO,      rt['embudo']),
            ]:
                guardar_csv(ruta, df)
                upload_periodo(str(ruta), ruta.name, ciudad_slug, mes, anio)
            log.append(f"   → 02,04,06,09,16 subidos a {ciudad_slug}/{anio}_{mes}/")

            if arch_alt is not None:
                prog.progress(92, f"Alertas: {arch_alt.name}...")
                ra = procesar_alertas(arch_alt, arch_alt.name, df_aps, {**ri['id_aps'], **rt['id_aps']})
                if ra['mes_anio'] != ri['mes_anio']:
                    raise ValueError(f"El ISC es de {ri['mes_anio']} pero las Alertas son de {ra['mes_anio']}. Carga archivos del mismo mes.")
                guardar_csv(RUTA_ALERTAS, ra['alertas'])
                upload_periodo(str(RUTA_ALERTAS), RUTA_ALERTAS.name, ciudad_slug, mes, anio)
                al = ra['alertas']
                log.append(f"✅ Alertas — {len(al)} casos ({(al['estado']=='Pendiente').sum()} pendientes, "
                           f"{(al['estado']=='Vencida').sum()} vencidas) → 13 subido a {ciudad_slug}/{anio}_{mes}/")
                if ra['sin_asesor']:
                    log.append(f"⚠️ Alertas sin asesor (ID no encontrado en RawData/Extracto): {', '.join(ra['sin_asesor'])}")
                if ra['estados_desconocidos']:
                    log.append(f"⚠️ Estados de alerta no reconocidos (se muestran como 'Gestionada'): {', '.join(ra['estados_desconocidos'])}")

            prog.progress(100, "✅ Listo")
            st.markdown("<div class='success-box'>" + "<br>".join(log) + "</div>", unsafe_allow_html=True)
            st.balloons()

        except Exception as e:
            prog.progress(100, "❌ Error")
            st.markdown(f"<div class='error-box'>❌ {e}</div>", unsafe_allow_html=True)
            import traceback; st.code(traceback.format_exc())

st.markdown("---")

# ---------------------------------------------------------------------------
# Paso 3 — Verificar (lectura local)
# ---------------------------------------------------------------------------
st.subheader("🔍 Paso 3 — Verificar (último procesado localmente)")
opciones = {
    "01-ISC base":RUTA_ISC_BASE,"02-TRE base":RUTA_TRE_BASE,
    "03-ISC×Dealer":RUTA_ISC_MENSUAL,"04-TRE×Dealer":RUTA_TRE_MENSUAL,
    "05-Atrib×Dealer":RUTA_ATRIBUTOS,"06-Pendientes":RUTA_PENDIENTES,
    "07-Objetivos":RUTA_OBJETIVOS,"08-ISC×APS":RUTA_ISC_APS,
    "09-TRE×APS":RUTA_TRE_APS,"10-Atrib×APS":RUTA_ATRIB_APS,
    "12-Verbalizaciones":RUTA_VERBALIZACIONES,
    "13-Alertas":RUTA_ALERTAS,
    "14-Palancas":RUTA_DRIVERS,
    "15-Comentarios":RUTA_COMENTARIOS,
    "16-Embudo":RUTA_EMBUDO,
}
sel = st.selectbox("CSV:", list(opciones.keys()))
rsel = opciones[sel]

if rsel.exists():
    dv = pd.read_csv(rsel)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Filas", f"{len(dv):,}")
    c2.metric("Cols", len(dv.columns))
    if 'mes_anio' in dv.columns:
        periodos_unicos = dv['mes_anio'].unique()
        c3.metric("Periodos", len(periodos_unicos))
        if 'orden_mes' in dv.columns:
            c4.metric("Período", dv.sort_values('orden_mes')['mes_anio'].iloc[-1])
        else:
            pp = sorted(periodos_unicos)
            c4.metric("Período", pp[-1] if pp else "-")
    if 'ciudad' in dv.columns:
        st.info(f"Ciudades: {', '.join(sorted(dv['ciudad'].unique().astype(str)))}")
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
