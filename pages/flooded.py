import streamlit as st
import pandas as pd
import numpy as np
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
import rasterio
from rasterio import features
from rasterio.transform import from_bounds
from rasterio.io import MemoryFile

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Fast Explorer", page_icon="📡")

# --- INITIALIZE SESSION STATE ---
if 'search_results' not in st.session_state: st.session_state.search_results = None
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'app_uuid' not in st.session_state: st.session_state.app_uuid = str(uuid.uuid4())[:8]
if 'lat' not in st.session_state: st.session_state.lat, st.session_state.lon = 42.041, 3.126

# --- COMPACT HELPER FUNCTIONS ---
def get_img_url(np_img):
    img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
    buffered = BytesIO()
    Image.fromarray(img_data).save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

def create_geotiff_download(data, filename, lat, lon, r_km, key):
    off = (r_km / 111.32) / 2
    tf = from_bounds(lon-off, lat-off, lon+off, lat+off, data.shape[1], data.shape[0])
    with MemoryFile() as mem:
        with mem.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                      dtype='float32', crs='EPSG:4326', transform=tf) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button("💾 TIFF", mem.read(), filename, "image/tiff", key=key)

def create_geojson_download(mask, lat, lon, r_km, key):
    off = (r_km / 111.32) / 2
    tf = from_bounds(lon-off, lat-off, lon+off, lat+off, mask.shape[1], mask.shape[0])
    shps = features.shapes(mask.astype('int16'), mask=(mask > 0), transform=tf)
    gj = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": g} for g, v in shps]}
    return st.download_button("📐 GeoJSON", json.dumps(gj), "flood.geojson", "application/json", key=key)

# --- SIDEBAR ---
st.sidebar.title("🌊 S1 Explorer")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

with st.sidebar.expander("📍 Area & Time", expanded=True):
    city = st.sidebar.text_input("Location", "Torroella de Montgrí, Spain")
    r_km = st.sidebar.slider("Radius (km)", 1, 30, 8)
    win = st.sidebar.date_input("Window", [datetime.date.today() - datetime.timedelta(days=30), datetime.date.today()])

gain = st.sidebar.slider("Gain", 0.5, 8.0, 3.0)
btn_search = st.sidebar.button("🔍 FETCH DATA", type="primary", use_container_width=True)

# --- CORE SEARCH ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        geolocator = Nominatim(user_agent=f"f_{st.session_state.app_uuid}")
        loc = geolocator.geocode(city, timeout=10)
        if loc:
            st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
            st.session_state.image_cache = {}
            catalog = SentinelHubCatalog(config=config)
            off = (r_km / 111.32) / 2
            bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, 
                              st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
            st.session_state.search_results = list(catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(win[0]), str(win[1]))))
        else: st.error("Loc Error")

    t1, t2, t3 = st.tabs(["Dashboard", "Compare Lab", "Flood Analysis"])

    with t1:
        if st.session_state.search_results:
            res = st.session_state.search_results
            opts = [f"{i}: {r['properties']['datetime'][:16]}" for i, r in enumerate(res)]
            picks = st.multiselect("Pick dates:", opts, default=opts[:2])
            
            if st.button("RENDER", use_container_width=True):
                off = (r_km / 111.32) / 2
                bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, 
                                  st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
                ev = "//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}"
                for p in picks:
                    dt = res[int(p.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=ev, input_data=[SentinelHubRequest.input_data(DataCollection.SENTINEL1_IW, (dt, dt))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox, size=(500, 500), config=config)
                    st.session_state.image_cache[dt] = req.get_data()[0]

            if st.session_state.image_cache:
                for dt, data in st.session_state.image_cache.items():
                    c_i, c_d = st.columns([5, 1])
                    c_i.image(np.clip(data*gain, 0, 1), caption=dt[:10], use_container_width=True)
                    create_geotiff_download(data, f"{dt[:10]}.tif", st.session_state.lat, st.session_state.lon, r_km, f"d_{dt}")

    with t2:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            c_sel1, c_sel2 = st.columns(2)
            d1 = c_sel1.selectbox("Dry Date", keys, index=0)
            d2 = c_sel2.selectbox("Wet Date", keys, index=1)
            
            # Efficient side-by-side using Streamlit columns instead of Matplotlib
            col1, col2 = st.columns(2)
            img1 = np.clip(st.session_state.image_cache[d1] * gain, 0, 1)
            img2 = np.clip(st.session_state.image_cache[d2] * gain, 0, 1)
            
            col1.image(img1, caption="Baseline (Dry)", use_container_width=True)
            col2.image(img2, caption="Analysis (Wet)", use_container_width=True)
            
            st.divider()
            c_dl1, c_dl2 = st.columns(2)
            with c_dl1: create_geotiff_download(st.session_state.image_cache[d1], "dry.tif", st.session_state.lat, st.session_state.lon, r_km, "dl_l1")
            with c_dl2: create_geotiff_download(st.session_state.image_cache[d2], "wet.tif", st.session_state.lat, st.session_state.lon, r_km, "dl_l2")
        else: st.info("Render 2 images first")

    with t3:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            c_f1, c_f2, c_f3 = st.columns(3)
            b_dt = c_f1.selectbox("Dry", keys, index=0, key="fb")
            w_dt = c_f2.selectbox("Wet", keys, index=1, key="fw")
            f_col = c_f3.color_picker("Flood Color", "#00D1FF")
            
            thresh = st.slider("Sensitivity (dB)", -12.0, -2.0, -6.0)
            
            # SAR Math
            b_val = st.session_state.image_cache[b_dt]
            w_val = st.session_state.image_cache[w_dt]
            diff = 10 * np.log10(w_val + 1e-10) - 10 * np.log10(b_val + 1e-10)
            mask = (diff < thresh).astype(float)
            
            m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
            off = (r_km / 111.32) / 2
            bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
            
            # Transparent Background
            folium.raster_layers.ImageOverlay(get_img_url(np.clip(w_val*gain, 0, 1)), bnds, opacity=0.4).add_to(m)
            
            # Colored Mask
            h = f_col.lstrip('#'); rgb = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
            m_rgb = np.zeros((*mask.shape[:2], 4))
            m_rgb[mask[:,:,0] == 1] = [*rgb, 0.8]
            folium.raster_layers.ImageOverlay(get_img_url(m_rgb), bnds).add_to(m)
            
            st_folium(m, height=450, width=None)
            
            st.write("### 📥 Download Results")
            cd1, cd2 = st.columns(2)
            with cd1: create_geotiff_download(mask, "flood.tif", st.session_state.lat, st.session_state.lon, r_km, "fdl1")
            with cd2: create_geojson_download(mask, st.session_state.lat, st.session_state.lon, r_km, "fdl2")
else:
    st.info("👋 Credentials needed.")
