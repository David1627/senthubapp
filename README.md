
[Try the App](https://sentappapp-ej6o5f4gymr3z222xg4ehn.streamlit.app/)

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

git clone [senthubapp](https://github.com/David1627/senthubapp.git)
cd senthubapp
pip install -r requirements.txt

Note: Required libraries include streamlit, sentinelhub, folium, streamlit-folium, matplotlib, pandas, and geopy

streamlit run app.py

**Indices**
The Analysis Lab utilizes the following formulas to transform raw satellite data into actionable insights:

<img width="1200" height="250" alt="image" src="https://github.com/user-attachments/assets/8a431aa6-c6b3-4f09-9646-0008c6fdc098" />



**User Interface Preview**

**Dashboard Tab:** A layout optimized for side-by-side time-lapse comparison.
<img width="1300" height="600" alt="image" src="https://github.com/user-attachments/assets/24cd03f9-e551-413a-8fbb-e65a23622bfc" />


**Analysis Tab:** A scientific workspace with colormap selectors and pixel-distribution histograms.
<img width="1300" height="600" alt="image" src="https://github.com/user-attachments/assets/2a54ffad-f4fd-4b63-a0bc-72655f210a8d" />



