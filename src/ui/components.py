import streamlit as st
import pandas as pd

def render_alert_card(title, desc, icon="🔵", severity="info", loss_rate=0.0):
    cls = f"alert-{severity}"
    st.markdown(f"""
    <div class="cp-alert {cls}">
      <div class="cp-alert-icon">{icon}</div>
      <div>
        <div class="cp-alert-title">{title}</div>
        <div class="cp-alert-desc">{desc}</div>
        <div class="cp-alert-rate">{loss_rate*100:.0f}% de derrotas</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

def render_metric_card(label, value, delta=None):
    st.metric(label, value, delta)

def render_insight_card(title, body, severity="info"):
    st.markdown(f"""
    <div class="cp-insight-card {severity}">
      <div class="cp-insight-title">{title.upper()}</div>
      <div class="cp-insight-body">{body}</div>
    </div>
    """, unsafe_allow_html=True)

def render_loss_route(pct, title, desc, severity="info"):
    color = {"critical": "#F87171", "warning": "#F0B429", "info": "#A78BFA"}.get(severity, "#A78BFA")
    st.markdown(f"""
    <div class="cp-loss-card">
      <div class="cp-loss-pct" style="color:{color}">{pct}%</div>
      <div>
        <div class="cp-loss-title">{title}</div>
        <div class="cp-loss-desc">{desc}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

def render_drill_item(num, title, desc, color="#64748B"):
    st.markdown(f"""
    <div class="cp-drill-item">
      <div class="cp-drill-num" style="color:{color}; border-color:{color}">{num}</div>
      <div>
        <div class="cp-drill-title">{title}</div>
        <div class="cp-drill-desc">{desc}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

def render_kpi_card(label, value, delta=None):
    delta_html = ""
    if delta:
        color = "#4ADE80" if not str(delta).startswith("-") else "#F87171"
        delta_html = f'<div style="font-size:12px; color:{color}; font-weight:700;">{delta}</div>'
    
    st.markdown(f"""
    <div class="cp-glass-card">
        <div class="cp-kpi-label">{label}</div>
        <div class="cp-kpi-val">{value}</div>
        {delta_html}
    </div>
    """, unsafe_allow_html=True)

def render_war_room_header(team_name="CHALLENGER PROTOCOL", status="OPERATIONAL"):
    st.markdown(f"""
    <div class="cp-header">
        <div class="cp-header-left">
            <div class="cp-hexbadge">C</div>
            <div>
                <div class="cp-title">{team_name} <span>ACTIVO</span></div>
                <div class="cp-subtitle">SISTEMA DE ANÁLISIS TÁCTICO DE ÉLITE</div>
            </div>
        </div>
        <div class="cp-header-right">
            <div class="cp-status-pill">
                <div class="cp-status-dot"></div>
                {status}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_scoreboard(df_summary, name_map, df_bench):
    sb_html = '<table class="cp-scoreboard">'
    sb_html += '<thead><tr style="text-align:left;">'
    sb_html += '<th class="cp-sb-cell cp-table-header">JUGADOR</th>'
    sb_html += '<th class="cp-sb-cell cp-table-header" style="text-align:center;">KDA</th>'
    sb_html += '<th class="cp-sb-cell cp-table-header" style="text-align:center;">IMPACTO</th>'
    sb_html += '<th class="cp-sb-cell cp-table-header" style="text-align:center;">CS/MIN</th>'
    sb_html += '<th class="cp-sb-cell cp-table-header" style="text-align:center;">DPM</th>'
    sb_html += '<th class="cp-sb-cell cp-table-header" style="text-align:center;">VS/MIN</th></tr></thead>'
    
    for _, row in df_summary.iterrows():
        name = name_map.get(row["game_name"].lower(), row["game_name"])
        role = row["role"]
        
        p_impact = row["avg_impact_score"]
        p_kda = row["avg_kda"]
        p_cs = row.get("avg_cs_per_min", 0)
        p_dpm = row.get("avg_damage_per_min", 0)
        p_vision = row["avg_vision_per_min"]
        
        b_impact, b_kda, b_cs, b_dpm, b_vision = None, None, None, None, None
        
        if not df_bench.empty:
            role_bench = df_bench[df_bench["role"].str.strip().str.upper() == role.strip().upper()]
            
            if not role_bench.empty:
                b_impact = role_bench["impact_score"].median()
                b_kda    = role_bench["kda"].median()
                b_cs     = role_bench["cs_per_min"].median() if "cs_per_min" in role_bench.columns else None
                b_dpm    = role_bench["damage_per_min"].median() if "damage_per_min" in role_bench.columns else None
                b_vision = role_bench["vision_per_min"].median()

        def format_metric_gap(val, bench):
            if bench is None or bench == 0:
                return f'<div class="cp-sb-val">{val:.2f}</div><div class="cp-sb-gap">---</div>'
            gap_pct = ((val / bench) - 1) * 100
            cls = "gap-positive" if gap_pct >= 0 else "gap-negative"
            sign = "+" if gap_pct >= 0 else ""
            return f'<div class="cp-sb-val">{val:.2f}</div><div class="cp-sb-gap {cls}">{sign}{gap_pct:.1f}%</div>'

        row_style = 'style="background: rgba(239, 68, 68, 0.05);"' if b_impact and p_impact < b_impact else ""
        
        sb_html += f'<tr class="cp-sb-row" {row_style}>'
        sb_html += f'<td class="cp-sb-cell"><div class="cp-sb-player"><div class="cp-sb-role">{role}</div><div class="cp-sb-name">{name}</div></div></td>'
        sb_html += f'<td class="cp-sb-cell cp-sb-metric" style="text-align:center;">{format_metric_gap(p_kda, b_kda)}</td>'
        sb_html += f'<td class="cp-sb-cell cp-sb-metric" style="text-align:center;">{format_metric_gap(p_impact, b_impact)}</td>'
        sb_html += f'<td class="cp-sb-cell cp-sb-metric" style="text-align:center;">{format_metric_gap(p_cs, b_cs)}</td>'
        sb_html += f'<td class="cp-sb-cell cp-sb-metric" style="text-align:center;">{format_metric_gap(p_dpm, b_dpm)}</td>'
        sb_html += f'<td class="cp-sb-cell cp-sb-metric" style="text-align:center;">{format_metric_gap(p_vision, b_vision)}</td>'
        sb_html += '</tr>'
    
    sb_html += '</table>'
    st.markdown(sb_html, unsafe_allow_html=True)

def render_role_badge(role):
    icons = {"TOP": "🛡️", "JUNGLE": "🌿", "MID": "⚡", "BOT": "🏹", "SUPPORT": "💜"}
    icon = icons.get(role, "❓")
    return f"{icon} `{role}`"

def render_match_card(match_id, champions: list, result_str, duration, is_selected=False):
    # Determine result color and label
    if result_str.lower() == "win":
        res_color = "#4ADE80"
        res_label = "VICTORY"
    elif result_str.lower() == "loss":
        res_color = "#EF4444"
        res_label = "DEFEAT"
    else:
        res_color = "#A1A1AA"
        res_label = result_str.upper()

    border = "1px solid #38BDF8" if is_selected else "1px solid rgba(255, 255, 255, 0.1)"
    bg = "rgba(56, 189, 248, 0.15)" if is_selected else "rgba(15, 23, 42, 0.6)"
    
    # Obtener versión de DDragon del estado de sesión
    dd_ver = st.session_state.get("ddragon_version", "16.9.1")
    
    imgs_html = ""
    for champ in champions:
        if not champ or champ == "Unknown":
            url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/champion-icons/-1.png"
        else:
            url = f"https://ddragon.leagueoflegends.com/cdn/{dd_ver}/img/champion/{champ}.png"
        imgs_html += f'<img src="{url}" style="flex: 1; min-width: 0; width: 100%; aspect-ratio: 1/1; border-radius: 4px; border: 1px solid rgba(255,255,255,0.2); object-fit: cover;">'
        
    html = f"""
    <div id="match_card_{match_id}" style="
        border: {border};
        border-radius: 8px;
        background: {bg};
        padding: 10px;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 8px;
        cursor: pointer;
        transition: all 0.2s ease-in-out;
        height: 100%;
        text-align: center;
    ">
        <div style="display: flex; flex-direction: row; gap: 2px; justify-content: space-between; width: 100%;">
            {imgs_html}
        </div>
        <div style="display: flex; flex-direction: column;">
            <span style="color: {res_color}; font-weight: 800; font-size: 13px; letter-spacing: 0.5px;">{res_label}</span>
            <span style="color: #94A3B8; font-size: 11px;">{duration}</span>
        </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
