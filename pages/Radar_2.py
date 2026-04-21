import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import geopandas as gpd
from sentinelhub import (SHConfig, SentinelHubRequest, DataCollection, MimeType, 
                         BBox, CRS, SentinelHubCatalog)
from geopy.geocoders import Nominatim
import datetime
import folium
from streamlit_folium import st_folium
import base64
import json
from io import BytesIO
from PIL import Image
import rasterio
from rasterio import features
from rasterio.transform import from_bounds
from rasterio.io import MemoryFile
import osmnx as ox
from shapely.geometry import shape, mapping

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Flood Intelligence", page_icon="🛰️")

# --- SESSION STATE INITIALIZATION ---
if 'lat' not in st.session_state: st.session_state.lat = 39.4699
if 'lon' not in st.session_state: st.session_state.lon = -0.3763
if 'img_cache' not in st.session_state: st.session_state.img_cache = {}
if 'search_results' not in st.session_state: st.session_state.search_results = None
if 'buildings_gdf' not in st.session_state: st.session_state.buildings_gdf = None

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    try:
        img_8bit = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_8bit)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
    except: return ""

def fetch_buildings(lat, lon, radius_km):
    try:
        offset = (radius_km / 111.32) / 2
        gdf = ox.features_from_bbox(lat+offset, lat-offset, lon+offset, lon-offset, tags={'building': True})
        return gdf[['geometry']]
    except: return None

# --- SIDEBAR CONTROLS ---
with st.sidebar:
    st.header("🔑 Authentication")
    CLIENT_ID = st.text_input("Client ID", type="password")
    CLIENT_SECRET = st.text_input("Client Secret", type="password")
    
    st.markdown("---")
    st.header("📍 Location Discovery")
    
    # City Search
    city_input = st.text_input("Search City", placeholder="e.g. Valencia, Spain")
    if st.button("🔍 Locate City"):
        if city_input:
            loc = Nominatim(user_agent="flood_app").geocode(city_input)
            if loc:
                st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
                st.success(f"Found: {loc.latitude:.4f}, {loc.longitude:.4f}")
    
    # Manual Override
    st.session_state.lat = st.number_input("Latitude", value=st.session_state.lat, format="%.6f")
    st.session_state.lon = st.number_input("Longitude", value=st.session_state.lon, format="%.6f")
    
    st.markdown("---")
    radius = st.slider("Radius (km)", 1, 20, 5)
    date_range = st.date_input("Date Window", [datetime.date(2024, 10, 25), datetime.date(2024, 11, 5)])
    
    btn_run = st.button("🚀 SEARCH RADAR DATA", type="primary", use_container_width=True)

# --- CORE ENGINE ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)
    
    if btn_run:
        with st.spinner("Fetching Data..."):
            cat = SentinelHubCatalog(config=config)
            off = (radius / 111.32) / 2
            bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
            
            # Search Results
            search = cat.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(date_range[0]), str(date_range[1])))
            st.session_state.search_results = list(search)
            
            # Fetch OSM Buildings
            st.session_state.buildings_gdf = fetch_buildings(st.session_state.lat, st.session_state.lon, radius)
            st.session_state.img_cache = {} 

    # --- TABS ---
    tab_dash, tab_lab, tab_flood = st.tabs(["🗺️ Dashboard", "🧪 Advanced Lab", "🚨 Flood Impact"])

    with tab_dash:
        if st.session_state.search_results:
            res = st.session_state.search_results
            opts = [f"{i}: {r['properties']['datetime'][:10]}" for i,r in enumerate(res)]
            picks = st.multiselect("Dates to render:", opts, default=opts[:min(2, len(opts))])
            
            if st.button("🖼️ Render Maps"):
                off = (radius / 111.32) / 2
                bbox_obj = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
                evalscript = "//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}"
                
                for p in picks:
                    d = res[int(p.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=evalscript, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(d,d))],
                                           responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(600, 600), config=config)
                    st.session_state.img_cache[d] = req.get_data()[0]

            if st.session_state.img_cache:
                cols = st.columns(len(st.session_state.img_cache))
                for i, (dk, data) in enumerate(st.session_state.img_cache.items()):
                    with cols[i]:
                        st.caption(f"Radar Date: {dk[:10]}")
                        m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
                        off = (radius / 111.32) / 2
                        bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
                        folium.raster_layers.ImageOverlay(get_image_url(data*3), bounds=bnds, opacity=0.7).add_to(m)
                        st_folium(m, height=350, key=f"map_{dk}")

    with tab_lab:
        if len(st.session_state.img_cache) >= 2:
            st.subheader("🧪 Side-by-Side Analysis")
            c1, c2, c3 = st.columns(3)
            d1 = c1.selectbox("Left (Baseline)", list(st.session_state.img_cache.keys()), index=0)
            d2 = c2.selectbox("Right (Crisis)", list(st.session_state.img_cache.keys()), index=1)
            cmap = c3.selectbox("Colormap", ["viridis", "inferno", "Greys_r", "RdBu"])
            
            # Use LaTeX for formal dB calculation
            # $$dB = 10 \cdot \log_{10}(Intensity)$$
            db1 = 10 * np.log10(st.session_state.img_cache[d1][:,:,0] + 1e-10)
            db2 = 10 * np.log10(st.session_state.img_cache[d2][:,:,0] + 1e-10)
            
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
            ax1.imshow(db1, cmap=cmap, vmin=-25, vmax=-5); ax1.set_title(d1[:10]); ax1.axis('off')
            ax2.imshow(db2, cmap=cmap, vmin=-25, vmax=-5); ax2.set_title(d2[:10]); ax2.axis('off')
            st.pyplot(fig)

    with tab_flood:
        if len(st.session_state.img_cache) >= 2:
            st.subheader("🚨 Damage Assessment")
            # All options are preserved here from the Sidebar
            sens = st.slider("Flood Sensitivity (dB Drop)", -15.0, -2.0, -6.0)
            
            # Simple Change Detection
            d_keys = list(st.session_state.img_cache.keys())
            diff = (10*np.log10(st.session_state.img_cache[d_keys[1]]+1e-10)) - (10*np.log10(st.session_state.img_cache[d_keys[0]]+1e-10))
            flood_mask = (diff < sens).astype(np.uint8)
            
            m_final = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
            off = (radius / 111.32) / 2
            bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
            
            # Render Flood (Red Overlay)
            f_overlay = np.zeros((600,600,4))
            f_overlay[flood_mask[:,:,0]==1] = [1, 0, 0, 0.6]
            folium.raster_layers.ImageOverlay(get_image_url(f_overlay), bounds=bnds).add_to(m_final)
            
            if st.session_state.buildings_gdf is not None:
                folium.GeoJson(st.session_state.buildings_gdf, style_function=lambda x: {'color':'orange','weight':1}).add_to(m_final)
                st.info(f"Loaded {len(st.session_state.buildings_gdf)} building footprints for this area.")
            
            st_folium(m_final, height=600, width=1200)

else:
    st.warning("⚠️ Please enter your Sentinel Hub Credentials in the Sidebar to continue.")

# --- THE FIX FOR THE SYNTAX ERROR ---
# This else belongs to the 'if CLIENT_ID and CLIENT_SECRET' block.
# If no credentials, we show the warning above.
