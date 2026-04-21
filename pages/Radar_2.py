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
st.set_page_config(layout="wide", page_title="S1 Intelligence Pro", page_icon="🛰️")

# --- 1. SESSION STATE INITIALIZATION ---
if 'lat' not in st.session_state: st.session_state.lat = 39.4699
if 'lon' not in st.session_state: st.session_state.lon = -0.3763
if 'img_cache' not in st.session_state: st.session_state.img_cache = {}
if 'search_results' not in st.session_state: st.session_state.search_results = None

# --- 2. LOCATION SYNC CALLBACKS ---
def update_coords_from_city():
    if st.session_state.city_query:
        try:
            loc = Nominatim(user_agent="flood_pro").geocode(st.session_state.city_query)
            if loc:
                st.session_state.lat = loc.latitude
                st.session_state.lon = loc.longitude
        except: st.error("Geocoding service busy.")

# --- 3. HELPER FUNCTIONS ---
def get_image_url(np_img):
    """Encodes array to Base64, handling 2D or 3D inputs."""
    try:
        # Normalize to 2D for processing
        if np_img.ndim == 3:
            render_img = np_img[:,:,0] # Take first band if 3D
        else:
            render_img = np_img # Use as is if 2D
            
        img_8bit = (np.clip(render_img, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_8bit)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
    except: return ""

def to_db(data):
    """Safely converts radar intensity to Decibels, handling array shapes."""
    # Ensure we are looking at a 2D slice for log calculation
    slice_2d = data[:,:,0] if data.ndim == 3 else data
    return 10 * np.log10(slice_2d + 1e-10)

# --- 4. SIDEBAR GLOBAL CONTROLS ---
with st.sidebar:
    st.header("🔑 Authentication")
    c_id = st.text_input("Client ID", value="", type="password")
    c_sec = st.text_input("Client Secret", value="", type="password")
    
    st.markdown("---")
    st.header("📍 Location & Area")
    st.text_input("Search City", key="city_query", on_change=update_coords_from_city)
    
    c1, c2 = st.columns(2)
    st.session_state.lat = c1.number_input("Lat", value=st.session_state.lat, format="%.6f")
    st.session_state.lon = c2.number_input("Lon", value=st.session_state.lon, format="%.6f")
    
    radius = st.slider("Radius (km)", 1, 20, 5)
    date_range = st.date_input("Date Window", [datetime.date(2024, 10, 20), datetime.date(2024, 11, 10)])
    
    st.markdown("---")
    btn_fetch = st.button("🚀 FETCH RADAR DATA", type="primary", use_container_width=True)

# --- 5. CORE LOGIC ---
if btn_fetch and c_id and c_sec:
    try:
        config = SHConfig(sh_client_id=c_id, sh_client_secret=c_sec)
        cat = SentinelHubCatalog(config=config)
        off = (radius / 111.32) / 2
        bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
        
        search = cat.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(date_range[0]), str(date_range[1])))
        st.session_state.search_results = list(search)
        st.session_state.img_cache = {} # Flush old images
        st.success(f"Found {len(st.session_state.search_results)} captures.")
    except Exception as e:
        st.error(f"Access Denied: {e}")

# --- 6. TABS ---
tab1, tab2, tab3 = st.tabs(["🗺️ Dashboard", "🧪 Radar Lab", "🚨 Flood Impact"])

with tab1:
    if st.session_state.search_results:
        res = st.session_state.search_results
        opts = [f"{i}: {r['properties']['datetime'][:10]}" for i,r in enumerate(res)]
        picks = st.multiselect("Select captures:", opts, default=opts[:min(2, len(opts))])
        
        if st.button("🖼️ Render Radar"):
            config = SHConfig(sh_client_id=c_id, sh_client_secret=c_sec)
            off = (radius / 111.32) / 2
            bbox_obj = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
            # Evalscript returning 2 bands for flexibility
            es = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
            
            for p in picks:
                d = res[int(p.split(":")[0])]['properties']['datetime']
                req = SentinelHubRequest(evalscript=es, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(d,d))],
                                       responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(600, 600), config=config)
                st.session_state.img_cache[d] = req.get_data()[0]

        if st.session_state.img_cache:
            cols = st.columns(len(st.session_state.img_cache))
            off = (radius / 111.32) / 2
            bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
            for i, (dk, data) in enumerate(st.session_state.img_cache.items()):
                with cols[i]:
                    st.caption(f"Radar: {dk[:10]}")
                    m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
                    folium.raster_layers.ImageOverlay(get_image_url(data*3), bounds=bnds).add_to(m)
                    st_folium(m, height=300, key=f"map_{dk}")

with tab2:
    if len(st.session_state.img_cache) >= 2:
        keys = list(st.session_state.img_cache.keys())
        st.subheader("Comparison Analysis (dB)")
        
        # Safe 2D conversion for plotting
        db1 = to_db(st.session_state.img_cache[keys[0]])
        db2 = to_db(st.session_state.img_cache[keys[1]])
        
        fig, ax = plt.subplots(1, 2, figsize=(10, 4))
        ax[0].imshow(db1, cmap='magma', vmin=-25, vmax=-5); ax[0].axis('off'); ax[0].set_title(keys[0][:10])
        ax[1].imshow(db2, cmap='magma', vmin=-25, vmax=-5); ax[1].axis('off'); ax[1].set_title(keys[1][:10])
        st.pyplot(fig)

with tab3:
    if len(st.session_state.img_cache) >= 2:
        st.subheader("🚨 Flood Change Detection")
        sens = st.slider("Flood Sensitivity (dB Drop)", -15.0, -2.0, -6.0)
        
        keys = list(st.session_state.img_cache.keys())
        diff = to_db(st.session_state.img_cache[keys[1]]) - to_db(st.session_state.img_cache[keys[0]])
        flood_mask = (diff < sens).astype(np.uint8)
        
        m_flood = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
        off = (radius / 111.32) / 2
        bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
        
        # Background Radar
        folium.raster_layers.ImageOverlay(get_image_url(st.session_state.img_cache[keys[1]]*3), bounds=bnds, opacity=0.4).add_to(m_flood)
        
        # Flood Layer (Red)
        f_overlay = np.zeros((600,600,4))
        f_overlay[flood_mask == 1] = [1, 0, 0, 0.7] # Red for flood
        folium.raster_layers.ImageOverlay(get_image_url(f_overlay), bounds=bnds).add_to(m_flood)
        
        st_folium(m_flood, height=550, width=1100)
    else:
        st.info("Render at least 2 radar captures in the Dashboard to proceed.")
