import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from sentinelhub import (SHConfig, SentinelHubRequest, DataCollection, MimeType, 
                         BBox, CRS, SentinelHubCatalog)
from geopy.geocoders import Nominatim
import datetime
import folium
from folium.plugins import DualMap
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
from shapely.geometry import shape

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Radar Master Pro", page_icon="📡")

# --- INITIALIZE SESSION STATE ---
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'app_uuid' not in st.session_state: st.session_state.app_uuid = str(uuid.uuid4())[:8]
if 'lat' not in st.session_state: st.session_state.lat, st.session_state.lon = 42.041, 3.126

# --- BASEMAPS ---
BASEMAPS = {
    "OpenStreetMap": "OpenStreetMap",
    "Satellite (Esri)": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "Topographic (OTM)": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    "Dark Matter": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
}

# --- HELPERS ---
def get_img_url(np_img):
    img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
    buffered = BytesIO()
    Image.fromarray(img_data).save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

def create_geotiff_download(data, filename, lat, lon, r_km, key):
    off = (r_km / 111.32) / 2
    if len(data.shape) == 3: data = data[:,:,0]
    tf = from_bounds(lon-off, lat-off, lon+off, lat+off, data.shape[1], data.shape[0])
    with MemoryFile() as mem:
        with mem.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                      dtype='float32', crs='EPSG:4326', transform=tf) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button("💾 Export TIFF", mem.read(), filename, "image/tiff", key=key)

# --- SIDEBAR ---
st.sidebar.title("🌊 Radar Master Pro")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

with st.sidebar.expander("📍 Location & Parameters", expanded=True):
    loc_mode = st.radio("Input Mode", ["City Search", "Manual XY"])
    if loc_mode == "City Search":
        city = st.text_input("City", "Torroella de Montgrí, Spain")
    else:
        c1, c2 = st.columns(2)
        st.session_state.lat = c1.number_input("Lat", value=st.session_state.lat, format="%.5f")
        st.session_state.lon = c2.number_input("Lon", value=st.session_state.lon, format="%.5f")
    r_km = st.sidebar.slider("Radius (km)", 1, 30, 8)
    win = st.sidebar.date_input("Window", [datetime.date.today() - datetime.timedelta(days=60), datetime.date.today()])

selected_bm = st.sidebar.selectbox("Global Basemap", list(BASEMAPS.keys()))
gain = st.sidebar.slider("Radar Gain", 0.5, 10.0, 3.0)
btn_search = st.sidebar.button("🔍 SEARCH ARCHIVE", type="primary", use_container_width=True)

if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        if loc_mode == "City Search":
            geolocator = Nominatim(user_agent=f"f_{st.session_state.app_uuid}")
            loc = geolocator.geocode(city, timeout=10)
            if loc: st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
        
        st.session_state.image_cache = {}
        catalog = SentinelHubCatalog(config=config)
        off = (r_km / 111.32) / 2
        bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
        st.session_state.search_results = list(catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(win[0]), str(win[1]))))

    tab1, tab2, tab3 = st.tabs(["📊 Quad Catalog", "🧪 Dual-Sync Lab", "🌊 Flood & Area Calc"])

    with tab1:
        if st.session_state.get('search_results'):
            res = st.session_state.search_results
            opts = [f"{i}: {r['properties']['datetime'][:16]}" for i, r in enumerate(res)]
            picks = st.multiselect("Pick up to 4 dates:", opts, default=opts[:min(len(opts), 2)])
            
            if st.button("RENDER QUAD-VIEW", use_container_width=True):
                off = (r_km / 111.32) / 2
                bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
                ev = "//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}"
                for p in picks:
                    dt = res[int(p.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=ev, input_data=[SentinelHubRequest.input_data(DataCollection.SENTINEL1_IW, (dt, dt))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox, size=(500, 500), config=config)
                    st.session_state.image_cache[dt] = req.get_data()[0]

            if st.session_state.image_cache:
                keys = list(st.session_state.image_cache.keys())
                g_cols = st.columns(2)
                for i, k in enumerate(keys[:4]):
                    with g_cols[i % 2]:
                        st.caption(f"📅 {k[:16]}")
                        m_q = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13, tiles=BASEMAPS[selected_bm], attr="Radar")
                        off = (r_km / 111.32) / 2
                        bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
                        folium.raster_layers.ImageOverlay(get_img_url(np.clip(st.session_state.image_cache[k]*gain, 0, 1)), bnds).add_to(m_q)
                        st_folium(m_q, height=300, key=f"q_{i}")

    with tab2:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            c_l1, c_l2, c_l3 = st.columns(3)
            d1 = c_l1.selectbox("Left Map", keys, index=0)
            d2 = c_l2.selectbox("Right Map", keys, index=1)
            cmap_name = c_l3.selectbox("Colormap", ["viridis", "magma", "bone", "RdYlBu", "hot"])
            
            # Create Dual Sync Map
            dm = DualMap(location=[st.session_state.lat, st.session_state.lon], zoom_start=14)
            off = (r_km / 111.32) / 2
            bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
            
            def get_colored_url(data, name):
                db = 10 * np.log10(np.squeeze(data) + 1e-10)
                norm = np.clip((db - (-25)) / ((-5) - (-25)), 0, 1)
                colored = plt.get_cmap(name)(norm)
                return get_img_url(colored), db

            url1, db1 = get_colored_url(st.session_state.image_cache[d1], cmap_name)
            url2, db2 = get_colored_url(st.session_state.image_cache[d2], cmap_name)
            
            folium.raster_layers.ImageOverlay(url1, bnds).add_to(dm.m1)
            folium.raster_layers.ImageOverlay(url2, bnds).add_to(dm.m2)
            st_folium(dm, height=500, use_container_width=True, key="dual_lab")

            # COLORBAR CHART
            st.write("### 📊 Backscatter Intensity Distribution (dB)")
            fig_hist, ax_hist = plt.subplots(figsize=(10, 2))
            ax_hist.hist(db1.flatten(), bins=100, color='gray', alpha=0.5, label="Left")
            ax_hist.hist(db2.flatten(), bins=100, color='blue', alpha=0.3, label="Right")
            ax_hist.set_xlim(-30, 0); ax_hist.legend(); ax_hist.set_yticks([])
            # Gradient bar under chart
            grad = np.linspace(0, 1, 256).reshape(1, -1)
            ax_hist.imshow(grad, extent=[-30, 0, -500, -100], aspect='auto', cmap=cmap_name)
            st.pyplot(fig_hist)

    with tab3:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            cf1, cf2, cf3 = st.columns(3)
            b_dt = cf1.selectbox("Baseline (Dry)", keys, index=0, key="fb")
            w_dt = cf2.selectbox("Crisis (Wet)", keys, index=1, key="fw")
            f_col = cf3.color_picker("Flood Color", "#00F6FF")
            
            c_s1, c_s2 = st.columns(2)
            sens = c_s1.slider("Sensitivity (dB drop)", -15.0, -2.0, -6.0)
            trans = c_s2.slider("Radar Transparency", 0.0, 1.0, 0.4)
            
            # Logic
            b_raw = np.squeeze(st.session_state.image_cache[b_dt])
            w_raw = np.squeeze(st.session_state.image_cache[w_dt])
            diff = 10 * np.log10(w_raw + 1e-10) - 10 * np.log10(b_raw + 1e-10)
            mask = (diff < sens).astype(np.uint8)
            
            # AREA CALCULATION
            pixel_size = (r_km * 1000 * 2) / 500 # Approx meters per pixel
            total_flooded_pixels = np.sum(mask)
            area_m2 = total_flooded_pixels * (pixel_size ** 2)
            
            st.metric("Total Flooded Area", f"{area_m2/10000:.2f} Hectares", delta=f"{area_m2/1e6:.3f} km²")
            
            mf = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14, tiles=BASEMAPS[selected_bm], attr="Radar")
            off = (r_km / 111.32) / 2
            bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
            
            folium.raster_layers.ImageOverlay(get_img_url(np.clip(w_raw*gain, 0, 1)), bnds, opacity=trans).add_to(mf)
            
            h = f_col.lstrip('#'); rgb = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
            m_rgb = np.zeros((*mask.shape, 4))
            m_rgb[mask == 1] = [*rgb, 0.8]
            folium.raster_layers.ImageOverlay(get_img_url(m_rgb), bnds).add_to(mf)
            
            st_folium(mf, height=550, use_container_width=True, key="f_final")
            
            # VECTOR & RASTER DOWNLOAD
            st.write("### 📥 Download Results")
            cd1, cd2 = st.columns(2)
            with cd1: create_geotiff_download(mask, "flood_mask.tif", st.session_state.lat, st.session_state.lon, r_km, "f_rast")
            with cd2:
                tf = from_bounds(st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off, mask.shape[1], mask.shape[0])
                shps = features.shapes(mask.astype('int16'), mask=(mask > 0), transform=tf)
                features_list = [{"type": "Feature", "properties": {"area_approx_m2": area_m2/total_flooded_pixels if total_flooded_pixels > 0 else 0}, "geometry": g} for g, v in shps]
                gj = {"type": "FeatureCollection", "features": features_list}
                st.download_button("📐 Export GeoJSON", json.dumps(gj), "flood.geojson", "application/json")
else:
    st.info("👋 Enter credentials to start.")
