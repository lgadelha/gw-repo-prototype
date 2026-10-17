import streamlit as st

from views import process_profile

process_profile.render(st.session_state['all_runs'])
