import streamlit as st

from views import provenance

all_runs = st.session_state['all_runs']
selected = all_runs[all_runs['id'] == st.session_state.get('run')] if not all_runs.empty else all_runs
if selected.empty:
    st.title("Provenance")
    st.info("Pick a run in the sidebar, or select one on the Runs page.")
else:
    provenance.render(selected.iloc[0], all_runs)
