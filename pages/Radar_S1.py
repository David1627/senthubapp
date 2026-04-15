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

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Radar Explorer", page_icon="📡")

# --- INITIALIZE SESSION STATE ---
if 'search_results_s1' not in st.session_state: st.session_state.search_results_s1 = None
if 'image_cache_s1' not in st.session_state: st.session_state.image_cache_s1 = {}
if 'group_a_pos_s1' not in st.session_state: st.session_state.group_a_pos_s1 = {"center": [40.4168, -3.7038], "zoom": 13}
if 'group_b_pos_s1' not in st.session_state: st.session_state.group_b_pos_s1 = {"center": [40.4168, -3.7038], "zoom": 13}
if 'current_bounds_s1' not in st.session_state: st.session_state.current_bounds_s1 = None
if 'app_uuid_s1' not in st.session_state: st.session_state.app_uuid_s1 = str(uuid.uuid4())
if 'last_search_coords_s1' not in st.session_state: st.session_state.last_search_coords_s1 = None

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    img = Image.fromarray((np_img * 255).astype(np.uint8))
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

def create_geotiff_download(data, filename, lat, lon, radius_km):
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
            label=f"💾 Download {filename}", data=memfile.read(),
            file_name=filename, mime="image/tiff", use_container_width=True
        )

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Area of Interest")
city_name = st.sidebar.text_input("City Name", "Valencia, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 25, 5)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2025, 1, 1), datetime.date(2025, 2, 28)))

btn_search = st.sidebar.button("🔍 SEARCH RADAR IMAGES", type="primary", use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.header("3. Global View")
# Radar requires higher multiplier to visualize linear backscatter properly
brightness = st.sidebar.slider("Radar Gain (Brightness)", 1.0, 10.0, 3.0) 
opacity = st.sidebar.slider("Radar Opacity", 0.0, 1.0, 0.8)
selected_basemap = st.sidebar.selectbox("Base Map Style", ["OpenStreetMap", "CartoDB Positron", "Esri World Imagery"])

# --- CORE SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig()
    config.sh_client_id, config.sh_client_secret = CLIENT_ID, CLIENT_SECRET

    if btn_search:
        try:
            geolocator = Nominatim(user_agent=f"sentinel_s1_{st.session_state.app_uuid_s1}")
            location = geolocator.geocode(city_name, timeout=10)
            if location: 
                lat, lon = location.latitude, location.longitude
                st.session_state.last_search_coords_s1 = (lat, lon, radius_km)
                offset = (radius_km / 111.32) / 2
                st.session_state.current_bounds_s1 = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
                start_pos = {"center": [lat, lon], "zoom": 13}
                st.session_state.group_a_pos_s1 = start_pos
                st.session_state.group_b_pos_s1 = start_pos
                
                catalog = SentinelHubCatalog(config=config)
                bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                
                # NO CLOUD FILTER FOR SAR!
                search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox_obj,
                                        time=(str(date_range[0]), str(date_range[1])))
                st.session_state.search_results_s1 = list(search)
                st.session_state.image_cache_s1 = {}
        except Exception as e:
            st.error(f"Geocoding failed: {e}")

    # --- NAVIGATION ---
    tab_map, tab_analysis = st.tabs(["🗺️ SAR Dashboard", "🧪 Water & Backscatter Lab"])

    with tab_map:
        if st.session_state.search_results_s1 and st.session_state.last_search_coords_s1:
            results = st.session_state.search_results_s1
            lat, lon, r_km = st.session_state.last_search_coords_s1
            
            # Identify Orbit Direction (Ascending/Descending)
            date_options = [f"{i}: {r['properties']['datetime'][:10]} ({r['properties'].get('sat:orbit_state', 'Unknown')})" for i, r in enumerate(results)]
            selected_dates = st.multiselect("Pick 4 dates for comparison:", date_options, default=date_options[:min(len(date_options), 4)])

            if st.button("🖼️ RENDER RADAR QUADRANTS", use_container_width=True):
                if len(selected_dates) == 4:
                    offset = (r_km / 111.32) / 2
                    bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                    
                    # Request Linear VV and VH
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
                c_left, c_right = st.columns(2)
                for i, date_str in enumerate(selected_dates):
                    target_col = c_left if i % 2 == 0 else c_right
                    actual_date = results[int(date_str.split(":")[0])]['properties']['datetime']
                    
                    with target_col:
                        data = st.session_state.image_cache_s1[actual_date]
                        VV = data[:, :, 0]
                        VH = data[:, :, 1]
                        
                        with st.expander(f"⚙️ View {i+1} Options ({actual_date[:10]})", expanded=False):
                            v_preset = st.selectbox("SAR Composite", ["False Color (VV/VH/Ratio)", "Grayscale (VV)", "Grayscale (VH)"], key=f"p{i}")
                            v_sync = st.selectbox("Sync Group", ["None", "Group A", "Group B"], key=f"s{i}")
                            create_geotiff_download(data, f"S1_{actual_date[:10]}_VV_VH.tif", lat, lon, r_km)

                        # Create visual RGB representation
                        if v_preset == "False Color (VV/VH/Ratio)":
                            ratio = np.clip(VV / (VH + 1e-5), 0, 1)
                            img_rgb = np.dstack([np.clip(VV * brightness, 0, 1), np.clip(VH * (brightness*2), 0, 1), ratio])
                        elif v_preset == "Grayscale (VV)":
                            v_chan = np.clip(VV * brightness, 0, 1)
                            img_rgb = np.dstack([v_chan, v_chan, v_chan])
                        else:
                            v_chan = np.clip(VH * (brightness*2), 0, 1)
                            img_rgb = np.dstack([v_chan, v_chan, v_chan])

                        pos = st.session_state.group_a_pos_s1 if v_sync == "Group A" else st.session_state.group_b_pos_s1 if v_sync == "Group B" else st.session_state.group_a_pos_s1
                        m = folium.Map(location=pos["center"], zoom_start=pos["zoom"], tiles=selected_basemap)
                        folium.raster_layers.ImageOverlay(image=get_image_url(img_rgb), bounds=st.session_state.current_bounds_s1, opacity=opacity).add_to(m)
                        st_folium(m, height=350, width=550, key=f"s1_v6_{i}_{actual_date}")

    with tab_analysis:
        if not st.session_state.image_cache_s1:
            st.warning("⚠️ Please download data in the Dashboard tab first.")
        else:
            lat, lon, r_km = st.session_state.last_search_coords_s1
            ana_date_str = st.selectbox("📅 Select Capture Date", list(st.session_state.image_cache_s1.keys()), key="s1_lab_date")
            dt_obj = datetime.datetime.fromisoformat(ana_date_str.replace('Z', '+00:00'))
            
            st.markdown(f"## 🌊 Flood & Terrain Lab: {city_name}")
            
            data = st.session_state.image_cache_s1[ana_date_str]
            VV_linear = data[:, :, 0]
            VH_linear = data[:, :, 1]
            
            # Convert linear backscatter to Decibels (dB) for scientific analysis
            # Adding 1e-10 to avoid log(0) warnings
            VV_db = 10 * np.log10(VV_linear + 1e-10)
            VH_db = 10 * np.log10(VH_linear + 1e-10)

            col_sidebar, col_main = st.columns([1, 3])

            with col_sidebar:
                st.subheader("🛠️ Polarization")
                target_idx = st.selectbox("Metric", ["VV Backscatter (dB)", "VH Backscatter (dB)"])
                
                val = VV_db if "VV" in target_idx else VH_db
                
                # Water is very dark in SAR (low dB).
                cmap_sel = st.selectbox("Colormap", ["Blues_r", "Greys_r", "viridis", "plasma"], index=0)
                
                st.markdown("---")
                st.subheader("🔦 Decibel Masking")
                st.write("*Hint: Water is usually < -15 dB in VV*")
                min_mask, max_mask = st.slider("dB Range", -35.0, 5.0, (-35.0, 5.0))
                masked_val = np.copy(val)
                masked_val[(val < min_mask) | (val > max_mask)] = np.nan

                st.markdown("---")
                st.subheader("💾 Export GeoTIFF")
                prefix = "VV_dB" if "VV" in target_idx else "VH_dB"
                create_geotiff_download(val, f"S1_{prefix}_{ana_date_str[:10]}.tif", lat, lon, r_km)
                
                show_overlay = st.checkbox("Overlay Base", value=False)
                overlay_alpha = st.slider("Transparency", 0.0, 1.0, 0.5) if show_overlay else 1.0

            with col_main:
                fig, ax = plt.subplots(figsize=(10, 5))
                if show_overlay:
                    # Show grayscale background if overlay is on
                    bg = np.clip(VV_linear * brightness, 0, 1)
                    ax.imshow(np.dstack([bg, bg, bg]))
                    im = ax.imshow(masked_val, cmap=cmap_sel, alpha=overlay_alpha, vmin=-30, vmax=0)
                else:
                    im = ax.imshow(masked_val, cmap=cmap_sel, vmin=-30, vmax=0)
                
                plt.colorbar(im, fraction=0.03, pad=0.04, label="Decibels (dB)")
                ax.axis('off')
                st.pyplot(fig)

                st.markdown("### 📊 Decibel Distribution")
                clean_data = val[~np.isnan(val)]
                
                fig_h, ax_h = plt.subplots(figsize=(10, 2))
                ax_h.hist(clean_data, bins=100, color='royalblue', edgecolor='black', alpha=0.7)
                ax_h.set_title(f"{target_idx} Frequency", fontsize=10)
                st.pyplot(fig_h)

else:
    st.info("👋 Enter your Client ID and Secret in the sidebar to start exploring.")
