import streamlit as st

from common import api_key
from views import dashboard

dashboard.API_KEY = api_key()
df = dashboard.fetch_process_data(api_key())
tabs = st.tabs(["📊 Dashboard", "📈 Analytics"])
with tabs[0]:
    dashboard.render_resource_charts(df)
with tabs[1]:
    dashboard.render_analytics()
