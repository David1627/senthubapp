import streamlit as st
from sentinelhub import (SHConfig, SentinelHubRequest, DataCollection, MimeType, 
                         BBox, CRS, SentinelHubCatalog)
import numpy as np
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
import datetime
import folium
from streamlit_folium import st_folium
import base64
from io import BytesIO
from PIL import Image
import time

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

# --- SIDEBAR: AUTHENTICATION ---
st.sidebar.header("1. Authentication")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

# --- SIDEBAR: BASE MAP SETTINGS ---
st.sidebar.markdown("---")
st.sidebar.header("2. Map & Layers")
BASEMAPS = {
    "OpenStreetMap": "OpenStreetMap",
    "CartoDB Positron": "CartoDB positron",
    "CartoDB Dark Matter": "CartoDB dark_matter",
    "Esri World Imagery": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "Esri World Terrain": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}"
}
selected_basemap = st.sidebar.selectbox("Base Map", list(BASEMAPS.keys()))
show_labels = st.sidebar.checkbox("Show Labels / Roads", value=True)
opacity = st.sidebar.slider("Satellite Opacity", 0.0, 1.0, 0.8)
brightness = st.sidebar.slider("Brightness", 0.5, 5.0, 2.5)

# --- SIDEBAR: SEARCH PARAMETERS ---
st.sidebar.markdown("---")
st.sidebar.header("3. Search Area")
city_name = st.sidebar.text_input("City Name", "Madrid, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 20, 5)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2025, 6, 1), datetime.date(2025, 8, 30)))

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    img = Image.fromarray((np_img * 255).astype(np.uint8))
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

# --- MAIN LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig()
    config.sh_client_id, config.sh_client_secret = CLIENT_ID, CLIENT_SECRET

    # Robust Geocoding
    location = None
    if city_name:
        try:
            # Using a custom user_agent to avoid service blocking
            geolocator = Nominatim(user_agent=f"sentinel_explorer_{hash(city_name)}")
            location = geolocator.geocode(city_name, timeout=10)
        except (GeocoderServiceError, GeocoderTimedOut):
            st.error("🌍 Geocoding service is temporarily busy. Please wait a moment and click Search again.")
            time.sleep(1)
    
    if location:
        lat, lon = location.latitude, location.longitude
        offset = (radius_km / 111.32) / 2 
        bounds = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
        bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)

        if st.sidebar.button("🔍 Search Images", type="primary"):
            try:
                catalog = SentinelHubCatalog(config=config)
                search = catalog.search(
                    DataCollection.SENTINEL2_L2A, 
                    bbox=bbox_obj,
                    time=(str(date_range[0]), str(date_range[1])), 
                    filter="eo:cloud_cover < 20"
                )
                st.session_state.search_results = list(search)
                st.session_state.image_cache = {}
                st.session_state.map_center = [lat, lon]
                st.session_state.map_zoom = 13
            except Exception as e:
                st.error(f"Sentinel Hub Error: {e}")

        if st.session_state.search_results:
            results = st.session_state.search_results
            options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(results)]
            selected = st.multiselect("Select exactly 4 dates:", options, default=options[:min(len(options), 4)])

            st.markdown("### 🔗 Sync Control")
            link_cols = st.columns(4)
            links = [link_cols[i].checkbox(f"Link Map {i+1}", value=True) for i in range(4)]

            if st.button("🖼️ Render Maps"):
                if len(selected) == 4:
                    for selection in selected:
                        res_idx = int(selection.split(":")[0])
                        date_key = results[res_idx]['properties']['datetime']
                        if date_key not in st.session_state.image_cache:
                            request = SentinelHubRequest(
                                evalscript="//VERSION=3\nfunction setup() { return { input: ['B01','B02','B03','B04','B05','B06','B07','B08','B8A','B09','B11','B12'], output: { bands: 12, sampleType: 'FLOAT32' } }; }\nfunction evaluatePixel(sample) { return [sample.B01, sample.B02, sample.B03, sample.B04, sample.B05, sample.B06, sample.B07, sample.B08, sample.B8A, sample.B09, sample.B11, sample.B12]; }",
                                input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL2_L2A, time_interval=(date_key, date_key))],
                                responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(800, 800), config=config)
                            st.session_state.image_cache[date_key] = request.get_data()[0]

            if len(st.session_state.image_cache) >= 4:
                # Configuration for Bands
                BANDS_MAP = {"Coastal": 0, "Blue": 1, "Green": 2, "Red": 3, "NIR": 7, "SWIR1": 10, "SWIR2": 11}
                r_idx, g_idx, b_idx = BANDS_MAP["Red"], BANDS_MAP["Green"], BANDS_MAP["Blue"] # Defaults

                col_left, col_right = st.columns(2)
                for idx, selection in enumerate(selected):
                    res_idx = int(selection.split(":")[0])
                    date_key = results[res_idx]['properties']['datetime']
                    
                    if date_key in st.session_state.image_cache:
                        raw_data = st.session_state.image_cache[date_key]
                        img_rgb = np.clip(raw_data[:, :, [r_idx, g_idx, b_idx]] * brightness, 0, 1)
                        img_url = get_image_url(img_rgb)

                        tile_provider = BASEMAPS[selected_basemap]
                        m = folium.Map(
                            location=st.session_state.map_center, 
                            zoom_start=st.session_state.map_zoom, 
                            tiles=tile_provider if "http" not in tile_provider else tile_provider,
                            attr="BaseMap" if "http" in tile_provider else None
                        )
                        
                        if "http" in tile_provider:
                            folium.TileLayer(tiles=tile_provider, attr=selected_basemap, name=selected_basemap).add_to(m)
                        
                        if show_labels:
                            folium.TileLayer(
                                tiles="https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png",
                                attr="Labels", overlay=True
                            ).add_to(m)

                        folium.raster_layers.ImageOverlay(image=img_url, bounds=bounds, opacity=opacity).add_to(m)
                        
                        with (col_left if idx % 2 == 0 else col_right):
                            st.write(f"**Map {idx+1}: {date_key[:10]}**")
                            map_data = st_folium(m, height=400, width=500, key=f"map_{idx}")
                            
                            # Sync Logic
                            if map_data and map_data.get('center') and links[idx]:
                                nl, ng, nz = map_data['center']['lat'], map_data['center']['lng'], map_data['zoom']
                                if abs(nl - st.session_state.map_center[0]) > 0.001 or abs(ng - st.session_state.map_center[1]) > 0.001 or nz != st.session_state.map_zoom:
                                    st.session_state.map_center = [nl, ng]
                                    st.session_state.map_zoom = nz
                                    st.rerun()
    else:
        if city_name:
            st.warning("🔍 Searching for location...")
else:
    st.info("👋 Please enter credentials in the sidebar.")
