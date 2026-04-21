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
import uuid
import rasterio
from rasterio import features
from rasterio.transform import from_bounds
from rasterio.io import MemoryFile
import time
import osmnx as ox
from shapely.geometry import shape, mapping

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Flood Intelligence Pro", page_icon="🏢")

# --- INITIALIZE SESSION STATE ---
for key in ['lat', 'lon', 'search_results', 'img_cache', 'buildings_gdf', 'water_mask']:
    if key not in st.session_state: st.session_state[key] = None
if 'lat' not in st.session_state: st.session_state.lat = "" # Start empty
if 'lon' not in st.session_state: st.session_state.lon = ""

# --- HELPER: PNG ENCODING ---
def get_image_url(np_img):
    try:
        img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_data)
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"
    except: return ""

# --- HELPER: BUILDING FETCH ---
@st.cache_data
def get_aoi_buildings(lat, lon, radius_km):
    offset = (radius_km / 111.32) / 2
    bbox = (lat-offset, lat+offset, lon-offset, lon+offset)
    try:
        # Fetch built-up area footprints from OSM
        gdf = ox.features_from_bbox(bbox[1], bbox[0], bbox[3], bbox[2], tags={'building': True})
        return gdf[['geometry']]
    except: return None

# --- SIDEBAR: SEARCH & PERSISTENCE ---
st.sidebar.header("🗺️ Global Location & Search")
city_q = st.sidebar.text_input("1. Search City", placeholder="e.g. Valencia, Spain")
if st.sidebar.button("🔍 Resolve City"):
    loc = Nominatim(user_agent="flood_pro").geocode(city_q)
    if loc:
        st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude

st.sidebar.markdown("---")
st.sidebar.write("2. Manual Coordinates")
u_lat = st.sidebar.text_input("Latitude", value=str(st.session_state.lat))
u_lon = st.sidebar.text_input("Longitude", value=str(st.session_state.lon))

radius = st.sidebar.slider("Radius (km)", 1, 20, 5)
dates = st.sidebar.date_input("Analysis Window", [datetime.date(2024, 10, 25), datetime.date(2024, 11, 5)])

st.sidebar.markdown("---")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

btn_run = st.sidebar.button("🚀 EXECUTE ANALYSIS", type="primary", use_container_width=True)

# --- MAIN LOGIC ---
if btn_run and CLIENT_ID and CLIENT_SECRET:
    try:
        st.session_state.lat, st.session_state.lon = float(u_lat), float(u_lon)
        config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)
        cat = SentinelHubCatalog(config=config)
        
        off = (radius / 111.32) / 2
        bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
        
        # 1. Search Radar
        st.session_state.search_results = list(cat.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(dates[0]), str(dates[1]))))
        # 2. Fetch Buildings
        st.session_state.buildings_gdf = get_aoi_buildings(st.session_state.lat, st.session_state.lon, radius)
        st.session_state.img_cache = {}
        st.success("Search Complete!")
    except Exception as e: st.error(f"Setup Error: {e}")

# --- TAB INTERFACE ---
tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "🧪 Advanced Lab", "🚨 Flood Mapping"])

with tab1: # DASHBOARD: Interactive Viewers
    if st.session_state.search_results:
        res = st.session_state.search_results
        opts = [f"{i}: {r['properties']['datetime'][:10]}" for i,r in enumerate(res)]
        picks = st.multiselect("Render Dates:", opts, default=opts[:min(2, len(opts))])
        
        if st.button("Generate Map Views"):
            config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)
            es = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
            off = (radius / 111.32) / 2
            bbox_obj = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
            
            for p in picks:
                d = res[int(p.split(":")[0])]['properties']['datetime']
                req = SentinelHubRequest(evalscript=es, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(d,d))],
                                       responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(600, 600), config=config)
                st.session_state.img_cache[d] = req.get_data()[0]

        if st.session_state.img_cache:
            cols = st.columns(len(st.session_state.img_cache))
            off = (radius / 111.32) / 2
            bounds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
            
            for i, (dk, img_data) in enumerate(st.session_state.img_cache.items()):
                with cols[i]:
                    alpha = st.slider(f"Transparency {dk[:10]}", 0.0, 1.0, 0.8, key=f"a_{dk}")
                    proc = np.dstack([np.clip(img_data[:,:,0]*3, 0, 1)]*3)
                    m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
                    folium.raster_layers.ImageOverlay(get_image_url(proc), bounds=bounds, opacity=alpha).add_to(m)
                    if st.session_state.buildings_gdf is not None:
                        folium.GeoJson(st.session_state.buildings_gdf, style_function=lambda x:{'color':'orange','weight':1}).add_to(m)
                    st_folium(m, height=400, key=f"map_{dk}")

with tab2: # ADVANCED LAB: Colormaps & Sync
    if st.session_state.img_cache:
        cmaps = ['viridis', 'magma', 'inferno', 'plasma', 'cividis', 'Greys_r', 'Blues', 'YlGnBu', 'winter', 'coolwarm', 'bwr', 'tab20c', 'brg', 'gray', 'pink', 'spring', 'summer', 'autumn', 'hot', 'Spectral', 'seismic', 'twilight', 'hsv', 'terrain', 'jet', 'turbo']
        c1, c2, c3 = st.columns(3)
        d_l = c1.selectbox("Left Map", list(st.session_state.img_cache.keys()), index=0)
        d_r = c2.selectbox("Right Map", list(st.session_state.img_cache.keys()), index=min(1, len(st.session_state.img_cache)-1))
        cmap = c3.selectbox("Active Color Ramp", cmaps)
        
        sync = st.toggle("Sync Map Sync (Experimental)", value=True)
        
        # Calculate dB
        db_l = 10 * np.log10(st.session_state.img_cache[d_l][:,:,0] + 1e-10)
        db_r = 10 * np.log10(st.session_state.img_cache[d_r][:,:,0] + 1e-10)
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        ax1.imshow(db_l, cmap=cmap, vmin=-25, vmax=-5); ax1.axis('off'); ax1.set_title(d_l[:10])
        ax2.imshow(db_r, cmap=cmap, vmin=-25, vmax=-5); ax2.axis('off'); ax2.set_title(d_r[:10])
        st.pyplot(fig)

with tab3: # FLOOD MAPPING: Overlap Analysis
    if len(st.session_state.img_cache) >= 2:
        st.subheader("🚨 Building Exposure Analysis")
        c1, c2 = st.columns(2)
        base_k = c1.selectbox("Dry Baseline", list(st.session_state.img_cache.keys()), index=0, key="fb")
        cris_k = c2.selectbox("Wet Crisis", list(st.session_state.img_cache.keys()), index=1, key="fc")
        
        sens = st.slider("Flood Sensitivity (dB Change)", -15.0, -2.0, -6.0)
        
        # 1. Flood Mask Calculation
        b_db = 10 * np.log10(st.session_state.img_cache[base_k][:,:,0] + 1e-10)
        a_db = 10 * np.log10(st.session_state.img_cache[cris_k][:,:,0] + 1e-10)
        flood_mask = ((a_db - b_db) < sens).astype(np.uint8)
        
        # 2. Identify Overlapped Buildings
        impact_gdf = None
        if st.session_state.buildings_gdf is not None:
            off = (radius / 111.32) / 2
            transform = from_bounds(st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off, 600, 600)
            shapes_gen = features.shapes(flood_mask, mask=(flood_mask==1), transform=transform)
            flood_polys = [shape(s) for s, v in shapes_gen]
            
            if flood_polys:
                flood_gdf = gpd.GeoDataFrame({'geometry': flood_polys}, crs="EPSG:4326")
                # Spatial Join: Find buildings intersecting flood
                impact_gdf = gpd.sjoin(st.session_state.buildings_gdf, flood_gdf, how="inner", predicate="intersects")
                st.metric("Total Buildings Overlapped", len(impact_gdf))

        # 3. Final Impact Map
        m_impact = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
        # Background Radar
        bg = np.dstack([np.clip(st.session_state.img_cache[cris_k][:,:,0]*3, 0, 1)]*3)
        off = (radius / 111.32) / 2
        bounds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
        folium.raster_layers.ImageOverlay(get_image_url(bg), bounds=bounds, opacity=0.5).add_to(m_impact)
        
        # Overlay Flooded Area (Red)
        f_rgb = np.zeros((600,600,4)); f_rgb[flood_mask==1] = [1, 0, 0, 0.6]
        folium.raster_layers.ImageOverlay(get_image_url(f_rgb), bounds=bounds).add_to(m_impact)
        
        if impact_gdf is not None:
            folium.GeoJson(impact_gdf, style_function=lambda x:{'color':'red','fillColor':'red','weight':2}).add_to(m_impact)
        
        st_folium(m_impact, height=600, width=1200)

        # 4. EXPORTS
        st.write("### 💾 Export Area Results")
        ec1, ec2 = st.columns(2)
        with ec1:
            # Export Flood TIFF
            with MemoryFile() as mem:
                with mem.open(driver='GTiff', height=600, width=600, count=1, dtype='uint8', crs='EPSG:4326', transform=transform) as ds:
                    ds.write(flood_mask, 1)
                st.download_button("💾 Download Flood TIFF", mem.read(), "flood_mask.tif")
        with ec2:
            if impact_gdf is not None:
                st.download_button("📐 Download Impacted Buildings (GeoJSON)", impact_gdf.to_json(), "at_risk_buildings.geojson")

else:
    st.info("Input your Search City or Coordinates and hit 'Execute Analysis'.")
