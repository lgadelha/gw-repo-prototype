import streamlit as st

from views import ml

ml.render(st.session_state['all_runs'])
