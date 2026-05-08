"""
app_scout.py — Dashboard de Streamlit para el Scout Protocol (Modo Individual).
"""

import os
import sys
import logging
from pathlib import Path
import yaml
import pandas as pd
from datetime import datetime, timedelta, timezone as tz
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from supabase import create_client
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

from src.config_scout import load_profile, SEASONS, CURRENT_SEASON
from src.data_loader_scout import ScoutMatchClient
from src.profile_manager import (
    validate_riot_id,
    create_profile_in_db,
    sync_profile_data,
    get_all_profiles,
    update_last_synced,
)
from src.analysis_scout import (
    compute_player_summary,
    analyze_performance_trend,
    get_scout_alerts,
    compute_peer_benchmarks,
)
from src.jungle_metrics import compute_jungle_metrics, compute_pathing_efficiency
from src.error_patterns import detect_error_patterns
from src.visualization_scout import (
    plot_death_heatmap,
    plot_gold_diff_timeline_individual,
    plot_impact_score_evolution,
    plot_pillar_radar_vs_challenger,
    plot_jungle_pathing,
    create_synergy_heatmap,
    plot_match_momentum,
)
from src.ui.styles_scout import inject_css
from src.ui.components_scout import (
    render_scout_header,
    render_kpi,
    render_alert,
    render_match_card,
    render_champion_pool_table,
)

st.set_page_config(page_title="Scout Protocol", layout="wide", initial_sidebar_state="expanded")
inject_css()

# ──────────────────────────────────────────────────────────────────
# INIT SUPABASE & CACHE
# ──────────────────────────────────────────────────────────────────
@st.cache_resource
def init_supabase():
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        return create_client(url, key)
    return None

supabase = init_supabase()

class ProfileWrapper:
    """Wrapper para convertir dict de profile en objeto con atributos."""
    def __init__(self, profile_dict):
        for key, value in profile_dict.items():
            setattr(self, key, value)

def load_profile_and_data(riot_id: str, season_start: str = None):
    """
    Carga profile + datos desde Supabase (sin cachear con decorator).
    Retorna profile como dict serializable para session_state.
    """
    if not supabase:
        return None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    
    # 1. Load profile from DB
    prof_res = supabase.table("scout_profiles").select("*").eq("riot_id", riot_id).execute()
    if not prof_res.data:
        return None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    
    profile_data = prof_res.data[0]
    
    # 2. Create profile dict (serializable)
    game_name = profile_data["riot_id"].split("#")[0]
    historical = profile_data.get("historical_names", []) or []
    all_names = [game_name] + historical
    
    profile = {
        "id": profile_data["id"],
        "riot_id": profile_data["riot_id"],
        "game_name": game_name,
        "tag_line": profile_data["riot_id"].split("#")[1] if "#" in profile_data["riot_id"] else "",
        "display_name": profile_data["display_name"],
        "primary_role": profile_data["primary_role"],
        "platform": profile_data.get("platform", "la2"),
        "region": "americas",  # Hardcoded for LA2
        "last_synced": profile_data.get("last_synced", "Nunca"),
        "all_game_names": all_names,  # Para filtrado en UI
    }
    
    # 3. Load player data
    client = ScoutMatchClient("", profile["platform"], profile["region"])
    client.supabase = supabase
    
    # Create temp object for client (not serialized)
    class TempProfile:
        def __init__(self, p, profile_data):
            self.game_name = p["game_name"]
            self.tag_line = p["tag_line"]
            self.platform = p["platform"]
            self.region = p["region"]
            self.riot_id = p["riot_id"]
            # Soportar nombres históricos - cargar desde scout_profiles
            historical = profile_data.get("historical_names", []) or []
            self.all_game_names = [p["game_name"]] + historical
    
    temp_profile = TempProfile(profile, profile_data)
    df_p, df_t, df_e = client.load_player_from_supabase(temp_profile, season_start=season_start)
    
    # 4. Load champion pool
    df_pool = pd.DataFrame()
    pool_res = supabase.table("scout_champion_pool").select("*").eq("profile_id", profile["id"]).execute()
    if pool_res.data:
        df_pool = pd.DataFrame(pool_res.data)
    
    # 5. Load benchmarks con paginación completa (igual que war room)
    df_bench = pd.DataFrame()
    try:
        all_bench_rows = []
        off = 0
        lim = 1000
        while True:
            r = supabase.table("benchmarks_summary").select("*").range(off, off + lim - 1).execute()
            if not r or not r.data:
                break
            all_bench_rows.extend(r.data)
            if len(r.data) < lim:
                break
            off += lim
        if all_bench_rows:
            df_bench = pd.DataFrame(all_bench_rows)
    except Exception as _e:
        logger.warning(f"Error cargando benchmarks: {_e}")
    
    return profile, df_p, df_t, df_e, df_pool, df_bench

# ──────────────────────────────────────────────────────────────────
# SIDEBAR - MULTI-USUARIO
# ──────────────────────────────────────────────────────────────────
st.sidebar.markdown('### 🎯 SCOUT PROTOCOL')

if not supabase:
    st.sidebar.error("⚠️ Supabase no configurado. Verifica variables de entorno.")
    st.stop()

# Get all profiles from DB
all_profiles = get_all_profiles(supabase)

if all_profiles:
    profile_options = {p["riot_id"]: f"{p['display_name']} ({p['primary_role']})" for p in all_profiles}
    
    # Initialize session state for selected profile
    if "selected_riot_id" not in st.session_state:
        st.session_state.selected_riot_id = all_profiles[0]["riot_id"]
    
    selected_riot_id = st.sidebar.selectbox(
        "Seleccionar Jugador",
        options=list(profile_options.keys()),
        format_func=lambda x: profile_options[x],
        key="profile_selector"
    )
    
    st.session_state.selected_riot_id = selected_riot_id
else:
    st.sidebar.info("No hay perfiles. Agrega uno abajo.")
    selected_riot_id = None

# Season selector
st.sidebar.markdown("---")
st.sidebar.markdown("### 📅 Rango de Datos")

season_options = list(SEASONS.keys())
selected_season = st.sidebar.selectbox(
    "Season",
    season_options,
    index=season_options.index(CURRENT_SEASON),
    key="season_selector",
)

# Calculate season_start from selection
_season_dt = SEASONS[selected_season]
if _season_dt is not None:
    season_start_iso = _season_dt.isoformat()
elif selected_season == "Últimos 90 días":
    season_start_iso = (datetime.now(tz=tz.utc) - timedelta(days=90)).isoformat()
elif selected_season == "Últimos 30 días":
    season_start_iso = (datetime.now(tz=tz.utc) - timedelta(days=30)).isoformat()
else:
    season_start_iso = None

# Add new profile section
st.sidebar.markdown("---")
st.sidebar.markdown("### ➕ Agregar Nuevo Jugador")

with st.sidebar.form("add_profile_form"):
    new_riot_id = st.text_input(
        "Riot ID",
        placeholder="GameName#TAG",
        help="Ejemplo: AEVI ray#ray"
    )
    
    new_role = st.selectbox(
        "Rol Principal",
        ["JUNGLE", "TOP", "MID", "ADC", "SUPPORT"],
        index=0
    )
    
    submit_button = st.form_submit_button("🚀 Agregar y Sincronizar")
    
    if submit_button:
        if not new_riot_id or "#" not in new_riot_id:
            st.sidebar.error("❌ Riot ID inválido. Formato: GameName#TAG")
        else:
            with st.spinner(f"Validando {new_riot_id}..."):
                # Validate with Riot API
                riot_account = validate_riot_id(new_riot_id, region="americas")
                
                if not riot_account:
                    st.sidebar.error(f"❌ Riot ID no encontrado: {new_riot_id}")
                else:
                    # Create profile in DB
                    profile_created = create_profile_in_db(
                        supabase,
                        riot_account,
                        primary_role=new_role,
                        platform="la2"
                    )
                    
                    if profile_created:
                        st.sidebar.success(f"✅ Perfil creado: {riot_account.riot_id}")
                        
                        # Sync data
                        with st.spinner(f"Sincronizando datos de {riot_account.riot_id}... (esto puede tomar 1-2 min)"):
                            sync_success = sync_profile_data(
                                riot_account.riot_id,
                                platform="la2",
                                region="americas"
                            )
                            
                            if sync_success:
                                # Update last_synced
                                update_last_synced(supabase, riot_account.riot_id)
                                st.sidebar.success(f"✅ Datos sincronizados exitosamente!")
                                st.sidebar.info("🔄 Recarga la página para ver el nuevo perfil.")
                                # Clear cache to reload profiles
                                st.cache_data.clear()
                            else:
                                st.sidebar.error("❌ Error al sincronizar datos. Revisa los logs.")
                    else:
                        st.sidebar.error("❌ Error al crear perfil en la base de datos.")

# ──────────────────────────────────────────────────────────────────
# SESSION STATE + LAZY LOADING
# ──────────────────────────────────────────────────────────────────

# Initialize session state
if "current_riot_id" not in st.session_state:
    st.session_state.current_riot_id = None
if "current_season" not in st.session_state:
    st.session_state.current_season = None
if "cached_data" not in st.session_state:
    st.session_state.cached_data = None

# Check if user or season changed
if selected_riot_id:
    user_changed = selected_riot_id != st.session_state.current_riot_id
    season_changed = selected_season != st.session_state.current_season
    
    if user_changed or season_changed or st.session_state.cached_data is None:
        label = selected_season if not season_changed else selected_season
        with st.spinner(f"⏳ Cargando datos de {selected_riot_id} ({label})..."):
            st.session_state.cached_data = load_profile_and_data(selected_riot_id, season_start=season_start_iso)
            st.session_state.current_riot_id = selected_riot_id
            st.session_state.current_season = selected_season
    
    # Use cached data
    profile, df_p, df_t, df_e, df_pool, df_bench = st.session_state.cached_data
    
    if not profile or df_p.empty:
        st.error(f"No hay datos para {selected_riot_id}. Usa el botón '➕ Agregar Nuevo Jugador' en el sidebar para sincronizar.")
        st.stop()
else:
    st.info("👈 Agrega un jugador en el sidebar para comenzar.")
    st.stop()

# Optional: Add manual refresh button in sidebar
st.sidebar.markdown("---")
if st.sidebar.button("🔄 Re-sync Data", help="Recarga los datos desde Supabase"):
    with st.spinner("Sincronizando datos..."):
        st.session_state.cached_data = load_profile_and_data(selected_riot_id, season_start=season_start_iso)
        # Update last_synced in DB
        update_last_synced(supabase, selected_riot_id)
    st.success("✅ Datos actualizados!")
    st.rerun()

# DEBUG: Mostrar info de filtrado
with st.sidebar.expander("🔍 Debug Info"):
    st.write(f"**Rango seleccionado:** {selected_season}")
    st.write(f"**Fecha inicio (ISO):** {season_start_iso}")
    st.write(f"**Riot ID:** {profile['riot_id']}")
    st.write(f"**Game names buscados:** {profile.get('all_game_names', [profile['game_name']])}")
    st.write(f"**Partidas cargadas (df_p):** {len(df_p)}")
    st.write(f"**Unique match_ids:** {df_p['match_id'].nunique() if not df_p.empty else 0}")
    if not df_p.empty and "game_name" in df_p.columns:
        st.write(f"**Game names en datos:** {df_p['game_name'].unique().tolist()}")

# Filtrar jugador usando todos los nombres (actual + históricos)
all_names = profile.get("all_game_names", [profile["game_name"]])
all_names_lower = [n.lower() for n in all_names]
player_mask = df_p["game_name"].str.lower().isin(all_names_lower)
df_player = df_p[player_mask].copy()

# Sort by timestamp to have correct timeline
if "match_id" in df_player.columns:
    df_player = df_player.sort_values("match_id", ascending=False).reset_index(drop=True)

# Compute synergy from events and merge into df_player
if not df_e.empty and not df_p.empty:
    from src.features_scout import compute_player_synergy_from_events
    df_syn = compute_player_synergy_from_events(df_e, df_p, profile["game_name"])
    if not df_syn.empty:
        df_player = df_player.merge(df_syn, on="match_id", how="left")
        # Fill NaN synergy values with 0
        syn_cols = [c for c in df_syn.columns if c.startswith("synergy_")]
        for c in syn_cols:
            if c in df_player.columns:
                df_player[c] = df_player[c].fillna(0)

render_scout_header(profile["display_name"], profile["primary_role"], profile.get("last_synced", "Desconocido"))

# ──────────────────────────────────────────────────────────────────
# COMPUTE ADVANCED METRICS
# ──────────────────────────────────────────────────────────────────
with st.spinner("Calculando métricas avanzadas..."):
    # Create profile wrapper for functions that expect objects
    profile_obj = ProfileWrapper(profile)
    
    # Jungle metrics (si es jungle)
    if profile["primary_role"] == "JUNGLE" and not df_e.empty:
        df_player_with_jungle = compute_jungle_metrics(df_player, df_e, df_t, profile_obj)
        
        # Validate merge succeeded
        if "gank_success_rate" in df_player_with_jungle.columns:
            df_player = df_player_with_jungle
            df_player = compute_pathing_efficiency(df_t, df_player, profile_obj)
        else:
            st.warning("⚠️ Jungle metrics failed to compute. Using base metrics.")
    
    # Error patterns
    error_patterns = detect_error_patterns(df_player, df_e, df_t, df_bench, profile_obj)

# ──────────────────────────────────────────────────────────────────
# TABS
# ──────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🎯 SCOUT HUB",
    "🗺️ ANÁLISIS ESPACIAL",
    "📈 EVOLUCIÓN",
    "🏆 CHAMPION POOL",
    "🎮 PARTIDAS",
    "🧠 PATRONES",
])

with tab1:
    summary = compute_player_summary(df_player)
    
    # ── ALERTAS CRÍTICAS (P0) ────────────────────────────────────────
    if error_patterns:
        critical_patterns = [p for p in error_patterns if p["severity"] == "critical"]
        if critical_patterns:
            st.markdown('<div class="scout-section-label" style="margin-top: 10px;">🚨 ALERTAS CRÍTICAS (P0)</div>', unsafe_allow_html=True)
            for pattern in critical_patterns[:3]:  # Top 3
                render_alert(
                    pattern["title"],
                    pattern['description'],
                    pattern["severity"],
                    "⚠️"
                )
    
    # ── Calcular deltas vs Challenger por rol ────────────────────────
    _chall_impact = _chall_kc = _chall_cs = _chall_dpm = _chall_vision = None
    _rb = pd.DataFrame()

    def _delta(player_val, bench_col):
        if _rb.empty or bench_col not in _rb.columns:
            return None
        bv = _rb[bench_col].median()
        if bv and bv > 0:
            pct = (player_val / bv - 1) * 100
            sign = "" if pct >= 0 else ""
            return f"{sign}{pct:.1f}% vs Chall"
        return None

    if not df_bench.empty and not df_player.empty:
        _role = df_player["role"].mode().iloc[0] if "role" in df_player.columns else profile["primary_role"]
        _rb = df_bench[df_bench["role"].str.strip().str.upper() == _role.strip().upper()]
        _chall_impact  = _delta(summary.get("avg_impact_score", 0),      "impact_score")
        _chall_kc      = _delta(summary.get("avg_kill_conversion", 0),    "kill_conversion")
        _chall_cs      = _delta(df_player["cs_per_min"].mean() if "cs_per_min" in df_player.columns else 0, "cs_per_min")
        _chall_dpm     = _delta(df_player["damage_per_min"].mean() if "damage_per_min" in df_player.columns else 0, "damage_per_min")
        _chall_vision  = _delta(df_player["vision_per_min"].mean() if "vision_per_min" in df_player.columns else 0, "vision_per_min")

    # ── KPIs Globales con comparativa Challenger ──────────────────────
    st.markdown('<div class="scout-section-label" style="margin-top: 30px;">📊 PILARES DE RENDIMIENTO</div>', unsafe_allow_html=True)
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1: render_kpi("WIN RATE",       f"{summary.get('win_rate', 0)*100:.1f}%",          f"{summary.get('n_games',0)} games")
    with col2: render_kpi("IMPACT SCORE",   f"{summary.get('avg_impact_score', 0):.2f}",       _chall_impact)
    with col3: render_kpi("KILL CONV.",     f"{summary.get('avg_kill_conversion', 0)*100:.1f}%", _chall_kc)
    with col4: render_kpi("CS / MIN",       f"{df_player['cs_per_min'].mean():.1f}" if not df_player.empty and 'cs_per_min' in df_player.columns else "—", _chall_cs)
    with col5: render_kpi("DPM",            f"{df_player['damage_per_min'].mean():.0f}" if not df_player.empty and 'damage_per_min' in df_player.columns else "—", _chall_dpm)
    
    # Jungle-specific KPIs
    if profile["primary_role"] == "JUNGLE":
        st.markdown('<div class="scout-section-label" style="margin-top: 20px;">🌲 MÉTRICAS DE JUNGLE</div>', unsafe_allow_html=True)
        jc1, jc2, jc3, jc4 = st.columns(4)
        # Deltas jungle usando columnas disponibles en benchmarks_summary
        _jd_kc     = _delta(summary.get("avg_kill_conversion", 0), "kill_conversion")
        _jd_kp     = _delta(df_player["kill_participation"].mean() if "kill_participation" in df_player.columns else 0, "kill_participation")
        _jd_map    = _delta(df_player["pilar_map_pressure"].mean()  if "pilar_map_pressure"  in df_player.columns else 0, "pilar_map_pressure")
        _jd_combat = _delta(df_player["pilar_combat_efficiency"].mean() if "pilar_combat_efficiency" in df_player.columns else 0, "pilar_combat_efficiency")
        with jc1: render_kpi("KILL CONV.",    f"{summary.get('avg_kill_conversion', 0)*100:.1f}%",                                                       _jd_kc)
        with jc2: render_kpi("KILL PART.",    f"{df_player['kill_participation'].mean()*100:.1f}%" if "kill_participation" in df_player.columns else "—", _jd_kp)
        with jc3: render_kpi("MAP PRESSURE",  f"{df_player['pilar_map_pressure'].mean():.2f}"     if "pilar_map_pressure"  in df_player.columns else "—", _jd_map)
        with jc4: render_kpi("COMBAT EFF.",   f"{df_player['pilar_combat_efficiency'].mean():.2f}" if "pilar_combat_efficiency" in df_player.columns else "—", _jd_combat)
    
    # Radar Chart + Synergy Matrix
    if not df_bench.empty and not df_player.empty:
        champ = df_player["champion"].mode().iloc[0]
        role = df_player["role"].mode().iloc[0]
        st.markdown('<div class="scout-section-label" style="margin-top: 30px;">🎯 RADAR VS CHALLENGER & 🔗 SINERGIA</div>', unsafe_allow_html=True)
        col_radar, col_syn = st.columns([3, 2])
        with col_radar:
            radar_fig = plot_pillar_radar_vs_challenger(df_player, df_bench, champ, role, profile)
            st.plotly_chart(radar_fig, use_container_width=True)
        with col_syn:
            syn_cols = [c for c in df_player.columns if c.startswith("synergy_")]
            if syn_cols:
                syn_vals = {c: float(df_player[c].mean()) for c in syn_cols}
                b_syn = {c: {"p50": float(df_bench[c].median())} if not df_bench.empty and c in df_bench.columns else {"p50": 0.5} for c in syn_cols}
                st.plotly_chart(create_synergy_heatmap(syn_vals, b_syn), use_container_width=True, key="syn_main")
            else:
                st.caption("Sin datos de sinergia.")
    
    # Error Patterns Summary
    if error_patterns:
        st.markdown('<div class="scout-section-label" style="margin-top: 30px;">🔍 PATRONES DE ERROR DETECTADOS</div>', unsafe_allow_html=True)
        for pattern in error_patterns[:5]:  # Top 5
            severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(pattern["severity"], "⚪")
            st.markdown(f"""
            <div style="background: rgba(15,23,42,0.6); border-left: 3px solid {'#EF4444' if pattern['severity'] == 'critical' else '#F59E0B' if pattern['severity'] == 'high' else '#FCD34D'}; padding: 12px; border-radius: 8px; margin-bottom: 10px;">
                <div style="font-weight: 700; font-size: 14px; color: #E2E8F0;">{severity_icon} {pattern['title']}</div>
                <div style="font-size: 12px; color: #94A3B8; margin-top: 4px;">{pattern['description']}</div>
            </div>
            """, unsafe_allow_html=True)
        
    
with tab2:
    st.markdown('<div class="scout-section-label">🗺️ HEATMAP DE MUERTES</div>', unsafe_allow_html=True)
    
    # Load death events
    client = ScoutMatchClient("", profile["platform"], profile["region"])
    client.supabase = supabase
    profile_obj = ProfileWrapper(profile)
    df_deaths = client.load_death_events_optimized(profile_obj, limit=200, season_start=season_start_iso)
    
    if not df_deaths.empty:
        death_heatmap = plot_death_heatmap(df_deaths, profile_obj)
        st.plotly_chart(death_heatmap, use_container_width=True)
        
        st.markdown(f"""
        <div style="background: rgba(15,23,42,0.6); padding: 12px; border-radius: 8px; margin-top: 10px;">
            <div style="font-size: 12px; color: #94A3B8;">
                📍 **Zonas rojas** = Alta mortalidad. Evita overextender en esas áreas sin vision.<br>
                📍 **Zonas verdes** = Zonas seguras. Usa estas áreas para farming/recalls.
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("No hay datos de posición de muertes disponibles.")
    
    # Jungle Pathing (si hay datos)
    if profile["primary_role"] == "JUNGLE" and not df_t.empty:
        st.markdown('<div class="scout-section-label" style="margin-top: 30px;">🌲 JUNGLE PATHING (Última Partida)</div>', unsafe_allow_html=True)
        if not df_player.empty:
            last_match = df_player.iloc[0]["match_id"]
            profile_obj = ProfileWrapper(profile)
            pathing_fig = plot_jungle_pathing(df_t, last_match, profile_obj)
            st.plotly_chart(pathing_fig, use_container_width=True)

with tab3:
    trend = analyze_performance_trend(df_player)
    
    st.markdown(f"""
    <div class="scout-glass">
        <div style="font-size: 14px; font-weight: 700; color: #94A3B8; margin-bottom: 8px;">TENDENCIA RECIENTE</div>
        <div style="font-size: 18px; color: #FFF;">{trend['insight']}</div>
    </div>
    """, unsafe_allow_html=True)
    
    # Impact Score Evolution
    if not df_player.empty and "impact_score" in df_player.columns:
        profile_obj = ProfileWrapper(profile)
        evolution_fig = plot_impact_score_evolution(df_player, profile_obj, df_bench)
        st.plotly_chart(evolution_fig, use_container_width=True)

with tab4:
    if not df_pool.empty:
        render_champion_pool_table(df_pool)
    else:
        st.info("No hay datos suficientes de champion pool.")

with tab5:
    if df_player.empty:
        st.info("No hay partidas disponibles.")
    else:
        # ── Paginación de partidas estilo war room ───────────────────
        all_match_ids = df_player["match_id"].tolist()
        if "scout_history_page" not in st.session_state:
            st.session_state.scout_history_page = 0
        if "scout_selected_match" not in st.session_state:
            st.session_state.scout_selected_match = None

        ITEMS = 10
        page = st.session_state.scout_history_page
        max_page = max(0, (len(all_match_ids) - 1) // ITEMS)
        if page > max_page:
            st.session_state.scout_history_page = max_page
            page = max_page
        display_ids = all_match_ids[page * ITEMS : page * ITEMS + ITEMS]
        sel_mid = st.session_state.scout_selected_match

        dd_ver = st.session_state.get("ddragon_version", "15.9.1")

        import streamlit.components.v1 as components
        components.html("""
        <script>
        if (!window.parent.window.matchCardListenerAttached) {
            window.parent.document.addEventListener('click', function(e) {
                let card = e.target.closest('div[id^="match_card_"]');
                if (card) {
                    let match_id = card.id.replace('match_card_', '');
                    let btns = window.parent.document.querySelectorAll('[data-testid="stButton"] button');
                    for (let b of btns) {
                        if (b.innerText.includes(match_id + '_HIDDEN')) {
                            b.click();
                            break;
                        }
                    }
                }
            });
            window.parent.window.matchCardListenerAttached = true;
        }
        </script>
        """, height=0)

        st.markdown("""<style>
        div.element-container:has(div[id^="match_card_"]) + div.element-container {
            position: absolute !important;
            opacity: 0 !important;
            pointer-events: none !important;
            height: 0px !important;
            overflow: hidden !important;
        }
        </style>""", unsafe_allow_html=True)

        c_left, c_cards, c_right = st.columns([0.5, 9, 0.5])
        with c_left:
            st.markdown("<br><br>", unsafe_allow_html=True)
            if st.button("◀", disabled=(page == 0), key="scout_prev"):
                st.session_state.scout_history_page -= 1
                st.rerun()
        with c_cards:
            card_cols = st.columns(ITEMS)
            for i_m, mid in enumerate(display_ids):
                mrow = df_player[df_player["match_id"] == mid].iloc[0]
                result_bool = bool(mrow.get("result", False))
                res_color  = "#4ADE80" if result_bool else "#EF4444"
                res_label  = "VICTORY" if result_bool else "DEFEAT"
                champ      = mrow.get("champion", "Unknown")
                dur_raw    = mrow.get("duration_minutes", 0)
                dur_str    = f"{int(dur_raw)}:{int((dur_raw % 1)*60):02d}"
                is_sel     = (mid == sel_mid)
                border     = "1px solid #38BDF8" if is_sel else "1px solid rgba(255,255,255,0.1)"
                bg         = "rgba(56,189,248,0.15)" if is_sel else "rgba(15,23,42,0.6)"
                champ_url  = f"https://ddragon.leagueoflegends.com/cdn/{dd_ver}/img/champion/{champ}.png"

                with card_cols[i_m]:
                    st.markdown(f"""
                    <div id="match_card_{mid}" style="
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
                            <img src="{champ_url}" style="flex: 1; min-width: 0; width: 100%; aspect-ratio: 1/1; border-radius: 4px; border: 1px solid rgba(255,255,255,0.2); object-fit: cover;">
                        </div>
                        <div style="display: flex; flex-direction: column;">
                            <span style="color: {res_color}; font-weight: 800; font-size: 13px; letter-spacing: 0.5px;">{res_label}</span>
                            <span style="color: #94A3B8; font-size: 11px;">{dur_str}</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button(f"{mid}_HIDDEN", key=f"scout_sel_{mid}"):
                        st.session_state.scout_selected_match = mid
                        st.rerun()
        with c_right:
            st.markdown("<br><br>", unsafe_allow_html=True)
            if st.button("▶", disabled=(page == max_page), key="scout_next"):
                st.session_state.scout_history_page += 1
                st.rerun()

        # ── Detalle de partida seleccionada ──────────────────────────
        if not sel_mid or sel_mid not in all_match_ids:
            st.info("👆 Selecciona una partida para ver su análisis.")
        else:
            if st.button("✕ Cerrar", key="scout_close_match"):
                st.session_state.scout_selected_match = None
                st.rerun()

            mrow = df_player[df_player["match_id"] == sel_mid].iloc[0]
            result_bool = bool(mrow.get("result", False))
            res_label   = "✅ VICTORIA" if result_bool else "❌ DERROTA"
            champ       = mrow.get("champion", "Unknown")
            player_team_id = int(mrow.get("team_id", 100))

            st.markdown(f"""
            <div style="background:rgba(168,85,247,0.07);border:1px solid rgba(168,85,247,0.2);
                        border-radius:14px;padding:18px 24px;margin:16px 0;display:flex;gap:20px;align-items:center;">
                <img src="https://ddragon.leagueoflegends.com/cdn/{dd_ver}/img/champion/{champ}.png"
                     style="width:72px;height:72px;border-radius:10px;border:2px solid #A855F7;">
                <div>
                    <div style="font-size:20px;font-weight:800;color:#FFF;">{champ} &nbsp;
                        <span style="color:{'#4ADE80' if result_bool else '#F87171'};">{res_label}</span></div>
                    <div style="font-size:12px;color:#64748B;margin-top:4px;font-family:monospace;">{sel_mid}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # KPIs de la partida
            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            b_role = mrow.get("role", profile["primary_role"])
            _rb_m = df_bench[df_bench["role"].str.strip().str.upper() == b_role.strip().upper()] if not df_bench.empty else pd.DataFrame()
            def _md(val, col):
                if _rb_m.empty or col not in _rb_m.columns: return None
                bv = _rb_m[col].median()
                if bv and bv > 0:
                    pct = (val / bv - 1) * 100
                    return f"{pct:.1f}% vs Chall"
                return None
            with mc1: render_kpi("IMPACT",     f"{mrow.get('impact_score', 0):.3f}",    _md(mrow.get('impact_score', 0), 'impact_score'))
            with mc2: render_kpi("KDA",        f"{mrow.get('kda', 0):.2f}",             _md(mrow.get('kda', 0), 'kda'))
            with mc3: render_kpi("CS/MIN",     f"{mrow.get('cs_per_min', 0):.1f}",      _md(mrow.get('cs_per_min', 0), 'cs_per_min'))
            with mc4: render_kpi("DPM",        f"{mrow.get('damage_per_min', 0):.0f}",  _md(mrow.get('damage_per_min', 0), 'damage_per_min'))
            with mc5: render_kpi("VS/MIN",     f"{mrow.get('vision_per_min', 0):.2f}",  _md(mrow.get('vision_per_min', 0), 'vision_per_min'))

            st.markdown("<br>", unsafe_allow_html=True)

            # Momentum + Synergy Heatmap
            col_mom, col_syn = st.columns([3, 2])
            match_t = df_t[df_t["match_id"] == sel_mid] if not df_t.empty else pd.DataFrame()

            with col_mom:
                st.markdown('<div class="scout-section-label">⏱️ MOMENTUM DE EQUIPO</div>', unsafe_allow_html=True)
                if not match_t.empty:
                    st.plotly_chart(plot_match_momentum(match_t, player_team_id),
                                    use_container_width=True, key=f"momentum_{sel_mid}")
                else:
                    st.caption("Sin datos de timeline.")

            with col_syn:
                st.markdown('<div class="scout-section-label">🔗 SINERGIA EN ESTA PARTIDA</div>', unsafe_allow_html=True)
                syn_cols = [c for c in df_player.columns if c.startswith("synergy_")]
                if syn_cols:
                    syn_vals = {c: float(mrow.get(c, 0)) for c in syn_cols}
                    b_syn = {c: {"p50": df_bench[c].median()} if not df_bench.empty and c in df_bench.columns else {"p50": 0.5} for c in syn_cols}
                    st.plotly_chart(create_synergy_heatmap(syn_vals, b_syn),
                                    use_container_width=True, key=f"syn_{sel_mid}")
                else:
                    st.caption("Sin datos de sinergia disponibles.")

            # Gold Diff Timeline
            st.markdown('<div class="scout-section-label" style="margin-top:20px;">💰 GOLD DIFF VS OPONENTE</div>', unsafe_allow_html=True)
            profile_obj_m = ProfileWrapper(profile)
            gold_fig = plot_gold_diff_timeline_individual(df_t, df_player, sel_mid, profile_obj_m)
            st.plotly_chart(gold_fig, use_container_width=True, key=f"gold_{sel_mid}")


with tab6:
    st.markdown('<div class="scout-section-label">🧠 PATRONES DE ERROR DETECTADOS</div>', unsafe_allow_html=True)

    if df_player.empty:
        st.info("Sin partidas para analizar.")
    else:
        if not error_patterns:
            st.success("✅ No se detectaron patrones de error recurrentes en las últimas partidas.")
        else:
            sev_order = {"critical": 0, "high": 1, "medium": 2}
            sorted_patterns = sorted(error_patterns, key=lambda p: sev_order.get(p.get("severity", "medium"), 2))

            sev_colors = {
                "critical": ("#F87171", "rgba(239,68,68,0.08)"),
                "high":     ("#FBBF24", "rgba(251,191,36,0.08)"),
                "medium":   ("#A78BFA", "rgba(168,85,247,0.08)"),
            }
            sev_icons = {"critical": "🔴", "high": "🟠", "medium": "🟡"}

            # Cards de patrones
            for pat in sorted_patterns:
                sev   = pat.get("severity", "medium")
                color, bg = sev_colors.get(sev, ("#94A3B8", "rgba(255,255,255,0.04)"))
                icon  = sev_icons.get(sev, "⚪")
                freq  = pat.get("frequency_pct", 0)
                n_occ = pat.get("frequency", 0)
                corr  = pat.get("impact_on_winrate", 0)

                st.markdown(f"""
                <div style="background:{bg};border-left:4px solid {color};border-radius:0 12px 12px 0;
                            padding:16px 20px;margin-bottom:12px;">
                    <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                        <div style="font-weight:800;font-size:15px;color:#FFF;">{icon} {pat['title']}</div>
                        <div style="display:flex;gap:12px;">
                            <span style="background:rgba(0,0,0,0.3);padding:3px 10px;border-radius:20px;
                                         font-size:11px;font-weight:700;color:{color};">
                                {freq*100:.0f}% de partidas
                            </span>
                            <span style="background:rgba(0,0,0,0.3);padding:3px 10px;border-radius:20px;
                                         font-size:11px;font-weight:700;color:#94A3B8;">
                                {n_occ}x detectado
                            </span>
                        </div>
                    </div>
                    <div style="font-size:13px;color:#94A3B8;margin-top:8px;line-height:1.5;">{pat['description']}</div>
                    {f'<div style="font-size:12px;color:#64748B;margin-top:6px;">Correlación con derrotas: <b style="color:{color};">{corr:+.2f}</b></div>' if corr else ''}
                </div>
                """, unsafe_allow_html=True)

        # ── Synergy Heatmap global (todas las partidas) ──────────────
        st.markdown('<div class="scout-section-label" style="margin-top:30px;">🔗 SINERGIA PROMEDIO (TODAS LAS PARTIDAS)</div>', unsafe_allow_html=True)
        syn_cols_g = [c for c in df_player.columns if c.startswith("synergy_")]
        if syn_cols_g:
            syn_avg = {c: float(df_player[c].mean()) for c in syn_cols_g}
            b_syn_g = {c: {"p50": df_bench[c].median()} if not df_bench.empty and c in df_bench.columns else {"p50": 0.5} for c in syn_cols_g}
            st.plotly_chart(create_synergy_heatmap(syn_avg, b_syn_g),
                            use_container_width=True, key="syn_global")
        else:
            st.caption("Sin datos de sinergia disponibles para estas partidas.")
