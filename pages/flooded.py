import streamlit as st
import pandas as pd
import numpy as np
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
st.set_page_config(layout="wide", page_title="S1 Interactive Flood Pro", page_icon="🌓")

# --- INITIALIZE SESSION STATE ---
if 'search_results' not in st.session_state: st.session_state.search_results = None
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'app_uuid' not in st.session_state: st.session_state.app_uuid = str(uuid.uuid4())[:8]
if 'lat' not in st.session_state: st.session_state.lat, st.session_state.lon = 42.041, 3.126

# --- HELPERS ---
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

# --- SIDEBAR ---
st.sidebar.title("🌊 S1 Interactive")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

with st.sidebar.expander("📍 Search Parameters", expanded=True):
    city = st.sidebar.text_input("Location", "Torroella de Montgrí, Spain")
    r_km = st.sidebar.slider("Radius (km)", 1, 30, 8)
    win = st.sidebar.date_input("Window", [datetime.date.today() - datetime.timedelta(days=45), datetime.date.today()])

gain = st.sidebar.slider("Radar Brightness", 0.5, 10.0, 3.0)
btn_search = st.sidebar.button("🔍 FETCH DATA", type="primary", use_container_width=True)

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

    tab1, tab2, tab3 = st.tabs(["📑 Dual Catalog", "🌓 Sync Comparison", "🚨 Flood Extraction"])

    with tab1:
        if st.session_state.search_results:
            res = st.session_state.search_results
            opts = [f"{i}: {r['properties']['datetime'][:16]}" for i, r in enumerate(res)]
            picks = st.multiselect("Pick two dates to load into Dual-View:", opts, default=opts[:min(len(opts), 2)])
            
            if st.button("RENDER DUAL-VIEW", use_container_width=True):
                off = (r_km / 111.32) / 2
                bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, 
                                  st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
                ev = "//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}"
                for p in picks:
                    dt = res[int(p.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=ev, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(dt, dt))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox, size=(600, 600), config=config)
                    st.session_state.image_cache[dt] = req.get_data()[0]

            if len(st.session_state.image_cache) >= 2:
                keys = list(st.session_state.image_cache.keys())
                dm1 = DualMap(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
                off = (r_km / 111.32) / 2
                bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
                
                folium.raster_layers.ImageOverlay(get_img_url(np.clip(st.session_state.image_cache[keys[0]]*gain, 0, 1)), bnds).add_to(dm1.m1)
                folium.raster_layers.ImageOverlay(get_img_url(np.clip(st.session_state.image_cache[keys[1]]*gain, 0, 1)), bnds).add_to(dm1.m2)
                st_folium(dm1, height=500, width=None, use_container_width=True, key="catalog_dual")
        else: st.info("Search for location to see catalog.")

    with tab2:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            c1, c2 = st.columns(2)
            d1 = c1.selectbox("Baseline (Left)", keys, index=0, key="sel_l")
            d2 = c2.selectbox("Analysis (Right)", keys, index=1, key="sel_r")
            
            m_sync = DualMap(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
            off = (r_km / 111.32) / 2
            bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
            
            folium.raster_layers.ImageOverlay(get_img_url(np.clip(st.session_state.image_cache[d1]*gain, 0, 1)), bnds).add_to(m_sync.m1)
            folium.raster_layers.ImageOverlay(get_img_url(np.clip(st.session_state.image_cache[d2]*gain, 0, 1)), bnds).add_to(m_sync.m2)
            st_folium(m_sync, height=600, width=None, use_container_width=True, key="compare_sync")
        else: st.info("Render images in Tab 1 first.")

    with tab3:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            cf1, cf2, cf3 = st.columns(3)
            b_dt = cf1.selectbox("Baseline (Dry)", keys, index=0, key="f_b")
            w_dt = cf2.selectbox("Crisis (Wet)", keys, index=1, key="f_w")
            f_col = cf3.color_picker("Flood Color", "#00F6FF")
            
            col_s1, col_s2 = st.columns(2)
            sens = col_s1.slider("Sensitivity (dB)", -15.0, -2.0, -6.0)
            bg_opac = col_s2.slider("Background Opacity", 0.0, 1.0, 0.4)
            
            # Flood Math
            b_val = st.session_state.image_cache[b_dt]
            w_val = st.session_state.image_cache[w_dt]
            # Convert to Decibels and find difference
            diff = 10 * np.log10(w_val + 1e-10) - 10 * np.log10(b_val + 1e-10)
            # Create a mask where the signal dropped significantly
            mask = (diff < sens).astype(np.uint8)
            
            mf = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
            off = (r_km / 111.32) / 2
            bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
            
            # Background with adjustable transparency
            folium.raster_layers.ImageOverlay(get_img_url(np.clip(w_val*gain, 0, 1)), bnds, opacity=bg_opac).add_to(mf)
            
            # Correctly map flooded areas (blue overlay)
            h = f_col.lstrip('#'); rgb = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
            m_rgb = np.zeros((*mask.shape[:2], 4))
            m_rgb[mask[:,:,0] == 1] = [*rgb, 0.9] # Set flood pixels to color with high opacity
            
            folium.raster_layers.ImageOverlay(get_img_url(m_rgb), bnds, name="Flood Mask").add_to(mf)
            st_folium(mf, height=550, width=None, use_container_width=True, key="flood_map")
            
            st.write("### 📥 Export Layer")
            c_d1, c_d2 = st.columns(2)
            with c_d1: create_geotiff_download(mask[:,:,0], "flood.tif", st.session_state.lat, st.session_state.lon, r_km, "fd_r")
            with c_d2:
                tf = from_bounds(st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off, mask.shape[1], mask.shape[0])
                shps = features.shapes(mask[:,:,0].astype('int16'), mask=(mask[:,:,0] > 0), transform=tf)
                gj = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": g} for g, v in shps]}
                st.download_button("📐 Export GeoJSON", json.dumps(gj), "flood.geojson", "application/json")
