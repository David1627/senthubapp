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
from io import BytesIO
from PIL import Image
import uuid
import rasterio
from rasterio.transform import from_bounds
from rasterio.io import MemoryFile
import time

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Flood Detector Pro", page_icon="🌊")

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
    west, south = lon - offset, lat - offset
    east, north = lon + offset, lat + offset
    height, width = data.shape[:2]
    count = data.shape[2] if len(data.shape) == 3 else 1
    transform = from_bounds(west, south, east, north, width, height)
    with MemoryFile() as memfile:
        with memfile.open(driver='GTiff', height=height, width=width, count=count,
                          dtype='float32', crs='EPSG:4326', transform=transform) as ds:
            if count == 1: ds.write(data.astype('float32'), 1)
            else:
                for i in range(count): ds.write(data[:, :, i].astype('float32'), i + 1)
        return st.download_button(label=f"💾 Export {filename}", data=memfile.read(),
                                  file_name=filename, mime="image/tiff", key=key, use_container_width=True)

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Area of Interest")
city_name = st.sidebar.text_input("City Name", "Valencia, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 25, 10)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2025, 10, 1), datetime.date(2025, 11, 15)))

with st.sidebar.expander("📍 Manual Coords"):
    man_lat = st.number_input("Lat", value=39.4699, format="%.4f")
    man_lon = st.number_input("Lon", value=-0.3763, format="%.4f")
    use_manual = st.checkbox("Force Manual")

btn_search = st.sidebar.button("🔍 SEARCH RADAR", type="primary", use_container_width=True)

st.sidebar.markdown("---")
brightness = st.sidebar.slider("Radar Gain", 1.0, 10.0, 3.0)
selected_basemap = st.sidebar.selectbox("Base Map", ["OpenStreetMap", "Esri World Imagery"])

# --- CORE SEARCH ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        lat, lon = None, None
        if not use_manual:
            try:
                geolocator = Nominatim(user_agent=f"flood_pro_{st.session_state.app_uuid_s1}")
                time.sleep(1.2)
                loc = geolocator.geocode(city_name, timeout=10)
                if loc: lat, lon = loc.latitude, loc.longitude
            except: st.warning("Geocoding 429. Using manual.")
        
        if use_manual or lat is None: lat, lon = man_lat, man_lon

        if lat:
            st.session_state.last_search_coords_s1 = (lat, lon, radius_km)
            offset = (radius_km / 111.32) / 2
            st.session_state.current_bounds_s1 = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
            catalog = SentinelHubCatalog(config=config)
            bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
            search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox_obj, time=(str(date_range[0]), str(date_range[1])))
            st.session_state.search_results_s1 = list(search)
            st.session_state.image_cache_s1 = {}

    # --- TABS ---
    tab_dash, tab_ana, tab_flood = st.tabs(["🗺️ Dashboard", "🧪 Lab", "🚨 Flood Mapping"])

    with tab_dash:
        if st.session_state.search_results_s1:
            res = st.session_state.search_results_s1
            lat, lon, r_km = st.session_state.last_search_coords_s1
            date_options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(res)]
            sel_dates = st.multiselect("Select 4 dates:", date_options, default=date_options[:min(len(date_options), 4)])

            if st.button("🖼️ RENDER QUADRANTS", use_container_width=True):
                bbox_obj = BBox(bbox=[lon-(r_km/222), lat-(r_km/222), lon+(r_km/222), lat+(r_km/222)], crs=CRS.WGS84)
                evalscript = """//VERSION=3
                function setup() { return { input: ['VV', 'VH'], output: { bands: 2, sampleType: 'FLOAT32' } }; }
                function evaluatePixel(sample) { return [sample.VV, sample.VH]; }"""
                for d_str in sel_dates:
                    actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=evalscript, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(actual_date, actual_date))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(600, 600), config=config)
                    st.session_state.image_cache_s1[actual_date] = req.get_data()[0]

            if len(st.session_state.image_cache_s1) >= 1:
                cols = st.columns(2)
                for i, d_str in enumerate(sel_dates):
                    with cols[i % 2]:
                        actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                        data = st.session_state.image_cache_s1[actual_date]
                        create_geotiff_download(data, f"S1_{actual_date[:10]}.tif", lat, lon, r_km, key=f"dl_{i}")
                        img_vv = np.clip(data[:,:,0] * brightness, 0, 1)
                        m = folium.Map(location=[lat, lon], zoom_start=12, tiles=selected_basemap)
                        folium.raster_layers.ImageOverlay(image=get_image_url(np.dstack([img_vv]*3)), bounds=st.session_state.current_bounds_s1).add_to(m)
                        st_folium(m, height=300, key=f"map_{i}")

    with tab_ana:
        if st.session_state.image_cache_s1:
            ana_date = st.selectbox("Select Date", list(st.session_state.image_cache_s1.keys()))
            data = st.session_state.image_cache_s1[ana_date]
            db_val = 10 * np.log10(data[:,:,0] + 1e-10)
            fig, ax = plt.subplots()
            im = ax.imshow(db_val, cmap='Blues_r', vmin=-25, vmax=0)
            plt.colorbar(im, label="dB")
            st.pyplot(fig)

    with tab_flood:
        if len(st.session_state.image_cache_s1) < 2:
            st.info("Load at least 2 images in Dashboard.")
        else:
            d_list = list(st.session_state.image_cache_s1.keys())
            before_date = st.selectbox("Baseline (Dry)", d_list, index=0)
            after_date = st.selectbox("Crisis (Wet)", d_list, index=min(1, len(d_list)-1))
            
            # Change Detection Logic
            before_db = 10 * np.log10(st.session_state.image_cache_s1[before_date][:,:,0] + 1e-10)
            after_db = 10 * np.log10(st.session_state.image_cache_s1[after_date][:,:,0] + 1e-10)
            diff = after_db - before_db
            
            thresh = st.slider("Flood Threshold (dB drop)", -15.0, -2.0, -6.0)
            flood_mask = (diff < thresh).astype(float)
            
            # Remove permanent water (anything < -16dB in baseline)
            if st.checkbox("Exclude Permanent Water", value=True):
                flood_mask[before_db < -16] = 0
            
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.imshow(np.clip(st.session_state.image_cache_s1[after_date][:,:,0]*3, 0, 1), cmap='gray')
            overlay = np.zeros((*flood_mask.shape, 4))
            overlay[flood_mask == 1] = [0, 1, 1, 0.7] # Cyan
            ax.imshow(overlay)
            ax.axis('off')
            st.pyplot(fig)
            create_geotiff_download(flood_mask, "Flood_Map.tif", *st.session_state.last_search_coords_s1, key="dl_f")
else:
    st.info("Enter Credentials to begin.")
