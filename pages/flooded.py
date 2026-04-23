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

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Flood Explorer Pro", page_icon="🌊")

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

def create_geotiff_download(data, filename, lat, lon, radius_km, key):
    offset = (radius_km / 111.32) / 2
    transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, data.shape[1], data.shape[0])
    with MemoryFile() as memfile:
        with memfile.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                          dtype='float32', crs='EPSG:4326', transform=transform) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button(label=f"💾 Export TIFF", data=memfile.read(), file_name=filename, mime="image/tiff", key=key)

def create_geojson_download(mask, lat, lon, radius_km, key):
    offset = (radius_km / 111.32) / 2
    transform = from_bounds(lon-offset, lat-offset, lon+offset, lat+offset, mask.shape[1], mask.shape[0])
    mask_int = mask.astype('int16')
    shapes = features.shapes(mask_int, mask=(mask_int > 0), transform=transform)
    features_list = [{"type": "Feature", "properties": {"class": "flood_area"}, "geometry": geom} for geom, val in shapes]
    geojson_data = {"type": "FeatureCollection", "features": features_list}
    return st.download_button(label="📐 Export GeoJSON", data=json.dumps(geojson_data), file_name="flood.geojson", mime="application/json", key=key)

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

st.sidebar.markdown("---")
st.sidebar.header("2. Search Area")
city_query = st.sidebar.text_input("Location", value="Torroella de Montgrí, Spain")
radius_km = st.sidebar.slider("Radius (km)", 1, 30, 10)
today = datetime.date.today()
date_range = st.sidebar.date_input("Date Window", value=(today - datetime.timedelta(days=30), today))

brightness = st.sidebar.slider("Radar Gain", 0.5, 10.0, 3.0)
btn_search = st.sidebar.button("🔍 FETCH RADAR DATA", type="primary", use_container_width=True)

# --- SEARCH LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        try:
            geolocator = Nominatim(user_agent=f"flood_app_{st.session_state.app_uuid}")
            location = geolocator.geocode(city_query, timeout=10)
            if location:
                st.session_state.lat, st.session_state.lon = location.latitude, location.longitude
                st.session_state.image_cache = {} 
                catalog = SentinelHubCatalog(config=config)
                offset = (radius_km / 111.32) / 2
                bbox = BBox(bbox=[st.session_state.lon-offset, st.session_state.lat-offset, 
                                  st.session_state.lon+offset, st.session_state.lat+offset], crs=CRS.WGS84)
                search = catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(date_range[0]), str(date_range[1])))
                st.session_state.search_results = list(search)
                if not st.session_state.search_results: st.warning("No images found.")
            else: st.error("Location not found.")
        except Exception as e: st.error(f"Search failed: {e}")

    # --- TABS ---
    tab_dash, tab_lab, tab_flood = st.tabs(["🗺️ Dashboard", "🧪 Advanced Lab", "🚨 Change Analysis"])

    with tab_dash:
        if st.session_state.search_results:
            res = st.session_state.search_results
            options = [f"{i}: {r['properties']['datetime'][:16]}" for i, r in enumerate(res)]
            selected = st.multiselect("Select acquisitions:", options, default=options[:min(2, len(options))])

            if st.button("🖼️ RENDER RADAR", use_container_width=True):
                offset = (radius_km / 111.32) / 2
                bbox = BBox(bbox=[st.session_state.lon-offset, st.session_state.lat-offset, 
                                  st.session_state.lon+offset, st.session_state.lat+offset], crs=CRS.WGS84)
                evalscript = "//VERSION=3\nfunction setup(){return{input:['VV','VH'],output:{bands:2,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV,s.VH];}"
                for opt in selected:
                    dt = res[int(opt.split(":")[0])]['properties']['datetime']
                    with st.spinner(f"Downloading {dt[:10]}..."):
                        req = SentinelHubRequest(evalscript=evalscript, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(dt, dt))],
                                                responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox, size=(600, 600), config=config)
                        st.session_state.image_cache[dt] = req.get_data()[0]

            if st.session_state.image_cache:
                for dt in st.session_state.image_cache:
                    col_img, col_dl = st.columns([4, 1])
                    data = st.session_state.image_cache[dt]
                    with col_img:
                        st.write(f"**Acquisition: {dt}**")
                        img_vv = np.dstack([np.clip(data[:,:,0]*brightness, 0, 1)]*3)
                        st.image(img_vv, use_container_width=True)
                    with col_dl:
                        st.write("Downloads")
                        create_geotiff_download(data[:,:,0], f"S1_{dt[:10]}_VV.tif", st.session_state.lat, st.session_state.lon, radius_km, f"dl_{dt}")

    with tab_lab:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            c1, c2, c3 = st.columns(3)
            d1 = c1.selectbox("Baseline (Dry)", keys, index=0, key="lab_b")
            d2 = c2.selectbox("Crisis (Wet)", keys, index=1, key="lab_w")
            cmap_choice = c3.selectbox("Colormap", ["Greys_r", "viridis", "magma", "bone", "inferno"])
            
            db1 = 10 * np.log10(st.session_state.image_cache[d1][:,:,0] + 1e-10)
            db2 = 10 * np.log10(st.session_state.image_cache[d2][:,:,0] + 1e-10)
            
            fig, ax = plt.subplots(1, 2, figsize=(10, 4))
            ax[0].imshow(db1, cmap=cmap_choice, vmin=-25, vmax=-5); ax[0].set_title(f"Dry: {d1[:10]}"); ax[0].axis('off')
            im = ax[1].imshow(db2, cmap=cmap_choice, vmin=-25, vmax=-5); ax[1].set_title(f"Wet: {d2[:10]}"); ax[1].axis('off')
            plt.colorbar(im, ax=ax, orientation='horizontal', label='dB intensity', pad=0.1)
            st.pyplot(fig)
            
            # Download Options for Lab
            cdl1, cdl2 = st.columns(2)
            cdl1.write(f"Download Dry ({d1[:10]})")
            create_geotiff_download(db1, "dry_db.tif", st.session_state.lat, st.session_state.lon, radius_km, "lab_dl1")
            cdl2.write(f"Download Wet ({d2[:10]})")
            create_geotiff_download(db2, "wet_db.tif", st.session_state.lat, st.session_state.lon, radius_km, "lab_dl2")

    with tab_flood:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            c1, c2, c3 = st.columns(3)
            b_dt = c1.selectbox("Baseline Date", keys, index=0, key="f_b")
            w_dt = c2.selectbox("Flood Date", keys, index=1, key="f_w")
            flood_color = c3.color_picker("Flood Overlay Color", "#00E0FF")
            
            threshold = st.slider("Flood Sensitivity (dB Change)", -15.0, -2.0, -6.0)
            
            b_data = st.session_state.image_cache[b_dt][:,:,0]
            w_data = st.session_state.image_cache[w_dt][:,:,0]
            diff = 10 * np.log10(w_data + 1e-10) - 10 * np.log10(b_data + 1e-10)
            
            flood_mask = (diff < threshold).astype(float)
            if st.checkbox("Clean Permanent Water (Deep Lows)"): flood_mask[10*np.log10(b_data+1e-10) < -18] = 0
            
            # Mapping
            m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
            bg = np.dstack([np.clip(w_data*brightness, 0, 1)]*3)
            offset = (radius_km / 111.32) / 2
            bounds = [[st.session_state.lat-offset, st.session_state.lon-offset], [st.session_state.lat+offset, st.session_state.lon+offset]]
            folium.raster_layers.ImageOverlay(image=get_image_url(bg), bounds=bounds, opacity=0.5).add_to(m)
            
            # Apply Color to Mask
            h = flood_color.lstrip('#'); rgb = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
            mask_rgb = np.zeros((*flood_mask.shape, 4))
            mask_rgb[flood_mask == 1] = [*rgb, 0.8]
            folium.raster_layers.ImageOverlay(image=get_image_url(mask_rgb), bounds=bounds).add_to(m)
            
            st_folium(m, height=500, width=None)

            # --- DOWNLOADS FOR LAST TAB ---
            st.markdown("### 📥 Export Flood Analysis")
            ce1, ce2 = st.columns(2)
            with ce1:
                st.write("Export Raster (TIFF)")
                create_geotiff_download(flood_mask, "flood_mask.tif", st.session_state.lat, st.session_state.lon, radius_km, "f_dl_raster")
            with ce2:
                st.write("Export Vector (GeoJSON)")
                create_geojson_download(flood_mask, st.session_state.lat, st.session_state.lon, radius_km, "f_dl_vector")
        else:
            st.info("Render at least 2 images in Dashboard to analyze change.")

else:
    st.info("👋 Enter credentials to begin.")
