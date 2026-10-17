import streamlit as st

from views import compare

compare.render(st.session_state['all_runs'], st.session_state['filtered_runs'])
