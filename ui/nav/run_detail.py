import streamlit as st

from views import run_detail

all_runs = st.session_state['all_runs']
selected = all_runs[all_runs['id'] == st.session_state.get('run')] if not all_runs.empty else all_runs
if selected.empty:
    st.title("Run detail")
    st.info("Pick a run in the sidebar, or select one on the Runs page.")
else:
    run_detail.render(selected.iloc[0])
