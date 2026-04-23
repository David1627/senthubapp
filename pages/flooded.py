import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from sentinelhub import (SHConfig, SentinelHubRequest, DataCollection, MimeType, 
                         BBox, CRS, SentinelHubCatalog)
from geopy.geocoders import Nominatim
import datetime
import folium
from streamlit_folium import st_folium
import base64
import json
from io import BytesIO
from PIL import Image
import uuid
from rasterio import features
from rasterio.transform import from_bounds
from rasterio.io import MemoryFile

# --- CONFIG & THEME ---
st.set_page_config(layout="wide", page_title="S1 Radar Command Center", page_icon="📡")

# Custom CSS for the "Dashboard" look
st.markdown("""
    <style>
    [data-testid="stMetricValue"] { font-size: 1.8rem; color: #00FFFF; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { background-color: #1e1e1e; border-radius: 4px 4px 0 0; padding: 10px; }
    .main { background-color: #0e1117; }
    </style>
    """, unsafe_allow_html=True)

# --- SESSION STATE ---
for key in ['image_cache', 'search_results', 'lat', 'lon']:
    if key not in st.session_state:
        if key == 'lat': st.session_state[key] = 42.041
        elif key == 'lon': st.session_state[key] = 3.126
        else: st.session_state[key] = {} if key == 'image_cache' else None

if 'app_uuid' not in st.session_state: st.session_state.app_uuid = str(uuid.uuid4())[:8]

# --- HELPERS ---
def get_img_url(np_img):
    img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
    buffered = BytesIO()
    Image.fromarray(img_data).save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

def create_geotiff_download(data, filename, lat, lon, r_km, key, label="📥 TIFF"):
    off = (r_km / 111.32) / 2
    if len(data.shape) == 3: data = data[:,:,0]
    tf = from_bounds(lon-off, lat-off, lon+off, lat+off, data.shape[1], data.shape[0])
    with MemoryFile() as mem:
        with mem.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                      dtype='float32', crs='EPSG:4326', transform=tf) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button(label, mem.read(), filename, "image/tiff", key=key)

# --- HEADER ---
st.markdown("<h2 style='text-align: center; color: #00FFFF; margin-bottom: 20px;'>📡 S1 RADAR ANALYTICS COMMAND CENTER</h2>", unsafe_allow_html=True)

# --- SIDEBAR CONTROLS ---
with st.sidebar:
    st.title("🛰️ System Controls")
    cid = st.text_input("Client ID", type="password")
    sec = st.text_input("Client Secret", type="password")
    
    with st.expander("🌍 AOI Settings", expanded=True):
        city = st.text_input("Target Location", "Torroella de Montgrí")
        r_km = st.slider("Radius (km)", 1, 20, 8)
        gain = st.slider("Signal Gain", 0.5, 10.0, 3.0)
        lock_view = st.checkbox("Lock Map Extent", value=True)
    
    search_btn = st.button("🛰️ SCAN ARCHIVE", type="primary", use_container_width=True)

# --- MAIN LAYOUT ---
l_col, m_col, r_col = st.columns([2, 6, 2])

# --- LOGIC & FETCHING ---
if cid and sec:
    config = SHConfig(sh_client_id=cid, sh_client_secret=sec)
    
    if search_btn:
        geolocator = Nominatim(user_agent=f"f_{st.session_state.app_uuid}")
        loc = geolocator.geocode(city, timeout=10)
        if loc: st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
        
        catalog = SentinelHubCatalog(config=config)
        off = (r_km / 111.32) / 2
        bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, 
                          st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
        st.session_state.search_results = list(catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=("2024-01-01", "2026-12-31")))

    # 1. LEFT COLUMN: Data List
    with l_col:
        st.markdown("### 📋 Image Catalog")
        if st.session_state.search_results:
            res = st.session_state.search_results
            date_opts = [f"{i}: {r['properties']['datetime'][:16]}" for i, r in enumerate(res)]
            picks = st.multiselect("Active Layers:", date_opts, default=date_opts[:2])
            
            if st.button("⚡ PROCESS LAYERS", use_container_width=True):
                off = (r_km / 111.32) / 2
                bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, 
                                  st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
                ev = "//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}"
                for p in picks:
                    idx = int(p.split(":")[0])
                    dt = res[idx]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=ev, input_data=[SentinelHubRequest.input_data(DataCollection.SENTINEL1_IW, (dt, dt))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox, size=(600, 600), config=config)
                    st.session_state.image_cache[dt] = req.get_data()[0]
            
            for k in list(st.session_state.image_cache.keys()):
                st.info(f"Loaded: {k[:16]}")
        else:
            st.caption("No data scanned yet.")

    # 2. CENTER COLUMN: The Map Workbench
    with m_col:
        tab_lab, tab_flood = st.tabs(["🧪 Sync Color Lab", "🌊 Flood Detection"])
        
        with tab_lab:
            if len(st.session_state.image_cache) >= 2:
                keys = list(st.session_state.image_cache.keys())
                h1, h2, h3, h4 = st.columns([1,1,1,1])
                d1 = h1.selectbox("L-Panel", keys, index=0)
                d2 = h2.selectbox("R-Panel", keys, index=1)
                cmap = h3.selectbox("Palette", ["GnBu", "PuBu", "magma", "bone"])
                show_r = h4.checkbox("Overlay", value=True)
                
                # Floating Download Bar
                dl1, dl2 = st.columns(2)
                with dl1: create_geotiff_download(st.session_state.image_cache[d1], "L.tif", st.session_state.lat, st.session_state.lon, r_km, "dl_l")
                with dl2: create_geotiff_download(st.session_state.image_cache[d2], "R.tif", st.session_state.lat, st.session_state.lon, r_km, "dl_r")

                m_l, space, m_r = st.columns([10, 1, 10])
                off = (r_km / 111.32) / 2
                bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
                
                def apply_c(data):
                    db = 10 * np.log10(np.squeeze(data) + 1e-10)
                    norm = np.clip((db - (-25)) / ((-5) - (-25)), 0, 1)
                    return get_img_url(plt.get_cmap(cmap)(norm))

                with m_l:
                    map1 = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
                    if show_r: folium.raster_layers.ImageOverlay(apply_c(st.session_state.image_cache[d1]), bnds).add_to(map1)
                    st_folium(map1, height=500, key="m1", center=[st.session_state.lat, st.session_state.lon] if lock_view else None)
                
                with m_r:
                    map2 = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
                    if show_r: folium.raster_layers.ImageOverlay(apply_c(st.session_state.image_cache[d2]), bnds).add_to(map2)
                    st_folium(map2, height=500, key="m2", center=[st.session_state.lat, st.session_state.lon] if lock_view else None)

        with tab_flood:
            if len(st.session_state.image_cache) >= 2:
                keys = list(st.session_state.image_cache.keys())
                f_b, f_w = st.columns(2)
                d_dry = f_b.selectbox("Dry Reference", keys, index=0)
                d_wet = f_w.selectbox("Crisis Date", keys, index=1)
                
                sens = st.slider("Sensitivity (dB Drop)", -15.0, -2.0, -6.0)
                
                b_raw, w_raw = np.squeeze(st.session_state.image_cache[d_dry]), np.squeeze(st.session_state.image_cache[d_wet])
                mask = ((10 * np.log10(w_raw + 1e-10) - 10 * np.log10(b_raw + 1e-10)) < sens).astype(np.uint8)
                
                m_flood = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
                folium.raster_layers.ImageOverlay(get_img_url(np.clip(w_raw*gain, 0, 1)), bnds, opacity=0.5).add_to(m_flood)
                
                m_rgb = np.zeros((*mask.shape, 4))
                m_rgb[mask == 1] = [0, 1, 1, 0.8] # Cyan flood
                folium.raster_layers.ImageOverlay(get_img_url(m_rgb), bnds).add_to(m_flood)
                st_folium(m_flood, height=550, use_container_width=True, center=[st.session_state.lat, st.session_state.lon] if lock_view else None)

    # 3. RIGHT COLUMN: Analytics & Legend
    with r_col:
        st.markdown("### 📊 Metrics")
        if 'mask' in locals():
            px_m = (r_km * 2000) / 600
            ha = (np.sum(mask) * (px_m**2)) / 10000
            st.metric("Flood Area", f"{ha:.1f} Ha")
            st.metric("Flood Area", f"{ha/100:.3f} km²")
            
            create_geotiff_download(mask, "mask.tif", st.session_state.lat, st.session_state.lon, r_km, "main_dl")
            
            st.markdown("---")
            st.caption("Backscatter Legend (dB)")
            fig_l, ax_l = plt.subplots(figsize=(0.6, 6))
            norm = plt.Normalize(vmin=-25, vmax=-5)
            plt.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap if 'cmap' in locals() else 'GnBu'), cax=ax_l)
            st.pyplot(fig_l)
