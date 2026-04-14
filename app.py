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
# This is the "brain" that keeps data from disappearing
if 'search_results' not in st.session_state:
    st.session_state.search_results = None
if 'image_cache' not in st.session_state:
    st.session_state.image_cache = {}
if 'bbox' not in st.session_state:
    st.session_state.bbox = None

# --- SIDEBAR ---
st.sidebar.header("1. Settings")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Search Parameters")
city_name = st.sidebar.text_input("City Name", "Madrid, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 20, 5)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2025, 6, 1), datetime.date(2025, 8, 30)))
cloud_limit = st.sidebar.slider("Max Cloud Cover (%)", 0, 100, 10)

st.sidebar.markdown("---")
st.sidebar.header("3. Visualization")
BANDS_MAP = {"Coastal": 0, "Blue": 1, "Green": 2, "Red": 3, "NIR": 7, "SWIR1": 10, "SWIR2": 11}
c1, c2, c3 = st.sidebar.columns(3)
r_band = c1.selectbox("R", list(BANDS_MAP.keys()), index=3)
g_band = c2.selectbox("G", list(BANDS_MAP.keys()), index=2)
b_band = c3.selectbox("B", list(BANDS_MAP.keys()), index=1)
brightness = st.sidebar.slider("Brightness", 0.5, 5.0, 2.5)

# Clear button to reset everything
if st.sidebar.button(" Reset Cache"):
    st.session_state.search_results = None
    st.session_state.image_cache = {}
    st.rerun()

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
    
    geolocator = Nominatim(user_agent="sentinel_explorer")
    location = geolocator.geocode(city_name)
    
    if location:
        lat, lon = location.latitude, location.longitude
        offset = (radius_km / 111.32) / 2 
        bounds = [[lat - offset, lon - offset], [lat + offset, lon + offset]]
        st.session_state.bbox = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)

        # BUTTON 1: SEARCH
        if st.sidebar.button(" Search & Update Area", type="primary"):
            with st.spinner("Searching..."):
                catalog = SentinelHubCatalog(config=config)
                search = catalog.search(DataCollection.SENTINEL2_L2A, bbox=st.session_state.bbox,
                                        time=(str(date_range[0]), str(date_range[1])), 
                                        filter=f"eo:cloud_cover < {cloud_limit}")
                st.session_state.search_results = list(search)
                st.session_state.image_cache = {} # Clear images when new search happens

        # IF RESULTS EXIST
        if st.session_state.search_results:
            results = st.session_state.search_results
            options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(results)]
            
            # Use session state to keep selection
            selected = st.multiselect("Select exactly 4 dates:", options, 
                                      default=options[:min(len(options), 4)])

            # BUTTON 2: DOWNLOAD & RENDER
            if st.button(" Render / Refresh Maps"):
                evalscript = """
                //VERSION=3
                function setup() { 
                    return { input: ["B01","B02","B03","B04","B05","B06","B07","B08","B8A","B09","B11","B12"], 
                             output: { bands: 12, sampleType: "FLOAT32" } }; 
                }
                function evaluatePixel(sample) {
                    return [sample.B01, sample.B02, sample.B03, sample.B04, sample.B05, sample.B06, 
                            sample.B07, sample.B08, sample.B8A, sample.B09, sample.B11, sample.B12];
                }
                """
                
                if len(selected) == 4:
                    for selection in selected:
                        res_idx = int(selection.split(":")[0])
                        img_date = results[res_idx]['properties']['datetime']
                        
                        # Only download if we don't already have it
                        if img_date not in st.session_state.image_cache:
                            with st.spinner(f"Downloading {img_date[:10]}..."):
                                request = SentinelHubRequest(
                                    evalscript=evalscript,
                                    input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL2_L2A, time_interval=(img_date, img_date))],
                                    responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                                    bbox=st.session_state.bbox, size=(800, 800), config=config
                                )
                                st.session_state.image_cache[img_date] = request.get_data()[0]

            # DISPLAY GRID (This part now pulls from the "cache" in session_state)
            if len(st.session_state.image_cache) >= 4:
                col1, col2 = st.columns(2)
                for idx, selection in enumerate(selected):
                    res_idx = int(selection.split(":")[0])
                    date_key = results[res_idx]['properties']['datetime']
                    
                    if date_key in st.session_state.image_cache:
                        raw_data = st.session_state.image_cache[date_key]
                        r_i, g_i, b_i = BANDS_MAP[r_band], BANDS_MAP[g_band], BANDS_MAP[b_band]
                        img_rgb = np.clip(raw_data[:, :, [r_i, g_i, b_i]] * brightness, 0, 1)
                        img_url = get_image_url(img_rgb)

                        m = folium.Map(location=[lat, lon], zoom_start=13, tiles="OpenStreetMap")
                        folium.raster_layers.ImageOverlay(image=img_url, bounds=bounds, opacity=0.7).add_to(m)
                        
                        target_col = col1 if idx % 2 == 0 else col2
                        with target_col:
                            st.markdown(f"**Date: {date_key[:10]}**")
                            st_folium(m, height=400, width=500, key=f"map_{date_key}")
                else:
                    if len(selected) != 4:
                        st.warning("Please select exactly 4 dates.")
    else:
        st.error("Location not found.")
else:
    st.info(" Welcome! Enter credentials and click 'Search' to start.")
