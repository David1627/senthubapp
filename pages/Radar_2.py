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
import osmnx as ox # New value: Building footprints

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Flood Intelligence Pro", page_icon="🌊")

# --- INITIALIZE SESSION STATE ---
if 'search_results_s1' not in st.session_state: st.session_state.search_results_s1 = None
if 'image_cache_s1' not in st.session_state: st.session_state.image_cache_s1 = {}
if 'app_uuid_s1' not in st.session_state: st.session_state.app_uuid_s1 = str(uuid.uuid4())
if 'last_search_coords_s1' not in st.session_state: st.session_state.last_search_coords_s1 = (39.4699, -0.3763, 10)
if 'current_bounds_s1' not in st.session_state: st.session_state.current_bounds_s1 = None
if 'buildings_gdf' not in st.session_state: st.session_state.buildings_gdf = None

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    try:
        if np_img is None: return ""
        # Handle 4-channel (RGBA) or 3-channel (RGB)
        img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_data)
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"
    except: return ""

def safe_db(data_slice):
    """Prevents log10(0) and handles potential 3D array errors."""
    return 10 * np.log10(np.clip(data_slice, 1e-10, None))

def create_geotiff_download(data, filename, lat, lon, radius_km, key):
    offset = (radius_km / 111.32) / 2
    transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, data.shape[1], data.shape[0])
    with MemoryFile() as memfile:
        with memfile.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                          dtype='float32', crs='EPSG:4326', transform=transform) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button(label=f"💾 Export TIFF", data=memfile.read(), file_name=filename, mime="image/tiff", key=key)

# --- SIDEBAR ---
with st.sidebar:
    st.header("🔑 1. Credentials")
    CLIENT_ID = st.text_input("Client ID", type="password")
    CLIENT_SECRET = st.text_input("Client Secret", type="password")

    st.markdown("---")
    st.header("📍 2. Search Area")
    search_mode = st.radio("Mode:", ["Search City", "Manual Coordinates"])
    city_query = st.text_input("City Name", "Valencia, Spain") if search_mode == "Search City" else None
    radius_km = st.slider("Radius (km)", 1, 20, 5) # Kept small for performance
    date_range = st.date_input("Date Window", value=(datetime.date(2024, 10, 25), datetime.date(2024, 11, 5)))

    with st.expander("📍 Manual Coords"):
        man_lat = st.number_input("Lat", value=st.session_state.last_search_coords_s1[0], format="%.4f")
        man_lon = st.number_input("Lon", value=st.session_state.last_search_coords_s1[1], format="%.4f")

    brightness = st.slider("Radar Gain", 0.5, 10.0, 3.0)
    selected_basemap = st.selectbox("Base Map", ["OpenStreetMap", "Esri World Imagery", "CartoDB Positron"])
    
    fetch_infra = st.toggle("🛰️ Load Infrastructure (OSM)", value=False)
    btn_search = st.button("🔍 FETCH RADAR DATA", type="primary", use_container_width=True)

# --- CORE LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        lat, lon = (man_lat, man_lon)
        if search_mode == "Search City":
            try:
                geolocator = Nominatim(user_agent=f"flood_pro_{st.session_state.app_uuid_s1}")
                location = geolocator.geocode(city_query, timeout=10)
                if location: lat, lon = location.latitude, location.longitude
            except: st.error("Geocoder busy.")

        st.session_state.last_search_coords_s1 = (lat, lon, radius_km)
        offset = (radius_km / 111.32) / 2
        st.session_state.current_bounds_s1 = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
        st.session_state.image_cache_s1 = {}
        
        # New Value: OSM Building Fetch
        # Enhanced Infrastructure Fetch
        if fetch_infra:
            with st.spinner("🛰️ Downloading Building Footprints from OSM..."):
                try:
                    # Using a slightly smaller box than the radar to ensure fast return
                    st.session_state.buildings_gdf = ox.features_from_point(
                        (lat, lon), 
                        dist=radius_km * 1000, 
                        tags={'building': True}
                    )
                    if st.session_state.buildings_gdf is not None:
                        st.sidebar.success(f"✅ {len(st.session_state.buildings_gdf)} buildings loaded!")
                except Exception as e:
                    st.sidebar.warning("⚠️ OSM Timeout. Try a smaller radius (1-5km).")
                    st.session_state.buildings_gdf = None

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
                # Requesting VV and VH
                evalscript = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
                
                for d_str in sel_dates:
                    actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=evalscript, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(actual_date, actual_date))],
                                           responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(600, 600), config=config)
                    st.session_state.image_cache_s1[actual_date] = req.get_data()[0]

            if st.session_state.image_cache_s1:
                cols = st.columns(2)
                for i, d_str in enumerate(sel_dates):
                    actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                    if actual_date in st.session_state.image_cache_s1:
                        with cols[i % 2]:
                            data = st.session_state.image_cache_s1[actual_date]
                            mode = st.radio(f"Band ({actual_date[:10]})", ["VV", "VH", "Dual-Pol"], key=f"m_{i}", horizontal=True)
                            
                            # Shape-safe rendering
                            vv = data[:,:,0] if data.ndim == 3 else data
                            vh = data[:,:,1] if data.ndim == 3 and data.shape[2] > 1 else vv
                            
                            if mode == "VV": img = np.dstack([np.clip(vv*brightness, 0, 1)]*3)
                            elif mode == "VH": img = np.dstack([np.clip(vh*brightness*2, 0, 1)]*3)
                            else: # RGB Composite: R=VV, G=VH, B=Ratio
                                ratio = np.clip(vv/(vh+1e-5), 0, 1)
                                img = np.dstack([np.clip(vv*brightness,0,1), np.clip(vh*brightness,0,1), ratio])

                            m = folium.Map(location=[st.session_state.last_search_coords_s1[0], st.session_state.last_search_coords_s1[1]], zoom_start=13, tiles=selected_basemap)
                            folium.raster_layers.ImageOverlay(image=get_image_url(img), bounds=st.session_state.current_bounds_s1).add_to(m)
                            st_folium(m, height=400, width=None, key=f"map_{i}")

    with tab_ana:
        if len(st.session_state.image_cache_s1) >= 2:
            d_list = list(st.session_state.image_cache_s1.keys())
            c1, c2 = st.columns(2)
            b_date = c1.selectbox("Baseline", d_list, index=0)
            a_date = c2.selectbox("Crisis", d_list, index=1)
            
            # Use safe_db to prevent crashes
            db1 = safe_db(st.session_state.image_cache_s1[b_date][:,:,0])
            db2 = safe_db(st.session_state.image_cache_s1[a_date][:,:,0])
            
            fig, ax = plt.subplots(1, 2, figsize=(10, 4))
            im1 = ax[0].imshow(db1, cmap='magma', vmin=-25, vmax=-5); ax[0].set_title("Pre-Event (dB)")
            im2 = ax[1].imshow(db2, cmap='magma', vmin=-25, vmax=-5); ax[1].set_title("Post-Event (dB)")
            plt.colorbar(im2, ax=ax, orientation='horizontal', fraction=0.05, pad=0.1)
            st.pyplot(fig)

    with tab_flood:
        if len(st.session_state.image_cache_s1) >= 2:
            st.subheader("🚨 Damage Assessment")
            keys = list(st.session_state.image_cache_s1.keys())
            
            sens = st.slider("Flood Threshold (dB Drop)", -15.0, -2.0, -6.0)
            
            # Calculation
            pre = safe_db(st.session_state.image_cache_s1[keys[0]][:,:,0])
            post = safe_db(st.session_state.image_cache_s1[keys[1]][:,:,0])
            flood_mask = ((post - pre) < sens).astype(np.uint8)
            
            # Value Add: Statistics
            flood_pixels = np.count_nonzero(flood_mask)
            total_pixels = flood_mask.size
            st.metric("Estimated Flood Coverage", f"{(flood_pixels/total_pixels)*100:.2f}%")

            m_flood = folium.Map(location=[st.session_state.last_search_coords_s1[0], st.session_state.last_search_coords_s1[1]], zoom_start=14)
            
            # Background
            bg_data = st.session_state.image_cache_s1[keys[1]][:,:,0]
            folium.raster_layers.ImageOverlay(image=get_image_url(np.dstack([np.clip(bg_data*3,0,1)]*3)), 
                                              bounds=st.session_state.current_bounds_s1, opacity=0.5).add_to(m_flood)
            
            # Flood Layer
            mask_rgba = np.zeros((*flood_mask.shape, 4))
            mask_rgba[flood_mask == 1] = [1, 0, 0, 0.7] # Solid Red
            folium.raster_layers.ImageOverlay(image=get_image_url(mask_rgba), bounds=st.session_state.current_bounds_s1).add_to(m_flood)
            
            # New Value: Infrastructure Overlay
            if st.session_state.buildings_gdf is not None:
                folium.GeoJson(st.session_state.buildings_gdf, name="Buildings", 
                               style_function=lambda x: {'color':'orange', 'weight':1, 'fillOpacity':0.2}).add_to(m_flood)
            
            st_folium(m_flood, height=600, width=1200)
            
            create_geotiff_download(flood_mask, "flood_mask.tif", *st.session_state.last_search_coords_s1, "dl_final")
else:
    st.warning("⚠️ Enter your credentials in the sidebar to start.")
