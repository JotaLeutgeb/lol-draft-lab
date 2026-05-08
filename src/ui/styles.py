import streamlit as st

def inject_css():
    st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap');
    
    * { font-family: 'Outfit', sans-serif; }
    
    /* GLOBAL FONT SIZE INCREASE */
    html, body, [class*="st-"], .stMarkdown, p, div, span, label { 
        font-size: 18px !important; 
    }
    
    .stApp { background-color: #0B0E14; color: #E0E0E0; }
    
    [data-testid="stSidebar"] { 
        background: linear-gradient(180deg, #10141D 0%, #0B0E14 100%);
        border-right: 1px solid #1F2937;
    }
    
    /* Challenger Protocol Header */
    .cp-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: rgba(16, 20, 29, 0.8);
        backdrop-filter: blur(10px);
        padding: 30px 40px;
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.05);
        margin-bottom: 40px;
    }
    .cp-header-left { display: flex; align-items: center; gap: 30px; }
    .cp-hexbadge {
        width: 70px; height: 70px;
        background: #3B82F6;
        clip-path: polygon(25% 0%, 75% 0%, 100% 50%, 75% 100%, 25% 100%, 0% 50%);
        display: flex; align-items: center; justify-content: center;
        font-weight: 800; font-size: 32px; color: #FFF;
    }
    .cp-title { 
        margin: 0; font-size: 42px; font-weight: 800; color: #FFF; line-height: 1;
        background: linear-gradient(90deg, #FFF 0%, #94A3B8 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    .cp-title span { color: #3B82F6; -webkit-text-fill-color: #3B82F6; }
    .cp-subtitle { font-size: 16px; color: #64748B; letter-spacing: 3px; text-transform: uppercase; margin-top: 8px; font-weight: 600; }
    
    .cp-header-right { display: flex; align-items: center; gap: 40px; }
    .cp-status-pill {
        background: rgba(34, 197, 94, 0.1);
        border: 1px solid rgba(34, 197, 94, 0.2);
        color: #4ADE80;
        padding: 12px 24px;
        border-radius: 100px;
        font-size: 14px; font-weight: 700;
        display: flex; align-items: center; gap: 12px;
    }
    .cp-status-dot { width: 12px; height: 12px; background: #4ADE80; border-radius: 50%; box-shadow: 0 0 15px #4ADE80; }

    /* Alerts */
    .cp-alert {
        padding: 24px;
        border-radius: 16px;
        display: flex; gap: 20px;
        border: 1px solid transparent;
        margin-bottom: 15px;
    }
    .alert-critical { background: rgba(239, 68, 68, 0.12); border-color: rgba(239, 68, 68, 0.2); }
    .alert-warning { background: rgba(245, 158, 11, 0.12); border-color: rgba(245, 158, 11, 0.2); }
    .alert-info { background: rgba(59, 130, 246, 0.12); border-color: rgba(59, 130, 246, 0.2); }
    .alert-good { background: rgba(34, 197, 94, 0.12); border-color: rgba(34, 197, 94, 0.2); }
    
    .cp-alert-icon { font-size: 32px; margin-top: 2px; }
    .cp-alert-title { font-weight: 800; font-size: 20px; color: #FFF; line-height: 1.3; }
    .cp-alert-desc { font-size: 16px; color: #94A3B8; margin-top: 8px; }
    .cp-alert-rate { font-size: 14px; font-weight: 700; text-transform: uppercase; margin-top: 12px; opacity: 0.9; }

    /* Glassmorphism Cards */
    .cp-glass-card {
        background: rgba(255, 255, 255, 0.04);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 20px;
        padding: 30px;
        margin-bottom: 20px;
    }
    .cp-kpi-val { font-size: 48px; font-weight: 800; color: #FFF; margin-bottom: 8px; line-height: 1; }
    .cp-kpi-label { font-size: 16px; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 2px; }
    
    /* Scoreboard Professional */
    .cp-scoreboard { width: 100%; border-collapse: collapse; }
    .cp-sb-row { border-bottom: 1px solid rgba(255, 255, 255, 0.05); transition: all 0.3s ease; }
    .cp-sb-row:hover { background: rgba(255, 255, 255, 0.03); }
    .cp-sb-cell { padding: 20px 15px; vertical-align: middle; }
    .cp-sb-name { font-weight: 700; font-size: 20px; color: #F1F5F9; }
    .cp-sb-role { font-size: 14px; color: #64748B; text-transform: uppercase; font-weight: 700; margin-bottom: 4px; }
    .cp-sb-val { font-family: monospace; font-size: 22px; font-weight: 800; color: #CBD5E1; }
    .cp-sb-gap { font-size: 16px; font-weight: 800; }
    .gap-positive { color: #4ADE80; }
    .gap-negative { color: #F87171; }
    
    .cp-table-header { font-size: 15px; font-weight: 800; color: #64748B; text-transform: uppercase; letter-spacing: 2px; }

    /* Insights */
    .cp-insight-card { padding: 30px; border-radius: 16px; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); }
    .cp-insight-title { font-size: 16px; font-weight: 800; letter-spacing: 2px; margin-bottom: 15px; }
    .cp-insight-body { font-size: 18px; color: #CBD5E1; line-height: 1.6; }

    /* Loss Routes */
    .cp-loss-card {
        display: flex; gap: 20px; align-items: center;
        padding: 20px; background: rgba(255,255,255,0.02);
        border-radius: 14px; margin-bottom: 12px; border: 1px solid rgba(255,255,255,0.05);
    }
    .cp-loss-pct { font-size: 32px; font-weight: 800; font-family: monospace; }
    .cp-loss-title { font-weight: 700; font-size: 18px; color: #FFF; }
    .cp-loss-desc { font-size: 14px; color: #64748B; margin-top: 4px; }

    /* Custom Scrollbar */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #1F2937; border-radius: 10px; }
    ::-webkit-scrollbar-thumb:hover { background: #334155; }

    /* Streamlit Overrides */
    .stTabs [data-baseweb="tab-list"] { gap: 15px; background: transparent; padding: 0 20px; }
    .stTabs [data-baseweb="tab"] { 
        height: 65px; background: #111827; border-radius: 14px 14px 0 0; 
        color: #64748B; font-weight: 800; padding: 0 35px; border: 1px solid #1F2937;
        border-bottom: none; font-size: 18px;
    }
    .stTabs [aria-selected="true"] { 
        background: #1F2937 !important; color: #FFF !important; border-top: 4px solid #3B82F6 !important;
    }
    
    /* Buttons */
    .stButton > button {
        font-size: 16px !important; font-weight: 700 !important;
        padding: 10px 20px !important; border-radius: 10px !important;
    }
</style>
""", unsafe_allow_html=True)
