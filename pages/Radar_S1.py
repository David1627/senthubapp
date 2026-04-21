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
    west, south = lon - offset, lat - offset
    east, north = lon + offset, lat + offset
    height, width = data.shape[:2]
    transform = from_bounds(west, south, east, north, width, height)
    with MemoryFile() as memfile:
        with memfile.open(driver='GTiff', height=height, width=width, count=1,
                          dtype='float32', crs='EPSG:4326', transform=transform) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button(label=f"💾 Export {filename}", data=memfile.read(),
                                  file_name=filename, mime="image/tiff", key=key)

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Search Area")
search_mode = st.sidebar.radio("Location Mode:", ["Search City", "Manual Coordinates"])

if search_mode == "Search City":
    city_query = st.sidebar.text_input("City Name", "Lisbon, Portugal")
    use_manual = False
else:
    use_manual = True

radius_km = st.sidebar.slider("Search Radius (km)", 1, 50, 10)

# OPTIONAL DATES: Default to a 3-month window ending today
today = datetime.date.today()
default_start = today - datetime.timedelta(days=90)
date_range = st.sidebar.date_input("Optional: Select Date Window", value=(default_start, today))

with st.sidebar.expander("📍 Manual Coordinate Overrides"):
    man_lat = st.number_input("Lat", value=38.7223, format="%.6f")
    man_lon = st.number_input("Lon", value=-9.1393, format="%.6f")

btn_search = st.sidebar.button("🔍 SEARCH RADAR", type="primary", use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.header("3. Display")
brightness = st.sidebar.slider("Radar Gain", 0.5, 10.0, 3.0)
selected_basemap = st.sidebar.selectbox("Base Map", ["OpenStreetMap", "Esri World Imagery", "CartoDB Positron"])

# --- CORE SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        lat, lon = None, None
        
        if search_mode == "Search City":
            try:
                # Unique User Agent is key for Nominatim!
                geolocator = Nominatim(user_agent=f"flood_explorer_{st.session_state.app_uuid_s1}")
                time.sleep(1) 
                location = geolocator.geocode(city_query, timeout=10)
                if location:
                    lat, lon = location.latitude, location.longitude
                    st.sidebar.success(f"Found: {city_query}")
                else:
                    st.error(f"City '{city_query}' not found. Using manual coords.")
            except:
                st.error("Geocoding timed out. Using manual coords.")

        if use_manual or (lat is None):
            lat, lon = man_lat, man_lon

        if lat:
            # RESET STATE ON NEW SEARCH
            st.session_state.last_search_coords_s1 = (lat, lon, radius_km)
            offset = (radius_km / 111.32) / 2
            st.session_state.current_bounds_s1 = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
            st.session_state.image_cache_s1 = {} # Clear old images
            
            catalog = SentinelHubCatalog(config=config)
            bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
            
            search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox_obj, time=(str(date_range[0]), str(date_range[1])))
            st.session_state.search_results_s1 = list(search)
            
            if not st.session_state.search_results_s1:
                st.warning("No images found. Try a wider date range.")

    # --- TABS ---
    tab_dash, tab_flood = st.tabs(["🗺️ Dashboard", "🚨 Flood Mapping"])

    with tab_dash:
        if st.session_state.search_results_s1:
            res = st.session_state.search_results_s1
            lat, lon, r_km = st.session_state.last_search_coords_s1
            
            # Show users what they found
            date_options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(res)]
            sel_dates = st.multiselect("Select up to 4 dates to render:", date_options, default=date_options[:min(len(date_options), 2)])

            if st.button("🖼️ RENDER SELECTED DATES", use_container_width=True):
                offset = (r_km / 111.32) / 2
                bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                evalscript = """//VERSION=3
                function setup() { return { input: ['VV', 'VH'], output: { bands: 2, sampleType: 'FLOAT32' } }; }
                function evaluatePixel(sample) { return [sample.VV, sample.VH]; }"""
                
                for d_str in sel_dates:
                    actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=evalscript, 
                                            input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(actual_date, actual_date))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], 
                                            bbox=bbox_obj, size=(600, 600), config=config)
                    st.session_state.image_cache_s1[actual_date] = req.get_data()[0]

            if st.session_state.image_cache_s1:
                cols = st.columns(2)
                for i, d_key in enumerate(st.session_state.image_cache_s1.keys()):
                    with cols[i % 2]:
                        data = st.session_state.image_cache_s1[d_key]
                        pol_choice = st.radio(f"Polarization ({d_key[:10]})", ["VV", "VH"], key=f"pol_{i}", horizontal=True)
                        
                        channel = 0 if pol_choice == "VV" else 1
                        img = np.dstack([np.clip(data[:,:,channel] * brightness, 0, 1)]*3)

                        m = folium.Map(location=[lat, lon], zoom_start=13, tiles=selected_basemap)
                        folium.raster_layers.ImageOverlay(image=get_image_url(img), bounds=st.session_state.current_bounds_s1).add_to(m)
                        st_folium(m, height=400, width=None, key=f"map_{i}")

    with tab_flood:
        if len(st.session_state.image_cache_s1) >= 2:
            d_list = list(st.session_state.image_cache_s1.keys())
            c1, c2 = st.columns(2)
            before_date = c1.selectbox("Select Baseline (Dry)", d_list, index=0)
            after_date = c2.selectbox("Select Crisis (Wet)", d_list, index=1)

            # CALCULATION
            before_db = 10 * np.log10(st.session_state.image_cache_s1[before_date][:,:,0] + 1e-10)
            after_db = 10 * np.log10(st.session_state.image_cache_s1[after_date][:,:,0] + 1e-10)
            diff = after_db - before_db
            
            thresh = st.slider("Flood Sensitivity", -15.0, -2.0, -6.0)
            flood_mask = (diff < thresh).astype(float)
            
            if st.checkbox("Clean Permanent Water"):
                flood_mask[before_db < -16] = 0
            
            m_flood = folium.Map(location=[lat, lon], zoom_start=13, tiles=selected_basemap)
            
            # Show "After" image as background
            bg = np.dstack([np.clip(st.session_state.image_cache_s1[after_date][:,:,0]*brightness, 0, 1)]*3)
            folium.raster_layers.ImageOverlay(image=get_image_url(bg), bounds=st.session_state.current_bounds_s1, opacity=0.4).add_to(m_flood)
            
            # Show Flood in Bright Red
            mask_rgb = np.zeros((*flood_mask.shape, 4))
            mask_rgb[flood_mask == 1] = [1, 0, 0, 0.8] 
            folium.raster_layers.ImageOverlay(image=get_image_url(mask_rgb), bounds=st.session_state.current_bounds_s1).add_to(m_flood)
            
            st_folium(m_flood, height=600, width=None, key="f_map")
            create_geotiff_download(flood_mask, "Flood_Detection.tif", lat, lon, r_km, key="dl_f")
        else:
            st.info("Please render at least two images in the Dashboard to run flood analysis.")

else:
    st.info("Login to access Radar Search.")
