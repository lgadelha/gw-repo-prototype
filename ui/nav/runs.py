import streamlit as st

from views import runs

runs.render(st.session_state['filtered_runs'], "nav/run_detail.py")
