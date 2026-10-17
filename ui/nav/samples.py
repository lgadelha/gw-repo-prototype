import streamlit as st

from views import samples

all_runs = st.session_state['all_runs']
selected = all_runs[all_runs['id'] == st.session_state.get('run')] if not all_runs.empty else all_runs
if selected.empty:
    st.title("Samples")
    st.info("Pick a run in the sidebar, or select one on the Runs page.")
else:
    samples.render(selected.iloc[0], st.session_state['filtered_runs'])
