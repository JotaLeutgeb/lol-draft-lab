import streamlit as st

def inject_css():
    """Scout Protocol — Dark theme premium con acento violeta (diferenciado del equipo)."""
    st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap');
    
    * { font-family: 'Outfit', sans-serif; }
    
    html, body, [class*="st-"], .stMarkdown, p, div, span, label { 
        font-size: 16px !important; 
    }
    
    /* Scout: acento violeta en lugar de azul */
    :root {
        --accent: #A855F7;
        --accent-dim: rgba(168, 85, 247, 0.15);
        --accent-border: rgba(168, 85, 247, 0.25);
        --bg-main: #09090F;
        --bg-card: rgba(255,255,255,0.03);
        --bg-sidebar: linear-gradient(180deg, #0F0A1A 0%, #09090F 100%);
        --text-primary: #F1F5F9;
        --text-secondary: #94A3B8;
        --text-muted: #475569;
        --border: rgba(255,255,255,0.07);
        --positive: #4ADE80;
        --negative: #F87171;
        --warning: #FBBF24;
    }
    
    .stApp { background-color: var(--bg-main); color: var(--text-primary); }
    
    [data-testid="stSidebar"] { 
        background: var(--bg-sidebar);
        border-right: 1px solid var(--border);
    }
    
    /* Scout Hub Header */
    .scout-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: linear-gradient(135deg, rgba(168, 85, 247, 0.08) 0%, rgba(9,9,15,0.9) 100%);
        backdrop-filter: blur(12px);
        padding: 28px 36px;
        border-radius: 20px;
        border: 1px solid var(--accent-border);
        margin-bottom: 32px;
    }
    .scout-header-left { display: flex; align-items: center; gap: 24px; }
    .scout-hexbadge {
        width: 64px; height: 64px;
        background: linear-gradient(135deg, #A855F7, #7C3AED);
        clip-path: polygon(25% 0%, 75% 0%, 100% 50%, 75% 100%, 25% 100%, 0% 50%);
        display: flex; align-items: center; justify-content: center;
        font-weight: 800; font-size: 28px; color: #FFF;
    }
    .scout-title { 
        margin: 0; font-size: 36px; font-weight: 800; line-height: 1;
        background: linear-gradient(90deg, #FFF 0%, #A855F7 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    .scout-subtitle { font-size: 12px; color: var(--text-muted); letter-spacing: 3px; text-transform: uppercase; margin-top: 6px; font-weight: 700; }
    .scout-player-tag {
        background: var(--accent-dim);
        border: 1px solid var(--accent-border);
        color: #C084FC;
        padding: 8px 20px;
        border-radius: 100px;
        font-size: 14px; font-weight: 700;
        display: flex; align-items: center; gap: 8px;
    }

    /* KPI Cards */
    .scout-kpi {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 24px;
        text-align: center;
        transition: border-color 0.2s;
    }
    .scout-kpi:hover { border-color: var(--accent-border); }
    .scout-kpi-val { font-size: 42px; font-weight: 800; color: #FFF; line-height: 1; margin-bottom: 6px; }
    .scout-kpi-label { font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 2px; }
    .scout-kpi-delta { font-size: 13px; font-weight: 700; margin-top: 6px; }
    .delta-positive { color: var(--positive); }
    .delta-negative { color: var(--negative); }

    /* Glassmorphism Card */
    .scout-glass {
        background: var(--bg-card);
        backdrop-filter: blur(10px);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 24px;
        margin-bottom: 16px;
    }

    /* Trend Badge */
    .trend-up   { color: var(--positive); font-weight: 800; }
    .trend-down { color: var(--negative); font-weight: 800; }
    .trend-flat { color: var(--text-muted); font-weight: 700; }

    /* Champion Pool Table */
    .scout-table { width: 100%; border-collapse: collapse; }
    .scout-tr { border-bottom: 1px solid var(--border); transition: background 0.2s; }
    .scout-tr:hover { background: rgba(168,85,247,0.04); }
    .scout-td { padding: 14px 12px; vertical-align: middle; }
    .scout-th { padding: 12px; font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1.5px; border-bottom: 1px solid var(--border); }
    .scout-champ-name { font-weight: 700; font-size: 15px; color: var(--text-primary); }
    .scout-champ-role { font-size: 11px; color: var(--accent); font-weight: 700; margin-top: 2px; }
    .scout-val { font-family: monospace; font-size: 16px; font-weight: 800; color: var(--text-secondary); }
    .scout-wr-good { color: var(--positive); font-weight: 800; }
    .scout-wr-bad  { color: var(--negative); font-weight: 800; }

    /* Alerts */
    .scout-alert { padding: 20px 24px; border-radius: 14px; display: flex; gap: 16px; border: 1px solid transparent; margin-bottom: 12px; }
    .alert-critical { background: rgba(239,68,68,0.08); border-color: rgba(239,68,68,0.2); }
    .alert-warning  { background: rgba(251,191,36,0.08); border-color: rgba(251,191,36,0.2); }
    .alert-info     { background: rgba(168,85,247,0.08); border-color: rgba(168,85,247,0.2); }
    .scout-alert-icon  { font-size: 28px; }
    .scout-alert-title { font-weight: 800; font-size: 16px; color: #FFF; }
    .scout-alert-desc  { font-size: 14px; color: var(--text-secondary); margin-top: 4px; }

    /* Stat Gap Row */
    .gap-positive { color: var(--positive); font-weight: 800; font-size: 14px; }
    .gap-negative { color: var(--negative); font-weight: 800; font-size: 14px; }

    /* Scoreboard estilo war room */
    .scout-sb-val { font-size: 15px; font-weight: 800; color: #E2E8F0; }
    .scout-sb-gap { font-size: 11px; font-weight: 700; margin-top: 2px; }
    .scout-sb-gap.gap-positive { color: var(--positive); font-size: 11px; }
    .scout-sb-gap.gap-negative { color: var(--negative); font-size: 11px; }

    /* Peer Rank Badge */
    .peer-rank-badge {
        display: inline-flex; align-items: center; justify-content: center;
        width: 36px; height: 36px; border-radius: 50%;
        font-weight: 800; font-size: 16px;
        border: 2px solid var(--accent);
        color: #C084FC;
    }

    /* Section Headers */
    .scout-section-label {
        font-size: 11px; font-weight: 800; color: var(--text-muted);
        text-transform: uppercase; letter-spacing: 2px;
        margin-bottom: 14px;
    }

    /* Tabs */
    .stTabs [data-baseweb="tab-list"] { gap: 10px; background: transparent; padding: 0 10px; }
    .stTabs [data-baseweb="tab"] { 
        height: 56px; background: #110D1C; border-radius: 12px 12px 0 0;
        color: var(--text-muted); font-weight: 700; padding: 0 28px;
        border: 1px solid #1F1635; border-bottom: none; font-size: 15px;
    }
    .stTabs [aria-selected="true"] { 
        background: #1A1130 !important; color: #FFF !important;
        border-top: 3px solid #A855F7 !important;
    }

    /* Buttons */
    .stButton > button {
        font-size: 14px !important; font-weight: 700 !important;
        padding: 10px 18px !important; border-radius: 10px !important;
    }

    /* Custom Scrollbar */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #2D1F4A; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)
