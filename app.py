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
st.set_page_config(layout="wide", page_title="Sentinel 4-Way Explorer")

# --- AUTHENTICATION ---
st.sidebar.header("1. Settings")
CLIENT_ID = st.sidebar.text_input("SentinelHub Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("SentinelHub Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Location & Time")
city_name = st.sidebar.text_input("City Name", "Madrid, Spain")
radius_km = st.sidebar.slider("Zoom/Radius (km)", 1, 20, 5)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2025, 6, 1), datetime.date(2025, 8, 30)))
cloud_limit = st.sidebar.slider("Max Cloud Cover (%)", 0, 100, 10)

st.sidebar.header("3. Rendering")
BANDS_MAP = {"Coastal": 0, "Blue": 1, "Green": 2, "Red": 3, "NIR": 7, "SWIR1": 10, "SWIR2": 11}
col_r, col_g, col_b = st.sidebar.columns(3)
r_band = col_r.selectbox("R", list(BANDS_MAP.keys()), index=3)
g_band = col_g.selectbox("G", list(BANDS_MAP.keys()), index=2)
b_band = col_b.selectbox("B", list(BANDS_MAP.keys()), index=1)
brightness = st.sidebar.slider("Brightness", 0.5, 5.0, 2.5)

run_search = st.sidebar.button(" Search Images", use_container_width=True)

# --- HELPER: CONVERT NP TO BASE64 PNG ---
def get_image_url(np_img):
    img = Image.fromarray((np_img * 255).astype(np.uint8))
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return f"data:image/png;base64,{img_str}"

# --- LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig()
    config.sh_client_id, config.sh_client_secret = CLIENT_ID, CLIENT_SECRET
    
    geolocator = Nominatim(user_agent="sentinel_explorer")
    location = geolocator.geocode(city_name)
    
    if location:
        lat, lon = location.latitude, location.longitude
        degree_offset = (radius_km / 111.32) / 2 
        # Calculate bounds for Folium
        map_bounds = [[lat - degree_offset, lon - degree_offset], [lat + degree_offset, lon + degree_offset]]
        roi_bbox = BBox(bbox=[lon - degree_offset, lat - degree_offset, 
                              lon + degree_offset, lat + degree_offset], crs=CRS.WGS84)

        if run_search or 'results' in st.session_state:
            if run_search:
                catalog = SentinelHubCatalog(config=config)
                search_iterator = catalog.search(DataCollection.SENTINEL2_L2A, bbox=roi_bbox,
                    time=(str(date_range[0]), str(date_range[1])), filter=f"eo:cloud_cover < {cloud_limit}")
                st.session_state.results = list(search_iterator)

            if st.session_state.get('results'):
                res_list = st.session_state.results
                options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(res_list)]
                selected = st.multiselect("Select exactly 4 dates:", options, default=options[:min(len(options), 4)])

                if len(selected) == 4:
                    if st.button(" Render Quadrant View", type="primary"):
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

                        col1, col2 = st.columns(2)
                        
                        for idx, selection in enumerate(selected):
                            res_idx = int(selection.split(":")[0])
                            img_date = res_list[res_idx]['properties']['datetime']
                            
                            request = SentinelHubRequest(
                                evalscript=evalscript,
                                input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL2_L2A, time_interval=(img_date, img_date))],
                                responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                                bbox=roi_bbox, size=(800, 800), config=config
                            )
                            
                            data = request.get_data()[0]
                            r_i, g_i, b_i = BANDS_MAP[r_band], BANDS_MAP[g_band], BANDS_MAP[b_band]
                            img_rgb = np.clip(data[:, :, [r_i, g_i, b_i]] * brightness, 0, 1)
                            img_url = get_image_url(img_rgb)

                            # Create individual map
                            m = folium.Map(location=[lat, lon], zoom_start=13, tiles="OpenStreetMap")
                            folium.raster_layers.ImageOverlay(
                                image=img_url,
                                bounds=map_bounds,
                                opacity=0.7,
                                name=f"Sentinel {img_date[:10]}"
                            ).add_to(m)
                            
                            # Place in grid
                            target_col = col1 if idx % 2 == 0 else col2
                            with target_col:
                                st.markdown(f"### {img_date[:10]}")
                                st_folium(m, height=400, width=None, key=f"map_{idx}")
                else:
                    st.info("Please select exactly 4 images.")
    else:
        st.error("Location not found.")
else:
    st.info(" Enter credentials in the sidebar to start.")
