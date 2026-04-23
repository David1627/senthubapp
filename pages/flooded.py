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
st.set_page_config(layout="wide", page_title="S1 Radar Explorer", page_icon="📡")

# --- INITIALIZE SESSION STATE ---
if 'search_results' not in st.session_state: st.session_state.search_results = None
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'app_uuid' not in st.session_state: st.session_state.app_uuid = str(uuid.uuid4())[:8]
if 'lat' not in st.session_state: st.session_state.lat, st.session_state.lon = 42.041, 3.126

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    try:
        img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_data)
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"
    except: return ""

# --- SIDEBAR ---
st.sidebar.header("1. Sentinel Hub Access")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Search Parameters")
city_query = st.sidebar.text_input("Location", value="Torroella de Montgrí, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 20, 10)
today = datetime.date.today()
date_range = st.sidebar.date_input("Date Window", value=(today - datetime.timedelta(days=30), today))

brightness = st.sidebar.slider("Radar Gain", 0.5, 8.0, 3.0)
btn_search = st.sidebar.button("🔍 SEARCH IMAGES", type="primary", use_container_width=True)

# --- SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        # 1. Geocode
        try:
            geolocator = Nominatim(user_agent=f"radar_app_{st.session_state.app_uuid}")
            location = geolocator.geocode(city_query, timeout=10)
            if location:
                st.session_state.lat, st.session_state.lon = location.latitude, location.longitude
                st.session_state.image_cache = {} # Reset cache
                
                # 2. Search Catalog
                catalog = SentinelHubCatalog(config=config)
                offset = (radius_km / 111.32) / 2
                bbox = BBox(bbox=[st.session_state.lon-offset, st.session_state.lat-offset, 
                                  st.session_state.lon+offset, st.session_state.lat+offset], crs=CRS.WGS84)
                
                search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox, 
                                        time=(str(date_range[0]), str(date_range[1])))
                st.session_state.search_results = list(search)
                if not st.session_state.search_results:
                    st.warning("No images found for these dates.")
            else:
                st.error("Location not found.")
        except Exception as e:
            st.error(f"Search failed: {e}")

    # --- MAIN UI ---
    tab1, tab2, tab3 = st.tabs(["🖼️ Image Browser", "🧪 Radar Lab", "🚨 Change Detection"])

    with tab1:
        if st.session_state.search_results:
            res = st.session_state.search_results
            options = [f"{i}: {r['properties']['datetime'][:16]}" for i, r in enumerate(res)]
            selected = st.multiselect("Select up to 2 acquisitions:", options, default=options[:min(2, len(options))])

            if st.button("🚀 RENDER SELECTED", use_container_width=True):
                offset = (radius_km / 111.32) / 2
                bbox = BBox(bbox=[st.session_state.lon-offset, st.session_state.lat-offset, 
                                  st.session_state.lon+offset, st.session_state.lat+offset], crs=CRS.WGS84)
                
                evalscript = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
                
                for opt in selected:
                    idx = int(opt.split(":")[0])
                    dt = res[idx]['properties']['datetime']
                    with st.spinner(f"Fetching {dt[:10]}..."):
                        req = SentinelHubRequest(evalscript=evalscript, 
                                                input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(dt, dt))],
                                                responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)],
                                                bbox=bbox, size=(600, 600), config=config)
                        st.session_state.image_cache[dt] = req.get_data()[0]

            if st.session_state.image_cache:
                cols = st.columns(len(selected))
                for i, opt in enumerate(selected):
                    dt = res[int(opt.split(":")[0])]['properties']['datetime']
                    if dt in st.session_state.image_cache:
                        data = st.session_state.image_cache[dt]
                        with cols[i]:
                            st.write(f"**Date: {dt[:10]}**")
                            # Simple Greyscale VV
                            img_vv = np.dstack([np.clip(data[:,:,0]*brightness, 0, 1)]*3)
                            st.image(img_vv, use_container_width=True)

    with tab2:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            c1, c2 = st.columns(2)
            d1 = c1.selectbox("Baseline (Dry)", keys, index=0)
            d2 = c2.selectbox("Analysis (Wet)", keys, index=1)
            
            db1 = 10 * np.log10(st.session_state.image_cache[d1][:,:,0] + 1e-10)
            db2 = 10 * np.log10(st.session_state.image_cache[d2][:,:,0] + 1e-10)
            
            fig, ax = plt.subplots(1, 2, figsize=(10, 4))
            ax[0].imshow(db1, cmap='Greys_r', vmin=-25, vmax=-5); ax[0].set_title("Dry (dB)"); ax[0].axis('off')
            ax[1].imshow(db2, cmap='Greys_r', vmin=-25, vmax=-5); ax[1].set_title("Wet (dB)"); ax[1].axis('off')
            st.pyplot(fig)

    with tab3:
        if len(st.session_state.image_cache) >= 2:
            st.subheader("Automated Flood Mask")
            keys = list(st.session_state.image_cache.keys())
            b_dt = st.selectbox("Select Baseline", keys, index=0, key="b_sel")
            w_dt = st.selectbox("Select Flood Date", keys, index=1, key="w_sel")
            
            threshold = st.slider("Flood Sensitivity (Threshold)", -12.0, -2.0, -6.0)
            
            # Math: 10 * log10(after/before) < threshold
            b_data = st.session_state.image_cache[b_dt][:,:,0]
            w_data = st.session_state.image_cache[w_dt][:,:,0]
            diff = 10 * np.log10(w_data + 1e-10) - 10 * np.log10(b_data + 1e-10)
            
            flood_mask = (diff < threshold).astype(float)
            
            # Map View
            m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
            
            # Background (radar image)
            bg = np.dstack([np.clip(w_data*brightness, 0, 1)]*3)
            bounds = [[st.session_state.lat-0.05, st.session_state.lon-0.05], [st.session_state.lat+0.05, st.session_state.lon+0.05]]
            folium.raster_layers.ImageOverlay(image=get_image_url(bg), bounds=bounds, opacity=0.5).add_to(m)
            
            # Flood Overlay (Blue)
            mask_rgb = np.zeros((*flood_mask.shape, 4))
            mask_rgb[flood_mask == 1] = [0, 0.6, 1, 0.8] # Blue with alpha
            folium.raster_layers.ImageOverlay(image=get_image_url(mask_rgb), bounds=bounds).add_to(m)
            
            st_folium(m, height=500, width=None)
        else:
            st.info("Render at least 2 images in Tab 1 to enable analysis.")

else:
    st.info("🔑 Enter your Sentinel Hub credentials to begin.")
