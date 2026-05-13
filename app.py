"""Streamlit UI for the arXiv daily report generator."""

import datetime
import os

import streamlit as st
import streamlit.components.v1 as components

from arxiv_report.fetcher import ARXIV_TZ, fetch_arxiv_papers
from arxiv_report.providers import generate_report
from arxiv_report.render import REPORTS_DIR, save_html

st.set_page_config(page_title='arXiv astro-ph.HE Daily Report', layout='wide')
st.title('arXiv astro-ph.HE Daily Report Generator')

left, right = st.columns([1, 3], gap='large')

with left:
    st.subheader('🛠 Generate')
    today_et = datetime.datetime.now(ARXIV_TZ).date()
    selected_date = st.date_input('Date (ET)', value=today_et)
    expected_path = f'{REPORTS_DIR}/arXiv_astro_ph_HE_daily_report_{selected_date}.html'

    if os.path.exists(expected_path):
        st.caption('✅ A report already exists for this date (clicking will overwrite).')

    if st.button('Generate report', type='primary', use_container_width=True):
        as_of = ARXIV_TZ.localize(datetime.datetime.combine(selected_date, datetime.time(hour=12)))
        with st.status('Starting…', expanded=True) as status:
            st.write('🔍 Fetching arXiv papers…')
            papers = fetch_arxiv_papers(as_of=as_of)
            st.write(f'Found **{len(papers)}** papers')

            if not papers:
                status.update(label='Empty window', state='complete')
                st.warning('No papers for this date (weekend / holiday / out of range).')
            else:
                st.write('🧠 Calling LLM (may take 1–2 minutes)…')
                try:
                    report, provider = generate_report(papers)
                    st.write(f'Provider: **{provider}**')
                    st.write('💾 Writing HTML…')
                    save_html(papers, report, provider, as_of=as_of)
                    status.update(label='Done ✅', state='complete')
                except Exception as e:
                    status.update(label='Generation failed', state='error')
                    st.error(f'Failed: {e}')

with right:
    st.subheader(f'📄 Preview: {selected_date}')
    if os.path.exists(expected_path):
        with open(expected_path, encoding='utf-8-sig') as f:
            html = f.read()
        components.html(html, height=1200, scrolling=True)
    else:
        st.info(f'No report for `{selected_date}` yet. Click "Generate report" on the left.')
