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
# Two independent coordinate states for the two sync groups
if 'group_a_pos' not in st.session_state: st.session_state.group_a_pos = {"center": [40.4168, -3.7038], "zoom": 13}
if 'group_b_pos' not in st.session_state: st.session_state.group_b_pos = {"center": [40.4168, -3.7038], "zoom": 13}
if 'current_bounds' not in st.session_state: st.session_state.current_bounds = None

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Search Area")
city_name = st.sidebar.text_input("City Name", "Madrid, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 25, 5)
date_range = st.sidebar.date_input("Date Range", value=(datetime.date(2025, 6, 1), datetime.date(2025, 8, 30)))
btn_search = st.sidebar.button("🔍 SEARCH IMAGES", type="primary", use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.header("3. Global View Settings")
brightness = st.sidebar.slider("Global Brightness", 0.5, 5.0, 2.5)
opacity = st.sidebar.slider("Satellite Opacity", 0.0, 1.0, 0.8)
selected_basemap = st.sidebar.selectbox("Base Map Style", ["OpenStreetMap", "CartoDB Positron", "Esri World Imagery"])

# --- HELPERS ---
BAND_NAMES = {"B02 (Blue)": 0, "B03 (Green)": 1, "B04 (Red)": 2, "B08 (NIR)": 3, "B11 (SWIR1)": 4, "B12 (SWIR2)": 5}
PRESETS = {
    "Natural Color": [2, 1, 0],
    "False Color NIR": [3, 2, 1],
    "Agriculture": [4, 3, 0],
    "Custom": "CUSTOM"
}

def get_image_url(np_img):
    img = Image.fromarray((np_img * 255).astype(np.uint8))
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

# --- SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig()
    config.sh_client_id, config.sh_client_secret = CLIENT_ID, CLIENT_SECRET

    if btn_search:
        try:
            geolocator = Nominatim(user_agent=f"sentinel_sync_{uuid.uuid4()}")
            location = geolocator.geocode(city_name, timeout=10)
            if location:
                lat, lon = location.latitude, location.longitude
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
        except Exception as e:
            st.error(f"Search error: {e}")

    if st.session_state.search_results:
        results = st.session_state.search_results
        st.info(f"Total images: {len(results)}")
        date_options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(results)]
        selected_dates = st.multiselect("Pick 4 dates:", date_options, default=date_options[:min(len(date_options), 4)])

        if st.button("🖼️ DOWNLOAD DATA", use_container_width=True):
            if len(selected_dates) == 4:
                # Use Group A center as reference for download
                lat, lon = st.session_state.group_a_pos["center"]
                offset = (radius_km / 111.32) / 2
                bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                evalscript = """//VERSION=3
                function setup() { return { input: ['B02','B03','B04','B08','B11','B12'], output: { bands: 6, sampleType: 'FLOAT32' } }; }
                function evaluatePixel(sample) { return [sample.B02, sample.B03, sample.B04, sample.B08, sample.B11, sample.B12]; }"""
                
                for date_str in selected_dates:
                    idx = int(date_str.split(":")[0])
                    actual_date = results[idx]['properties']['datetime']
                    if actual_date not in st.session_state.image_cache:
                        request = SentinelHubRequest(evalscript=evalscript,
                            input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL2_L2A, time_interval=(actual_date, actual_date))],
                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                            bbox=bbox_obj, size=(800, 800), config=config)
                        st.session_state.image_cache[actual_date] = request.get_data()[0]

        if len(st.session_state.image_cache) >= 4:
            st.markdown("---")
            c_left, c_right = st.columns(2)
            
            for i, date_str in enumerate(selected_dates):
                target_col = c_left if i % 2 == 0 else c_right
                idx = int(date_str.split(":")[0])
                actual_date = results[idx]['properties']['datetime']
                
                with target_col:
                    st.subheader(f"View {i+1} ({actual_date[:10]})")
                    
                    # --- INDEPENDENT CONTROLS PER VIEW ---
                    exp = st.expander(f"⚙️ Config View {i+1}", expanded=False)
                    with exp:
                        sub1, sub2 = st.columns(2)
                        v_preset = sub1.selectbox("Composition", list(PRESETS.keys()), key=f"pre_{i}")
                        v_sync = sub2.selectbox("Sync Group", ["None", "Group A", "Group B"], key=f"sync_grp_{i}")
                        
                        if v_preset == "Custom":
                            b1, b2, b3 = st.columns(3)
                            v_rgb = [BAND_NAMES[b1.selectbox("R", list(BAND_NAMES.keys()), index=2, key=f"r_{i}")],
                                     BAND_NAMES[b2.selectbox("G", list(BAND_NAMES.keys()), index=1, key=f"g_{i}")],
                                     BAND_NAMES[b3.selectbox("B", list(BAND_NAMES.keys()), index=0, key=f"b_{i}")]]
                        else:
                            v_rgb = PRESETS[v_preset]

                    # Map Initialization
                    # Pick correct position based on group
                    pos = st.session_state.group_a_pos if v_sync == "Group A" else st.session_state.group_b_pos if v_sync == "Group B" else {"center": st.session_state.group_a_pos["center"], "zoom": st.session_state.group_a_pos["zoom"]}
                    
                    m = folium.Map(location=pos["center"], zoom_start=pos["zoom"], tiles=selected_basemap)
                    
                    if actual_date in st.session_state.image_cache:
                        img_rgb = np.clip(st.session_state.image_cache[actual_date][:, :, v_rgb] * brightness, 0, 1)
                        folium.raster_layers.ImageOverlay(image=get_image_url(img_rgb), bounds=st.session_state.current_bounds, opacity=opacity).add_to(m)

                    m_out = st_folium(m, height=350, width=550, key=f"v_map_{i}_{actual_date}", returned_objects=["center", "zoom"])

                    # --- ADVANCED SYNC ENGINE ---
                    if m_out and m_out.get('center') and v_sync != "None":
                        new_lat, new_lng, new_z = round(m_out['center']['lat'], 4), round(m_out['center']['lng'], 4), m_out['zoom']
                        
                        # Determine which state to update
                        state_key = 'group_a_pos' if v_sync == "Group A" else 'group_b_pos'
                        curr_pos = st.session_state[state_key]
                        
                        if (abs(new_lat - curr_pos["center"][0]) > 0.001 or new_z != curr_pos["zoom"]):
                            st.session_state[state_key] = {"center": [new_lat, new_lng], "zoom": new_z}
                            st.rerun()
else:
    st.info("👋 Enter credentials to begin.")
