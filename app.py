import streamlit as st
from sentinelhub import (SHConfig, SentinelHubRequest, DataCollection, MimeType, 
                         BBox, CRS, SentinelHubCatalog)
import matplotlib.pyplot as plt
import numpy as np
from geopy.geocoders import Nominatim

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="Sentinel Explorer")

# --- AUTHENTICATION ---
# In production, use st.secrets for security
CLIENT_ID = st.sidebar.text_input("SentinelHub Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("SentinelHub Client Secret", type="password")

# --- UI CONTROLS ---
st.sidebar.header("Search Parameters")
city_name = st.sidebar.text_input("City Name", "Madrid, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 50, 10)
date_range = st.sidebar.date_input("Date Range", [np.datetime64('2025-06-01'), np.datetime64('2025-08-30')])
cloud_limit = st.sidebar.slider("Max Cloud Cover (%)", 0, 100, 10)

st.sidebar.header("Band Combination")
BANDS_MAP = {
    "Coastal": 0, "Blue": 1, "Green": 2, "Red": 3, "RedEdge1": 4,
    "RedEdge2": 5, "RedEdge3": 6, "NIR": 7, "NarrowNIR": 8, 
    "WaterVapor": 9, "SWIR1": 10, "SWIR2": 11, "SCL": 12
}

r_band = st.sidebar.selectbox("Red Channel", list(BANDS_MAP.keys()), index=3)
g_band = st.sidebar.selectbox("Green Channel", list(BANDS_MAP.keys()), index=2)
b_band = st.sidebar.selectbox("Blue Channel", list(BANDS_MAP.keys()), index=1)
brightness = st.sidebar.slider("Brightness Gain", 0.5, 5.0, 2.5)

# --- LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig()
    config.sh_client_id = CLIENT_ID
    config.sh_client_secret = CLIENT_SECRET

    # Geocoding
    geolocator = Nominatim(user_agent="sentinel_explorer")
    location = geolocator.geocode(city_name)
    
    if location:
        lat, lon = location.latitude, location.longitude
        degree_offset = (radius_km / 111.32) / 2 
        roi_bbox = BBox(bbox=[lon - degree_offset, lat - degree_offset, 
                              lon + degree_offset, lat + degree_offset], crs=CRS.WGS84)

        # Catalog Search
        catalog = SentinelHubCatalog(config=config)
        search_iterator = catalog.search(
            DataCollection.SENTINEL2_L2A,
            bbox=roi_bbox,
            time=(str(date_range[0]), str(date_range[1])),
            filter=f"eo:cloud_cover < {cloud_limit}"
        )
        results = list(search_iterator)

        if results:
            st.success(f"Found {len(results)} images!")
            
            # Selection Interface
            options = [f"{i}: {res['properties']['datetime']} (Cloud: {res['properties']['eo:cloud_cover']}%)" for i, res in enumerate(results)]
            selected_indices = st.multiselect("Select up to 4 images to compare:", options, default=options[:min(len(options), 4)])

            if st.button("Generate Visualization"):
                # Evalscript (same as yours)
                evalscript = """
                //VERSION=3
                function setup() {
                    return {
                        input: ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12", "SCL"],
                        output: { bands: 13, sampleType: "FLOAT32" }
                    };
                }
                function evaluatePixel(sample) {
                    return [sample.B01, sample.B02, sample.B03, sample.B04, sample.B05, sample.B06, sample.B07, sample.B08, sample.B8A, sample.B09, sample.B11, sample.B12, sample.SCL];
                }
                """

                cols = st.columns(len(selected_indices))
                
                for idx, selection in enumerate(selected_indices):
                    res_idx = int(selection.split(":")[0])
                    img_date = results[res_idx]['properties']['datetime']
                    
                    request = SentinelHubRequest(
                        evalscript=evalscript,
                        input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL2_L2A, time_interval=(img_date, img_date))],
                        responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                        bbox=roi_bbox, size=(800, 800), config=config
                    )
                    
                    data = request.get_data()[0]
                    
                    # Band selection and processing
                    r, g, b = BANDS_MAP[r_band], BANDS_MAP[g_band], BANDS_MAP[b_band]
                    display_img = np.clip(data[:, :, [r, g, b]] * brightness, 0, 1)
                    
                    with cols[idx]:
                        st.image(display_img, caption=f"Date: {img_date[:10]}", use_container_width=True)
        else:
            st.warning("No images found for these parameters.")
    else:
        st.error("City not found.")
else:
    st.info("Please enter your Sentinel Hub credentials in the sidebar to start.")