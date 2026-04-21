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
import json
from io import BytesIO
from PIL import Image
import uuid
import rasterio
from rasterio import features
from rasterio.transform import from_bounds
from rasterio.io import MemoryFile
import time
import osmnx as ox
from shapely.geometry import box, shape, mapping

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Flood Intelligence Pro", page_icon="🏢")

# --- INITIALIZE SESSION STATE ---
if 'lat' not in st.session_state: st.session_state.lat = 39.4699
if 'lon' not in st.session_state: st.session_state.lon = -0.3763
if 'radius' not in st.session_state: st.session_state.radius = 5
if 'dates' not in st.session_state: st.session_state.dates = [datetime.date(2024, 10, 25), datetime.date(2024, 11, 5)]
if 'image_cache_s1' not in st.session_state: st.session_state.image_cache_s1 = {}
if 'search_results_s1' not in st.session_state: st.session_state.search_results_s1 = None
if 'water_mask' not in st.session_state: st.session_state.water_mask = None
if 'building_gdf' not in st.session_state: st.session_state.building_gdf = None

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    """Refined to ensure Folium recognizes this as a Data URI."""
    try:
        if np_img is None: return None
        # Ensure 8-bit
        img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_data)
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        # Ensure the string is clean and correctly padded
        img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
        return f"data:image/png;base64,{img_str}"
    except Exception as e:
        st.error(f"Image encoding error: {e}")
        return None

def fetch_osm_geometries(lat, lon, radius_km, mask_shape):
    offset = (radius_km / 111.32) / 2
    bbox = (lat - offset, lat + offset, lon - offset, lon + offset)
    # 1. Water Mask
    try:
        w_tags = {'natural': 'water', 'landuse': 'reservoir', 'waterway': 'riverbank'}
        w_gdf = ox.features_from_bbox(bbox[1], bbox[0], bbox[3], bbox[2], tags=w_tags)
        transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, mask_shape[1], mask_shape[0])
        w_mask = features.rasterize([(geom, 1) for geom in w_gdf.geometry], out_shape=mask_shape, transform=transform, fill=0)
    except: w_mask = np.zeros(mask_shape)
    # 2. Buildings
    try:
        b_tags = {'building': True}
        b_gdf = ox.features_from_bbox(bbox[1], bbox[0], bbox[3], bbox[2], tags=b_tags)
    except: b_gdf = None
    return w_mask, b_gdf

# --- GLOBAL CONTROLS (Always Visible) ---
with st.sidebar:
    st.header("🔑 Credentials")
    CLIENT_ID = st.text_input("Client ID", type="password")
    CLIENT_SECRET = st.text_input("Client Secret", type="password")
    
    st.markdown("---")
    st.header("📍 Search & Location")
    
    # City Search (Triggers updates to Lat/Lon)
    city_search = st.text_input("Search City (Valencia, Spain, etc.)")
    if st.button("🔍 Resolve City"):
        if city_search:
            loc = Nominatim(user_agent="flood_pro").geocode(city_search)
            if loc:
                st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
                st.success(f"Centered on {city_search}")

    # Manual Coords (Synced with City search)
    c1, c2 = st.columns(2)
    st.session_state.lat = c1.number_input("Lat", value=st.session_state.lat, format="%.6f")
    st.session_state.lon = c2.number_input("Lon", value=st.session_state.lon, format="%.6f")
    
    st.session_state.radius = st.slider("Radius (km)", 1, 30, st.session_state.radius)
    st.session_state.dates = st.date_input("Analysis Window", st.session_state.dates)

    st.markdown("---")
    st.header("⚙️ Map Options")
    base_map = st.selectbox("Base Layer", ["OpenStreetMap", "Esri World Imagery", "CartoDB Positron"])
    show_buildings = st.toggle("Show Buildings (OSM)", value=True)
    
    btn_fetch = st.button("🚀 FETCH DATA", type="primary", use_container_width=True)

# --- APP LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)
    
    if btn_fetch:
        off = (st.session_state.radius / 111.32) / 2
        cat = SentinelHubCatalog(config=config)
        bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
        
        with st.spinner("Searching Catalog..."):
            search = cat.search(DataCollection.SENTINEL1_IW, bbox=bbox, 
                               time=(str(st.session_state.dates[0]), str(st.session_state.dates[1])))
            st.session_state.search_results_s1 = list(search)
            st.session_state.image_cache_s1 = {} # Clear cache for new area
            st.session_state.water_mask = None

    # TABS
    tab_dash, tab_lab, tab_flood = st.tabs(["🗺️ Dashboard", "🧪 Advanced Lab", "🚨 Flood Impact"])

    with tab_dash:
        if st.session_state.search_results_s1:
            res = st.session_state.search_results_s1
            date_opts = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(res)]
            sel_dates = st.multiselect("Select Images to Render:", date_opts, default=date_opts[:min(2, len(date_opts))])
            
            if st.button("Process Images"):
                off = (st.session_state.radius / 111.32) / 2
                bbox_obj = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
                evalscript = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
                
                with st.spinner("Downloading Imagery & Geo-features..."):
                    for d_str in sel_dates:
                        actual_d = res[int(d_str.split(":")[0])]['properties']['datetime']
                        req = SentinelHubRequest(evalscript=evalscript, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(actual_d, actual_d))],
                                               responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(600, 600), config=config)
                        st.session_state.image_cache_s1[actual_d] = req.get_data()[0]
                    
                    st.session_state.water_mask, st.session_state.building_gdf = fetch_osm_geometries(st.session_state.lat, st.session_state.lon, st.session_state.radius, (600, 600))

            if st.session_state.image_cache_s1:
                cols = st.columns(len(st.session_state.image_cache_s1))
                for i, (date_key, raw_data) in enumerate(st.session_state.image_cache_s1.items()):
                    with cols[i]:
                        st.write(f"**Date: {date_key[:10]}**")
                        gain = st.slider("Gain", 0.5, 10.0, 3.0, key=f"g_{date_key}")
                        alpha = st.slider("Alpha", 0.0, 1.0, 0.7, key=f"a_{date_key}")
                        
                        proc = np.dstack([np.clip(raw_data[:,:,0]*gain, 0, 1)]*3)
                        m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13, tiles=base_map)
                        url = get_image_url(proc)
                        if url:
                            off = (st.session_state.radius / 111.32) / 2
                            bounds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
                            folium.raster_layers.ImageOverlay(url, bounds=bounds, opacity=alpha).add_to(m)
                            st_folium(m, height=400, key=f"map_{date_key}")

    with tab_lab:
        if st.session_state.image_cache_s1:
            cmaps = ["viridis", "magma", "inferno", "plasma", "cividis", "Greys_r", "Blues", "YlGnBu", "winter", "coolwarm", "bwr", "tab20c", "brg", "binary", "gist_yarg", "gray", "bone", "pink", "spring", "summer", "autumn", "cool", "Wistia", "hot", "afmhot", "copper", "Spectral", "seismic", "twilight", "hsv", "Paired", "Accent", "Set1", "Set2", "tab10", "ocean", "terrain", "gnuplot", "jet", "turbo"]
            c1, c2, c3 = st.columns(3)
            d_l = c1.selectbox("Left Side", list(st.session_state.image_cache_s1.keys()), index=0, key="lab_l")
            d_r = c2.selectbox("Right Side", list(st.session_state.image_cache_s1.keys()), index=min(1, len(st.session_state.image_cache_s1)-1), key="lab_r")
            cmap = c3.selectbox("Color Ramp", cmaps)
            
            db_l = 10 * np.log10(st.session_state.image_cache_s1[d_l][:,:,0] + 1e-10)
            db_r = 10 * np.log10(st.session_state.image_cache_s1[d_r][:,:,0] + 1e-10)
            
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
            ax1.imshow(db_l, cmap=cmap, vmin=-25, vmax=-5); ax1.set_title(f"Ref: {d_l[:10]}"); ax1.axis('off')
            ax2.imshow(db_r, cmap=cmap, vmin=-25, vmax=-5); ax2.set_title(f"Target: {d_r[:10]}"); ax2.axis('off')
            st.pyplot(fig)

    with tab_flood:
        if len(st.session_state.image_cache_s1) >= 2:
            st.subheader("🚨 Flood Analysis & Exposed Assets")
            keys = list(st.session_state.image_cache_s1.keys())
            c_f1, c_f2 = st.columns(2)
            b_key = c_f1.selectbox("Dry Baseline", keys, index=0)
            a_key = c_f2.selectbox("Wet Crisis", keys, index=1)
            
            sens = st.slider("Flood Threshold (Sensitivity)", -15.0, -2.0, -6.0)
            
            # Flood Calculation
            b_db = 10 * np.log10(st.session_state.image_cache_s1[b_key][:,:,0] + 1e-10)
            a_db = 10 * np.log10(st.session_state.image_cache_s1[a_key][:,:,0] + 1e-10)
            f_mask = ((a_db - b_db) < sens).astype(np.uint8)
            
            if st.session_state.water_mask is not None:
                f_mask[st.session_state.water_mask == 1] = 0
            
            # Building Exposure
            impact_b = None
            if st.session_state.building_gdf is not None:
                off = (st.session_state.radius / 111.32) / 2
                trans = from_bounds(st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off, 600, 600)
                shps = list(features.shapes(f_mask, mask=(f_mask==1), transform=trans))
                flood_polys = [shape(s) for s, v in shps]
                
                if flood_polys:
                    impact_b = st.session_state.building_gdf.copy()
                    impact_b['is_flooded'] = impact_b.geometry.apply(lambda x: any(x.intersects(p) for p in flood_polys))
                    st.error(f"Found {len(impact_b[impact_b.is_flooded])} buildings at risk.")

            # Map Render
            m_f = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14, tiles=base_map)
            # Background
            bg = np.dstack([np.clip(st.session_state.image_cache_s1[a_key][:,:,0]*3, 0, 1)]*3)
            off = (st.session_state.radius / 111.32) / 2
            bounds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
            
            folium.raster_layers.ImageOverlay(get_image_url(bg), bounds=bounds, opacity=0.9).add_to(m_f)
            
            # Flood layer (Red)
            f_rgb = np.zeros((600,600,4))
            f_rgb[f_mask==1] = [1, 0, 0, 0.7]
            folium.raster_layers.ImageOverlay(get_image_url(f_rgb), bounds=bounds).add_to(m_f)
            
            if show_buildings and impact_b is not None:
                folium.GeoJson(impact_b, style_function=lambda x: {'color': 'red' if x['properties'].get('is_flooded') else 'green', 'weight': 1, 'fillOpacity': 0.4}).add_to(m_f)
            
            st_folium(m_f, height=550, width=1200, key="f_map_final")
        else:
            st.info("Render at least 2 images in the Dashboard to unlock the Flood tab.")
else:
    st.info("Enter credentials to begin.")
