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
st.set_page_config(layout="wide", page_title="S1 Clean Explorer", page_icon="🛰️")

# --- 1. PERSISTENT STATE ---
if 'lat' not in st.session_state: st.session_state.lat = 39.4699
if 'lon' not in st.session_state: st.session_state.lon = -0.3763
if 'img_cache' not in st.session_state: st.session_state.img_cache = {}

# --- 2. THE ERROR-PROOF UTILITIES ---
def ensure_2d(data):
    """Ensures radar data is 2D, preventing IndexError [:,:,0] crashes."""
    if data is None: return None
    if len(data.shape) == 3:
        return data[:, :, 0] # Extract first band if 3D
    return data # Already 2D

def get_image_url(data):
    """Safe Base64 encoder for Folium overlays."""
    try:
        clean_data = ensure_2d(data)
        img_8bit = (np.clip(clean_data, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_8bit)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
    except: return ""

# --- 3. SIDEBAR: CLEAN & SYNCED ---
with st.sidebar:
    st.header("🔑 1. Credentials")
    cid = st.text_input("Client ID", type="password")
    csec = st.text_input("Client Secret", type="password")
    
    st.header("📍 2. Location")
    city = st.text_input("City Search (Press Enter)")
    if city:
        loc = Nominatim(user_agent="flood_fix").geocode(city)
        if loc:
            st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
    
    # Manual Sync
    st.session_state.lat = st.number_input("Lat", value=st.session_state.lat, format="%.4f")
    st.session_state.lon = st.number_input("Lon", value=st.session_state.lon, format="%.4f")
    
    st.markdown("---")
    run_btn = st.button("🚀 FETCH RADAR", type="primary", use_container_width=True)

# --- 4. DATA ENGINE ---
if run_btn and cid and csec:
    try:
        config = SHConfig(sh_client_id=cid, sh_client_secret=csec)
        cat = SentinelHubCatalog(config=config)
        
        # Area: 5km Box
        r = 5 / 111.32 / 2 
        bbox = BBox(bbox=[st.session_state.lon-r, st.session_state.lat-r, st.session_state.lon+r, st.session_state.lat+r], crs=CRS.WGS84)
        
        # Search last 30 days
        search = list(cat.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(datetime.date.today()-datetime.timedelta(days=30)), str(datetime.date.today()))))
        
        if search:
            st.session_state.img_cache = {} # Clear old data
            evalscript = "//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}"
            
            # Fetch only the 2 most recent captures
            for i in range(min(2, len(search))):
                d = search[i]['properties']['datetime']
                req = SentinelHubRequest(evalscript=evalscript, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(d, d))],
                                       responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox, size=(500, 500), config=config)
                st.session_state.img_cache[d] = req.get_data()[0]
            st.success(f"Synced {len(st.session_state.img_cache)} radar scenes.")
    except Exception as e:
        st.error(f"Auth or API Error: {e}")

# --- 5. TABS ---
t1, t2 = st.tabs(["🗺️ Map View", "🚨 Change Detection"])

with t1:
    if st.session_state.img_cache:
        for date, data in st.session_state.img_cache.items():
            st.subheader(f"Capture: {date[:10]}")
            m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=12)
            r = 5 / 111.32 / 2
            bnds = [[st.session_state.lat-r, st.session_state.lon-r], [st.session_state.lat+r, st.session_state.lon+r]]
            # Use data*2.5 for better radar visibility
            folium.raster_layers.ImageOverlay(get_image_url(data*2.5), bounds=bnds).add_to(m)
            st_folium(m, height=400, key=f"map_{date}")

with t2:
    if len(st.session_state.img_cache) >= 2:
        dates = list(st.session_state.img_cache.keys())
        # Safe dB calculation
        d1 = 10 * np.log10(ensure_2d(st.session_state.img_cache[dates[0]]) + 1e-10)
        d2 = 10 * np.log10(ensure_2d(st.session_state.img_cache[dates[1]]) + 1e-10)
        
        diff = d2 - d1
        st.subheader("Radar Backscatter Difference (dB)")
        st.write("Red = Backscatter Decrease (Potential Water/Flood)")
        
        fig, ax = plt.subplots()
        im = ax.imshow(diff, cmap='RdBu', vmin=-10, vmax=10)
        plt.colorbar(im, label="dB Change")
        ax.axis('off')
        st.pyplot(fig)
