import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import geopandas as gpd
from sentinelhub import (SHConfig, SentinelHubRequest, DataCollection, MimeType, 
                         BBox, CRS, SentinelHubCatalog)
from geopy.geocoders import Nominatim
import datetime
import folium
from streamlit_folium import st_folium
import base64
from io import BytesIO
from PIL import Image
import rasterio
from rasterio import features
from rasterio.transform import from_bounds
import osmnx as ox
from shapely.geometry import shape

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Flood Analyzer", page_icon="🛰️")

# --- 1. INITIALIZE SESSION STATE (Keep everything synced) ---
if 'lat' not in st.session_state: st.session_state.lat = None
if 'lon' not in st.session_state: st.session_state.lon = None
if 'img_cache' not in st.session_state: st.session_state.img_cache = {}
if 'search_results' not in st.session_state: st.session_state.search_results = None
if 'flood_sens' not in st.session_state: st.session_state.flood_sens = -6.0

# --- HELPER: ENCODING ---
def get_image_url(np_img):
    try:
        img_8bit = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_8bit)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
    except: return ""

# --- SIDEBAR: GLOBAL CONTROLS ---
with st.sidebar:
    st.header("🔑 1. Sentinel Hub Auth")
    c_id = st.text_input("Client ID", value="", placeholder="Paste ID here...")
    c_sec = st.text_input("Client Secret", value="", type="password", placeholder="Paste Secret here...")
    
    st.markdown("---")
    st.header("📍 2. Search & Location")
    
    # City Search logic
    city_q = st.text_input("Search City", value="", placeholder="e.g. Valencia, Spain")
    if st.button("🔍 Resolve Location"):
        if city_q:
            try:
                loc = Nominatim(user_agent="flood_explorer").geocode(city_q)
                if loc:
                    st.session_state.lat = loc.latitude
                    st.session_state.lon = loc.longitude
                    st.success(f"Found: {loc.latitude:.4f}, {loc.longitude:.4f}")
            except: st.error("Geocoding service timed out.")

    # Manual Coords (Empty by default, synced with City search)
    st.session_state.lat = st.number_input("Latitude (X)", value=st.session_state.lat if st.session_state.lat else 0.0, format="%.6f")
    st.session_state.lon = st.number_input("Longitude (Y)", value=st.session_state.lon if st.session_state.lon else 0.0, format="%.6f")
    
    radius = st.slider("Radius (km)", 1, 20, 5)
    date_range = st.date_input("Analysis Dates", [])
    
    st.markdown("---")
    btn_run = st.button("🚀 FETCH RADAR DATA", type="primary", use_container_width=True)

# --- CORE ENGINE: AUTH & FETCH ---
if btn_run:
    if not (c_id and c_sec):
        st.warning("Please enter credentials.")
    elif st.session_state.lat == 0.0:
        st.warning("Please search for a city or enter coordinates.")
    elif len(date_range) < 2:
        st.warning("Please select a date range (Start and End).")
    else:
        try:
            config = SHConfig(sh_client_id=c_id, sh_client_secret=c_sec)
            cat = SentinelHubCatalog(config=config)
            
            off = (radius / 111.32) / 2
            bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
            
            with st.spinner("Accessing Sentinel Hub Catalog..."):
                search = cat.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(date_range[0]), str(date_range[1])))
                st.session_state.search_results = list(search)
                st.session_state.img_cache = {} # Clear old images
                st.success(f"Found {len(st.session_state.search_results)} radar captures.")
                
        except Exception as e:
            if "InvalidClientError" in str(e):
                st.error("❌ **Authentication Failed**: The Client ID or Secret is incorrect. Check your Sentinel Hub dashboard for typos.")
            else:
                st.error(f"Error: {e}")

# --- TABS: CONTENT PERSISTENCE ---
tab1, tab2, tab3 = st.tabs(["🗺️ Dashboard", "🧪 Radar Lab", "🚨 Flood Impact"])

with tab1:
    if st.session_state.search_results:
        res = st.session_state.search_results
        opts = [f"{i}: {r['properties']['datetime'][:10]}" for i,r in enumerate(res)]
        picks = st.multiselect("Select captures to render:", opts)
        
        if st.button("🖼️ Render Selected"):
            config = SHConfig(sh_client_id=c_id, sh_client_secret=c_sec)
            off = (radius / 111.32) / 2
            bbox_obj = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
            es = "//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}"
            
            for p in picks:
                d = res[int(p.split(":")[0])]['properties']['datetime']
                req = SentinelHubRequest(evalscript=es, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(d,d))],
                                       responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(600, 600), config=config)
                st.session_state.img_cache[d] = req.get_data()[0]

        if st.session_state.img_cache:
            cols = st.columns(len(st.session_state.img_cache))
            for i, (dk, data) in enumerate(st.session_state.img_cache.items()):
                with cols[i]:
                    st.caption(f"Date: {dk[:10]}")
                    m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
                    off = (radius / 111.32) / 2
                    bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
                    folium.raster_layers.ImageOverlay(get_image_url(data*3), bounds=bnds).add_to(m)
                    st_folium(m, height=300, key=f"map_{dk}")

with tab2:
    if len(st.session_state.img_cache) >= 2:
        st.subheader("Comparison Analysis")
        d_keys = list(st.session_state.img_cache.keys())
        c1, c2 = st.columns(2)
        v1 = 10*np.log10(st.session_state.img_cache[d_keys[0]][:,:,0]+1e-10)
        v2 = 10*np.log10(st.session_state.img_cache[d_keys[1]][:,:,0]+1e-10)
        
        fig, ax = plt.subplots(1,2)
        ax[0].imshow(v1, cmap='gray', vmin=-25, vmax=-5); ax[0].axis('off'); ax[0].set_title(d_keys[0][:10])
        ax[1].imshow(v2, cmap='gray', vmin=-25, vmax=-5); ax[1].axis('off'); ax[1].set_title(d_keys[1][:10])
        st.pyplot(fig)

with tab3:
    if len(st.session_state.img_cache) >= 2:
        st.subheader("🚨 Flood Detection Settings")
        
        # Options inside the tab that don't reset
        st.session_state.flood_sens = st.slider("Detection Sensitivity (dB Change)", -15.0, -2.0, st.session_state.flood_sens)
        
        d_keys = list(st.session_state.img_cache.keys())
        diff = (10*np.log10(st.session_state.img_cache[d_keys[1]]+1e-10)) - (10*np.log10(st.session_state.img_cache[d_keys[0]]+1e-10))
        mask = (diff < st.session_state.flood_sens).astype(np.uint8)
        
        m_flood = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
        off = (radius / 111.32) / 2
        bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
        
        # Background
        folium.raster_layers.ImageOverlay(get_image_url(st.session_state.img_cache[d_keys[1]]*3), bounds=bnds, opacity=0.4).add_to(m_flood)
        
        # Flood Layer (Red)
        f_overlay = np.zeros((600,600,4))
        f_overlay[mask[:,:,0]==1] = [1, 0, 0, 0.6]
        folium.raster_layers.ImageOverlay(get_image_url(f_overlay), bounds=bnds).add_to(m_flood)
        
        st_folium(m_flood, height=500, width=1000)
    else:
        st.info("Render at least 2 radar dates in the Dashboard to calculate flood impact.")
