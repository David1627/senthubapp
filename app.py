import streamlit as st
from sentinelhub import (SHConfig, SentinelHubRequest, DataCollection, MimeType, 
                         BBox, CRS, SentinelHubCatalog)
import numpy as np
from geopy.geocoders import Nominatim
import datetime
import folium
from streamlit_folium import st_folium

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="Sentinel Linked Explorer")

# --- AUTHENTICATION ---
st.sidebar.header("1. Settings")
CLIENT_ID = st.sidebar.text_input("SentinelHub Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("SentinelHub Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Location & Time")
city_name = st.sidebar.text_input("City Name", "Madrid, Spain")
radius_km = st.sidebar.slider("Zoom/Radius (km)", 1, 50, 5)

date_range = st.sidebar.date_input(
    "Date Range", 
    value=(datetime.date(2025, 6, 1), datetime.date(2025, 8, 30))
)
cloud_limit = st.sidebar.slider("Max Cloud Cover (%)", 0, 100, 10)

st.sidebar.markdown("---")
st.sidebar.header("3. Rendering")
BANDS_MAP = {
    "Coastal": 0, "Blue": 1, "Green": 2, "Red": 3, "RedEdge1": 4,
    "RedEdge2": 5, "RedEdge3": 6, "NIR": 7, "NarrowNIR": 8, 
    "WaterVapor": 9, "SWIR1": 10, "SWIR2": 11, "SCL": 12
}

col_r, col_g, col_b = st.sidebar.columns(3)
r_band = col_r.selectbox("R", list(BANDS_MAP.keys()), index=3)
g_band = col_g.selectbox("G", list(BANDS_MAP.keys()), index=2)
b_band = col_b.selectbox("B", list(BANDS_MAP.keys()), index=1)
brightness = st.sidebar.slider("Brightness", 0.5, 5.0, 2.5)

# --- SEARCH ACTION ---
run_search = st.sidebar.button(" Search Images", use_container_width=True)

# --- LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig()
    config.sh_client_id = CLIENT_ID
    config.sh_client_secret = CLIENT_SECRET
    
    geolocator = Nominatim(user_agent="sentinel_explorer")
    location = geolocator.geocode(city_name)
    
    if location:
        lat, lon = location.latitude, location.longitude
        degree_offset = (radius_km / 111.32) / 2 
        roi_bbox = BBox(bbox=[lon - degree_offset, lat - degree_offset, 
                              lon + degree_offset, lat + degree_offset], crs=CRS.WGS84)

        # Reference Map in Sidebar
        with st.sidebar:
            m = folium.Map(location=[lat, lon], zoom_start=12)
            folium.Rectangle(bounds=[[lat - degree_offset, lon - degree_offset], 
                                     [lat + degree_offset, lon + degree_offset]],
                             color="red", fill=True, weight=2).add_to(m)
            st_folium(m, height=200, width=250)

        # Handle Search Results
        if run_search or 'results' in st.session_state:
            if run_search:
                with st.spinner("Searching Catalog..."):
                    catalog = SentinelHubCatalog(config=config)
                    search_iterator = catalog.search(
                        DataCollection.SENTINEL2_L2A,
                        bbox=roi_bbox,
                        time=(str(date_range[0]), str(date_range[1])),
                        filter=f"eo:cloud_cover < {cloud_limit}"
                    )
                    st.session_state.results = list(search_iterator)

            if st.session_state.get('results'):
                res_list = st.session_state.results
                st.success(f"Found {len(res_list)} images for {city_name}.")
                
                options = [f"{i}: {r['properties']['datetime']} ({r['properties']['eo:cloud_cover']}% cloud)" for i, r in enumerate(res_list)]
                selected = st.multiselect("Select up to 4 images to compare:", options, default=options[:min(len(options), 4)])

                # --- THE FINAL APPLY BUTTON ---
                if st.button("Generate Linked Views", type="primary"):
                    evalscript = """
                    //VERSION=3
                    function setup() {
                        return {
                            input: ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12", "SCL"],
                            output: { bands: 13, sampleType: "FLOAT32" }
                        };
                    }
                    function evaluatePixel(sample) {
                        return [sample.B01, sample.B02, sample.B03, sample.B04, sample.B05, sample.B06, 
                                sample.B07, sample.B08, sample.B8A, sample.B09, sample.B11, sample.B12, sample.SCL];
                    }
                    """
                    
                    to_display = selected[:4]
                    cols = st.columns(len(to_display))
                    
                    for idx, selection in enumerate(to_display):
                        res_idx = int(selection.split(":")[0])
                        img_metadata = res_list[res_idx]
                        img_date = img_metadata['properties']['datetime']
                        
                        with st.spinner(f"Downloading {img_date[:10]}..."):
                            # FIXED: Added explicit data_collection keyword
                            request = SentinelHubRequest(
                                evalscript=evalscript,
                                input_data=[SentinelHubRequest.input_data(
                                    data_collection=DataCollection.SENTINEL2_L2A, 
                                    time_interval=(img_date, img_date)
                                )],
                                responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                                bbox=roi_bbox, 
                                size=(800, 800), 
                                config=config
                            )
                            
                            data = request.get_data()[0]
                            
                            # Visualization processing
                            r_i, g_i, b_i = BANDS_MAP[r_band], BANDS_MAP[g_band], BANDS_MAP[b_band]
                            img_rgb = np.clip(data[:, :, [r_i, g_i, b_i]] * brightness, 0, 1)
                            
                            with cols[idx]:
                                st.markdown(f"**Date: {img_date[:10]}**")
                                st.image(img_rgb, use_container_width=True)
                                st.caption(f"Cloud Cover: {img_metadata['properties']['eo:cloud_cover']}%")
            else:
                st.warning("No images found. Adjust your search parameters.")
    else:
        st.error("Location not found.")
else:
    st.info("Enter your Sentinel Hub credentials in the sidebar to begin.")
