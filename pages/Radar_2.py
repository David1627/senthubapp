import streamlit as st
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

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Recovery Mode", page_icon="🩹")

# --- 1. STATE MANAGEMENT ---
if 'lat' not in st.session_state: st.session_state.lat = 39.4699
if 'lon' not in st.session_state: st.session_state.lon = -0.3763
if 'img_cache' not in st.session_state: st.session_state.img_cache = {}

# --- 2. THE "INDEX ERROR" PROTECTOR ---
def safe_to_db(data):
    """Safely converts radar intensity to Decibels without crashing."""
    try:
        # If 3D (H, W, Bands), take the first band
        if len(data.shape) == 3:
            processed = data[:, :, 0]
        else:
            processed = data
        return 10 * np.log10(processed + 1e-10)
    except Exception as e:
        st.error(f"Data Shape Error: {data.shape}. Details: {e}")
        return np.zeros((500, 500))

def get_image_url(np_img):
    """Converts array to base64 for Folium."""
    try:
        # Ensure we are rendering a 2D slice
        if len(np_img.shape) == 3:
            slice_to_show = np_img[:, :, 0]
        else:
            slice_to_show = np_img
        
        img_8bit = (np.clip(slice_to_show, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_8bit)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
    except: return ""

# --- 3. SIDEBAR ---
with st.sidebar:
    st.header("🔑 Credentials")
    cid = st.text_input("Client ID", type="password")
    csec = st.text_input("Client Secret", type="password")
    
    st.header("📍 Location")
    city = st.text_input("Search City (Enter to apply)")
    if city:
        loc = Nominatim(user_agent="recovery_app").geocode(city)
        if loc:
            st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
    
    st.session_state.lat = st.number_input("Lat", value=st.session_state.lat, format="%.4f")
    st.session_state.lon = st.number_input("Lon", value=st.session_state.lon, format="%.4f")
    
    btn = st.button("🚀 RUN ANALYSIS", type="primary", use_container_width=True)

# --- 4. MAIN LOGIC ---
if btn and cid and csec:
    try:
        config = SHConfig(sh_client_id=cid, sh_client_secret=csec)
        cat = SentinelHubCatalog(config=config)
        
        # Bounding Box
        r = 5 / 111.32 / 2 # 5km radius
        bbox = BBox(bbox=[st.session_state.lon-r, st.session_state.lat-r, st.session_state.lon+r, st.session_state.lat+r], crs=CRS.WGS84)
        
        # Simple Search (Last 15 days)
        end = datetime.date.today()
        start = end - datetime.timedelta(days=15)
        search = list(cat.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(start), str(end))))
        
        if search:
            st.success(f"Found {len(search)} scenes!")
            # Pull the most recent 2 scenes automatically
            evalscript = "//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}"
            
            for i in range(min(2, len(search))):
                date = search[i]['properties']['datetime']
                req = SentinelHubRequest(evalscript=evalscript, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(date, date))],
                                       responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox, size=(500, 500), config=config)
                st.session_state.img_cache[date] = req.get_data()[0]
        else:
            st.warning("No radar data found for this location/time.")
            
    except Exception as e:
        st.error(f"Global Error: {e}")

# --- 5. TABS ---
t1, t2 = st.tabs(["🗺️ Map View", "📊 Analysis"])

with t1:
    if st.session_state.img_cache:
        for date, data in st.session_state.img_cache.items():
            st.write(f"**Date:** {date[:10]}")
            m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=12)
            r = 5 / 111.32 / 2
            bnds = [[st.session_state.lat-r, st.session_state.lon-r], [st.session_state.lat+r, st.session_state.lon+r]]
            folium.raster_layers.ImageOverlay(get_image_url(data*3), bounds=bnds).add_to(m)
            st_folium(m, height=400, key=f"map_{date}")

with t2:
    if len(st.session_state.img_cache) >= 2:
        dates = list(st.session_state.img_cache.keys())
        db1 = safe_to_db(st.session_state.img_cache[dates[0]])
        db2 = safe_to_db(st.session_state.img_cache[dates[1]])
        
        diff = db2 - db1
        st.subheader("Radar Backscatter Change (dB)")
        fig, ax = plt.subplots()
        im = ax.imshow(diff, cmap='RdBu', vmin=-10, vmax=10)
        plt.colorbar(im)
        st.pyplot(fig)
    else:
        st.info("Need 2 radar captures for change analysis.")
