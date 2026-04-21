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
import osmnx as ox  # <--- New for the Handshake
from shapely.geometry import box

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Flood Explorer Pro", page_icon="🌊")

# --- INITIALIZE SESSION STATE ---
if 'search_results_s1' not in st.session_state: st.session_state.search_results_s1 = None
if 'image_cache_s1' not in st.session_state: st.session_state.image_cache_s1 = {}
if 'app_uuid_s1' not in st.session_state: st.session_state.app_uuid_s1 = str(uuid.uuid4())
if 'last_search_coords_s1' not in st.session_state: st.session_state.last_search_coords_s1 = None
if 'current_bounds_s1' not in st.session_state: st.session_state.current_bounds_s1 = None
if 'water_mask' not in st.session_state: st.session_state.water_mask = None

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    try:
        if np_img is None: return ""
        img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_data)
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"
    except: return ""

def fetch_osm_water_mask(lat, lon, radius_km, shape):
    """Fetches water bodies from OSM and returns a mask matching the radar data shape."""
    offset = (radius_km / 111.32) / 2
    bbox = (lat - offset, lat + offset, lon - offset, lon + offset) # (south, north, west, east)
    
    try:
        # Fetch water geometries (oceans, rivers, lakes)
        tags = {'natural': 'water', 'landuse': 'reservoir', 'waterway': ['riverbank', 'dock']}
        gdf = ox.features_from_bbox(bbox[1], bbox[0], bbox[3], bbox[2], tags=tags)
        
        if gdf.empty: return np.zeros(shape, dtype=np.uint8)

        # Create transform to map polygons to our pixel grid
        transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, shape[1], shape[0])
        
        # Rasterize the polygons: 1 for water, 0 for land
        mask = features.rasterize(
            [(geom, 1) for geom in gdf.geometry],
            out_shape=shape,
            transform=transform,
            fill=0,
            dtype='uint8'
        )
        return mask
    except:
        return np.zeros(shape, dtype=np.uint8)

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Search Area")
search_mode = st.sidebar.radio("Location Mode:", ["Search City", "Manual Coordinates"])
city_query = st.sidebar.text_input("City Name", "Valencia, Spain") if search_mode == "Search City" else None
radius_km = st.sidebar.slider("Radius (km)", 1, 50, 10)
date_range = st.sidebar.date_input("Date Window", value=(datetime.date(2024, 10, 20), datetime.date(2024, 11, 15)))

st.sidebar.markdown("---")
st.sidebar.header("3. Smart Filters")
exclude_sea_osm = st.sidebar.checkbox("OSM Handshake: Hide Sea", value=True, help="Uses OpenStreetMap data to mask out permanent water bodies.")
brightness = st.sidebar.slider("Radar Gain", 0.5, 10.0, 3.0)
selected_basemap = st.sidebar.selectbox("Base Map", ["OpenStreetMap", "Esri World Imagery", "CartoDB Positron"])

btn_search = st.sidebar.button("🔍 FETCH RADAR DATA", type="primary", use_container_width=True)

# --- CORE SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        lat, lon = (39.4699, -0.3763) # Default
        if search_mode == "Search City":
            try:
                geolocator = Nominatim(user_agent=f"flood_explorer_{st.session_state.app_uuid_s1}")
                time.sleep(1) 
                location = geolocator.geocode(city_query, timeout=10)
                if location: lat, lon = location.latitude, location.longitude
            except: st.error("Geocoder busy.")

        st.session_state.last_search_coords_s1 = (lat, lon, radius_km)
        offset = (radius_km / 111.32) / 2
        st.session_state.current_bounds_s1 = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
        st.session_state.image_cache_s1 = {}
        st.session_state.water_mask = None # Reset mask
        
        catalog = SentinelHubCatalog(config=config)
        bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
        search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox_obj, time=(str(date_range[0]), str(date_range[1])))
        st.session_state.search_results_s1 = list(search)

    # --- TABS ---
    tab_dash, tab_ana, tab_flood = st.tabs(["🗺️ Dashboard", "🧪 Advanced Lab", "🚨 Flood Mapping"])

    with tab_dash:
        if st.session_state.search_results_s1:
            res = st.session_state.search_results_s1
            date_options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(res)]
            sel_dates = st.multiselect("Select dates:", date_options, default=date_options[:min(len(date_options), 2)])

            if st.button("🖼️ RENDER RADAR", use_container_width=True):
                lat, lon, r_km = st.session_state.last_search_coords_s1
                offset = (r_km / 111.32) / 2
                bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                evalscript = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
                
                with st.spinner("Downloading and Masking..."):
                    for d_str in sel_dates:
                        actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                        req = SentinelHubRequest(evalscript=evalscript, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(actual_date, actual_date))],
                                                responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(500, 500), config=config)
                        data = req.get_data()[0]
                        st.session_state.image_cache_s1[actual_date] = data
                        
                        if st.session_state.water_mask is None:
                            st.session_state.water_mask = fetch_osm_water_mask(lat, lon, r_km, data.shape[:2])

            if st.session_state.image_cache_s1:
                cols = st.columns(2)
                for i, d_str in enumerate(sel_dates):
                    actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                    if actual_date in st.session_state.image_cache_s1:
                        with cols[i % 2]:
                            data = st.session_state.image_cache_s1[actual_date].copy()
                            
                            # APPLY OSM MASK IF ENABLED
                            if exclude_sea_osm and st.session_state.water_mask is not None:
                                data[st.session_state.water_mask == 1] = 0
                            
                            pol = st.radio(f"View ({actual_date[:10]})", ["VV", "VH"], key=f"p_{i}", horizontal=True)
                            channel = 0 if pol == "VV" else 1
                            img = np.dstack([np.clip(data[:,:,channel]*brightness, 0, 1)]*3)
                            
                            m = folium.Map(location=[st.session_state.last_search_coords_s1[0], st.session_state.last_search_coords_s1[1]], zoom_start=12, tiles=selected_basemap)
                            folium.raster_layers.ImageOverlay(image=get_image_url(img), bounds=st.session_state.current_bounds_s1).add_to(m)
                            st_folium(m, height=350, width=None, key=f"map_{i}")

    with tab_ana:
        if len(st.session_state.image_cache_s1) >= 2:
            d_list = list(st.session_state.image_cache_s1.keys())
            c_lab1, c_lab2, c_lab3 = st.columns([1,1,1])
            lab_before = c_lab1.selectbox("Left (Baseline)", d_list, index=0)
            lab_after = c_lab2.selectbox("Right (Crisis)", d_list, index=1)
            cmap_choice = c_lab3.selectbox("Color Ramp", ["viridis", "magma", "inferno", "plasma"])
            
            data_left = 10 * np.log10(st.session_state.image_cache_s1[lab_before][:,:,0] + 1e-10)
            data_right = 10 * np.log10(st.session_state.image_cache_s1[lab_after][:,:,0] + 1e-10)
            
            if exclude_sea_osm and st.session_state.water_mask is not None:
                data_left[st.session_state.water_mask == 1] = np.nan
                data_right[st.session_state.water_mask == 1] = np.nan

            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
            ax1.imshow(data_left, cmap=cmap_choice, vmin=-25, vmax=-5); ax1.set_title("Before"); ax1.axis('off')
            ax2.imshow(data_right, cmap=cmap_choice, vmin=-25, vmax=-5); ax2.set_title("After"); ax2.axis('off')
            st.pyplot(fig)
        else:
            st.info("Render images in Dashboard first.")

    with tab_flood:
        if len(st.session_state.image_cache_s1) >= 2:
            d_list = list(st.session_state.image_cache_s1.keys())
            before = st.selectbox("Select Dry Baseline", d_list, index=0, key="f_b")
            after = st.selectbox("Select Wet Crisis", d_list, index=1, key="f_a")
            
            b_db = 10 * np.log10(st.session_state.image_cache_s1[before][:,:,0] + 1e-10)
            a_db = 10 * np.log10(st.session_state.image_cache_s1[after][:,:,0] + 1e-10)
            
            flood_mask = ((a_db - b_db) < st.slider("Flood Sensitivity", -15.0, -2.0, -6.0)).astype(float)
            
            # THE OSM HANDSHAKE FINAL STEP: 
            # If a pixel is in the sea, it cannot be "flooded land"
            if exclude_sea_osm and st.session_state.water_mask is not None:
                flood_mask[st.session_state.water_mask == 1] = 0

            m_f = folium.Map(location=[st.session_state.last_search_coords_s1[0], st.session_state.last_search_coords_s1[1]], zoom_start=12, tiles=selected_basemap)
            
            # After Image Background
            bg_data = st.session_state.image_cache_s1[after][:,:,0].copy()
            if exclude_sea_osm and st.session_state.water_mask is not None: bg_data[st.session_state.water_mask == 1] = 0
            bg = np.dstack([np.clip(bg_data*brightness, 0, 1)]*3)
            folium.raster_layers.ImageOverlay(image=get_image_url(bg), bounds=st.session_state.current_bounds_s1, opacity=0.4).add_to(m_f)
            
            # Red Flood Overlay
            mask_rgb = np.zeros((*flood_mask.shape, 4))
            mask_rgb[flood_mask == 1] = [1, 0, 0, 0.8]
            folium.raster_layers.ImageOverlay(image=get_image_url(mask_rgb), bounds=st.session_state.current_bounds_s1).add_to(m_f)
            
            st_folium(m_f, height=500, width=None, key="f_map_final")
else:
    st.info("Enter Credentials to begin.")
