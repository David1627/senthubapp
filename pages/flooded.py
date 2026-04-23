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
st.set_page_config(layout="wide", page_title="S1 Radar Master", page_icon="📡")

# --- INITIALIZE SESSION STATE ---
if 'search_results' not in st.session_state: st.session_state.search_results = None
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'app_uuid' not in st.session_state: st.session_state.app_uuid = str(uuid.uuid4())[:8]
if 'lat' not in st.session_state: st.session_state.lat, st.session_state.lon = 42.041, 3.126

# --- BASEMAP DICTIONARY ---
BASEMAPS = {
    "OpenStreetMap": "OpenStreetMap",
    "Satellite (Esri)": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "Terrain (Esri)": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}",
    "Gray (Esri)": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    "Dark Matter (CartoDB)": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    "Positron (CartoDB)": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    "Voyager (CartoDB)": "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
    "Topographic (OTM)": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    "National Geographic": "https://server.arcgisonline.com/ArcGIS/rest/services/NatGeo_World_Map/MapServer/tile/{z}/{y}/{x}",
    "Stamen Toner": "http://tile.stamen.com/toner/{z}/{x}/{y}.png"
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
        return st.download_button("💾 TIFF", mem.read(), filename, "image/tiff", key=key)

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
    
    r_km = st.slider("Radius (km)", 1, 30, 8)
    win = st.date_input("Window", [datetime.date.today() - datetime.timedelta(days=60), datetime.date.today()])

selected_bm = st.sidebar.selectbox("Global Basemap", list(BASEMAPS.keys()))
gain = st.sidebar.slider("Radar Gain", 0.5, 10.0, 3.0)
btn_search = st.sidebar.button("🔍 SEARCH ARCHIVE", type="primary", use_container_width=True)

# --- CORE LOGIC ---
if CLIENT_ID and CLIENT_SECRET:
    config = SHConfig(sh_client_id=CLIENT_ID, sh_client_secret=CLIENT_SECRET)

    if btn_search:
        if loc_mode == "City Search":
            geolocator = Nominatim(user_agent=f"f_{st.session_state.app_uuid}")
            loc = geolocator.geocode(city, timeout=10)
            if loc:
                st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
            else: st.error("City not found.")
        
        st.session_state.image_cache = {}
        catalog = SentinelHubCatalog(config=config)
        off = (r_km / 111.32) / 2
        bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, 
                          st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
        st.session_state.search_results = list(catalog.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(win[0]), str(win[1]))))

    tab1, tab2, tab3 = st.tabs(["📊 Quad Catalog", "🧪 Multi-Color Lab", "🌊 Flood Processor"])

    with tab1:
        if st.session_state.search_results:
            res = st.session_state.search_results
            opts = [f"{i}: {r['properties']['datetime'][:16]}" for i, r in enumerate(res)]
            picks = st.multiselect("Pick up to 4 dates for Quad-Sync:", opts, default=opts[:min(len(opts), 4)])
            
            if st.button("RENDER QUAD-VIEW", use_container_width=True):
                off = (r_km / 111.32) / 2
                bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, 
                                  st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
                ev = "//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}"
                for p in picks:
                    dt = res[int(p.split(":")[0])]['properties']['datetime']
                    req = SentinelHubRequest(evalscript=ev, input_data=[SentinelHubRequest.input_data(DataCollection.SENTINEL1_IW, (dt, dt))],
                                            responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox, size=(500, 500), config=config)
                    st.session_state.image_cache[dt] = req.get_data()[0]

            if st.session_state.image_cache:
                keys = list(st.session_state.image_cache.keys())
                # Dynamic Grid
                grid_cols = st.columns(2)
                for i, k in enumerate(keys[:4]):
                    with grid_cols[i % 2]:
                        st.caption(f"📅 {k[:16]}")
                        m_q = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13, tiles=BASEMAPS[selected_bm], attr="SentinelHub")
                        off = (r_km / 111.32) / 2
                        bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
                        folium.raster_layers.ImageOverlay(get_img_url(np.clip(st.session_state.image_cache[k]*gain, 0, 1)), bnds).add_to(m_q)
                        st_folium(m_q, height=350, key=f"quad_{i}")

    with tab2:
        if len(st.session_state.image_cache) >= 1:
            keys = list(st.session_state.image_cache.keys())
            c_s1, c_s2 = st.columns([1, 3])
            date_to_color = c_s1.selectbox("Target Date", keys)
            cmap_name = c_s1.selectbox("Colormap (15+ Options)", 
                ["viridis", "magma", "inferno", "plasma", "cividis", "Spectral", "RdYlBu", "coolwarm", "terrain", "ocean", "gist_earth", "bone", "pink", "hot", "jet"])
            
            # Apply color mapping
            raw = np.squeeze(st.session_state.image_cache[date_to_color])
            db = 10 * np.log10(raw + 1e-10)
            norm_db = np.clip((db - (-25)) / ((-5) - (-25)), 0, 1)
            colored = plt.get_cmap(cmap_name)(norm_db)
            
            with c_s2:
                m_lab = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14, tiles=BASEMAPS[selected_bm], attr="SentinelHub")
                folium.raster_layers.ImageOverlay(get_img_url(colored), bnds).add_to(m_lab)
                st_folium(m_lab, height=500, use_container_width=True, key="lab_map")
            
            st.divider()
            create_geotiff_download(raw, f"SAR_{cmap_name}.tif", st.session_state.lat, st.session_state.lon, r_km, "dl_lab")

    with tab3:
        if len(st.session_state.image_cache) >= 2:
            keys = list(st.session_state.image_cache.keys())
            cf1, cf2, cf3 = st.columns(3)
            b_dt = cf1.selectbox("Dry Date", keys, index=0)
            w_dt = cf2.selectbox("Wet Date", keys, index=1)
            f_col = cf3.color_picker("Flood Color", "#FF0000")
            
            c_sl1, c_sl2 = st.columns(2)
            sens = c_sl1.slider("Sensitivity (dB)", -15.0, -2.0, -6.0)
            trans = c_sl2.slider("Transparency", 0.0, 1.0, 0.5)
            
            # Flood Logic
            b_val = np.squeeze(st.session_state.image_cache[b_dt])
            w_val = np.squeeze(st.session_state.image_cache[w_dt])
            diff = 10 * np.log10(w_val + 1e-10) - 10 * np.log10(b_val + 1e-10)
            mask = (diff < sens).astype(np.uint8)
            
            mf = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14, tiles=BASEMAPS[selected_bm], attr="SentinelHub")
            folium.raster_layers.ImageOverlay(get_img_url(np.clip(w_val*gain, 0, 1)), bnds, opacity=trans).add_to(mf)
            
            h = f_col.lstrip('#'); rgb = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
            m_rgb = np.zeros((*mask.shape, 4))
            m_rgb[mask == 1] = [*rgb, 0.9]
            folium.raster_layers.ImageOverlay(get_img_url(m_rgb), bnds).add_to(mf)
            
            st_folium(mf, height=550, use_container_width=True, key="f_final")
            
            st.write("### 📥 Export Result")
            create_geotiff_download(mask, "flood_mask.tif", st.session_state.lat, st.session_state.lon, r_km, "f_dl")
else:
    st.info("👋 Enter credentials in the sidebar to start.")
