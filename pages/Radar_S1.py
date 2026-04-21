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
    if np_img is None: return ""
    # Ensure values are 0-255 for PIL
    img_data = (np_img * 255).astype(np.uint8)
    img = Image.fromarray(img_data)
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.base64encode(buffered.getvalue()).decode()}"

def create_geotiff_download(data, filename, lat, lon, radius_km, key):
    offset = (radius_km / 111.32) / 2
    transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, data.shape[1], data.shape[0])
    with MemoryFile() as memfile:
        with memfile.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                          dtype='float32', crs='EPSG:4326', transform=transform) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button(label=f"💾 Export TIFF", data=memfile.read(), file_name=filename, mime="image/tiff", key=key)

def create_geojson_download(mask, lat, lon, radius_km):
    offset = (radius_km / 111.32) / 2
    transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, mask.shape[1], mask.shape[0])
    mask_int = mask.astype('int16')
    shapes = features.shapes(mask_int, mask=(mask_int > 0), transform=transform)
    features_list = [{"type": "Feature", "properties": {"class": "flood_area"}, "geometry": geom} for geom, val in shapes]
    geojson_data = {"type": "FeatureCollection", "features": features_list}
    return st.download_button(label="📐 Download GeoJSON", data=json.dumps(geojson_data), file_name="flood.geojson", mime="application/json", use_container_width=True)

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Search Area")
search_mode = st.sidebar.radio("Location Mode:", ["Search City", "Manual Coordinates"])

if search_mode == "Search City":
    city_query = st.sidebar.text_input("City Name", "Valencia, Spain")
    use_manual = False
else:
    use_manual = True

radius_km = st.sidebar.slider("Radius (km)", 1, 50, 10)
date_range = st.sidebar.date_input("Optional Date Window", value=(datetime.date(2024, 10, 20), datetime.date(2024, 11, 15)))

with st.sidebar.expander("📍 Manual X/Y Coords"):
    man_lat = st.number_input("Lat", value=39.4699, format="%.6f")
    man_lon = st.number_input("Lon", value=-0.3763, format="%.6f")

st.sidebar.markdown("---")
st.sidebar.header("3. Visualization")
brightness = st.sidebar.slider("Radar Gain", 0.5, 10.0, 3.0)
selected_basemap = st.sidebar.selectbox("Base Map", ["OpenStreetMap", "Esri World Imagery", "CartoDB Positron"])

btn_search = st.sidebar.button("🔍 FETCH RADAR DATA", type="primary", use_container_width=True)

# --- SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        lat, lon = None, None
        if search_mode == "Search City":
            try:
                geolocator = Nominatim(user_agent=f"flood_explorer_{st.session_state.app_uuid_s1}")
                time.sleep(1) 
                location = geolocator.geocode(city_query, timeout=10)
                if location:
                    lat, lon = location.latitude, location.longitude
                else: st.error("City not found.")
            except: st.error("Geocoding timeout.")

        if use_manual or (lat is None): lat, lon = man_lat, man_lon

        if lat:
            st.session_state.last_search_coords_s1 = (lat, lon, radius_km)
            offset = (radius_km / 111.32) / 2
            st.session_state.current_bounds_s1 = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
            st.session_state.image_cache_s1 = {}
            
            catalog = SentinelHubCatalog(config=config)
            bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
            search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox_obj, time=(str(date_range[0]), str(date_range[1])))
            st.session_state.search_results_s1 = list(search)

    # --- TABS ---
    tab_dash, tab_ana, tab_flood = st.tabs(["🗺️ Dashboard", "🧪 Lab", "🚨 Flood Mapping"])

    with tab_dash:
        if st.session_state.search_results_s1:
            res = st.session_state.search_results_s1
            lat, lon, r_km = st.session_state.last_search_coords_s1
            date_options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(res)]
            sel_dates = st.multiselect("Select dates to render:", date_options, default=date_options[:min(len(date_options), 2)])

            if st.button("🖼️ RENDER RADAR", use_container_width=True):
                offset = (r_km / 111.32) / 2
                bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                evalscript = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
                for d_str in sel_dates:
                    actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=evalscript, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(actual_date, actual_date))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(500, 500), config=config)
                    st.session_state.image_cache_s1[actual_date] = req.get_data()[0]

            if st.session_state.image_cache_s1:
                cols = st.columns(2)
                for i, d_key in enumerate(sel_dates):
                    actual_date = res[int(d_key.split(":")[0])]['properties']['datetime']
                    if actual_date in st.session_state.image_cache_s1:
                        with cols[i % 2]:
                            data = st.session_state.image_cache_s1[actual_date]
                            pol = st.radio(f"Mode ({actual_date[:10]})", ["VV", "VH", "False Color"], key=f"p_{i}", horizontal=True)
                            
                            # Build image array
                            if pol == "VV": 
                                img = np.dstack([np.clip(data[:,:,0]*brightness,0,1)]*3)
                            elif pol == "VH": 
                                img = np.dstack([np.clip(data[:,:,1]*brightness*2,0,1)]*3)
                            else:
                                ratio = np.clip(data[:,:,0]/(data[:,:,1]+1e-5),0,1)
                                img = np.dstack([np.clip(data[:,:,0]*brightness,0,1), np.clip(data[:,:,1]*brightness,0,1), ratio])
                            
                            m = folium.Map(location=[lat, lon], zoom_start=12, tiles=selected_basemap)
                            img_url = get_image_url(img)
                            if img_url:
                                folium.raster_layers.ImageOverlay(image=img_url, bounds=st.session_state.current_bounds_s1).add_to(m)
                                st_folium(m, height=350, width=None, key=f"map_{i}")

    with tab_ana:
        if st.session_state.image_cache_s1:
            ana_date = st.selectbox("Analyze Date", list(st.session_state.image_cache_s1.keys()))
            db_val = 10 * np.log10(st.session_state.image_cache_s1[ana_date][:,:,0] + 1e-10)
            fig, ax = plt.subplots(figsize=(7, 3))
            im = ax.imshow(db_val, cmap='viridis', vmin=-25, vmax=0)
            plt.colorbar(im, label="dB")
            st.pyplot(fig)

    with tab_flood:
        if len(st.session_state.image_cache_s1) >= 2:
            d_list = list(st.session_state.image_cache_s1.keys())
            c1, c2, c3 = st.columns(3)
            before = c1.selectbox("Baseline (Dry)", d_list, index=0)
            after = c2.selectbox("Crisis (Wet)", d_list, index=1)
            f_color = c3.color_picker("Flood Color", "#FF0000")

            b_db = 10 * np.log10(st.session_state.image_cache_s1[before][:,:,0] + 1e-10)
            a_db = 10 * np.log10(st.session_state.image_cache_s1[after][:,:,0] + 1e-10)
            flood_mask = ((a_db - b_db) < st.slider("Sensitivity", -15.0, -2.0, -6.0)).astype(float)
            if st.checkbox("Exclude Permanent Water"): flood_mask[b_db < -16] = 0
            
            col_m, col_exp = st.columns([3, 1])
            with col_exp:
                st.write("### 📥 Downloads")
                create_geotiff_download(flood_mask, "flood.tif", *st.session_state.last_search_coords_s1, key="dl_t")
                create_geojson_download(flood_mask, *st.session_state.last_search_coords_s1)
            
            with col_m:
                m_f = folium.Map(location=[lat, lon], zoom_start=12, tiles=selected_basemap)
                bg = np.dstack([np.clip(st.session_state.image_cache_s1[after][:,:,0]*brightness,0,1)]*3)
                folium.raster_layers.ImageOverlay(image=get_image_url(bg), bounds=st.session_state.current_bounds_s1, opacity=0.4).add_to(m_f)
                
                h = f_color.lstrip('#'); rgb = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
                mask_rgb = np.zeros((*flood_mask.shape, 4))
                mask_rgb[flood_mask == 1] = [*rgb, 0.8]
                folium.raster_layers.ImageOverlay(image=get_image_url(mask_rgb), bounds=st.session_state.current_bounds_s1).add_to(m_f)
                st_folium(m_f, height=500, width=None, key="f_map")
        else:
            st.info("Render at least 2 images in Dashboard first.")
else:
    st.info("Enter credentials to begin.")
