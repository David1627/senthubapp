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
for key in ['search_results_s1', 'image_cache_s1', 'last_search_coords_s1', 
            'current_bounds_s1', 'water_mask', 'building_gdf']:
    if key not in st.session_state: st.session_state[key] = None
if 'app_uuid' not in st.session_state: st.session_state.app_uuid = str(uuid.uuid4())

# --- HELPER FUNCTIONS ---
def get_image_url(np_img):
    try:
        if np_img is None: return ""
        img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_data)
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"
    except: return ""

def fetch_osm_geometries(lat, lon, radius_km, mask_shape):
    offset = (radius_km / 111.32) / 2
    bbox = (lat - offset, lat + offset, lon - offset, lon + offset)
    
    # 1. Fetch Water Mask
    try:
        w_tags = {'natural': 'water', 'landuse': 'reservoir', 'waterway': 'riverbank'}
        w_gdf = ox.features_from_bbox(bbox[1], bbox[0], bbox[3], bbox[2], tags=w_tags)
        transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, mask_shape[1], mask_shape[0])
        w_mask = features.rasterize([(geom, 1) for geom in w_gdf.geometry], out_shape=mask_shape, transform=transform, fill=0)
    except: w_mask = np.zeros(mask_shape)

    # 2. Fetch Buildings
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

# --- SIDEBAR ---
st.sidebar.header("🛰️ Project Setup")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
search_mode = st.sidebar.radio("Location:", ["City", "Manual"])
city_q = st.sidebar.text_input("City", "Valencia") if search_mode=="City" else ""
radius = st.sidebar.slider("Radius (km)", 1, 20, 5)
dates = st.sidebar.date_input("Window", [datetime.date(2024, 10, 25), datetime.date(2024, 11, 5)])

st.sidebar.markdown("---")
st.sidebar.header("🗺️ Layers & Sync")
sync_maps = st.sidebar.toggle("Sync Map Views", value=True)
show_buildings = st.sidebar.toggle("Show Building Footprints", value=True)
base_map = st.sidebar.selectbox("Base", ["OpenStreetMap", "Esri World Imagery"])

btn_run = st.sidebar.button("🚀 EXECUTE ANALYSIS", type="primary", use_container_width=True)

# --- LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)
    
    if btn_run:
        lat, lon = (39.4699, -0.3763)
        if search_mode == "City":
            loc = Nominatim(user_agent="flood_pro").geocode(city_q)
            if loc: lat, lon = loc.latitude, loc.longitude
        
        st.session_state.last_search_coords_s1 = (lat, lon, radius)
        off = (radius / 111.32) / 2
        st.session_state.current_bounds_s1 = [[lat-off, lon-off], [lat+off, lon+off]]
        
        cat = SentinelHubCatalog(config=config)
        bbox = BBox(bbox=[lon-off, lat-off, lon+off, lat+off], crs=CRS.WGS84)
        search = cat.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(dates[0]), str(dates[1])))
        st.session_state.search_results_s1 = list(search)
        st.session_state.image_cache_s1 = {}

    tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "🧪 Advanced Lab", "🚨 Flood Impact"])

    with tab1: # DASHBOARD
        if st.session_state.search_results_s1:
            res = st.session_state.search_results_s1
            opts = [f"{i}: {r['properties']['datetime'][:10]}" for i,r in enumerate(res)]
            picks = st.multiselect("Render Dates:", opts, default=opts[:2])
            
            if st.button("Generate Viewers"):
                off = (st.session_state.last_search_coords_s1[2] / 111.32) / 2
                lat, lon, _ = st.session_state.last_search_coords_s1
                bbox_obj = BBox(bbox=[lon-off, lat-off, lon+off, lat+off], crs=CRS.WGS84)
                es = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
                
                for p in picks:
                    d = res[int(p.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=es, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(d,d))],
                                           responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(600, 600), config=config)
                    st.session_state.image_cache_s1[d] = req.get_data()[0]
                
                # Handshake OSM for water/buildings
                st.session_state.water_mask, st.session_state.building_gdf = fetch_osm_geometries(lat, lon, radius, (600,600))

            if st.session_state.image_cache_s1:
                cols = st.columns(len(st.session_state.image_cache_s1))
                for i, (dk, img_data) in enumerate(st.session_state.image_cache_s1.items()):
                    with cols[i]:
                        alpha = st.slider(f"Transparency {dk[:10]}", 0.0, 1.0, 0.8, key=f"a_{i}")
                        gain = st.slider(f"Gain {dk[:10]}", 0.5, 10.0, 3.0, key=f"g_{i}")
                        mode = st.radio(f"Pol {dk[:10]}", ["VV", "VH"], key=f"m_{i}", horizontal=True)
                        
                        ch = 0 if mode=="VV" else 1
                        proc = np.dstack([np.clip(img_data[:,:,ch]*gain, 0, 1)]*3)
                        m = folium.Map(location=[st.session_state.last_search_coords_s1[0], st.session_state.last_search_coords_s1[1]], zoom_start=13, tiles=base_map)
                        folium.raster_layers.ImageOverlay(get_image_url(proc), bounds=st.session_state.current_bounds_s1, opacity=alpha).add_to(m)
                        st_folium(m, height=400, key=f"map_dash_{i}")

    with tab2: # ADVANCED LAB
        if st.session_state.image_cache_s1:
            cmaps = ["viridis", "magma", "inferno", "plasma", "cividis", "Greys_r", "Blues", "YlGnBu", "winter", "coolwarm", "bwr", "tab20c", "brg", "binary", "gist_yarg", "gist_gray", "gray", "bone", "pink", "spring", "summer", "autumn", "winter", "cool", "Wistia", "hot", "afmhot", "gist_heat", "copper", "PiYG", "PRGn", "BrBG", "PuOr", "RdGy", "RdBu", "RdYlBu", "RdYlGn", "Spectral", "seismic", "twilight", "hsv", "Paired", "Accent", "Set1", "Set2", "tab10", "ocean", "terrain", "gnuplot", "jet", "turbo"]
            c1, c2, c3 = st.columns(3)
            date_l = c1.selectbox("Left Image", list(st.session_state.image_cache_s1.keys()), index=0)
            date_r = c2.selectbox("Right Image", list(st.session_state.image_cache_s1.keys()), index=1)
            cmap = c3.selectbox("Color Ramp", cmaps)
            
            db_l = 10 * np.log10(st.session_state.image_cache_s1[date_l][:,:,0] + 1e-10)
            db_r = 10 * np.log10(st.session_state.image_cache_s1[date_r][:,:,0] + 1e-10)
            
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
            ax1.imshow(db_l, cmap=cmap, vmin=-25, vmax=-5); ax1.set_title(f"Baseline: {date_l[:10]}"); ax1.axis('off')
            ax2.imshow(db_r, cmap=cmap, vmin=-25, vmax=-5); ax2.set_title(f"Crisis: {date_r[:10]}"); ax2.axis('off')
            st.pyplot(fig)
            
            export_geotiff(db_l, "Left_dB.tif", *st.session_state.last_search_coords_s1, "exp_l")
            export_geotiff(db_r, "Right_dB.tif", *st.session_state.last_search_coords_s1, "exp_r")

    with tab3: # FLOOD IMPACT
        if len(st.session_state.image_cache_s1) >= 2:
            st.subheader("🚨 Infrastructure Risk Assessment")
            b_key = st.selectbox("Baseline (Dry)", list(st.session_state.image_cache_s1.keys()), 0)
            a_key = st.selectbox("Crisis (Wet)", list(st.session_state.image_cache_s1.keys()), 1)
            
            sens = st.slider("Flood Sensitivity (dB Change)", -15.0, -2.0, -6.0)
            
            b_db = 10 * np.log10(st.session_state.image_cache_s1[b_key][:,:,0] + 1e-10)
            a_db = 10 * np.log10(st.session_state.image_cache_s1[a_key][:,:,0] + 1e-10)
            f_mask = ((a_db - b_db) < sens).astype(np.uint8)
            f_mask[st.session_state.water_mask == 1] = 0 # Exclude permanent water
            
            # --- SPATIAL JOIN: BUILDINGS VS FLOOD ---
            impact_b = None
            if st.session_state.building_gdf is not None:
                # Convert mask to polygons for intersection
                off = (st.session_state.last_search_coords_s1[2] / 111.32) / 2
                lat, lon, _ = st.session_state.last_search_coords_s1
                trans = from_bounds(lon-off, lat-off, lon+off, lat+off, 600, 600)
                shps = features.shapes(f_mask, mask=(f_mask==1), transform=trans)
                flood_polys = [shape(s) for s, v in shps]
                
                if flood_polys:
                    impact_b = st.session_state.building_gdf[st.session_state.building_gdf.intersects(box(lon-off, lat-off, lon+off, lat+off))]
                    # Just an example intersection check
                    impact_b['is_flooded'] = impact_b.geometry.apply(lambda x: any(x.intersects(p) for p in flood_polys))
                    affected = impact_b[impact_b['is_flooded'] == True]
                    st.metric("Buildings at Risk", len(affected))
            
            # --- MAP ---
            m_final = folium.Map(location=[lat, lon], zoom_start=14, tiles=base_map)
            
            # 1. Background
            bg = np.dstack([np.clip(st.session_state.image_cache_s1[a_key][:,:,0]*3, 0, 1)]*3)
            folium.raster_layers.ImageOverlay(get_image_url(bg), bounds=st.session_state.current_bounds_s1, opacity=0.5).add_to(m_final)
            
            # 2. Flood Overlay
            f_rgb = np.zeros((600,600,4))
            f_rgb[f_mask==1] = [0.1, 0.4, 0.9, 0.7] # Blue flood
            folium.raster_layers.ImageOverlay(get_image_url(f_rgb), bounds=st.session_state.current_bounds_s1).add_to(m_final)
            
            # 3. Buildings Overlay
            if show_buildings and impact_b is not None:
                folium.GeoJson(impact_b, style_function=lambda x: {'color': 'red' if x['properties'].get('is_flooded') else 'green', 'weight': 1, 'fillOpacity': 0.5}).add_to(m_final)

            st_folium(m_final, height=600, width=1200)
            
            # --- DOWNLOADS ---
            c1, c2 = st.columns(2)
            with c1:
                export_geotiff(f_mask, "flood_mask.tif", *st.session_state.last_search_coords_s1, "dl_f_mask")
            with c2:
                if impact_b is not None:
                    st.download_button("📐 Download Impacted Buildings (GeoJSON)", impact_b.to_json(), "buildings_at_risk.geojson")
else:
    st.info("Provide credentials to unlock full intelligence suite.")
