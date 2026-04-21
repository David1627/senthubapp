import streamlit as st
import numpy as np
from sentinelhub import (SHConfig, SentinelHubRequest, DataCollection, MimeType, 
                         BBox, CRS, SentinelHubCatalog)
from geopy.geocoders import Nominatim
import datetime
import folium
from streamlit_folium import st_folium
import base64
from io import BytesIO
from PIL import Image
import osmnx as ox

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="S1 Light Pro")

# --- STATE MANAGEMENT ---
if 'lat' not in st.session_state: st.session_state.lat = 39.4699
if 'lon' not in st.session_state: st.session_state.lon = -0.3763
if 'img_cache' not in st.session_state: st.session_state.img_cache = {}
if 'buildings_cache' not in st.session_state: st.session_state.buildings_cache = None

def get_light_url(data):
    """Downsamples and encodes for speed."""
    try:
        # Take VV band only for the preview to save 50% memory
        slice_2d = data[:,:,0] if data.ndim == 3 else data
        img_8bit = (np.clip(slice_2d * 2.5, 0, 1) * 255).astype(np.uint8)
        img = Image.fromarray(img_8bit)
        
        # DOWNSIZE: This makes the map snappy
        img = img.resize((400, 400), Image.NEAREST)
        
        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
    except: return ""

# --- SIDEBAR ---
with st.sidebar:
    st.header("🔑 Auth")
    cid = st.text_input("Client ID", type="password")
    csec = st.text_input("Client Secret", type="password")
    
    st.header("📍 Search")
    city = st.text_input("City Search")
    if city:
        try:
            loc = Nominatim(user_agent="light_app").geocode(city)
            if loc: 
                st.session_state.lat, st.session_state.lon = loc.latitude, loc.longitude
        except: pass
    
    radius = st.slider("Radius (km)", 1, 10, 3) # Capped at 10 for performance
    include_infra = st.checkbox("🛰️ Overlay Infrastructure", value=False)
    
    run_btn = st.button("🚀 FETCH DATA", type="primary", use_container_width=True)

# --- ENGINE ---
if run_btn and cid and csec:
    config = SHConfig(sh_client_id=cid, sh_client_secret=csec)
    cat = SentinelHubCatalog(config=config)
    
    # Simple Geometry
    off = (radius / 111.32) / 2
    bbox = BBox(bbox=[st.session_state.lon-off, st.session_state.lat-off, st.session_state.lon+off, st.session_state.lat+off], crs=CRS.WGS84)
    
    # 1. Fetch Buildings (Lightweight Mode)
    if include_infra:
        with st.spinner("Decimating Infrastructure Data..."):
            try:
                # Get footprints and simplify geometry to save browser memory
                gdf = ox.features_from_point((st.session_state.lat, st.session_state.lon), dist=radius*1000, tags={'building': True})
                st.session_state.buildings_cache = gdf[['geometry']].simplify(tolerance=0.0001)
            except: st.session_state.buildings_cache = None

    # 2. Fetch Radar (Last 2 dates)
    with st.spinner("Fetching Satellite Layers..."):
        search = list(cat.search(DataCollection.SENTINEL1_IW, bbox=bbox, time=(str(datetime.date.today()-datetime.timedelta(days=20)), str(datetime.date.today()))))
        if search:
            st.session_state.img_cache = {}
            es = "//VERSION=3\nfunction setup(){return{input:['VV'],output:{bands:1,sampleType:'FLOAT32'}};}function evaluatePixel(s){return[s.VV];}"
            for i in range(min(2, len(search))):
                d = search[i]['properties']['datetime']
                req = SentinelHubRequest(evalscript=es, input_data=[SentinelHubRequest.input_data(data_collection=DataCollection.SENTINEL1_IW, time_interval=(d,d))],
                                       responses=[SentinelHubRequest.output_response('default', MimeType.TIFF)], bbox=bbox, size=(400, 400), config=config)
                st.session_state.img_cache[d] = req.get_data()[0]

# --- UI DISPLAY ---
tab1, tab2 = st.tabs(["🗺️ Live Map", "📊 Analysis"])

with tab1:
    if st.session_state.img_cache:
        dates = list(st.session_state.img_cache.keys())
        m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=14, prefer_canvas=True)
        
        off = (radius / 111.32) / 2
        bnds = [[st.session_state.lat-off, st.session_state.lon-off], [st.session_state.lat+off, st.session_state.lon+off]]
        
        # Latest Layer
        folium.raster_layers.ImageOverlay(get_light_url(st.session_state.img_cache[dates[0]]), bounds=bnds, opacity=0.8, name="Radar").add_to(m)
        
        # Infrastructure (Green Outlines)
        if st.session_state.buildings_cache is not None:
            folium.GeoJson(st.session_state.buildings_cache, name="Buildings", 
                           style_function=lambda x: {'color':'#00FF00','weight':1,'fillOpacity':0}).add_to(m)
            
        st_folium(m, height=500, width=1000)
    else:
        st.info("Enter Credentials and click Fetch Data.")

with tab2:
    if len(st.session_state.img_cache) >= 2:
        st.subheader("Lightweight Change Analysis")
        keys = list(st.session_state.img_cache.keys())
        diff = st.session_state.img_cache[keys[0]] - st.session_state.img_cache[keys[1]]
        st.image(get_light_url(diff * 5), caption="Red/Dark = Potential Flooding", use_container_width=True)
