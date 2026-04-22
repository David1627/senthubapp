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
import geopandas as gpd
from shapely.geometry import shape

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Flood Explorer Pro", page_icon="🌊")

# --- INITIALIZE SESSION STATE ---
if 'search_results_s1' not in st.session_state: st.session_state.search_results_s1 = None
if 'image_cache_s1' not in st.session_state: st.session_state.image_cache_s1 = {}
if 'app_uuid_s1' not in st.session_state: st.session_state.app_uuid_s1 = str(uuid.uuid4())
if 'last_search_coords_s1' not in st.session_state: st.session_state.last_search_coords_s1 = None
if 'current_bounds_s1' not in st.session_state: st.session_state.current_bounds_s1 = None
if 'lat' not in st.session_state: st.session_state.lat = None
if 'lon' not in st.session_state: st.session_state.lon = None

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

def create_geotiff_download(data, filename, lat, lon, radius_km, key):
    offset = (radius_km / 111.32) / 2
    transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, data.shape[1], data.shape[0])
    with MemoryFile() as memfile:
        with memfile.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                          dtype='float32', crs='EPSG:4326', transform=transform) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button(label=f"💾 Export TIFF", data=memfile.read(), file_name=filename, mime="image/tiff", key=key)

def create_geojson_download(mask, lat, lon, radius_km):
    offset = (radius_km / 111.32) / 2
    transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, mask.shape[1], mask.shape[0])
    mask_int = mask.astype('int16')
    shapes = features.shapes(mask_int, mask=(mask_int > 0), transform=transform)
    features_list = [{"type": "Feature", "properties": {"class": "flood_area"}, "geometry": geom} for geom, val in shapes]
    geojson_data = {"type": "FeatureCollection", "features": features_list}
    return st.download_button(label="📐 Download GeoJSON", data=json.dumps(geojson_data), file_name="flood.geojson", mime="application/json", use_container_width=True)

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Search Area")
search_mode = st.sidebar.radio("Location Mode:", ["Search City", "Manual Coordinates"])

if search_mode == "Search City":
    city_query = st.sidebar.text_input("City Name", value="", placeholder="e.g. Valencia, Spain")
    man_lat, man_lon = None, None
else:
    with st.sidebar.expander("📍 Coordinates Input", expanded=True):
        man_lat = st.number_input("Lat", value=None, format="%.6f", placeholder="0.0000")
        man_lon = st.number_input("Lon", value=None, format="%.6f", placeholder="0.0000")
    city_query = None

radius_km = st.sidebar.slider("Radius (km)", 1, 50, 10)
today = datetime.date.today()
date_range = st.sidebar.date_input("Date Window", value=(today - datetime.timedelta(days=14), today))

brightness = st.sidebar.slider("Radar Gain", 0.5, 10.0, 3.0)
selected_basemap = st.sidebar.selectbox("Base Map", ["OpenStreetMap", "Esri World Imagery", "CartoDB Positron"])

btn_search = st.sidebar.button("🔍 FETCH RADAR DATA", type="primary", use_container_width=True)

# --- CORE SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        target_lat, target_lon = None, None
        if search_mode == "Search City" and city_query:
            try:
                geolocator = Nominatim(user_agent=f"flood_explorer_{st.session_state.app_uuid_s1}")
                location = geolocator.geocode(city_query, timeout=10)
                if location: target_lat, target_lon = location.latitude, location.longitude
                else: st.error("City not found.")
            except: st.error("Geocoder busy.")
        else:
            target_lat, target_lon = man_lat, man_lon

        if target_lat is not None and target_lon is not None:
            st.session_state.lat, st.session_state.lon = target_lat, target_lon
            st.session_state.last_search_coords_s1 = (target_lat, target_lon, radius_km)
            offset = (radius_km / 111.32) / 2
            st.session_state.current_bounds_s1 = [[target_lat - offset, target_lon - offset], [target_lat + offset, target_lon + offset]]
            st.session_state.image_cache_s1 = {}
            
            catalog = SentinelHubCatalog(config=config)
            bbox_obj = BBox(bbox=[target_lon-offset, target_lat-offset, target_lon+offset, target_lat+offset], crs=CRS.WGS84)
            search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox_obj, time=(str(date_range[0]), str(date_range[1])))
            st.session_state.search_results_s1 = list(search)
        else:
            st.error("Please provide valid location details.")

    # --- TABS ---
    tab_dash, tab_ana, tab_flood, tab_impact = st.tabs(["🗺️ Dashboard", "🧪 Advanced Lab", "🚨 Flood Mapping", "🛣️ Infrastructure Impact"])

    with tab_dash:
        if st.session_state.search_results_s1:
            res = st.session_state.search_results_s1
            date_options = [f"{i}: {r['properties']['datetime'][:10]}" for i, r in enumerate(res)]
            sel_dates = st.multiselect("Select dates:", date_options, default=date_options[:min(len(date_options), 2)])

            if st.button("🖼️ RENDER RADAR", use_container_width=True):
                lat, lon, r_km = st.session_state.last_search_coords_s1
                offset = (r_km / 111.32) / 2
                bbox_obj = BBox(bbox=[lon-offset, lat-offset, lon+offset, lat+offset], crs=CRS.WGS84)
                evalscript = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
                for d_str in sel_dates:
                    actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=evalscript, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(actual_date, actual_date))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox_obj, size=(500, 500), config=config)
                    st.session_state.image_cache_s1[actual_date] = req.get_data()[0]

            if st.session_state.image_cache_s1:
                cols = st.columns(2)
                for i, d_str in enumerate(sel_dates):
                    actual_date = res[int(d_str.split(":")[0])]['properties']['datetime']
                    if actual_date in st.session_state.image_cache_s1:
                        with cols[i % 2]:
                            data = st.session_state.image_cache_s1[actual_date]
                            pol = st.radio(f"View ({actual_date[:10]})", ["VV", "VH", "False Color"], key=f"p_{i}", horizontal=True)
                            if pol == "VV": img = np.dstack([np.clip(data[:,:,0]*brightness, 0, 1)]*3)
                            elif pol == "VH": img = np.dstack([np.clip(data[:,:,1]*brightness*2, 0, 1)]*3)
                            else:
                                r = np.clip(data[:,:,0]/(data[:,:,1]+1e-5), 0, 1)
                                img = np.dstack([np.clip(data[:,:,0]*brightness, 0, 1), np.clip(data[:,:,1]*brightness, 0, 1), r])
                            m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=12, tiles=selected_basemap)
                            folium.raster_layers.ImageOverlay(image=get_image_url(img), bounds=st.session_state.current_bounds_s1).add_to(m)
                            st_folium(m, height=350, width=None, key=f"map_{i}")

    with tab_ana:
        if len(st.session_state.image_cache_s1) >= 2:
            st.subheader("🧪 Radar Backscatter Lab")
            d_list = list(st.session_state.image_cache_s1.keys())
            c_lab1, c_lab2, c_lab3 = st.columns(3)
            lab_before = c_lab1.selectbox("Baseline", d_list, index=0)
            lab_after = c_lab2.selectbox("Crisis", d_list, index=1)
            cmap_choice = c_lab3.selectbox("CMap", ["viridis", "magma", "Greys_r"])
            db_min, db_max = st.slider("Range (dB)", -35, 5, (-25, -5))
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 3))
            d_left = 10 * np.log10(st.session_state.image_cache_s1[lab_before][:,:,0] + 1e-10)
            d_right = 10 * np.log10(st.session_state.image_cache_s1[lab_after][:,:,0] + 1e-10)
            ax1.imshow(d_left, cmap=cmap_choice, vmin=db_min, vmax=db_max); ax1.axis('off')
            ax2.imshow(d_right, cmap=cmap_choice, vmin=db_min, vmax=db_max); ax2.axis('off')
            st.pyplot(fig)

    with tab_flood:
        if len(st.session_state.image_cache_s1) >= 2:
            d_list = list(st.session_state.image_cache_s1.keys())
            c1, c2, c3 = st.columns(3)
            before = c1.selectbox("Dry Date", d_list, index=0, key="f1")
            after = c2.selectbox("Wet Date", d_list, index=1, key="f2")
            f_color = c3.color_picker("Color", "#0060F6")
            b_db = 10 * np.log10(st.session_state.image_cache_s1[before][:,:,0] + 1e-10)
            a_db = 10 * np.log10(st.session_state.image_cache_s1[after][:,:,0] + 1e-10)
            sens_val = st.slider("Sensitivity", -15.0, -2.0, -6.0, key="flood_sens")
            flood_mask = ((a_db - b_db) < sens_val).astype(float)
            if st.checkbox("Clean Perm Water"): flood_mask[b_db < -16] = 0
            m_f = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=12, tiles=selected_basemap)
            bg = np.dstack([np.clip(st.session_state.image_cache_s1[after][:,:,0]*brightness, 0, 1)]*3)
            folium.raster_layers.ImageOverlay(image=get_image_url(bg), bounds=st.session_state.current_bounds_s1, opacity=0.4).add_to(m_f)
            h = f_color.lstrip('#'); rgb = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
            mask_rgb = np.zeros((*flood_mask.shape, 4))
            mask_rgb[flood_mask == 1] = [*rgb, 0.8]
            folium.raster_layers.ImageOverlay(image=get_image_url(mask_rgb), bounds=st.session_state.current_bounds_s1).add_to(m_f)
            st_folium(m_f, height=500, width=None, key="f_map")
            create_geotiff_download(flood_mask, "flood.tif", *st.session_state.last_search_coords_s1, key="dl_t")
            create_geojson_download(flood_mask, *st.session_state.last_search_coords_s1)

    with tab_impact:
        if len(st.session_state.image_cache_s1) >= 2 and st.session_state.last_search_coords_s1:
            st.subheader("🏠 Building Impact Analysis")
            
            try:
                # 1. Load Building Parcels (Ensure you have this file or use osmnx to fetch)
                # If you use OSMnx: buildings_gdf = ox.geometries_from_point((lat, lon), tags={'building': True}, dist=radius_km*1000)
                buildings_gdf = gpd.read_file("data/buildings/parcels.geojson")
                if buildings_gdf.crs is None: buildings_gdf.set_crs("EPSG:4326", inplace=True)
                
                # 2. Re-generate the Flood Mask Polygons
                b_db = 10 * np.log10(st.session_state.image_cache_s1[before][:,:,0] + 1e-10)
                a_db = 10 * np.log10(st.session_state.image_cache_s1[after][:,:,0] + 1e-10)
                # Use the sensitivity slider value from the session state
                f_mask = ((a_db - b_db) < st.session_state.flood_sens).astype(np.uint8)
                
                lat, lon, r_km = st.session_state.last_search_coords_s1
                off = (r_km / 111.32) / 2
                trans = from_bounds(lon-off, lat-off, lon+off, lat+offset, f_mask.shape[1], f_mask.shape[0])
                
                flood_shapes = features.shapes(f_mask, mask=(f_mask > 0), transform=trans)
                flood_polys = [shape(geom) for geom, val in flood_shapes]
                
                if flood_polys:
                    flood_gdf = gpd.GeoDataFrame({'geometry': flood_polys}, crs="EPSG:4326")
                    
                    # 3. SPATIAL JOIN (Check which buildings are touched by flood)
                    # We use sjoin (Spatial Join) because it's faster for entire polygons than 'intersection'
                    affected_buildings = gpd.sjoin(buildings_gdf, flood_gdf, predicate='intersects')
                    
                    # 4. Map Visualization
                    m_imp = folium.Map(location=[lat, lon], zoom_start=14, tiles=selected_basemap)
                    
                    # Style for Gray (Safe) Buildings
                    folium.GeoJson(
                        buildings_gdf, 
                        name="All Buildings",
                        style_function=lambda x: {
                            'fillColor': 'gray', 
                            'color': 'gray', 
                            'weight': 0.5, 
                            'fillOpacity': 0.2
                        }
                    ).add_to(m_imp)
                    
                    # Style for Red (Affected) Buildings
                    if not affected_buildings.empty:
                        folium.GeoJson(
                            affected_buildings, 
                            name="Flooded Buildings",
                            style_function=lambda x: {
                                'fillColor': 'red', 
                                'color': 'darkred', 
                                'weight': 1, 
                                'fillOpacity': 0.7
                            }
                        ).add_to(m_imp)
                        
                        st.error(f"🚨 ALERT: {len(affected_buildings)} building parcels detected within the flooded zone.")
                    else:
                        st.success("✅ No buildings detected within the current flood mask.")
                        
                    st_folium(m_imp, height=600, width=None, key="building_impact_viewer")
                else:
                    st.info("No significant flood detected to perform building analysis.")
                    
            except Exception as e:
                st.warning(f"Building data not found. To use this, add a GeoJSON of parcels to your data folder. Error: {e}")
        else:
            st.info("💡 Please fetch radar data and complete the 'Flood Mapping' tab first.")
else:
    st.info("👋 Enter credentials to begin.")
