import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from sentinelhub import (SHConfig, SentinelHubRequest, DataCollection, MimeType, 
                         BBox, CRS, SentinelHubCatalog)
from geopy.geocoders import Nominatim
import datetime
import folium
from folium.plugins import DualMap
from streamlit_folium import st_folium
import base64
import json
from io import BytesIO
from PIL import Image
import uuid
import rasterio
from rasterio import features
from rasterio.transform import from_bounds
from rasterio.io import MemoryFile

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Radar Master Pro", page_icon="📡")

# --- INITIALIZE SESSION STATE ---
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'app_uuid' not in st.session_state: st.session_state.app_uuid = str(uuid.uuid4())[:8]
if 'lat' not in st.session_state: st.session_state.lat, st.session_state.lon = 42.041, 3.126

# --- HELPERS ---
def get_img_url(np_img):
    img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
    buffered = BytesIO()
    Image.fromarray(img_data).save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

def create_geotiff_download(data, filename, lat, lon, r_km, key, label="📥"):
    off = (r_km / 111.32) / 2
    if len(data.shape) == 3: data = data[:,:,0]
    tf = from_bounds(lon-off, lat-off, lon+off, lat+off, data.shape[1], data.shape[0])
    with MemoryFile() as mem:
        with mem.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                      dtype='float32', crs='EPSG:4326', transform=tf) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button(label, mem.read(), filename, "image/tiff", key=key, use_container_width=True)

# --- SIDEBAR ---
st.sidebar.title("🌊 Radar Master Pro")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

with st.sidebar.expander("📍 Configuration", expanded=True):
    city = st.text_input("City Search", "Torroella de Montgrí, Spain")
    r_km = st.slider("Radius (km)", 1, 30, 8)
    center_map = st.checkbox("Lock View to Center", value=True)
    gain = st.slider("Radar Gain", 0.5, 10.0, 3.0)

btn_search = st.sidebar.button("🔍 FETCH DATA", type="primary", use_container_width=True)

# --- CORE LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        geolocator = Nominatim(user_agent=f"f_{st.session_state.app_uuid}")
        loc = geolocator.geocode(city, timeout=10)
        if loc: st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
        st.session_state.image_cache = {}
        catalog = SentinelHubCatalog(config=config)
        off = (r_km / 111.32) / 2
        bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, 
                          st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
        st.session_state.search_results = list(catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=("2024-01-01", "2026-12-31")))

    tab1, tab2, tab3 = st.tabs(["📊 Catalog View", "🧪 Sync Color Lab", "🌊 Flood Analyst"])

    # --- TAB 2: COLOR LAB ---
    with tab2:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            
            # Dashboard Header
            c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
            d1 = c1.selectbox("Baseline (L)", keys, index=0)
            d2 = c2.selectbox("Analysis (R)", keys, index=1)
            cmap_name = c3.selectbox("Colormap", ["GnBu", "YlGnBu", "Blues", "viridis", "magma", "bone"])
            show_radar_lab = c4.checkbox("Show Radar", value=True)

            # Map Row with Vertical Spacing
            m_col_l, spacer, m_col_r, leg_col = st.columns([10, 1, 10, 1])
            
            def apply_c(data):
                db = 10 * np.log10(np.squeeze(data) + 1e-10)
                norm = np.clip((db - (-25)) / ((-5) - (-25)), 0, 1)
                return get_img_url(plt.get_cmap(cmap_name)(norm))

            off = (r_km / 111.32) / 2
            bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]

            with m_col_l:
                st.markdown("##### Left Panel")
                create_geotiff_download(st.session_state.image_cache[d1], "left.tif", st.session_state.lat, st.session_state.lon, r_km, "l_lab_d")
                m1 = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
                if show_radar_lab: folium.raster_layers.ImageOverlay(apply_c(st.session_state.image_cache[d1]), bnds).add_to(m1)
                st_folium(m1, height=500, key="lab_m1", center=[st.session_state.lat, st.session_state.lon] if center_map else None)

            with m_col_r:
                st.markdown("##### Right Panel")
                create_geotiff_download(st.session_state.image_cache[d2], "right.tif", st.session_state.lat, st.session_state.lon, r_km, "r_lab_d")
                m2 = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
                if show_radar_lab: folium.raster_layers.ImageOverlay(apply_c(st.session_state.image_cache[d2]), bnds).add_to(m2)
                st_folium(m2, height=500, key="lab_m2", center=[st.session_state.lat, st.session_state.lon] if center_map else None)

            with leg_col:
                st.write("") # Spacer
                fig_v, ax_v = plt.subplots(figsize=(0.4, 8)) # Taller legend
                norm = plt.Normalize(vmin=-25, vmax=-5)
                plt.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap_name), cax=ax_v, orientation='vertical')
                ax_v.set_ylabel('dB Strength', fontsize=8)
                st.pyplot(fig_v)

    # --- TAB 3: FLOOD ANALYST ---
    with tab3:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            
            # Setup Row
            cf1, cf2, cf3, cf4 = st.columns([2, 2, 1, 1])
            b_dt, w_dt = cf1.selectbox("Dry Date", keys, index=0, key="f1"), cf2.selectbox("Wet Date", keys, index=1, key="f2")
            f_col = cf3.color_picker("Flood Hex", "#00FFFF")
            show_flood = cf4.checkbox("Show Mask", value=True)

            # Calculation & Dashboard Buttons
            b_raw, w_raw = np.squeeze(st.session_state.image_cache[b_dt]), np.squeeze(st.session_state.image_cache[w_dt])
            sens = st.slider("Threshold (dB Drop)", -15.0, -2.0, -6.0)
            mask = ((10 * np.log10(w_raw + 1e-10) - 10 * np.log10(b_raw + 1e-10)) < sens).astype(np.uint8)
            
            px_m = (r_km * 2000) / 600
            area_ha = (np.sum(mask) * (px_m**2)) / 10000
            
            # Action Dashboard (Integrated atop map)
            d_act1, d_act2, d_met1, d_met2 = st.columns([1,1,2,2])
            with d_act1: create_geotiff_download(mask, "mask.tif", st.session_state.lat, st.session_state.lon, r_km, "f_t_btn")
            with d_act2:
                tf = from_bounds(st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off, mask.shape[1], mask.shape[0])
                shps = list(features.shapes(mask.astype('int16'), mask=(mask > 0), transform=tf))
                gj = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": g} for g, v in shps]}
                st.download_button("📐 JSON", json.dumps(gj), "flood.geojson", "application/json", use_container_width=True)
            d_met1.metric("Area (Ha)", f"{area_ha:.2f}")
            d_met2.metric("Area (km²)", f"{area_ha/100:.4f}")

            # Main Flood Map
            mf = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
            if show_flood:
                folium.raster_layers.ImageOverlay(get_img_url(np.clip(w_raw*gain, 0, 1)), bnds, opacity=0.4).add_to(mf)
                m_rgb = np.zeros((*mask.shape, 4))
                h = f_col.lstrip('#'); rgb = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
                m_rgb[mask == 1] = [*rgb, 0.8]
                folium.raster_layers.ImageOverlay(get_img_url(m_rgb), bnds).add_to(mf)
            
            st_folium(mf, height=600, use_container_width=True, center=[st.session_state.lat, st.session_state.lon] if center_map else None)
