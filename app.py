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
from io import BytesIO
from PIL import Image
import uuid
import rasterio
from rasterio.transform import from_bounds

# --- PAGE CONFIG ---
st.set_page_config(layout="wide", page_title="Sentinel Explorer Pro V7.1", page_icon="🌍")

# --- INITIALIZE SESSION STATE ---
if 'search_results' not in st.session_state: st.session_state.search_results = None
if 'image_cache' not in st.session_state: st.session_state.image_cache = {}
if 'group_a_pos' not in st.session_state: st.session_state.group_a_pos = {"center": [40.4168, -3.7038], "zoom": 13}
if 'group_b_pos' not in st.session_state: st.session_state.group_b_pos = {"center": [40.4168, -3.7038], "zoom": 13}
if 'current_bounds' not in st.session_state: st.session_state.current_bounds = None
if 'bbox_coords' not in st.session_state: st.session_state.bbox_coords = None

# --- NEW: TRUE GEOTIFF DOWNLOAD FUNCTION ---
def download_geotiff(data, filename):
    """Writes a real GeoTIFF with spatial coordinates"""
    if st.session_state.bbox_coords is None:
        return st.error("No spatial metadata found.")

    # Get bounds from session state [lon_min, lat_min, lon_max, lat_max]
    bounds = st.session_state.bbox_coords
    height, width = data.shape[:2]
    
    # Handle multi-band vs single-band (Index)
    count = data.shape[2] if len(data.shape) > 2 else 1
    
    # Create the spatial transform
    transform = from_bounds(bounds[0], bounds[1], bounds[2], bounds[3], width, height)
    
    buf = BytesIO()
    with rasterio.open(
        buf, 'w',
        driver='GTiff',
        height=height,
        width=width,
        count=count,
        dtype='float32',
        crs='EPSG:4326',
        transform=transform,
    ) as dst:
        if count > 1:
            for i in range(count):
                dst.write(data[:, :, i], i + 1)
        else:
            dst.write(data, 1)

    return st.download_button(
        label=f"📥 Download Georeferenced {filename}",
        data=buf.getvalue(),
        file_name=filename,
        mime="image/tiff",
        use_container_width=True
    )

# ... [Keep get_image_url and get_season from previous versions] ...

# --- MODIFIED SEARCH LOGIC ---
# Inside your 'if btn_search:' block, add this line:
# st.session_state.bbox_coords = [lon-offset, lat-offset, lon+offset, lat+offset]

# --- UPDATED ANALYSIS LAB VIEW ---
with tab_analysis:
    if not st.session_state.image_cache:
        st.warning("⚠️ Download data in the Dashboard first.")
    else:
        # ... [Keep your existing headers and calculations] ...
        
        with col_sidebar:
            st.subheader("🛠️ Parameters")
            # [Your existing Index selection logic]
            
            st.markdown("---")
            st.subheader("💾 GIS Export")
            # THIS IS THE KEY PART
            download_geotiff(val, f"{target_idx}_Analysis.tif")
            st.caption("Standard GeoTIFF (EPSG:4326) compatible with QGIS/ArcGIS.")

        with col_main:
            # Main Figure (Reasonable Size: 10x6)
            fig, ax = plt.subplots(figsize=(10, 6), dpi=100)
            if show_overlay:
                ax.imshow(np.clip(data[:, :, [2, 1, 0]] * brightness, 0, 1))
                im = ax.imshow(masked_val, cmap=cmap_sel, alpha=overlay_alpha, vmin=-1, vmax=1)
            else:
                im = ax.imshow(masked_val, cmap=cmap_sel, vmin=-1, vmax=1)
            plt.colorbar(im, fraction=0.046, pad=0.04)
            ax.axis('off')
            st.pyplot(fig)

            # Histogram (Reasonable Size: Wide but Short)
            st.markdown("### 📊 Pixel Distribution")
            fig_h, ax_h = plt.subplots(figsize=(10, 2.5))
            ax_h.hist(val[~np.isnan(val)], bins=100, color='royalblue', alpha=0.7)
            ax_h.set_facecolor('#f0f2f6')
            st.pyplot(fig_h)
