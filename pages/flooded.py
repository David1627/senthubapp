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

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Radar Master Pro", page_icon="📡")

# --- INITIALIZE SESSION STATE ---
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'app_uuid' not in st.session_state: st.session_state.app_uuid = str(uuid.uuid4())[:8]
if 'lat' not in st.session_state: st.session_state.lat, st.session_state.lon = 42.041, 3.126

# --- HELPERS ---
def get_img_url(np_img):
    img_data = (np.clip(np_img, 0, 1) * 255).astype(np.uint8)
    buffered = BytesIO()
    Image.fromarray(img_data).save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

def create_geotiff_download(data, filename, lat, lon, r_km, key, label="📥"):
    off = (r_km / 111.32) / 2
    if len(data.shape) == 3: data = data[:,:,0]
    tf = from_bounds(lon-off, lat-off, lon+off, lat+off, data.shape[1], data.shape[0])
    with MemoryFile() as mem:
        with mem.open(driver='GTiff', height=data.shape[0], width=data.shape[1], count=1,
                      dtype='float32', crs='EPSG:4326', transform=tf) as ds:
            ds.write(data.astype('float32'), 1)
        return st.download_button(label, mem.read(), filename, "image/tiff", key=key, use_container_width=True)

# --- SIDEBAR ---
st.sidebar.title("🌊 Radar Master Pro")
CLIENT_ID = st.sidebar.text_input("Client ID", type="password")
CLIENT_SECRET = st.sidebar.text_input("Client Secret", type="password")

with st.sidebar.expander("📍 Location & View Controls", expanded=True):
    city = st.text_input("City Search", "Torroella de Montgrí, Spain")
    r_km = st.sidebar.slider("Radius (km)", 1, 30, 8)
    center_map = st.checkbox("Auto-Center Map", value=True, help="Uncheck to keep your manual zoom/pan position.")
    win = st.sidebar.date_input("Window", [datetime.date.today() - datetime.timedelta(days=60), datetime.date.today()])

gain = st.sidebar.slider("Radar Gain", 0.5, 10.0, 3.0)
btn_search = st.sidebar.button("🔍 SEARCH", type="primary", use_container_width=True)

# --- CORE LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        geolocator = Nominatim(user_agent=f"f_{st.session_state.app_uuid}")
        loc = geolocator.geocode(city, timeout=10)
        if loc: st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
        
        st.session_state.image_cache = {}
        catalog = SentinelHubCatalog(config=config)
        off = (r_km / 111.32) / 2
        bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, 
                          st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
        st.session_state.search_results = list(catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(win[0]), str(win[1]))))

    tab1, tab2, tab3 = st.tabs(["📊 Quad View", "🧪 Color Lab", "🌊 Flood Analyst"])

    with tab1:
        if st.session_state.get('search_results'):
            res = st.session_state.search_results
            opts = [f"{i}: {r['properties']['datetime'][:16]}" for i, r in enumerate(res)]
            picks = st.multiselect("Pick dates:", opts, default=opts[:min(len(opts), 2)])
            
            if st.button("RENDER IMAGES"):
                off = (r_km / 111.32) / 2
                bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, 
                                  st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
                ev = "//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}"
                for p in picks:
                    dt = res[int(p.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=ev, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(dt, dt))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox, size=(600, 600), config=config)
                    st.session_state.image_cache[dt] = req.get_data()[0]

            if st.session_state.image_cache:
                keys = list(st.session_state.image_cache.keys())
                g_cols = st.columns(2)
                for i, k in enumerate(keys[:4]):
                    with g_cols[i % 2]:
                        c_m1, c_m2 = st.columns([4, 1])
                        c_m1.caption(f"📅 {k[:16]}")
                        create_geotiff_download(st.session_state.image_cache[k], f"S1_{k[:10]}.tif", st.session_state.lat, st.session_state.lon, r_km, f"q_{i}", "📥")
                        
                        m = folium.Map(location=[st.session_state.lat, st.session_state.lon] if center_map else None, zoom_start=13)
                        off = (r_km / 111.32) / 2
                        bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
                        folium.raster_layers.ImageOverlay(get_img_url(np.clip(st.session_state.image_cache[k]*gain, 0, 1)), bnds).add_to(m)
                        st_folium(m, height=300, key=f"quad_map_{i}", center=[st.session_state.lat, st.session_state.lon] if center_map else None)

    with tab2:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            cl1, cl2, cl3 = st.columns([2,2,1])
            d1, d2 = cl1.selectbox("Left", keys, index=0), cl2.selectbox("Right", keys, index=1)
            # Expanded Colormaps for Water/Wetness
            cmap_name = cl3.selectbox("Wetness Ramps", ["GnBu", "PuBu", "YlGnBu", "Blues", "winter", "viridis", "magma", "bone", "coolwarm", "Spectral"])
            
            # Dashboard Header
            dash_l, dash_r, _ = st.columns([2, 2, 1])
            with dash_l: create_geotiff_download(st.session_state.image_cache[d1], "left.tif", st.session_state.lat, st.session_state.lon, r_km, "l_lab_d", "📥 Download Baseline")
            with dash_r: create_geotiff_download(st.session_state.image_cache[d2], "right.tif", st.session_state.lat, st.session_state.lon, r_km, "r_lab_d", "📥 Download Analysis")

            m_col, leg_col = st.columns([7, 0.5])
            with m_col:
                dm = DualMap(location=[st.session_state.lat, st.session_state.lon] if center_map else None, zoom_start=14)
                off = (r_km / 111.32) / 2
                bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
                
                def apply_c(data):
                    db = 10 * np.log10(np.squeeze(data) + 1e-10)
                    norm = np.clip((db - (-25)) / ((-5) - (-25)), 0, 1)
                    return get_img_url(plt.get_cmap(cmap_name)(norm))

                folium.raster_layers.ImageOverlay(apply_c(st.session_state.image_cache[d1]), bnds).add_to(dm.m1)
                folium.raster_layers.ImageOverlay(apply_c(st.session_state.image_cache[d2]), bnds).add_to(dm.m2)
                st_folium(dm, height=550, use_container_width=True, center=[st.session_state.lat, st.session_state.lon] if center_map else None)

            with leg_col:
                fig_v, ax_v = plt.subplots(figsize=(0.5, 5)) # Thin vertical legend
                norm = plt.Normalize(vmin=-25, vmax=-5)
                plt.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap_name), cax=ax_v, orientation='vertical')
                ax_v.set_ylabel('dB Intensity', fontsize=8)
                st.pyplot(fig_v)

    with tab3:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            cf1, cf2, cf3 = st.columns(3)
            b_dt, w_dt = cf1.selectbox("Dry Baseline", keys, index=0, key="f1"), cf2.selectbox("Wet Crisis", keys, index=1, key="f2")
            f_col = cf3.color_picker("Flood Overlay Color", "#00FFFF")
            
            # Mask Logic & Area
            b_raw, w_raw = np.squeeze(st.session_state.image_cache[b_dt]), np.squeeze(st.session_state.image_cache[w_dt])
            sens = st.slider("Flood Threshold (dB drop)", -15.0, -2.0, -6.0)
            mask = ((10 * np.log10(w_raw + 1e-10) - 10 * np.log10(b_raw + 1e-10)) < sens).astype(np.uint8)
            
            px_m = (r_km * 2000) / 600
            area_ha = (np.sum(mask) * (px_m**2)) / 10000
            area_km2 = area_ha / 100
            
            # Dashboard Control Bar
            act1, act2, met1, met2 = st.columns([1,1,2,2])
            with act1: create_geotiff_download(mask, "flood_mask.tif", st.session_state.lat, st.session_state.lon, r_km, "f_t", "📥 TIFF")
            with act2:
                tf = from_bounds(st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off, mask.shape[1], mask.shape[0])
                shps = list(features.shapes(mask.astype('int16'), mask=(mask > 0), transform=tf))
                gj = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": g} for g, v in shps]}
                st.download_button("📐 JSON", json.dumps(gj), "flood.geojson", "application/json", use_container_width=True)
            met1.metric("Flooded (Ha)", f"{area_ha:.2f}")
            met2.metric("Flooded (km²)", f"{area_km2:.4f}")

            mf = folium.Map(location=[st.session_state.lat, st.session_state.lon] if center_map else None, zoom_start=14)
            bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
            folium.raster_layers.ImageOverlay(get_img_url(np.clip(w_raw*gain, 0, 1)), bnds, opacity=0.4).add_to(mf)
            
            m_rgb = np.zeros((*mask.shape, 4))
            h = f_col.lstrip('#'); rgb = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
            m_rgb[mask == 1] = [*rgb, 0.8]
            folium.raster_layers.ImageOverlay(get_img_url(m_rgb), bnds).add_to(mf)
            st_folium(mf, height=550, use_container_width=True, center=[st.session_state.lat, st.session_state.lon] if center_map else None)
