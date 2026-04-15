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
                                  file_name=filename, mime="image/tiff", key=key)

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Area of Interest")
city_name = st.sidebar.text_input("City Name", "Valencia, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 25, 5)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2025, 10, 1), datetime.date(2025, 11, 15)))

with st.sidebar.expander("📍 Manual Coords"):
    man_lat = st.number_input("Lat", value=39.4699, format="%.4f")
    man_lon = st.number_input("Lon", value=-0.3763, format="%.4f")
    use_manual = st.checkbox("Force Manual")

btn_search = st.sidebar.button("🔍 SEARCH RADAR", type="primary", use_container_width=True)

# --- CORE SEARCH ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        lat, lon = None, None
        if not use_manual:
            try:
                geolocator = Nominatim(user_agent=f"flood_detect_{st.session_state.app_uuid_s1}")
                time.sleep(1.2)
                loc = geolocator.geocode(city_name, timeout=10)
                if loc: lat, lon = loc.latitude, loc.longitude
            except: st.warning("Geocoding 429. Using manual.")
        
        if use_manual or lat is None: lat, lon = man_lat, man_lon

        if lat:
            st.session_state.last_search_coords_s1 = (lat, lon, radius_km)
            offset = (radius_km / 111.32) / 2
            catalog = SentinelHubCatalog(config=config)
            bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
            search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox_obj, time=(str(date_range[0]), str(date_range[1])))
            st.session_state.search_results_s1 = list(search)
            st.session_state.image_cache_s1 = {}

    # --- TABS ---
    tab_dash, tab_ana, tab_flood = st.tabs(["🗺️ Dashboard", "🧪 Lab", "🚨 Flood Mapping"])

    # (Skipping Lab/Dashboard code for brevity - same as before)
    # ... Assume st.session_state.image_cache_s1 is populated via Dashboard ...

    with tab_flood:
        if not st.session_state.image_cache_s1 or len(st.session_state.image_cache_s1) < 2:
            st.info("Please load at least 2 images in the Dashboard first.")
        else:
            st.header("🕵️ Automated Flood Extraction")
            col1, col2 = st.columns(2)
            dates = list(st.session_state.image_cache_s1.keys())
            
            with col1:
                before_date = st.selectbox("Select Baseline Date (Before Flood)", dates, index=0)
            with col2:
                after_date = st.selectbox("Select Crisis Date (During Flood)", dates, index=1)
            
            # Processing
            before_lin = st.session_state.image_cache_s1[before_date][:,:,0] # VV
            after_lin = st.session_state.image_cache_s1[after_date][:,:,0]   # VV
            
            before_db = 10 * np.log10(before_lin + 1e-10)
            after_db = 10 * np.log10(after_lin + 1e-10)
            
            # CHANGE DETECTION
            diff = after_db - before_db
            
            st.markdown("---")
            c_left, c_right = st.columns([1, 2])
            
            with c_left:
                st.subheader("Sensitivity")
                threshold = st.slider("Flood Threshold (dB drop)", -15.0, -2.0, -6.0)
                st.write("Pixels that dropped more than this value are marked as flood.")
                
                # Binary Mask
                flood_mask = np.zeros_like(diff)
                flood_mask[diff < threshold] = 1.0
                
                # Exclude pixels that were already water (Before < -15dB)
                exclude_permanent = st.checkbox("Exclude Permanent Water", value=True)
                if exclude_permanent:
                    flood_mask[before_db < -15] = 0.0

                create_geotiff_download(flood_mask, "Flood_Extent_Mask.tif", *st.session_state.last_search_coords_s1, key="dl_flood")

            with c_right:
                fig, ax = plt.subplots(figsize=(10, 6))
                # Show the "After" image in grayscale
                bg = np.clip(after_lin * 3, 0, 1)
                ax.imshow(bg, cmap='gray')
                # Overlay flood in bright Cyan
                overlay = np.zeros((*flood_mask.shape, 4))
                overlay[flood_mask == 1] = [0, 1, 1, 0.8] # Cyan with alpha
                ax.imshow(overlay)
                ax.set_title(f"Detected Flood: {after_date[:10]} vs {before_date[:10]}")
                ax.axis('off')
                st.pyplot(fig)
