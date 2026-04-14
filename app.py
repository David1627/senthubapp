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

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="Sentinel Explorer Pro V6")

# --- INITIALIZE SESSION STATE ---
if 'search_results' not in st.session_state: st.session_state.search_results = None
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'group_a_pos' not in st.session_state: st.session_state.group_a_pos = {"center": [40.4168, -3.7038], "zoom": 13}
if 'group_b_pos' not in st.session_state: st.session_state.group_b_pos = {"center": [40.4168, -3.7038], "zoom": 13}
if 'current_bounds' not in st.session_state: st.session_state.current_bounds = None
if 'app_uuid' not in st.session_state: st.session_state.app_uuid = str(uuid.uuid4())

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    img = Image.fromarray((np_img * 255).astype(np.uint8))
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

def get_season(month):
    if month in [12, 1, 2]: return "Winter ❄️"
    if month in [3, 4, 5]: return "Spring 🌱"
    if month in [6, 7, 8]: return "Summer ☀️"
    return "Autumn 🍂"

BAND_NAMES = {"B02 (Blue)": 0, "B03 (Green)": 1, "B04 (Red)": 2, "B08 (NIR)": 3, "B11 (SWIR1)": 4, "B12 (SWIR2)": 5}
PRESETS = {"Natural Color": [2, 1, 0], "False Color NIR": [3, 2, 1], "Agriculture": [4, 3, 0], "Custom": "CUSTOM"}

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Area of Interest")
city_name = st.sidebar.text_input("City Name", "Madrid, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 25, 5)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2025, 6, 1), datetime.date(2025, 8, 30)))

with st.sidebar.expander("📍 Manual Coords"):
    man_lat = st.number_input("Lat", value=40.4168, format="%.4f")
    man_lon = st.number_input("Lon", value=-3.7038, format="%.4f")
    use_manual = st.checkbox("Use manual coordinates")

btn_search = st.sidebar.button("🔍 SEARCH IMAGES", type="primary", use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.header("3. Global View")
brightness = st.sidebar.slider("Brightness", 0.5, 5.0, 2.5)
opacity = st.sidebar.slider("Satellite Opacity", 0.0, 1.0, 0.8)
selected_basemap = st.sidebar.selectbox("Base Map Style", ["OpenStreetMap", "CartoDB Positron", "Esri World Imagery"])

# --- CORE SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig()
    config.sh_client_id, config.sh_client_secret = CLIENT_ID, CLIENT_SECRET

    if btn_search:
        lat, lon = None, None
        if use_manual:
            lat, lon = man_lat, man_lon
        else:
            try:
                geolocator = Nominatim(user_agent=f"sentinel_pro_{st.session_state.app_uuid}")
                location = geolocator.geocode(city_name, timeout=10)
                if location: lat, lon = location.latitude, location.longitude
            except: pass
        
        if lat:
            offset = (radius_km / 111.32) / 2
            st.session_state.current_bounds = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
            start_pos = {"center": [lat, lon], "zoom": 13}
            st.session_state.group_a_pos = start_pos
            st.session_state.group_b_pos = start_pos
            
            catalog = SentinelHubCatalog(config=config)
            bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
            search = catalog.search(DataCollection.SENTINEL2_L2A, bbox=bbox_obj,
                                    time=(str(date_range[0]), str(date_range[1])), filter="eo:cloud_cover < 30")
            st.session_state.search_results = list(search)
            st.session_state.image_cache = {}

# --- NAVIGATION ---
tab_map, tab_analysis = st.tabs(["🗺️ Comparison Dashboard", "🧪 Analysis Lab"])

with tab_map:
    if st.session_state.search_results:
        results = st.session_state.search_results
        st.info(f"Total Images Found: {len(results)}")
        date_options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(results)]
        selected_dates = st.multiselect("Pick 4 dates:", date_options, default=date_options[:min(len(date_options), 4)])

        if st.button("🖼️ LOAD IMAGES", use_container_width=True):
            if len(selected_dates) == 4:
                lat, lon = st.session_state.group_a_pos["center"]
                offset = (radius_km / 111.32) / 2
                bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                evalscript = "//VERSION=3\nfunction setup() { return { input: ['B02','B03','B04','B08','B11','B12'], output: { bands: 6, sampleType: 'FLOAT32' } }; }\nfunction evaluatePixel(sample) { return [sample.B02, sample.B03, sample.B04, sample.B08, sample.B11, sample.B12]; }"
                
                for d_str in selected_dates:
                    idx = int(d_str.split(":")[0])
                    actual_date = results[idx]['properties']['datetime']
                    if actual_date not in st.session_state.image_cache:
                        with st.spinner(f"Downloading {actual_date[:10]}..."):
                            request = SentinelHubRequest(evalscript=evalscript,
                                input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL2_L2A, time_interval=(actual_date, actual_date))],
                                responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                                bbox=bbox_obj, size=(800, 800), config=config)
                            st.session_state.image_cache[actual_date] = request.get_data()[0]

        if len(st.session_state.image_cache) >= 4:
            c_left, c_right = st.columns(2)
            for i, date_str in enumerate(selected_dates):
                target_col = c_left if i % 2 == 0 else c_right
                actual_date = results[int(date_str.split(":")[0])]['properties']['datetime']
                
                with target_col:
                    with st.expander(f"View {i+1} Config", expanded=False):
                        v_preset = st.selectbox("Combo", list(PRESETS.keys()), key=f"p{i}")
                        v_sync = st.selectbox("Sync", ["None", "Group A", "Group B"], key=f"s{i}")
                        v_rgb = PRESETS[v_preset] if v_preset != "Custom" else [BAND_NAMES[st.selectbox("R", list(BAND_NAMES.keys()), index=2, key=f"r{i}")], BAND_NAMES[st.selectbox("G", list(BAND_NAMES.keys()), index=1, key=f"g{i}")], BAND_NAMES[st.selectbox("B", list(BAND_NAMES.keys()), index=0, key=f"b{i}")]]

                    pos = st.session_state.group_a_pos if v_sync == "Group A" else st.session_state.group_b_pos if v_sync == "Group B" else st.session_state.group_a_pos
                    m = folium.Map(location=pos["center"], zoom_start=pos["zoom"], tiles=selected_basemap)
                    
                    data = st.session_state.image_cache[actual_date]
                    img_rgb = np.clip(data[:, :, v_rgb] * brightness, 0, 1)
                    folium.raster_layers.ImageOverlay(image=get_image_url(img_rgb), bounds=st.session_state.current_bounds, opacity=opacity).add_to(m)
                    
                    m_out = st_folium(m, height=350, width=550, key=f"v6_{i}_{actual_date}", returned_objects=["center", "zoom"])

                    if m_out and m_out.get('center') and v_sync != "None":
                        new_lat, new_lng, new_z = round(m_out['center']['lat'], 4), round(m_out['center']['lng'], 4), m_out['zoom']
                        sk = 'group_a_pos' if v_sync == "Group A" else 'group_b_pos'
                        if abs(new_lat - st.session_state[sk]["center"][0]) > 0.001 or new_z != st.session_state[sk]["zoom"]:
                            st.session_state[sk] = {"center": [new_lat, new_lng], "zoom": new_z}
                            st.rerun()

with tab_analysis:
    if not st.session_state.image_cache:
        st.warning("Please download data in the Dashboard tab first.")
    else:
        ana_date_str = st.selectbox("Analysis Date", list(st.session_state.image_cache.keys()))
        dt_obj = datetime.datetime.fromisoformat(ana_date_str.replace('Z', '+00:00'))
        
        st.markdown(f"### 📊 Site Report: {city_name}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Date", dt_obj.strftime("%Y-%m-%d"))
        m2.metric("Season", get_season(dt_obj.month))
        m3.metric("Day", dt_obj.strftime("%A"))
        m4.metric("Month", dt_obj.strftime("%B"))

        st.markdown("---")
        col_ctrl, col_viz = st.columns([1, 2])
        
        with col_ctrl:
            idx_choice = st.selectbox("Spectral Index", ["NDVI", "NDMI", "NDWI", "NDBI"])
            cmap_choice = st.selectbox("Color Map", ["RdYlGn", "magma", "viridis", "Spectral", "terrain"])
            
            data = st.session_state.image_cache[ana_date_str]
            B3, B4, B8, B11 = data[:,:,1], data[:,:,2], data[:,:,3], data[:,:,4]
            
            if idx_choice == "NDVI": val = (B8 - B4) / (B8 + B4 + 1e-8)
            elif idx_choice == "NDMI": val = (B8 - B11) / (B8 + B11 + 1e-8)
            elif idx_choice == "NDWI": val = (B3 - B8) / (B3 + B8 + 1e-8)
            elif idx_choice == "NDBI": val = (B11 - B8) / (B11 + B8 + 1e-8)

            st.table(pd.DataFrame({"Metric": ["Mean", "Max", "Min"], "Value": [np.mean(val), np.max(val), np.min(val)]}))
            
            fig_h, ax_h = plt.subplots(figsize=(4, 2))
            ax_h.hist(val.flatten(), bins=30, color='green', alpha=0.7)
            st.pyplot(fig_h)

        with col_viz:
            fig_v, ax_v = plt.subplots(figsize=(10, 8))
            im = ax_v.imshow(val, cmap=cmap_choice)
            plt.colorbar(im)
            ax_v.axis('off')
            st.pyplot(fig_v)
            
            with st.expander("🔬 Formula Info"):
                formulas = {"NDVI": r"\frac{NIR - Red}{NIR + Red}", "NDMI": r"\frac{NIR - SWIR}{NIR + SWIR}", "NDWI": r"\frac{Green - NIR}{Green + NIR}", "NDBI": r"\frac{SWIR - NIR}{SWIR + NIR}"}
                st.latex(formulas[idx_choice])
