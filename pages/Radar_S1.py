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
    city_query = st.sidebar.text_input("Enter City Name (e.g. Lisbon, Portugal)", "Valencia, Spain")
    use_manual = False
else:
    st.sidebar.info("Enter coordinates below:")
    use_manual = True

radius_km = st.sidebar.slider("Search Radius (km)", 1, 50, 10)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2024, 10, 1), datetime.date(2024, 11, 15)))

with st.sidebar.expander("📍 Manual Coordinate Overrides"):
    man_lat = st.number_input("Lat", value=39.4699, format="%.6f")
    man_lon = st.number_input("Lon", value=-0.3763, format="%.6f")

btn_search = st.sidebar.button("🔍 SEARCH RADAR", type="primary", use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.header("3. Display Settings")
brightness = st.sidebar.slider("Radar Gain", 0.5, 10.0, 3.0)
selected_basemap = st.sidebar.selectbox("Base Map", ["OpenStreetMap", "Esri World Imagery", "CartoDB Positron"])

# --- CORE SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        lat, lon = None, None
        
        if search_mode == "Search City":
            with st.spinner(f"Geocoding '{city_query}'..."):
                try:
                    # Creating a unique user agent to avoid being blocked
                    ua = f"sentinel_flood_explorer_{st.session_state.app_uuid_s1}"
                    geolocator = Nominatim(user_agent=ua)
                    time.sleep(1.5) # Compliance with Nominatim usage policy
                    location = geolocator.geocode(city_query, timeout=10)
                    
                    if location:
                        lat, lon = location.latitude, location.longitude
                        st.sidebar.success(f"📍 Found: {location.address[:40]}...")
                    else:
                        st.error(f"Could not find '{city_query}'. Try adding the country name.")
                except Exception as e:
                    st.error(f"Geocoding error: {e}. Falling back to manual.")

        if use_manual or (lat is None):
            lat, lon = man_lat, man_lon
            st.sidebar.info(f"Using Coordinates: {lat}, {lon}")

        if lat:
            st.session_state.last_search_coords_s1 = (lat, lon, radius_km)
            offset = (radius_km / 111.32) / 2
            st.session_state.current_bounds_s1 = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
            
            catalog = SentinelHubCatalog(config=config)
            bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
            
            with st.spinner("Searching Satellite Catalog..."):
                search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox_obj, time=(str(date_range[0]), str(date_range[1])))
                st.session_state.search_results_s1 = list(search)
                st.session_state.image_cache_s1 = {}
                
            if not st.session_state.search_results_s1:
                st.warning("No radar images found for this area/date. Try expanding the date range.")

    # --- TABS (Rest of the app logic remains consistent) ---
    tab_dash, tab_ana, tab_flood = st.tabs(["🗺️ Dashboard", "🧪 Lab", "🚨 Flood Mapping"])

    with tab_dash:
        if st.session_state.search_results_s1:
            res = st.session_state.search_results_s1
            lat, lon, r_km = st.session_state.last_search_coords_s1
            date_options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(res)]
            sel_dates = st.multiselect("Select dates to process:", date_options, default=date_options[:min(len(date_options), 4)])

            if st.button("🖼️ RENDER RADAR QUADRANTS", use_container_width=True):
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
                for i, d_str in enumerate(sel_dates):
                    with cols[i % 2]:
                        actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                        if actual_date in st.session_state.image_cache_s1:
                            data = st.session_state.image_cache_s1[actual_date]
                            with st.expander(f"Visuals: {actual_date[:10]}", expanded=True):
                                pol_choice = st.radio("Mode", ["VV", "VH", "False Color"], key=f"pol_{i}", horizontal=True)
                            
                            VV, VH = data[:,:,0], data[:,:,1]
                            if pol_choice == "VV":
                                img = np.dstack([np.clip(VV * brightness, 0, 1)]*3)
                            elif pol_choice == "VH":
                                img = np.dstack([np.clip(VH * (brightness*1.5), 0, 1)]*3)
                            else:
                                ratio = np.clip(VV / (VH + 1e-5), 0, 1)
                                img = np.dstack([np.clip(VV * brightness, 0, 1), np.clip(VH * brightness, 0, 1), ratio])

                            m = folium.Map(location=[lat, lon], zoom_start=12, tiles=selected_basemap)
                            folium.raster_layers.ImageOverlay(image=get_image_url(img), bounds=st.session_state.current_bounds_s1).add_to(m)
                            st_folium(m, height=400, width=None, key=f"map_{i}_{actual_date}")

    with tab_flood:
        if len(st.session_state.image_cache_s1) >= 2:
            d_list = list(st.session_state.image_cache_s1.keys())
            c1, c2, c3 = st.columns(3)
            with c1: before_date = st.selectbox("Baseline (Dry)", d_list, index=0)
            with c2: after_date = st.selectbox("Crisis (Wet)", d_list, index=1)
            with c3: flood_color = st.color_picker("Flood Overlay Color", "#FF0000")

            before_db = 10 * np.log10(st.session_state.image_cache_s1[before_date][:,:,0] + 1e-10)
            after_db = 10 * np.log10(st.session_state.image_cache_s1[after_date][:,:,0] + 1e-10)
            diff = after_db - before_db
            
            col_ctrl, col_map = st.columns([1, 2])
            with col_ctrl:
                thresh = st.slider("Flood Sensitivity", -15.0, -2.0, -6.0)
                exclude_perm = st.checkbox("Hide Permanent Water", value=True)
                flood_mask = (diff < thresh).astype(float)
                if exclude_perm: flood_mask[before_db < -16] = 0
                create_geotiff_download(flood_mask, "Flood_Map.tif", *st.session_state.last_search_coords_s1, key="dl_f")

            with col_map:
                m_flood = folium.Map(location=[st.session_state.last_search_coords_s1[0], st.session_state.last_search_coords_s1[1]], zoom_start=12, tiles=selected_basemap)
                bg_vv = np.dstack([np.clip(st.session_state.image_cache_s1[after_date][:,:,0]*brightness, 0, 1)]*3)
                folium.raster_layers.ImageOverlay(image=get_image_url(bg_vv), bounds=st.session_state.current_bounds_s1, opacity=0.5).add_to(m_flood)
                
                h = flood_color.lstrip('#')
                rgb = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
                mask_rgb = np.zeros((*flood_mask.shape, 4))
                mask_rgb[flood_mask == 1] = [*rgb, 0.8]
                folium.raster_layers.ImageOverlay(image=get_image_url(mask_rgb), bounds=st.session_state.current_bounds_s1).add_to(m_flood)
                st_folium(m_flood, height=600, width=None, key="f_map_final")
        else:
            st.info("Render at least 2 images in the Dashboard to enable Flood Mapping.")

else:
    st.info("👋 Enter your Sentinel Hub credentials in the sidebar to begin.")
