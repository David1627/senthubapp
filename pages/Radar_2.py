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
from shapely.geometry import box, shape

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Flood Intelligence Pro", page_icon="🏢")

# --- INITIALIZE SESSION STATE ---
if 'lat' not in st.session_state: st.session_state.lat = 39.4699
if 'lon' not in st.session_state: st.session_state.lon = -0.3763
if 'search_results_s1' not in st.session_state: st.session_state.search_results_s1 = None
if 'image_cache_s1' not in st.session_state: st.session_state.image_cache_s1 = {}
if 'water_mask' not in st.session_state: st.session_state.water_mask = None
if 'building_gdf' not in st.session_state: st.session_state.building_gdf = None
if 'current_bounds_s1' not in st.session_state: st.session_state.current_bounds_s1 = None

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    try:
        if np_img is None: return ""
        img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_data)
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return f"data:image/png;base64,{base64.base64encode(buffered.getvalue()).decode()}"
    except: return ""

def fetch_osm_geometries(lat, lon, radius_km, mask_shape):
    offset = (radius_km / 111.32) / 2
    bbox = (lat - offset, lat + offset, lon - offset, lon + offset)
    try:
        w_tags = {'natural': 'water', 'landuse': 'reservoir', 'waterway': 'riverbank'}
        w_gdf = ox.features_from_bbox(bbox[1], bbox[0], bbox[3], bbox[2], tags=w_tags)
        transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, mask_shape[1], mask_shape[0])
        w_mask = features.rasterize([(geom, 1) for geom in w_gdf.geometry], out_shape=mask_shape, transform=transform, fill=0)
    except: w_mask = np.zeros(mask_shape)
    try:
        b_tags = {'building': True}
        b_gdf = ox.features_from_bbox(bbox[1], bbox[0], bbox[3], bbox[2], tags=b_tags)
    except: b_gdf = None
    return w_mask, b_gdf

def export_geotiff(data, filename, lat, lon, radius_km, key):
    offset = (radius_km / 111.32) / 2
    transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, data.shape[1], data.shape[0])
    with MemoryFile() as memfile:
        with memfile.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                          dtype='float32', crs='EPSG:4326', transform=transform) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button(f"💾 TIFF: {filename}", memfile.read(), filename, "image/tiff", key=key)

# --- SIDEBAR: SEARCH & LOCATION ---
st.sidebar.header("🛰️ 1. Area of Interest")
CLIENT_ID = st.sidebar.text_input("Sentinel Hub Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Sentinel Hub Client Secret", type="password")

st.sidebar.markdown("---")
city_input = st.sidebar.text_input("Search City (e.g. Valencia, Spain)", "")
if st.sidebar.button("🔍 Resolve City Location"):
    if city_input:
        loc = Nominatim(user_agent="flood_pro").geocode(city_input)
        if loc:
            st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
            st.sidebar.success(f"Located: {loc.latitude:.4f}, {loc.longitude:.4f}")

col_lat, col_lon = st.sidebar.columns(2)
st.session_state.lat = col_lat.number_input("Latitude", value=st.session_state.lat, format="%.6f")
st.session_state.lon = col_lon.number_input("Longitude", value=st.session_state.lon, format="%.6f")

radius = st.sidebar.slider("Radius (km)", 1, 20, 5)
dates = st.sidebar.date_input("Date Window", [datetime.date(2024, 10, 20), datetime.date(2024, 11, 10)])

st.sidebar.markdown("---")
st.sidebar.header("🗺️ 2. Visualization Settings")
show_buildings = st.sidebar.toggle("Building Footprints (OSM)", value=True)
base_map = st.sidebar.selectbox("Base Map", ["OpenStreetMap", "Esri World Imagery", "CartoDB Positron"])
btn_run = st.sidebar.button("🚀 FETCH & ANALYZE DATA", type="primary", use_container_width=True)

# --- CORE LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)
    
    if btn_run:
        off = (radius / 111.32) / 2
        st.session_state.current_bounds_s1 = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
        cat = SentinelHubCatalog(config=config)
        bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
        search = cat.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(dates[0]), str(dates[1])))
        st.session_state.search_results_s1 = list(search)
        st.session_state.image_cache_s1 = {}

    tab1, tab2, tab3 = st.tabs(["📊 Radar Dashboard", "🧪 Multi-Spectral Lab", "🚨 Flood Impact Assessment"])

    with tab1: # DASHBOARD
        if st.session_state.search_results_s1:
            res = st.session_state.search_results_s1
            opts = [f"{i}: {r['properties']['datetime'][:10]}" for i,r in enumerate(res)]
            picks = st.multiselect("Select Images to Render:", opts, default=opts[:min(2, len(opts))])
            
            if st.button("🖼️ Process Selected Dates"):
                off = (radius / 111.32) / 2
                bbox_obj = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
                es = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
                
                with st.spinner("Executing Sentinel Hub Request..."):
                    for p in picks:
                        d = res[int(p.split(":")[0])]['properties']['datetime']
                        req = SentinelHubRequest(evalscript=es, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(d,d))],
                                               responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(600, 600), config=config)
                        st.session_state.image_cache_s1[d] = req.get_data()[0]
                    
                    # Fetch OSM Handshake data
                    st.session_state.water_mask, st.session_state.building_gdf = fetch_osm_geometries(st.session_state.lat, st.session_state.lon, radius, (600,600))

            if st.session_state.image_cache_s1:
                cols = st.columns(len(st.session_state.image_cache_s1))
                for i, (dk, img_data) in enumerate(st.session_state.image_cache_s1.items()):
                    with cols[i]:
                        alpha = st.slider(f"Opacity ({dk[:10]})", 0.0, 1.0, 0.8, key=f"alpha_{dk}")
                        gain = st.slider(f"Gain ({dk[:10]})", 0.5, 10.0, 3.0, key=f"gain_{dk}")
                        mode = st.radio(f"Polarization", ["VV", "VH"], key=f"mode_{dk}", horizontal=True)
                        ch = 0 if mode=="VV" else 1
                        proc = np.dstack([np.clip(img_data[:,:,ch]*gain, 0, 1)]*3)
                        m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13, tiles=base_map)
                        folium.raster_layers.ImageOverlay(get_image_url(proc), bounds=st.session_state.current_bounds_s1, opacity=alpha).add_to(m)
                        st_folium(m, height=400, key=f"map_d_{dk}")

    with tab2: # ADVANCED LAB
        if st.session_state.image_cache_s1:
            cmaps = ["viridis", "magma", "inferno", "plasma", "cividis", "Greys_r", "Blues", "YlGnBu", "winter", "coolwarm", "bwr", "tab20c", "brg", "binary", "gist_yarg", "gist_gray", "gray", "bone", "pink", "spring", "summer", "autumn", "winter", "cool", "Wistia", "hot", "afmhot", "gist_heat", "copper", "PiYG", "PRGn", "BrBG", "PuOr", "RdGy", "RdBu", "RdYlBu", "RdYlGn", "Spectral", "seismic", "twilight", "hsv", "Paired", "Accent", "Set1", "Set2", "tab10", "ocean", "terrain", "gnuplot", "jet", "turbo"]
            c1, c2, c3 = st.columns(3)
            date_l = c1.selectbox("Left Panel", list(st.session_state.image_cache_s1.keys()), index=0, key="lab_l")
            date_r = c2.selectbox("Right Panel", list(st.session_state.image_cache_s1.keys()), index=min(1, len(st.session_state.image_cache_s1)-1), key="lab_r")
            cmap = c3.selectbox("Colormap Ramp", cmaps, index=0)
            
            db_l = 10 * np.log10(st.session_state.image_cache_s1[date_l][:,:,0] + 1e-10)
            db_r = 10 * np.log10(st.session_state.image_cache_s1[date_r][:,:,0] + 1e-10)
            
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
            ax1.imshow(db_l, cmap=cmap, vmin=-25, vmax=-5); ax1.set_title(f"Ref: {date_l[:10]} (dB)"); ax1.axis('off')
            ax2.imshow(db_r, cmap=cmap, vmin=-25, vmax=-5); ax2.set_title(f"Target: {date_r[:10]} (dB)"); ax2.axis('off')
            st.pyplot(fig)

    with tab3: # FLOOD IMPACT
        if len(st.session_state.image_cache_s1) >= 2:
            st.subheader("🚨 Damage Assessment & Infrastructure Exposure")
            b_key = st.selectbox("Baseline (Dry Period)", list(st.session_state.image_cache_s1.keys()), 0, key="f_base")
            a_key = st.selectbox("Crisis (Wet Period)", list(st.session_state.image_cache_s1.keys()), 1, key="f_crisis")
            
            sens = st.slider("Flood Threshold Sensitivity", -15.0, -2.0, -6.0)
            
            b_db = 10 * np.log10(st.session_state.image_cache_s1[b_key][:,:,0] + 1e-10)
            a_db = 10 * np.log10(st.session_state.image_cache_s1[a_key][:,:,0] + 1e-10)
            f_mask = ((a_db - b_db) < sens).astype(np.uint8)
            
            # Mask out the sea using OSM handshake
            if st.session_state.water_mask is not None:
                f_mask[st.session_state.water_mask == 1] = 0 
            
            # Calculate intersection with building polygons
            impact_b = None
            if st.session_state.building_gdf is not None:
                off = (radius / 111.32) / 2
                trans = from_bounds(st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off, 600, 600)
                shps = list(features.shapes(f_mask, mask=(f_mask==1), transform=trans))
                flood_polys = [shape(s) for s, v in shps]
                
                if flood_polys:
                    impact_b = st.session_state.building_gdf.copy()
                    impact_b['is_flooded'] = impact_b.geometry.apply(lambda x: any(x.intersects(p) for p in flood_polys))
                    affected = impact_b[impact_b['is_flooded'] == True]
                    st.error(f"Buildings potentially affected: {len(affected)}")
            
            m_final = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14, tiles=base_map)
            bg = np.dstack([np.clip(st.session_state.image_cache_s1[a_key][:,:,0]*3, 0, 1)]*3)
            folium.raster_layers.ImageOverlay(get_image_url(bg), bounds=st.session_state.current_bounds_s1, opacity=0.4).add_to(m_final)
            
            f_rgb = np.zeros((600,600,4))
            f_rgb[f_mask==1] = [1, 0, 0, 0.7] # Red flood pixels
            folium.raster_layers.ImageOverlay(get_image_url(f_rgb), bounds=st.session_state.current_bounds_s1).add_to(m_final)
            
            if show_buildings and impact_b is not None:
                folium.GeoJson(impact_b, style_function=lambda x: {'color': 'red' if x['properties'].get('is_flooded') else 'green', 'weight': 1, 'fillOpacity': 0.4}).add_to(m_final)

            st_folium(m_final, height=550, width=1200, key="final_render_flood")
            
            # --- EXPORTS ---
            st.markdown("---")
            ec1, ec2, ec3 = st.columns(3)
            with ec1: export_geotiff(f_mask, "flood_extent.tif", st.session_state.lat, st.session_state.lon, radius, "dl_f_tif")
            with ec2: 
                if impact_b is not None:
                    st.download_button("📐 Export Buildings GeoJSON", impact_b.to_json(), "affected_buildings.geojson")
            with ec3:
                st.download_button("📐 Export Flood GeoJSON", json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": mapping(p)} for p in flood_polys]}), "flood_vector.geojson")

else:
    st.info("👋 Enter your Sentinel Hub credentials in the sidebar to begin.")
