"""Streamlit UI for the arXiv daily report generator."""

import datetime
import os

import streamlit as st
import streamlit.components.v1 as components

from arxiv_report.fetcher import ARXIV_TZ, fetch_arxiv_papers
from arxiv_report.providers import generate_report
from arxiv_report.render import REPORTS_DIR, save_html

st.set_page_config(page_title='arXiv astro-ph.HE Daily Report', layout='wide')

_HEADER_HTML = """
<style>
    .block-container { padding-top: 2rem !important; }
</style>
<div style="margin: 0 0 22px;">
    <h1 style="margin: 0; font-size: 2.05rem; font-weight: 800;
               letter-spacing: -0.018em; line-height: 1.12;">
        arXiv astro-ph.HE<br><span style="
            background: linear-gradient(120deg, #1f4e8c 0%, #6c3eb0 55%, #c93d8a 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        ">Daily Report Generator</span>
    </h1>
</div>
"""
st.markdown(_HEADER_HTML, unsafe_allow_html=True)

left, right = st.columns([1, 3], gap='large')

with left:
    today_et = datetime.datetime.now(ARXIV_TZ).date()
    selected_date = st.date_input('Date (ET)', value=today_et)
    expected_path = f'{REPORTS_DIR}/arXiv_astro_ph_HE_daily_report_{selected_date}.html'

    if os.path.exists(expected_path):
        st.caption('✅ A report already exists for this date (clicking will overwrite).')

    if st.button('Generate report', type='primary', use_container_width=True):
        as_of = ARXIV_TZ.localize(datetime.datetime.combine(selected_date, datetime.time(hour=12)))
        with st.status('Starting…', expanded=True) as status:
            st.write('🔍 Fetching arXiv papers…')
            try:
                papers = fetch_arxiv_papers(as_of=as_of)
            except Exception as e:
                status.update(label='Fetch failed', state='error')
                st.error(f'arXiv fetch failed: {e}')
                st.info(
                    'arXiv API may be rate-limiting (HTTP 429). Wait 5-15 minutes and try again.'
                )
                st.stop()
            st.write(f'Found **{len(papers)}** papers')

            if not papers:
                status.update(label='Empty window', state='complete')
                st.warning('No papers for this date (weekend / holiday / out of range).')
            else:
                st.write('🧠 Calling LLM (may take several minutes)…')
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
    if os.path.exists(expected_path):
        with open(expected_path, encoding='utf-8-sig') as f:
            html = f.read()
        components.html(html, height=1200, scrolling=True)
    else:
        st.info(f'No report for {selected_date} yet. Click "Generate report" on the left.')
