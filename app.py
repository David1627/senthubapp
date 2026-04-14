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

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="Sentinel Explorer Pro")

# --- INITIALIZE SESSION STATE ---
if 'search_results' not in st.session_state:
    st.session_state.search_results = None
if 'image_cache' not in st.session_state:
    st.session_state.image_cache = {}
if 'map_center' not in st.session_state:
    st.session_state.map_center = [40.4168, -3.7038] 
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 13
if 'current_bounds' not in st.session_state:
    st.session_state.current_bounds = None

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password", key="cid")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password", key="cs")

st.sidebar.markdown("---")
st.sidebar.header("2. Search Area")
city_name = st.sidebar.text_input("City Name", "Madrid, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 25, 5)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2025, 6, 1), datetime.date(2025, 8, 30)))

# THE TRIGGER
btn_search = st.sidebar.button("🔍 SEARCH IMAGES", type="primary", use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.header("3. Display Settings")
BASEMAPS = {
    "OpenStreetMap": "OpenStreetMap",
    "CartoDB Positron": "CartoDB positron",
    "CartoDB Dark Matter": "CartoDB dark_matter",
    "Esri World Imagery": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
}
selected_basemap = st.sidebar.selectbox("Base Map", list(BASEMAPS.keys()))
show_labels = st.sidebar.checkbox("Show Labels", value=True)
opacity = st.sidebar.slider("Layer Opacity", 0.0, 1.0, 0.8)
brightness = st.sidebar.slider("Brightness", 0.5, 5.0, 2.5)

BANDS_MAP = {"Natural Color": [3, 2, 1], "False Color (NIR)": [7, 3, 2], "Shortwave Infrared": [11, 7, 3]}
selected_combo = st.sidebar.selectbox("Band Combination", list(BANDS_MAP.keys()))

# --- HELPERS ---
def get_image_url(np_img):
    img = Image.fromarray((np_img * 255).astype(np.uint8))
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

# --- LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig()
    config.sh_client_id, config.sh_client_secret = CLIENT_ID, CLIENT_SECRET

    # EXECUTE SEARCH
    if btn_search:
        try:
            geolocator = Nominatim(user_agent="sentinel_explorer_v2")
            location = geolocator.geocode(city_name, timeout=10)
            if location:
                lat, lon = location.latitude, location.longitude
                offset = (radius_km / 111.32) / 2
                st.session_state.current_bounds = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
                st.session_state.map_center = [lat, lon]
                
                catalog = SentinelHubCatalog(config=config)
                bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                search = catalog.search(DataCollection.SENTINEL2_L2A, bbox=bbox_obj,
                                        time=(str(date_range[0]), str(date_range[1])), filter="eo:cloud_cover < 30")
                st.session_state.search_results = list(search)
                st.session_state.image_cache = {} # Reset images for new location
            else:
                st.error("Location not found.")
        except Exception as e:
            st.error(f"Error: {e}")

    # UI PART 2: SHOW RESULTS (Always visible if results exist)
    if st.session_state.search_results:
        st.subheader("Comparison Dashboard")
        results = st.session_state.search_results
        date_options = [f"{i}: {r['properties']['datetime'][:10]} (Cloud: {r['properties']['eo:cloud_cover']}%)" for i, r in enumerate(results)]
        
        selected_dates = st.multiselect("Pick 4 dates for the quadrant view:", date_options, default=date_options[:min(len(date_options), 4)])

        # Sync Toggle
        st.markdown("### 🔗 Sync Maps")
        l_cols = st.columns(4)
        sync_1 = l_cols[0].checkbox("Sync 1", value=True)
        sync_2 = l_cols[1].checkbox("Sync 2", value=True)
        sync_3 = l_cols[2].checkbox("Sync 3", value=True)
        sync_4 = l_cols[3].checkbox("Sync 4", value=True)
        sync_states = [sync_1, sync_2, sync_3, sync_4]

        # DOWNLOAD BUTTON
        if st.button("🖼️ LOAD SATELLITE DATA", use_container_width=True):
            if len(selected_dates) == 4:
                lat, lon = st.session_state.map_center
                offset = (radius_km / 111.32) / 2
                bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                
                evalscript = "//VERSION=3\nfunction setup() { return { input: ['B02','B03','B04','B08','B11','B12'], output: { bands: 6, sampleType: 'FLOAT32' } }; }\nfunction evaluatePixel(sample) { return [sample.B02, sample.B03, sample.B04, sample.B08, sample.B11, sample.B12]; }"
                
                for date_str in selected_dates:
                    idx = int(date_str.split(":")[0])
                    actual_date = results[idx]['properties']['datetime']
                    if actual_date not in st.session_state.image_cache:
                        with st.spinner(f"Fetching {actual_date[:10]}..."):
                            request = SentinelHubRequest(
                                evalscript=evalscript,
                                input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL2_L2A, time_interval=(actual_date, actual_date))],
                                responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                                bbox=bbox_obj, size=(800, 800), config=config)
                            st.session_state.image_cache[actual_date] = request.get_data()[0]

        # QUADRANT RENDERING
        if len(st.session_state.image_cache) >= 4:
            # Map band names to our reduced 6-band request: [B02, B03, B04, B08, B11, B12]
            # Indices: B02=0, B03=1, B04=2, B08=3, B11=4, B12=5
            COMBO_MAP = {
                "Natural Color": [2, 1, 0],       # B04, B03, B02
                "False Color (NIR)": [3, 2, 1],   # B08, B04, B03
                "Shortwave Infrared": [5, 4, 3]   # B12, B11, B08
            }
            rgb_indices = COMBO_MAP[selected_combo]
            
            c_left, c_right = st.columns(2)
            for i, date_str in enumerate(selected_dates):
                idx = int(date_str.split(":")[0])
                actual_date = results[idx]['properties']['datetime']
                
                if actual_date in st.session_state.image_cache:
                    data = st.session_state.image_cache[actual_date]
                    img_rgb = np.clip(data[:, :, rgb_indices] * brightness, 0, 1)
                    img_url = get_image_url(img_rgb)

                    # Basemap logic
                    tile_url = BASEMAPS[selected_basemap]
                    m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
                    if "http" in tile_url:
                        folium.TileLayer(tiles=tile_url, attr=selected_basemap).add_to(m)
                    else:
                        folium.TileLayer(tile_url).add_to(m)
                    
                    if show_labels:
                        folium.TileLayer(tiles="https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png", attr="Labels", overlay=True).add_to(m)

                    folium.raster_layers.ImageOverlay(image=img_url, bounds=st.session_state.current_bounds, opacity=opacity).add_to(m)
                    
                    with (c_left if i % 2 == 0 else c_right):
                        st.caption(f"Map {i+1}: {actual_date[:10]}")
                        m_out = st_folium(m, height=350, width=550, key=f"quad_{actual_date}")
                        
                        # Sync Logic
                        if m_out and m_out.get('center') and sync_states[i]:
                            new_c = [m_out['center']['lat'], m_out['center']['lng']]
                            new_z = m_out['zoom']
                            if (abs(new_c[0] - st.session_state.map_center[0]) > 0.001 or new_z != st.session_state.map_zoom):
                                st.session_state.map_center = new_c
                                st.session_state.map_zoom = new_z
                                st.rerun()

    elif not CLIENT_ID or not CLIENT_SECRET:
        st.info("👋 Welcome! Please enter your credentials in the sidebar to get started.")
    else:
        st.info("👈 Enter a city and click **SEARCH IMAGES**.")
