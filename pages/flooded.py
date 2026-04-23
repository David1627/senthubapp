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

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="Radar Workbench Pro", page_icon="🛰️")

# --- INITIALIZE SESSION STATE ---
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'search_results' not in st.session_state: st.session_state.search_results = None
if 'lat' not in st.session_state: st.session_state.lat, st.session_state.lon = 42.041, 3.126
if 'map_center' not in st.session_state: st.session_state.map_center = [42.041, 3.126]

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
        return st.download_button(label, mem.read(), filename, "image/tiff", key=key, use_container_width=True)

# --- SIDEBAR ---
with st.sidebar:
    st.header("🛰️ Radar Archive Search")
    cid = st.text_input("Client ID", type="password")
    sec = st.text_input("Client Secret", type="password")
    city = st.text_input("Location", "Torroella de Montgrí")
    r_km = st.slider("Radius (km)", 1, 25, 8)
    gain = st.slider("Brightness", 0.5, 8.0, 3.0)
    
    # RESTORED DATE PART
    today = datetime.date.today()
    date_range = st.date_input("Search Archive Window", [today - datetime.timedelta(days=90), today])
    
    search_btn = st.button("🔍 SCAN S1 ARCHIVE", use_container_width=True, type="primary")

# --- CORE SEARCH LOGIC ---
if cid and sec:
    config = SHConfig(sh_client_id=cid, sh_client_secret=sec)
    
    if search_btn:
        loc = Nominatim(user_agent="radar_workbench").geocode(city)
        if loc:
            st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
            st.session_state.map_center = [loc.latitude, loc.longitude] # Only update center on search
        
        catalog = SentinelHubCatalog(config=config)
        off = (r_km / 111.32) / 2
        bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, 
                          st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
        st.session_state.search_results = list(catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(date_range[0]), str(date_range[1]))))
        st.session_state.image_cache = {} # Clear old images

    tab1, tab2, tab3 = st.tabs(["🖼️ Image Archive", "🧪 Color Lab", "🌊 Flood Analyst"])

    # Map Bounding Box
    off = (r_km / 111.32) / 2
    bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]

    # --- TAB 1: DATE SELECTION & RENDER ---
    with tab1:
        if st.session_state.search_results:
            res = st.session_state.search_results
            dates = [f"{i}: {r['properties']['datetime'][:16]}" for i, r in enumerate(res)]
            selected = st.multiselect("Pick acquisitions to process:", dates)
            
            if st.button("🚀 Render Selected Images"):
                with st.spinner("Processing SAR backscatter..."):
                    for s in selected:
                        dt = res[int(s.split(":")[0])]['properties']['datetime']
                        req = SentinelHubRequest(
                            evalscript="//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}",
                            input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(dt, dt))],
                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                            bbox=BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84),
                            size=(700, 700), config=config)
                        st.session_state.image_cache[dt] = req.get_data()[0]

            m1 = folium.Map(location=st.session_state.map_center, zoom_start=13)
            # Checkboxes to toggle layered images
            for k in st.session_state.image_cache:
                if st.checkbox(f"Toggle {k[:16]}", value=True, key=f"t1_{k}"):
                    folium.raster_layers.ImageOverlay(get_img_url(st.session_state.image_cache[k]*gain), bnds, name=k).add_to(m1)
            st_folium(m1, height=600, use_container_width=True, key="map_tab1")

    # --- TAB 2: COLOR LAB ---
    with tab2:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            c1, c2, c3 = st.columns([2,2,1])
            d1 = c1.selectbox("Base Layer", keys, index=0)
            d2 = c2.selectbox("Crisis Layer", keys, index=1)
            cmaps = ["GnBu", "YlGnBu", "PuBu", "Blues", "winter", "viridis", "magma", "plasma", "inferno", "Spectral", "coolwarm", "RdBu", "bone", "pink", "copper"]
            cmap_name = c3.selectbox("Palette", cmaps, index=5)
            
            def apply_c(data):
                db = 10 * np.log10(np.squeeze(data) + 1e-10)
                norm = np.clip((db - (-25)) / ((-5) - (-25)), 0, 1)
                return get_img_url(plt.get_cmap(cmap_name)(norm))

            # Overlay controls
            l1, l2, spacer = st.columns([1,1,2])
            show_base = l1.checkbox("Show Base", value=False)
            show_crisis = l2.checkbox("Show Crisis", value=True)
            
            m2 = folium.Map(location=st.session_state.map_center, zoom_start=13)
            if show_base: folium.raster_layers.ImageOverlay(apply_c(st.session_state.image_cache[d1]), bnds).add_to(m2)
            if show_crisis: folium.raster_layers.ImageOverlay(apply_c(st.session_state.image_cache[d2]), bnds).add_to(m2)
            
            # LONG VERTICAL LEGEND
            col_m, col_l = st.columns([15, 1])
            with col_m: st_folium(m2, height=600, use_container_width=True, key="map_tab2")
            with col_l:
                fig, ax = plt.subplots(figsize=(0.5, 9))
                norm = plt.Normalize(vmin=-25, vmax=-5)
                plt.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap_name), cax=ax)
                st.pyplot(fig)

    # --- TAB 3: FLOOD ANALYST ---
    with tab3:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            f1, f2, f3 = st.columns(3)
            b_dt, w_dt = f1.selectbox("Dry Ref", keys, index=0, key="fb"), f2.selectbox("Wet Ref", keys, index=1, key="fw")
            sens = f3.slider("Sensitivity", -15.0, -2.0, -6.0)
            
            b_raw, w_raw = np.squeeze(st.session_state.image_cache[b_dt]), np.squeeze(st.session_state.image_cache[w_dt])
            mask = ((10 * np.log10(w_raw + 1e-10) - 10 * np.log10(b_raw + 1e-10)) < sens).astype(np.uint8)
            px_m = (r_km * 2000) / 700
            ha = (np.sum(mask) * (px_m**2)) / 10000

            # FLOODED AREA DASHBOARD
            st.markdown(f"### 🌊 Flooded Area: **{ha:.2f} Ha** | **{ha/100:.4f} km²**")
            
            # Map Controls & Download Buttons INSIDE the map context
            ctrl1, ctrl2, ctrl3, dl1, dl2 = st.columns([1,1,1,1,1])
            v_ras = ctrl1.checkbox("Raster Mask", value=True)
            v_vec = ctrl2.checkbox("Vector Shapes", value=False)
            v_sar = ctrl3.checkbox("Radar Context", value=True)
            
            with dl1: create_dl(mask, "flood.tif", st.session_state.lat, st.session_state.lon, r_km, "dl1", "📥 Raster")
            with dl2:
                tf = from_bounds(st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off, mask.shape[1], mask.shape[0])
                shps = list(features.shapes(mask.astype('int16'), mask=(mask > 0), transform=tf))
                gj = json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": g} for g, v in shps]})
                st.download_button("📥 Vector", gj, "flood.geojson", use_container_width=True)

            m3 = folium.Map(location=st.session_state.map_center, zoom_start=14)
            if v_sar: folium.raster_layers.ImageOverlay(get_img_url(w_raw*gain), bnds, opacity=0.5).add_to(m3)
            if v_ras:
                m_rgb = np.zeros((*mask.shape, 4))
                m_rgb[mask == 1] = [1, 0, 0, 0.7] # Red flood
                folium.raster_layers.ImageOverlay(get_img_url(m_rgb), bnds).add_to(m3)
            if v_vec:
                folium.GeoJson(json.loads(gj), style_function=lambda x: {'fillColor': '#00FFFF', 'color': '#00FFFF'}).add_to(m3)
            
            st_folium(m3, height=600, use_container_width=True, key="map_tab3")
