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
st.set_page_config(layout="wide", page_title="S1 Radar Explorer Pro", page_icon="📡")

# --- INITIALIZE SESSION STATE ---
if 'search_results_s1' not in st.session_state: st.session_state.search_results_s1 = None
if 'image_cache_s1' not in st.session_state: st.session_state.image_cache_s1 = {}
if 'group_a_pos_s1' not in st.session_state: st.session_state.group_a_pos_s1 = {"center": [40.4168, -3.7038], "zoom": 13}
if 'current_bounds_s1' not in st.session_state: st.session_state.current_bounds_s1 = None
if 'app_uuid_s1' not in st.session_state: st.session_state.app_uuid_s1 = str(uuid.uuid4())
if 'last_search_coords_s1' not in st.session_state: st.session_state.last_search_coords_s1 = None

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    img = Image.fromarray((np_img * 255).astype(np.uint8))
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

def create_geotiff_download(data, filename, lat, lon, radius_km, key):
    """Generates a georeferenced TIFF and avoids Streamlit DuplicateID errors using a key."""
    offset = (radius_km / 111.32) / 2
    west, south = lon - offset, lat - offset
    east, north = lon + offset, lat + offset
    
    height, width = data.shape[:2]
    count = data.shape[2] if len(data.shape) == 3 else 1
    transform = from_bounds(west, south, east, north, width, height)
    
    with MemoryFile() as memfile:
        with memfile.open(
            driver='GTiff', height=height, width=width, count=count,
            dtype='float32', crs='EPSG:4326', transform=transform
        ) as dataset:
            if count == 1:
                dataset.write(data.astype('float32'), 1)
            else:
                for i in range(count):
                    dataset.write(data[:, :, i].astype('float32'), i + 1)
        
        return st.download_button(
            label=f"💾 Export {filename}", 
            data=memfile.read(),
            file_name=filename, 
            mime="image/tiff", 
            use_container_width=True,
            key=key # Prevents DuplicateElementId error
        )

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Area of Interest")
city_name = st.sidebar.text_input("City Name", "Valencia, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 25, 5)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2025, 10, 20), datetime.date(2025, 11, 10)))

with st.sidebar.expander("📍 Manual Coords / Fallback"):
    man_lat = st.number_input("Lat", value=39.4699, format="%.4f")
    man_lon = st.number_input("Lon", value=-0.3763, format="%.4f")
    use_manual = st.checkbox("Force Manual Coordinates")

btn_search = st.sidebar.button("🔍 SEARCH RADAR IMAGES", type="primary", use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.header("3. Display Settings")
brightness = st.sidebar.slider("Radar Gain", 1.0, 10.0, 3.0) 
opacity = st.sidebar.slider("Radar Opacity", 0.0, 1.0, 0.8)
selected_basemap = st.sidebar.selectbox("Base Map Style", ["OpenStreetMap", "CartoDB Positron", "Esri World Imagery"])

# --- CORE SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig()
    config.sh_client_id, config.sh_client_secret = CLIENT_ID, CLIENT_SECRET

    if btn_search:
        lat, lon = None, None
        
        if not use_manual:
            try:
                # Unique agent and delay to prevent 429 Rate Limit errors
                geolocator = Nominatim(user_agent=f"sentinel_explorer_{st.session_state.app_uuid_s1}")
                time.sleep(1.2) 
                location = geolocator.geocode(city_name, timeout=10)
                if location: 
                    lat, lon = location.latitude, location.longitude
                else:
                    st.error("City not found. Using manual fallback.")
            except Exception:
                st.warning("Geocoding Rate Limited (429). Using manual coordinates.")
        
        if use_manual or (lat is None):
            lat, lon = man_lat, man_lon

        if lat:
            st.session_state.last_search_coords_s1 = (lat, lon, radius_km)
            offset = (radius_km / 111.32) / 2
            st.session_state.current_bounds_s1 = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
            st.session_state.group_a_pos_s1 = {"center": [lat, lon], "zoom": 12}
            
            catalog = SentinelHubCatalog(config=config)
            bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
            
            # S1 Search (No cloud filter)
            search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox_obj,
                                    time=(str(date_range[0]), str(date_range[1])))
            st.session_state.search_results_s1 = list(search)
            st.session_state.image_cache_s1 = {}

    # --- TABS ---
    tab_map, tab_analysis = st.tabs(["🗺️ SAR Dashboard", "🧪 Water & Backscatter Lab"])

    with tab_map:
        if st.session_state.search_results_s1:
            results = st.session_state.search_results_s1
            lat, lon, r_km = st.session_state.last_search_coords_s1
            
            date_options = [f"{i}: {r['properties']['datetime'][:10]} ({r['properties'].get('sat:orbit_state', '?')})" for i, r in enumerate(results)]
            selected_dates = st.multiselect("Select 4 dates:", date_options, default=date_options[:min(len(date_options), 4)])

            if st.button("🖼️ RENDER RADAR QUADRANTS", use_container_width=True):
                if len(selected_dates) == 4:
                    offset = (r_km / 111.32) / 2
                    bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                    evalscript = """//VERSION=3
                    function setup() { return { input: ['VV', 'VH'], output: { bands: 2, sampleType: 'FLOAT32' } }; }
                    function evaluatePixel(sample) { return [sample.VV, sample.VH]; }"""
                    
                    for d_str in selected_dates:
                        idx = int(d_str.split(":")[0])
                        actual_date = results[idx]['properties']['datetime']
                        if actual_date not in st.session_state.image_cache_s1:
                            request = SentinelHubRequest(evalscript=evalscript,
                                input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(actual_date, actual_date))],
                                responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                                bbox=bbox_obj, size=(800, 800), config=config)
                            st.session_state.image_cache_s1[actual_date] = request.get_data()[0]

            if len(st.session_state.image_cache_s1) >= 4:
                cols = st.columns(2)
                for i, date_str in enumerate(selected_dates):
                    with cols[i % 2]:
                        actual_date = results[int(date_str.split(":")[0])]['properties']['datetime']
                        data = st.session_state.image_cache_s1[actual_date]
                        
                        with st.expander(f"⚙️ View {i+1} Setup ({actual_date[:10]})"):
                            v_preset = st.selectbox("Composite", ["False Color (VV/VH/Ratio)", "Grayscale (VV)", "Grayscale (VH)"], key=f"v_pre_{i}")
                            create_geotiff_download(data, f"S1_{actual_date[:10]}.tif", lat, lon, r_km, key=f"dl_dash_{i}_{actual_date}")

                        VV, VH = data[:,:,0], data[:,:,1]
                        if "False Color" in v_preset:
                            ratio = np.clip(VV / (VH + 1e-5), 0, 1)
                            img_rgb = np.dstack([np.clip(VV * brightness, 0, 1), np.clip(VH * (brightness*2), 0, 1), ratio])
                        else:
                            chan = VV if "VV" in v_preset else VH
                            v_chan = np.clip(chan * (brightness if "VV" in v_preset else brightness*2), 0, 1)
                            img_rgb = np.dstack([v_chan, v_chan, v_chan])

                        m = folium.Map(location=[lat, lon], zoom_start=12, tiles=selected_basemap)
                        folium.raster_layers.ImageOverlay(image=get_image_url(img_rgb), bounds=st.session_state.current_bounds_s1, opacity=opacity).add_to(m)
                        st_folium(m, height=300, width=500, key=f"s1_map_{i}_{actual_date}")

    with tab_analysis:
        if not st.session_state.image_cache_s1:
            st.warning("Please render images in the Dashboard first.")
        else:
            lat, lon, r_km = st.session_state.last_search_coords_s1
            ana_date = st.selectbox("📅 Select Date", list(st.session_state.image_cache_s1.keys()), key="ana_date_sel")
            data = st.session_state.image_cache_s1[ana_date]
            
            # Convert to Decibels
            VV_db = 10 * np.log10(data[:,:,0] + 1e-10)
            VH_db = 10 * np.log10(data[:,:,1] + 1e-10)

            col_side, col_plot = st.columns([1, 3])
            with col_side:
                target = st.selectbox("Polarization", ["VV (dB)", "VH (dB)"], key="ana_pol")
                val = VV_db if "VV" in target else VH_db
                cmap = st.selectbox("CMap", ["Blues_r", "Greys_r", "viridis", "magma"], index=0, key="ana_cmap")
                db_min, db_max = st.slider("dB Filter", -35.0, 5.0, (-35.0, 5.0), key="ana_db_slider")
                
                masked = np.copy(val)
                masked[(val < db_min) | (val > db_max)] = np.nan
                create_geotiff_download(val, f"S1_{target}_{ana_date[:10]}.tif", lat, lon, r_km, key=f"dl_ana_{ana_date}")

            with col_plot:
                fig, ax = plt.subplots(figsize=(10, 5))
                im = ax.imshow(masked, cmap=cmap, vmin=-30, vmax=0)
                plt.colorbar(im, fraction=0.03, pad=0.04, label="Decibels (dB)")
                ax.axis('off')
                st.pyplot(fig)
                
                # Distribution Histogram
                fig_h, ax_h = plt.subplots(figsize=(10, 2))
                ax_h.hist(val.flatten(), bins=100, color='royalblue', alpha=0.7)
                ax_h.set_title("Pixel Backscatter Distribution (dB)")
                st.pyplot(fig_h)

else:
    st.info("👋 Enter your Credentials in the sidebar to begin.")
