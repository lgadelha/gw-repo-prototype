import streamlit as st

from common import api_key
from views import ml

ml.API_KEY = api_key()
st.caption(":material/history: Legacy page, kept as it was until the ML rework (PLAN-provenance-ideas.md item 1).")
tabs = st.tabs(["🤖 Training", "🎯 Optimizations", "🚀 Bulk Optimizations"])
with tabs[0]:
    ml.render_ml_training()
with tabs[1]:
    ml.render_optimizations()
with tabs[2]:
    ml.render_bulk_optimizations()
