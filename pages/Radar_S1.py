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
import rasterio
from rasterio import features
from rasterio.transform import from_bounds
from rasterio.io import MemoryFile
import time

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Flood Explorer Pro", page_icon="🌊")

# --- INITIALIZE SESSION STATE ---
if 'search_results_s1' not in st.session_state: st.session_state.search_results_s1 = None
if 'image_cache_s1' not in st.session_state: st.session_state.image_cache_s1 = {}
if 'app_uuid_s1' not in st.session_state: st.session_state.app_uuid_s1 = str(uuid.uuid4())
if 'last_search_coords_s1' not in st.session_state: st.session_state.last_search_coords_s1 = None
if 'current_bounds_s1' not in st.session_state: st.session_state.current_bounds_s1 = None

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    img = Image.fromarray((np_img * 255).astype(np.uint8))
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

def create_geotiff_download(data, filename, lat, lon, radius_km, key):
    offset = (radius_km / 111.32) / 2
    transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, data.shape[1], data.shape[0])
    with MemoryFile() as memfile:
        with memfile.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                          dtype='float32', crs='EPSG:4326', transform=transform) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button(label=f"💾 TIFF", data=memfile.read(), file_name=filename, mime="image/tiff", key=key)

def create_geojson_download(mask, lat, lon, radius_km):
    offset = (radius_km / 111.32) / 2
    transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, mask.shape[1], mask.shape[0])
    mask_int = mask.astype('int16')
    shapes = features.shapes(mask_int, mask=(mask_int > 0), transform=transform)
    features_list = [{"type": "Feature", "properties": {"class": "flood"}, "geometry": geom} for geom, val in shapes]
    geojson_data = {"type": "FeatureCollection", "features": features_list}
    return st.download_button(label="📐 GeoJSON (Vector)", data=json.dumps(geojson_data), file_name="flood.geojson", mime="application/json", use_container_width=True)

# --- SIDEBAR ---
st.sidebar.header("🔑 1. Auth")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("📍 2. Location")
search_mode = st.sidebar.radio("Search By:", ["City Name", "Manual Coordinates"])

if search_mode == "City Name":
    city_name = st.sidebar.text_input("City", "Valencia, Spain")
    man_lat, man_lon = 39.4699, -0.3763 # Default fallback
else:
    col_lat, col_lon = st.sidebar.columns(2)
    man_lat = col_lat.number_input("Lat", value=39.4699, format="%.4f")
    man_lon = col_lon.number_input("Lon", value=-0.3763, format="%.4f")
    city_name = None

radius_km = st.sidebar.slider("Radius (km)", 1, 45, 10)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2024, 10, 20), datetime.date(2024, 11, 10)))

st.sidebar.markdown("---")
st.sidebar.header("🎨 3. Settings")
brightness = st.sidebar.slider("Radar Gain", 0.5, 10.0, 3.0)

btn_search = st.sidebar.button("🔍 FETCH RADAR DATA", type="primary", use_container_width=True)

# --- SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET and btn_search:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)
    lat, lon = man_lat, man_lon # Start with manual
    
    if search_mode == "City Name":
        try:
            geolocator = Nominatim(user_agent=f"flood_app_{st.session_state.app_uuid_s1}")
            time.sleep(1.2)
            loc = geolocator.geocode(city_name, timeout=10)
            if loc: lat, lon = loc.latitude, loc.longitude
        except: st.warning("Geocoding failed. Using default Valencia center.")

    st.session_state.last_search_coords_s1 = (lat, lon, radius_km)
    offset = (radius_km / 111.32) / 2
    st.session_state.current_bounds_s1 = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
    
    try:
        catalog = SentinelHubCatalog(config=config)
        bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
        search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox_obj, time=(str(date_range[0]), str(date_range[1])))
        st.session_state.search_results_s1 = list(search)
        st.session_state.image_cache_s1 = {}
        if not st.session_state.search_results_s1: st.warning("No images found for these dates.")
    except Exception as e: st.error(f"Sentinel Hub Error: {e}")

# --- MAIN UI ---
if st.session_state.last_search_coords_s1:
    tab_dash, tab_flood = st.tabs(["🗺️ Dashboard", "🚨 Flood Mapping"])

    with tab_dash:
        if st.session_state.search_results_s1:
            res = st.session_state.search_results_s1
            lat, lon, r_km = st.session_state.last_search_coords_s1
            date_options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(res)]
            sel_dates = st.multiselect("Select Dates:", date_options, default=date_options[:min(len(date_options), 4)])

            if st.button("🖼️ RENDER RADAR", use_container_width=True):
                bbox_obj = BBox(bbox=[lon-(r_km/222), lat-(r_km/222), lon+(r_km/222), lat+(r_km/222)], crs=CRS.WGS84)
                evalscript = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
                for d_str in sel_dates:
                    actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=evalscript, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(actual_date, actual_date))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(500, 500), config=config)
                    st.session_state.image_cache_s1[actual_date] = req.get_data()[0]

            if st.session_state.image_cache_s1:
                cols = st.columns(2)
                for i, d_str in enumerate(sel_dates):
                    actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                    if actual_date in st.session_state.image_cache_s1:
                        with cols[i % 2]:
                            data = st.session_state.image_cache_s1[actual_date]
                            pol = st.radio(f"Mode ({actual_date[:10]})", ["VV", "VH", "False Color"], horizontal=True, key=f"p_{i}")
                            
                            if pol == "VV": img = np.dstack([np.clip(data[:,:,0]*brightness, 0, 1)]*3)
                            elif pol == "VH": img = np.dstack([np.clip(data[:,:,1]*brightness*2, 0, 1)]*3)
                            else:
                                ratio = np.clip(data[:,:,0] / (data[:,:,1] + 1e-5), 0, 1)
                                img = np.dstack([np.clip(data[:,:,0]*brightness, 0, 1), np.clip(data[:,:,1]*brightness, 0, 1), ratio])
                            
                            m = folium.Map(location=[lat, lon], zoom_start=12)
                            folium.raster_layers.ImageOverlay(image=get_image_url(img), bounds=st.session_state.current_bounds_s1).add_to(m)
                            st_folium(m, height=300, width=450, key=f"m_{i}_{actual_date}")

    with tab_flood:
        if len(st.session_state.image_cache_s1) >= 2:
            d_list = list(st.session_state.image_cache_s1.keys())
            col_sel1, col_sel2, col_col = st.columns(3)
            before = col_sel1.selectbox("Dry Baseline", d_list, index=0)
            after = col_sel2.selectbox("Wet Crisis", d_list, index=1)
            f_color = col_col.color_picker("Flood Color", "#FF0000")

            b_db = 10 * np.log10(st.session_state.image_cache_s1[before][:,:,0] + 1e-10)
            a_db = 10 * np.log10(st.session_state.image_cache_s1[after][:,:,0] + 1e-10)
            
            sens = st.slider("Flood Sensitivity (dB drop)", -15.0, -2.0, -6.0)
            mask = ((a_db - b_db) < sens).astype(float)
            if st.checkbox("Exclude Permanent Water"): mask[b_db < -16] = 0
            
            col_map, col_exp = st.columns([3, 1])
            with col_exp:
                st.write("### 📥 Downloads")
                create_geotiff_download(mask, "flood_raster.tif", *st.session_state.last_search_coords_s1, key="dl_raster")
                create_geojson_download(mask, *st.session_state.last_search_coords_s1)
            
            with col_map:
                m_f = folium.Map(location=[st.session_state.last_search_coords_s1[0], st.session_state.last_search_coords_s1[1]], zoom_start=12)
                bg = np.dstack([np.clip(st.session_state.image_cache_s1[after][:,:,0]*brightness, 0, 1)]*3)
                folium.raster_layers.ImageOverlay(image=get_image_url(bg), bounds=st.session_state.current_bounds_s1, opacity=0.4).add_to(m_f)
                
                h = f_color.lstrip('#'); rgb = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
                mask_rgb = np.zeros((*mask.shape, 4))
                mask_rgb[mask == 1] = [*rgb, 0.8]
                folium.raster_layers.ImageOverlay(image=get_image_url(mask_rgb), bounds=st.session_state.current_bounds_s1).add_to(m_f)
                st_folium(m_f, height=500, width=800, key="flood_final")
        else:
            st.info("Render at least 2 images in Dashboard first.")
else:
    st.info("👋 Select a location and click 'Fetch Radar Data' to begin.")
