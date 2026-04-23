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

# --- CONFIG ---
st.set_page_config(layout="wide", page_title="Radar Workbench", page_icon="🛰️")

# --- SESSION STATE ---
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'search_results' not in st.session_state: st.session_state.search_results = None
if 'lat' not in st.session_state: st.session_state.lat, st.session_state.lon = 42.041, 3.126

# --- HELPERS ---
def get_img_url(np_img):
    img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
    buffered = BytesIO()
    Image.fromarray(img_data).save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

def create_dl(data, filename, lat, lon, r_km, key, label="📥"):
    off = (r_km / 111.32) / 2
    if len(data.shape) == 3: data = data[:,:,0]
    tf = from_bounds(lon-off, lat-off, lon+off, lat+off, data.shape[1], data.shape[0])
    with MemoryFile() as mem:
        with mem.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                      dtype='float32', crs='EPSG:4326', transform=tf) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button(label, mem.read(), filename, "image/tiff", key=key)

# --- SIDEBAR (Global Controls) ---
with st.sidebar:
    st.header("🛰️ Global Settings")
    cid = st.text_input("Client ID", type="password")
    sec = st.text_input("Client Secret", type="password")
    city = st.text_input("Location", "Torroella de Montgrí")
    r_km = st.slider("Radius (km)", 1, 20, 8)
    gain = st.slider("Brightness Gain", 0.5, 10.0, 3.0)
    center_map = st.checkbox("Auto-Center on Search", value=True)
    search_btn = st.button("🔍 FETCH METADATA", use_container_width=True)

# --- APP LOGIC ---
if cid and sec:
    config = SHConfig(sh_client_id=cid, sh_client_secret=sec)
    
    if search_btn:
        loc = Nominatim(user_agent="radar_app").geocode(city)
        if loc: st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
        bbox = BBox(bbox=[st.session_state.lon-(r_km/222), st.session_state.lat-(r_km/222), 
                          st.session_state.lon+(r_km/222), st.session_state.lat+(r_km/222)], crs=CRS.WGS84)
        st.session_state.search_results = list(SentinelHubCatalog(config=config).search(DataCollection.SENTINEL1_IW, bbox=bbox, time=("2024-01-01", "2026-12-31")))

    tab1, tab2, tab3 = st.tabs(["🖼️ Image Selection", "🎨 Color Overlay", "🌊 Flood Detection"])

    # Bounding Box for overlays
    off = (r_km / 111.32) / 2
    bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]

    # --- TAB 1: SELECTION ---
    with tab1:
        if st.session_state.search_results:
            res = st.session_state.search_results
            dates = [f"{i}: {r['properties']['datetime'][:16]}" for i, r in enumerate(res)]
            selected = st.multiselect("Select Images to Load", dates)
            
            if st.button("🚀 Render Layers"):
                for s in selected:
                    dt = res[int(s.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript="//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}",
                                            input_data=[SentinelHubRequest.input_data(DataCollection.SENTINEL1_IW, (dt, dt))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                                            bbox=BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84),
                                            size=(800, 800), config=config)
                    st.session_state.image_cache[dt] = req.get_data()[0]

            m1 = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
            # Layer Selection via Checkboxes
            for k in st.session_state.image_cache:
                if st.checkbox(f"Show {k[:16]}", value=True, key=f"t1_{k}"):
                    folium.raster_layers.ImageOverlay(get_img_url(st.session_state.image_cache[k]*gain), bnds, name=k).add_to(m1)
            st_folium(m1, height=600, use_container_width=True)

    # --- TAB 2: COLOR OVERLAY ---
    with tab2:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            c_m1, c_m2, c_m3 = st.columns([2,2,1])
            d1 = c_m1.selectbox("Baseline", keys, index=0)
            d2 = c_m2.selectbox("Crisis", keys, index=1)
            # 15 Industry standard color maps
            cmap_list = ["GnBu", "YlGnBu", "PuBu", "Blues", "winter", "viridis", "magma", "plasma", "inferno", "Spectral", "coolwarm", "RdBu", "bone", "pink", "copper"]
            cmap_name = c_m3.selectbox("Radar Palette", cmap_list)
            
            def apply_c(data):
                db = 10 * np.log10(np.squeeze(data) + 1e-10)
                norm = np.clip((db - (-25)) / ((-5) - (-25)), 0, 1)
                return get_img_url(plt.get_cmap(cmap_name)(norm))

            lay1 = st.checkbox(f"Show Baseline ({d1[:10]})", value=False)
            lay2 = st.checkbox(f"Show Crisis ({d2[:10]})", value=True)
            
            m2 = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
            if lay1: folium.raster_layers.ImageOverlay(apply_c(st.session_state.image_cache[d1]), bnds).add_to(m2)
            if lay2: folium.raster_layers.ImageOverlay(apply_c(st.session_state.image_cache[d2]), bnds).add_to(m2)
            st_folium(m2, height=650, use_container_width=True)

    # --- TAB 3: FLOOD DETECTION ---
    with tab3:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            ctrl1, ctrl2, ctrl3 = st.columns(3)
            b_dt = ctrl1.selectbox("Base (Dry)", keys, index=0, key="f_b")
            w_dt = ctrl2.selectbox("Crisis (Wet)", keys, index=1, key="f_w")
            sens = ctrl3.slider("Sensitivity (dB Drop)", -15.0, -2.0, -6.0)
            
            # Area Calculation
            b_raw, w_raw = np.squeeze(st.session_state.image_cache[b_dt]), np.squeeze(st.session_state.image_cache[w_dt])
            mask = ((10 * np.log10(w_raw + 1e-10) - 10 * np.log10(b_raw + 1e-10)) < sens).astype(np.uint8)
            px_m = (r_km * 2000) / 800
            ha = (np.sum(mask) * (px_m**2)) / 10000

            # Dashboard Header inside Map Frame
            st.markdown(f"### 📍 Flooded Area: **{ha:.2f} Ha** | **{ha/100:.4f} km²**")
            
            col_ras, col_vec, col_base = st.columns(3)
            show_ras = col_ras.checkbox("Show Raster Mask", value=True)
            show_vec = col_vec.checkbox("Show Vector Polygons", value=False)
            show_bg = col_base.checkbox("Show Radar Background", value=True)

            m3 = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
            if show_bg:
                folium.raster_layers.ImageOverlay(get_img_url(w_raw*gain), bnds, opacity=0.6).add_to(m3)
            
            if show_ras:
                m_rgb = np.zeros((*mask.shape, 4))
                m_rgb[mask == 1] = [0, 1, 1, 0.7] # Cyan
                folium.raster_layers.ImageOverlay(get_img_url(m_rgb), bnds).add_to(m3)
            
            if show_vec:
                tf = from_bounds(st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off, mask.shape[1], mask.shape[0])
                shps = list(features.shapes(mask.astype('int16'), mask=(mask > 0), transform=tf))
                gj = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": g} for g, v in shps]}
                folium.GeoJson(gj, style_function=lambda x: {'fillColor': '#ff0000', 'color': '#ff0000'}).add_to(m3)

            st_folium(m3, height=600, use_container_width=True)

            # Integrated Action Bar
            a1, a2, a3 = st.columns(3)
            with a1: create_dl(mask, "flood_raster.tif", st.session_state.lat, st.session_state.lon, r_km, "dl_f_r", "💾 Download Raster (.tif)")
            with a2:
                tf = from_bounds(st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off, mask.shape[1], mask.shape[0])
                shps = list(features.shapes(mask.astype('int16'), mask=(mask > 0), transform=tf))
                gj_str = json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": g} for g, v in shps]})
                st.download_button("📐 Download Vector (.json)", gj_str, "flood.geojson", use_container_width=True)
            with a3:
                create_dl(st.session_state.image_cache[w_dt], "radar_raw.tif", st.session_state.lat, st.session_state.lon, r_km, "dl_raw", "🛰️ Download Raw Radar")
