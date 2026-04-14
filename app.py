import streamlit as st
from sentinelhub import (SHConfig, SentinelHubRequest, DataCollection, MimeType, 
                         BBox, CRS, SentinelHubCatalog)
import numpy as np
from geopy.geocoders import Nominatim
import datetime
import folium
from streamlit_folium import st_folium
import base64
from io import BytesIO
from PIL import Image
import uuid

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="Sentinel Explorer Pro")

# --- SESSION STATE ---
if 'search_results' not in st.session_state: st.session_state.search_results = None
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'map_center' not in st.session_state: st.session_state.map_center = [40.4168, -3.7038] 
if 'map_zoom' not in st.session_state: st.session_state.map_zoom = 13
if 'current_bounds' not in st.session_state: st.session_state.current_bounds = None
if 'app_uuid' not in st.session_state: st.session_state.app_uuid = str(uuid.uuid4())

# --- SIDEBAR: SETTINGS ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Search Area")
city_name = st.sidebar.text_input("City Name", "Madrid, Spain")

# FALLBACK: Manual Coordinates
with st.sidebar.expander("📍 Manual Coordinates (Use if Search Fails)"):
    man_lat = st.number_input("Lat", value=40.4168, format="%.4f")
    man_lon = st.number_input("Lon", value=-3.7038, format="%.4f")
    use_manual = st.checkbox("Use manual coordinates")

radius_km = st.sidebar.slider("Radius (km)", 1, 25, 5)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2025, 6, 1), datetime.date(2025, 8, 30)))
btn_search = st.sidebar.button("🔍 SEARCH IMAGES", type="primary", use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.header("3. Visualization")
PRESETS = {
    "Natural Color (B4, B3, B2)": [2, 1, 0],
    "False Color NIR (B8, B4, B3)": [3, 2, 1],
    "Agriculture (B11, B8, B2)": [4, 3, 0],
    "Custom Selection": "CUSTOM"
}
selected_preset = st.sidebar.selectbox("Choose Composition", list(PRESETS.keys()))

BAND_NAMES = {"B02 (Blue)": 0, "B03 (Green)": 1, "B04 (Red)": 2, "B08 (NIR)": 3, "B11 (SWIR1)": 4, "B12 (SWIR2)": 5}
if selected_preset == "Custom Selection":
    c1, c2, c3 = st.sidebar.columns(3)
    rgb_indices = [BAND_NAMES[c1.selectbox("R", list(BAND_NAMES.keys()), index=2)],
                   BAND_NAMES[c2.selectbox("G", list(BAND_NAMES.keys()), index=1)],
                   BAND_NAMES[c3.selectbox("B", list(BAND_NAMES.keys()), index=0)]]
else:
    rgb_indices = PRESETS[selected_preset]

brightness = st.sidebar.slider("Brightness", 0.5, 5.0, 2.5)
opacity = st.sidebar.slider("Satellite Opacity", 0.0, 1.0, 0.8)
selected_basemap = st.sidebar.selectbox("Map Style", ["OpenStreetMap", "CartoDB Positron", "Esri World Imagery"])

def get_image_url(np_img):
    img = Image.fromarray((np_img * 255).astype(np.uint8))
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

# --- MAIN LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig()
    config.sh_client_id, config.sh_client_secret = CLIENT_ID, CLIENT_SECRET

    if btn_search:
        lat, lon = None, None
        if use_manual:
            lat, lon = man_lat, man_lon
        else:
            try:
                # Use unique UUID in user_agent to bypass 429 errors
                geolocator = Nominatim(user_agent=f"sentinel_explorer_{st.session_state.app_uuid}")
                location = geolocator.geocode(city_name, timeout=10)
                if location:
                    lat, lon = location.latitude, location.longitude
                else:
                    st.error("Location not found. Try manual coordinates.")
            except Exception as e:
                st.warning(f"Geocoding Error (429). Switching to manual coordinates below.")
                lat, lon = man_lat, man_lon

        if lat is not None:
            offset = (radius_km / 111.32) / 2
            st.session_state.current_bounds = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
            st.session_state.map_center = [lat, lon]
            catalog = SentinelHubCatalog(config=config)
            bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
            search = catalog.search(DataCollection.SENTINEL2_L2A, bbox=bbox_obj,
                                    time=(str(date_range[0]), str(date_range[1])), filter="eo:cloud_cover < 30")
            st.session_state.search_results = list(search)
            st.session_state.image_cache = {} 

    if st.session_state.search_results:
        results = st.session_state.search_results
        date_options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(results)]
        selected_dates = st.multiselect("Pick 4 dates:", date_options, default=date_options[:min(len(date_options), 4)])

        st.markdown("### 🔗 Map Synchronization")
        l_cols = st.columns(4)
        sync_states = [l_cols[i].checkbox(f"Link {i+1}", value=True) for i in range(4)]

        if st.button("🖼️ DOWNLOAD & RENDER", use_container_width=True):
            if len(selected_dates) == 4:
                lat, lon = st.session_state.map_center
                offset = (radius_km / 111.32) / 2
                bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                evalscript = """//VERSION=3
                function setup() { return { input: ['B02','B03','B04','B08','B11','B12'], output: { bands: 6, sampleType: 'FLOAT32' } }; }
                function evaluatePixel(sample) { return [sample.B02, sample.B03, sample.B04, sample.B08, sample.B11, sample.B12]; }"""
                
                for date_str in selected_dates:
                    idx = int(date_str.split(":")[0])
                    actual_date = results[idx]['properties']['datetime']
                    if actual_date not in st.session_state.image_cache:
                        try:
                            request = SentinelHubRequest(
                                evalscript=evalscript,
                                input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL2_L2A, time_interval=(actual_date, actual_date))],
                                responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                                bbox=bbox_obj, size=(800, 800), config=config)
                            st.session_state.image_cache[actual_date] = request.get_data()[0]
                        except Exception as e:
                            st.error(f"Sentinel API Error: {e}. If it is a 429, please wait 60 seconds.")
                            break

        if len(st.session_state.image_cache) >= 4:
            c_left, c_right = st.columns(2)
            for i, date_str in enumerate(selected_dates):
                idx = int(date_str.split(":")[0])
                actual_date = results[idx]['properties']['datetime']
                if actual_date in st.session_state.image_cache:
                    data = st.session_state.image_cache[actual_date]
                    img_rgb = np.clip(data[:, :, rgb_indices] * brightness, 0, 1)
                    img_url = get_image_url(img_rgb)
                    m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom, tiles=selected_basemap)
                    folium.raster_layers.ImageOverlay(image=img_url, bounds=st.session_state.current_bounds, opacity=opacity).add_to(m)
                    with (c_left if i % 2 == 0 else c_right):
                        st.caption(f"Map {i+1}: {actual_date[:10]}")
                        m_out = st_folium(m, height=350, width=500, key=f"sync_{actual_date}")
                        if m_out and m_out.get('center') and sync_states[i]:
                            new_c = [m_out['center']['lat'], m_out['center']['lng']]
                            new_z = m_out['zoom']
                            if (abs(new_c[0] - st.session_state.map_center[0]) > 0.001 or new_z != st.session_state.map_zoom):
                                st.session_state.map_center = new_c
                                st.session_state.map_zoom = new_z
                                st.rerun()
else:
    st.info("👋 Please enter credentials in the sidebar.")
