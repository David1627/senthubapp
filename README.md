[Try the App](https://david1627-senthubapp-app-1vx9cq.streamlit.app/)


***Sentinel Explorer Pro***
Sentinel Explorer Pro is a high-performance, Streamlit-based geospatial intelligence dashboard. It allows users to search, visualize, and analyze multi-spectral satellite imagery from the Sentinel-2 (L2A) constellation via the Sentinel Hub API.

The platform is designed for researchers, urban planners, and GIS enthusiasts who need to compare temporal changes and perform advanced spectral analysis without a complex desktop GIS setup.

**Key Features**
Comparison Dashboard (Quad-View)
Temporal Stacking: Compare up to 4 different dates for the same location simultaneously.

Synchronized Navigation: Use Sync Groups (A & B) to lock the pan and zoom of multiple maps together for frame-perfect change detection.

Independent Band Mapping: Render each quadrant with unique compositions:

Natural Color (B04, B03, B02)

False Color NIR (B08, B04, B03) — Ideal for vegetation health.

Agriculture (B11, B08, B02) — Deep crop analysis.

Custom RGB — Define your own band math.

**Spectral Analysis Lab**
Advanced Indices: Instant calculation of NDVI, NDMI, NDWI, and NDBI.

Feature Masking: Use dynamic threshold sliders to isolate specific landscape features (e.g., show only water or only dense forest).

Layer Blending: Overlay spectral heatmaps on top of natural color imagery with adjustable transparency.

Statistical Reporting: Real-time histograms and site metadata (Season, Cloud Cover, Mean Pixel Intensity).


**Installation & Setup**
1. Prerequisites
Ensure you have a Sentinel Hub account. You will need your Client ID and Client Secret from the Sentinel Hub Dashboard.

2. Install Dependencies
Clone this repository and install the required Python libraries:

git clone https://github.com/your-username/sentinel-explorer-pro.git
cd sentinel-explorer-pro
pip install -r requirements.txt

Note: Required libraries include streamlit, sentinelhub, folium, streamlit-folium, matplotlib, pandas, and geopy

streamlit run app.py


