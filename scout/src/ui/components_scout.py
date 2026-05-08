import streamlit as st
import pandas as pd

def render_scout_header(profile_name="SCOUT PROTOCOL", role="UNKNOWN", last_sync="Desconocido"):
    st.markdown(f"""
    <div class="scout-header">
        <div class="scout-header-left">
            <div class="scout-hexbadge">S</div>
            <div>
                <div class="scout-title">SCOUT <span>PROTOCOL</span></div>
                <div class="scout-subtitle">ANÁLISIS DE RENDIMIENTO INDIVIDUAL</div>
            </div>
        </div>
        <div style="display:flex; flex-direction:column; align-items:flex-end;">
             <div class="scout-player-tag">
                <span style="font-size:16px;">👤</span> {profile_name} | {role}
            </div>
            <div style="font-size: 11px; color: #64748B; margin-top: 8px;">ÚLTIMA SINCRONIZACIÓN: {last_sync}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_kpi(label, value, delta=None):
    delta_html = ""
    if delta:
        cls = "delta-negative" if str(delta).startswith("-") else "delta-positive"
        sign = "+" if not str(delta).startswith("-") else ""
        delta_html = f'<div class="scout-kpi-delta {cls}">{sign}{delta}</div>'
    
    st.markdown(f"""
    <div class="scout-kpi">
        <div class="scout-kpi-label">{label}</div>
        <div class="scout-kpi-val">{value}</div>
        {delta_html}
    </div>
    """, unsafe_allow_html=True)

def render_alert(title, desc, severity="info", icon="🔵"):
    st.markdown(f"""
    <div class="scout-alert alert-{severity}">
        <div class="scout-alert-icon">{icon}</div>
        <div>
            <div class="scout-alert-title">{title}</div>
            <div class="scout-alert-desc">{desc}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_match_card(match_id, champion, role, result_str, duration, is_selected=False, dd_ver="16.9.1"):
    res_color = "#4ADE80" if result_str.lower() == "win" else "#F87171"
    res_label = "VICTORY" if result_str.lower() == "win" else "DEFEAT"
    border = "1px solid #A855F7" if is_selected else "1px solid rgba(255,255,255,0.07)"
    bg = "rgba(168,85,247,0.1)" if is_selected else "rgba(255,255,255,0.02)"
    
    url = f"https://ddragon.leagueoflegends.com/cdn/{dd_ver}/img/champion/{champion}.png" if champion else ""
    
    st.markdown(f"""
    <div id="match_{match_id}" style="
        border: {border}; background: {bg}; border-radius: 12px; padding: 12px;
        display: flex; gap: 12px; align-items: center; cursor: pointer; transition: all 0.2s;
    ">
        <img src="{url}" style="width: 48px; height: 48px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1);">
        <div style="flex: 1;">
            <div style="font-size: 14px; font-weight: 800; color: #FFF;">{champion}</div>
            <div style="font-size: 11px; color: #A855F7; font-weight: 700;">{role}</div>
        </div>
        <div style="text-align: right;">
            <div style="font-size: 13px; font-weight: 800; color: {res_color};">{res_label}</div>
            <div style="font-size: 11px; color: #64748B;">{duration}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_scout_scoreboard(df_player, df_bench, display_name, role):
    """Scoreboard individual estilo war room con gap vs Challenger."""
    
    if df_player.empty:
        st.caption("Sin datos de partidas.")
        return
    
    # Métricas del jugador
    p_kda    = df_player["kda"].mean() if "kda" in df_player.columns else 0
    p_impact = df_player["impact_score"].mean() if "impact_score" in df_player.columns else 0
    p_cs     = df_player["cs_per_min"].mean() if "cs_per_min" in df_player.columns else 0
    p_dpm    = df_player["damage_per_min"].mean() if "damage_per_min" in df_player.columns else 0
    p_vision = df_player["vision_per_min"].mean() if "vision_per_min" in df_player.columns else 0
    
    # Benchmarks challenger por rol
    b_kda = b_impact = b_cs = b_dpm = b_vision = None
    bench_label = "---"
    if not df_bench.empty:
        role_bench = df_bench[df_bench["role"].str.strip().str.upper() == role.strip().upper()]
        if not role_bench.empty:
            b_kda    = role_bench["kda"].median() if "kda" in role_bench.columns else None
            b_impact = role_bench["impact_score"].median() if "impact_score" in role_bench.columns else None
            b_cs     = role_bench["cs_per_min"].median() if "cs_per_min" in role_bench.columns else None
            b_dpm    = role_bench["damage_per_min"].median() if "damage_per_min" in role_bench.columns else None
            b_vision = role_bench["vision_per_min"].median() if "vision_per_min" in role_bench.columns else None
            n = int(role_bench["sample_size"].sum()) if "sample_size" in role_bench.columns else len(role_bench)
            bench_label = f"Challenger median · {n} partidas"
    
    def fmt_gap(val, bench, decimals=2):
        fmt = f"{val:.{decimals}f}"
        if bench is None or bench == 0:
            return f'<div class="scout-sb-val">{fmt}</div><div class="scout-sb-gap" style="color:#475569;">vs Chall: ---</div>'
        gap_pct = ((val / bench) - 1) * 100
        cls = "gap-positive" if gap_pct >= 0 else "gap-negative"
        sign = "+" if gap_pct >= 0 else ""
        bench_fmt = f"{bench:.{decimals}f}"
        return f'<div class="scout-sb-val">{fmt}</div><div class="scout-sb-gap {cls}">{sign}{gap_pct:.1f}% vs {bench_fmt}</div>'
    
    row_bg = 'style="background:rgba(239,68,68,0.05);"' if b_impact and b_impact > 0 and p_impact < b_impact else ""
    
    html = f'<div style="font-size:11px;color:#475569;margin-bottom:10px;font-weight:700;letter-spacing:1px;">CHALLENGER STANDARD · {bench_label.upper()}</div>'
    html += '<table class="scout-table" style="width:100%;">'
    html += '<thead><tr>'
    html += '<th class="scout-th" style="text-align:left;">JUGADOR</th>'
    html += '<th class="scout-th" style="text-align:center;">KDA</th>'
    html += '<th class="scout-th" style="text-align:center;">IMPACTO</th>'
    html += '<th class="scout-th" style="text-align:center;">CS/MIN</th>'
    html += '<th class="scout-th" style="text-align:center;">DPM</th>'
    html += '<th class="scout-th" style="text-align:center;">VS/MIN</th>'
    html += '</tr></thead><tbody>'
    html += f'<tr class="scout-tr" {row_bg}>'
    html += f'<td class="scout-td"><div class="scout-champ-name">{display_name}</div><div class="scout-champ-role">{role}</div></td>'
    html += f'<td class="scout-td" style="text-align:center;">{fmt_gap(p_kda, b_kda)}</td>'
    html += f'<td class="scout-td" style="text-align:center;">{fmt_gap(p_impact, b_impact)}</td>'
    html += f'<td class="scout-td" style="text-align:center;">{fmt_gap(p_cs, b_cs)}</td>'
    html += f'<td class="scout-td" style="text-align:center;">{fmt_gap(p_dpm, b_dpm, 0)}</td>'
    html += f'<td class="scout-td" style="text-align:center;">{fmt_gap(p_vision, b_vision)}</td>'
    html += '</tr></tbody></table>'
    
    st.markdown(html, unsafe_allow_html=True)


def render_champion_pool_table(df_pool):
    html = '<table class="scout-table"><thead><tr>'
    html += '<th class="scout-th" style="text-align:left;">CAMPEÓN</th>'
    html += '<th class="scout-th" style="text-align:center;">GAMES</th>'
    html += '<th class="scout-th" style="text-align:center;">WINRATE</th>'
    html += '<th class="scout-th" style="text-align:center;">IMPACTO</th>'
    html += '<th class="scout-th" style="text-align:center;">KDA</th>'
    html += '<th class="scout-th" style="text-align:center;">CS/MIN</th>'
    html += '<th class="scout-th" style="text-align:center;">CONSISTENCIA</th>'
    html += '</tr></thead><tbody>'
    
    for _, row in df_pool.iterrows():
        wr = row["win_rate"]
        wr_cls = "scout-wr-good" if wr >= 0.5 else "scout-wr-bad"
        wr_str = f"{wr*100:.0f}%"
        
        html += '<tr class="scout-tr">'
        html += f'<td class="scout-td"><div class="scout-champ-name">{row["champion"]}</div><div class="scout-champ-role">{row.get("role","")}</div></td>'
        html += f'<td class="scout-td scout-val" style="text-align:center;">{int(row["n_games"])}</td>'
        html += f'<td class="scout-td scout-val {wr_cls}" style="text-align:center;">{wr_str}</td>'
        html += f'<td class="scout-td scout-val" style="text-align:center;">{row["avg_impact"]:.2f}</td>'
        html += f'<td class="scout-td scout-val" style="text-align:center;">{row["avg_kda"]:.2f}</td>'
        html += f'<td class="scout-td scout-val" style="text-align:center;">{row["avg_cs_min"]:.1f}</td>'
        html += f'<td class="scout-td scout-val" style="text-align:center;">{row.get("consistency", 0):.2f}</td>'
        html += '</tr>'
        
    html += '</tbody></table>'
    st.markdown(html, unsafe_allow_html=True)
