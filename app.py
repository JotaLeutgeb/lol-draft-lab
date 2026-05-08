"""
app.py — Challenger Protocol Dashboard
Sistema de análisis de élite para equipos competitivos de LoL.
"""
from __future__ import annotations

import os
import sys
import logging
import requests
import hashlib
import pandas as pd
import streamlit as st
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Configuración de rutas
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

from src import config
from src.features import (
    compute_gold_diff,
    compute_impact_score,
    compute_objective_control,
    compute_phase_stats,
    compute_player_metrics,
    compute_gank_deaths,
    compute_synergy_matrix,
    compute_kd_density,
)
from src.analysis import (
    analyze_compositions,
    analyze_team_tempo,
    analyze_vision_control,
    compute_player_impact_summary,
    analyze_objective_performance,
    filter_team_players,
    filter_to_team_matches,
    get_team_match_ids,
    get_war_room_alerts,
    identify_loss_phase,
    summarize_match,
    compute_synergy_matrix_display,
)
from src.patterns import PatternDetector
from src.data_loader import MatchV5Client
from src.benchmarks import BenchmarkManager
import importlib
import src.draft_engine
importlib.reload(src.draft_engine)
from src.draft_engine import DraftEngine
import src.ui.draft_tab
importlib.reload(src.ui.draft_tab)
from src.ui.draft_tab import render_draft_tab
from src.visualization import (
    plot_ghost_radar,
    plot_match_momentum,
    plot_death_map,
    plot_position_heatmap,
    plot_lane_dominance,
    plot_individual_timeline,
    plot_player_gold_diff,
    create_synergy_heatmap,
)
from src.ui.styles import inject_css
from src.ui.components import (
    render_alert_card,
    render_metric_card,
    render_insight_card,
    render_loss_route,
    render_drill_item,
    render_role_badge,
    render_kpi_card,
    render_war_room_header,
    render_scoreboard,
    render_match_card,
)
from src.ui.charts import (
    build_gold_timeline_chart,
    build_objectives_chart,
    build_radar_chart,
    build_synergy_heatmap_chart,
    build_density_heatmap,
    build_phase_winrate_chart,
)

# ─────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Challenger Protocol",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)
logging.basicConfig(level=logging.WARNING)

# Inyectar estilos premium
inject_css()

# Carga de la última versión de DDragon
if "ddragon_version" not in st.session_state:
    st.session_state.ddragon_version = config.get_latest_ddragon_version()
dd_ver = st.session_state.ddragon_version

# ─────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────
def _has_api_key() -> bool:
    key = os.environ.get("RIOT_API_KEY", "").strip()
    return bool(key) and not key.startswith("RGAPI-xxx")

def get_current_patch() -> str:
    try:
        r = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=5)
        if r.status_code == 200:
            return ".".join(r.json()[0].split(".")[:2])
    except Exception:
        pass
    return "26.8"

# ─────────────────────────────────────────────────────────────────────
# CACHED DATA LOADERS
# ─────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner="Cargando historial desde Riot API...")
def load_api_data(count: int, queues: list[int] | None, team_hash: str):
    loader = MatchV5Client(api_key=os.environ.get("RIOT_API_KEY", "").strip())
    return loader.load_team_matches(count_per_player=count, queues=queues)

@st.cache_data(ttl=300, show_spinner="Cargando partidas del equipo desde Supabase...")
def load_supabase_data():
    """Carga df_p, df_t, df_e desde las tablas team_* de Supabase."""
    loader = MatchV5Client(api_key="")
    return loader.load_team_matches_from_supabase()

@st.cache_data(ttl=3600, show_spinner="Sincronizando benchmarks...")
def load_benchmarks(region: str, patch: str):
    loader = MatchV5Client(os.environ.get("RIOT_API_KEY", ""))
    return loader.get_benchmarks_from_supabase(region, patch)

@st.cache_resource(show_spinner="Cargando base de drafts pro...")
def load_draft_engine_v2() -> DraftEngine:
    engine = DraftEngine()
    engine.load()
    return engine

@st.cache_data(ttl=3600, show_spinner="Buscando partida por ID...")
def load_single_match(match_id: str):
    loader = MatchV5Client(api_key=os.environ.get("RIOT_API_KEY", "").strip())
    return loader.load_single_match_by_id(match_id)

@st.cache_data(show_spinner="Calculando features...")
def compute_all_features(cache_key: str, df_p, df_t, df_e, df_bench=None, filter_team=True):
    """
    Pipeline unificado de extracción de métricas.

    Normalización de impact_score: Min-Max within-match sobre los 10 jugadores,
    idéntico al ETL de Challengers. Escala 0-1 en ambas pipelines → % comparables.
    df_bench se conserva en la firma para backward-compat pero NO se pasa a
    compute_impact_score (evita la escala 0-2 que rompe el Challenger Gap %).
    """
    if df_p.empty:
        return {}

    from src.config import TEAM_PLAYER_DISPLAY_MAP
    df_p = df_p.copy()
    df_p["game_name"] = df_p["game_name"].str.lower().map(TEAM_PLAYER_DISPLAY_MAP).fillna(df_p["game_name"])

    df_p_full = df_p.copy()

    if filter_team:
        df_p = filter_team_players(df_p)
        if df_p.empty:
            return {"no_team_matches": True}


    team_match_ids = set(df_p["match_id"].unique())

    # Pool de 10 jugadores de las partidas del equipo (mismo contexto que Challenger ETL)
    df_p_pool = df_p_full[df_p_full["match_id"].isin(team_match_ids)].copy()

    df_objectives  = compute_objective_control(df_e) if not df_e.empty else pd.DataFrame()
    df_gold_diff   = compute_gold_diff(df_t, df_p_full) if not df_t.empty else pd.DataFrame()
    df_phase_stats = compute_phase_stats(df_t) if not df_t.empty else pd.DataFrame()

    bm = BenchmarkManager("")

    # ── Sinergia SKP sobre los 10 jugadores ──────────────────────────────────
    synergy_results = []
    for match_id in team_match_ids:
        match_p_full = df_p_pool[df_p_pool["match_id"] == match_id]
        match_e      = df_e[df_e["match_id"] == match_id]
        if not match_e.empty and not match_p_full.empty:
            syn         = compute_synergy_matrix(match_e, match_p_full)
            conv_scores = bm._calculate_kill_conversion(match_e, match_id)
            for tid, metrics in syn.items():
                row = dict(metrics)
                row["match_id"]        = match_id
                row["team_id"]         = int(tid)
                row["kill_conversion"] = conv_scores.get(tid, 0)
                synergy_results.append(row)

    df_syn = pd.DataFrame(synergy_results) if synergy_results else pd.DataFrame()

    # ── Métricas sobre los 10 jugadores (pool para Min-Max) ──────────────────
    df_pool_metrics = compute_player_metrics(df_p_pool)
    df_pool_metrics["team_id"] = df_pool_metrics["team_id"].astype(int)

    if not df_syn.empty:
        syn_cols = ["match_id", "team_id", "kill_conversion"] + \
                   [c for c in df_syn.columns if c.startswith("synergy")]
        
        # FIX: Evitar duplicados con sufijos _x/_y si los datos ya venían de Supabase
        existing_cols = [c for c in syn_cols if c in df_pool_metrics.columns and c not in ["match_id", "team_id"]]
        if existing_cols:
            df_pool_metrics = df_pool_metrics.drop(columns=existing_cols)

        df_pool_metrics = df_pool_metrics.merge(
            df_syn[syn_cols], on=["match_id", "team_id"], how="left"
        ).fillna(0)

    if "synergy_score" not in df_pool_metrics.columns or df_pool_metrics["synergy_score"].eq(0).all():
        tk = df_pool_metrics.groupby(["match_id", "team_id"])["kills"].transform("sum").clip(lower=1)
        df_pool_metrics["synergy_score"] = (df_pool_metrics["kills"] + df_pool_metrics["assists"]) / tk

    # ── Impact Score: Min-Max sobre 10 jugadores, escala 0-1 ─────────────────
    # Igual que _process_single_match en benchmarks.py → ambas pipelines en la
    # misma escala → Challenger Gap % y radares son matemáticamente válidos.
    df_full_impact = compute_impact_score(df_pool_metrics, df_objectives)  # sin df_bench

    # Filtrar a nuestros jugadores DESPUÉS de la normalización
    our_names    = set(filter_team_players(df_p_full)["game_name"].str.lower().unique()) if filter_team else None
    df_with_impact = (
        df_full_impact[df_full_impact["game_name"].str.lower().isin(our_names)].copy()
        if our_names is not None else df_full_impact.copy()
    )

    # Participants para gank_deaths y kd_density (5 jugadores propios)
    df_participants = df_with_impact

    df_gank_deaths = compute_gank_deaths(df_e, df_participants) if not df_e.empty else pd.DataFrame()
    df_summary     = compute_player_impact_summary(df_with_impact)
    df_summary     = df_summary[df_summary["role"] != "UNKNOWN"]

    if not df_summary.empty and not df_gank_deaths.empty:
        df_summary = df_summary.merge(df_gank_deaths, on=["game_name", "role"], how="left").fillna(0)

    # ── Inyectar roles en timeline ────────────────────────────────────────────
    if not df_t.empty:
        df_roles_full = df_p_full[["match_id", "participant_id", "role", "game_name", "team_id"]].drop_duplicates().copy()
        df_roles_full["participant_id"] = df_roles_full["participant_id"].astype(int)
        df_t = df_t.merge(df_roles_full, on=["match_id", "participant_id"], how="left")

    kill_matrix, death_matrix = compute_kd_density(df_e, df_participants)

    return {
        "participants":   df_with_impact,
        "summary":        df_summary,
        "timeline":       df_t,
        "events":         df_e,
        "objectives":     df_objectives,
        "gold_diff":      df_gold_diff,
        "phase_stats":    df_phase_stats,
        "n_team_matches": len(team_match_ids),
        "gank_deaths":    df_gank_deaths,
        "kill_matrix":    kill_matrix,
        "death_matrix":   death_matrix,
    }

@st.cache_data(show_spinner="Estructurando métricas desde Supabase...")
def build_features_from_db(cache_key: str, df_p, df_t, df_e):
    """
    Vía rápida (Fast-Track) para datos pre-calculados.
    Evita el ETL pesado porque la DB ya tiene el Impact Score y la Sinergia.
    """
    if df_p.empty:
        return {}

    from src.config import TEAM_PLAYER_DISPLAY_MAP
    df_p = df_p.copy()
    df_p["game_name"] = df_p["game_name"].str.lower().map(TEAM_PLAYER_DISPLAY_MAP).fillna(df_p["game_name"])

    
    # 1. Filtramos directo a nuestro equipo
    df_team = filter_team_players(df_p)
    if df_team.empty:
        return {"no_team_matches": True}
        
    team_match_ids = set(df_team["match_id"].unique())
    df_p_pool = df_p[df_p["match_id"].isin(team_match_ids)].copy()
    
    # 2. Asegurar métricas por minuto (CS/min, Vision/min, etc.)
    df_team = compute_player_metrics(df_team)
    
    # 3. Generar Summary agrupando las métricas
    df_summary = compute_player_impact_summary(df_team)
    df_summary = df_summary[df_summary["role"] != "UNKNOWN"]
    
    # Adaptación para la War Room: mapear early_gank_deaths a avg_gank_deaths
    if "early_gank_deaths" in df_team.columns:
        gank_agg = df_team.groupby(["game_name", "role"])["early_gank_deaths"].mean().reset_index()
        gank_agg = gank_agg.rename(columns={"early_gank_deaths": "avg_gank_deaths"})
        df_summary = df_summary.merge(gank_agg, on=["game_name", "role"], how="left")
    
    # 3. Features Tácticas (Requieren los eventos y el timeline, se calculan en vivo)
    df_objectives = compute_objective_control(df_e, df_p_pool) if not df_e.empty else pd.DataFrame()
    df_gold_diff = compute_gold_diff(df_t, df_p_pool) if not df_t.empty else pd.DataFrame()
    df_phase_stats = compute_phase_stats(df_t) if not df_t.empty else pd.DataFrame()
    
    kill_matrix, death_matrix = pd.DataFrame(), pd.DataFrame()
    if not df_e.empty:
        kill_matrix, death_matrix = compute_kd_density(df_e, df_team)

    # 4. Inyectar roles en timeline para los gráficos de oro
    if not df_t.empty:
        df_roles_full = df_p_pool[["match_id", "participant_id", "role", "game_name", "team_id"]].drop_duplicates().copy()
        df_roles_full["participant_id"] = df_roles_full["participant_id"].astype(int)
        df_t = df_t.merge(df_roles_full, on=["match_id", "participant_id"], how="left")

    return {
        "participants":   df_team, 
        "summary":        df_summary,
        "timeline":       df_t,
        "events":         df_e,
        "objectives":     df_objectives,
        "gold_diff":      df_gold_diff,
        "phase_stats":    df_phase_stats,
        "n_team_matches": len(team_match_ids),
        "gank_deaths":    pd.DataFrame(), # Ya se resolvió arriba en el merge del summary
        "kill_matrix":    kill_matrix,
        "death_matrix":   death_matrix,
    }

# ─────────────────────────────────────────────────────────────────────
# INITIALIZE STATE
# ─────────────────────────────────────────────────────────────────────
if "team_data_loaded" not in st.session_state:
    st.session_state.team_data_loaded = False
if "df_p" not in st.session_state: st.session_state.df_p = pd.DataFrame()
if "df_t" not in st.session_state: st.session_state.df_t = pd.DataFrame()
if "df_e" not in st.session_state: st.session_state.df_e = pd.DataFrame()

# ─────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚡ Challenger Protocol")
    st.markdown("---")
    
    api_ok = _has_api_key()
    if api_ok:
        st.success("API Key activa", icon="✅")
    else:
        st.error("Sin API Key — añade RIOT_API_KEY en .env", icon="🔑")

    patch = get_current_patch()
    benchmark_region = "GLOBAL"
    df_bench = load_benchmarks(benchmark_region, patch)

    if df_bench.empty:
        st.warning(f"Sin benchmarks para patch {patch}. Ejecuta el ETL.")
    else:
        st.success(f"Standard: CHALLENGER ({benchmark_region})", icon="🏆")

    st.markdown("---")
    st.markdown("**Fuente de datos**")
    data_source = st.radio(
        "Cargar desde:",
        options=["☁️ Supabase (equipo)", "🌐 Riot API"],
        index=0,
        horizontal=True,
        help="Supabase: partidas ya subidas con upload_team_match.py. Riot API: descarga en vivo (requiere API Key)."
    )
    use_supabase = data_source.startswith("☁️")

    if use_supabase:
        supabase_ok = bool(
            os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        )
        if not supabase_ok:
            st.error("Sin URL de Supabase en .env", icon="🗄️")
        load_supabase_btn = st.button(
            "☁️ Cargar desde Supabase", type="primary",
            disabled=not supabase_ok, width='stretch'
        )
        if load_supabase_btn:
            try:
                st.cache_data.clear()
                dp, dt, de = load_supabase_data()
                if not dp.empty:
                    st.session_state.df_p = dp
                    st.session_state.df_t = dt
                    st.session_state.df_e = de
                    st.session_state.team_data_loaded = True
                    n_matches_loaded = dp["match_id"].nunique()
                    st.sidebar.success(f"¡{n_matches_loaded} partidas desde Supabase!")
                    st.rerun()
                else:
                    st.sidebar.warning(
                        "No hay partidas en Supabase. "
                        "Usa: python upload_team_match.py --all-cached"
                    )
            except Exception as e:
                st.sidebar.error(f"Error Supabase: {e}")
    else:
        st.markdown("**Configurar carga vía API**")
        n_matches = st.slider("Partidas por jugador", 5, 100, 20, disabled=not api_ok)

        st.markdown("**Colas:**")
        c1, c2 = st.columns(2)
        q_ranked = c1.checkbox("Ranked Solo", value=True, disabled=not api_ok)
        q_flex   = c1.checkbox("Flex",        value=True, disabled=not api_ok)
        q_scrim  = c2.checkbox("Scrims",      value=True, disabled=not api_ok)
        q_draft  = c2.checkbox("Draft",       value=False,disabled=not api_ok)

        selected_queues = []
        if q_ranked: selected_queues.append(420)
        if q_flex:   selected_queues.append(440)
        if q_scrim:  selected_queues.append(0)
        if q_draft:  selected_queues.append(400)
        queues_val = selected_queues if selected_queues else None

        load_btn = st.button("🔄 Cargar historial", type="primary", disabled=not api_ok, width='stretch')

        if load_btn:
            try:
                st.cache_data.clear()
                team_hash = str(config.TEAM_PLAYERS)
                dp, dt, de = load_api_data(n_matches, queues_val, team_hash)
                if not dp.empty:
                    st.session_state.df_p = dp
                    st.session_state.df_t = dt
                    st.session_state.df_e = de
                    st.session_state.team_data_loaded = True
                    st.sidebar.success(f"¡{len(dp['match_id'].unique())} partidas cargadas!")
                    st.rerun()
                else:
                    st.sidebar.warning("No se encontraron partidas con los filtros seleccionados.")
            except Exception as e:
                st.sidebar.error(f"Error: {e}")

    if use_supabase and st.session_state.team_data_loaded:
        st.markdown("---")
        st.markdown("**Filtrar Datos (Supabase):**")
        scrim_filter = st.radio(
            "Tipo de partida:",
            options=["Todas", "Oficiales (Flex)", "Scrims (Customs)"],
            index=0,
            horizontal=False,
            key="supabase_scrim_filter"
        )
    else:
        scrim_filter = "Todas"

def extract_synergy_dicts(df_summary: pd.DataFrame, df_b: pd.DataFrame):
    """Extrae directamente los promedios de sinergia desde el summary ya agrupado."""
    syn_keys = [
        "synergy_jg_sup", "synergy_jg_mid", "synergy_jg_top", "synergy_jg_adc",
        "synergy_adc_sup", "synergy_mid_bot", "synergy_mid_top", "synergy_mid_sup",
        "synergy_top_bot", "synergy_top_sup"
    ]
    t_syn = {}
    for k in syn_keys:
        avg_col = f"avg_{k}"
        # Busca la columna con 'avg_' (modo API/Summary) o normal (Fallback)
        if avg_col in df_summary.columns and not df_summary[avg_col].isna().all():
            t_syn[k] = float(df_summary[avg_col].mean())
        elif k in df_summary.columns and not df_summary[k].isna().all():
            t_syn[k] = float(df_summary[k].mean())
        else:
            t_syn[k] = 0.0
            
    b_syn = {k: {"p50": float(df_b[k].median())} if k in df_b.columns else {"p50": 0.5} for k in syn_keys}
    return t_syn, b_syn



# ─────────────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "⚡ War Room", "👤 Individual", "📈 Tempo & Macro", "🧠 Patrones",
    "🏆 Challenger Gap", "🗺️ Por Partida", "🎯 Draft Lab"
])

features = {}
if st.session_state.team_data_loaded:
    df_p_filtered = st.session_state.df_p.copy()
    if use_supabase and "is_custom" in df_p_filtered.columns:
        if scrim_filter == "Oficiales (Flex)":
            df_p_filtered = df_p_filtered[df_p_filtered["is_custom"] == False]
        elif scrim_filter == "Scrims (Customs)":
            df_p_filtered = df_p_filtered[df_p_filtered["is_custom"] == True]
            
    valid_match_ids = set(df_p_filtered["match_id"].unique()) if not df_p_filtered.empty else set()
    
    df_t_filtered = st.session_state.df_t
    if not df_t_filtered.empty and "match_id" in df_t_filtered.columns:
        df_t_filtered = df_t_filtered[df_t_filtered["match_id"].isin(valid_match_ids)]
        
    df_e_filtered = st.session_state.df_e
    if not df_e_filtered.empty and "match_id" in df_e_filtered.columns:
        df_e_filtered = df_e_filtered[df_e_filtered["match_id"].isin(valid_match_ids)]

    _ids_str = str(sorted(list(valid_match_ids)))
    _cache_key = "team_" + hashlib.md5(_ids_str.encode()).hexdigest()[:10]
    
    if use_supabase:
        features = build_features_from_db(
            _cache_key,
            df_p_filtered,
            df_t_filtered,
            df_e_filtered
        )
    else:
        features = compute_all_features(
            _cache_key,
            df_p_filtered,
            df_t_filtered,
            df_e_filtered,
            df_bench=df_bench,
        )

# TAB 1: WAR ROOM
with tab1:
    if not features:
        st.info("Carga el historial en el sidebar para comenzar.", icon="👈")
    elif features.get("no_team_matches"):
        st.warning("No se encontraron partidas del equipo completo.")
    else:
        
        # Header Estilo Pro
        render_war_room_header()

        # ── ALERTAS CRÍTICAS (get_war_room_alerts) ──────────────────────────
        war_alerts = get_war_room_alerts(features, df_bench)
        if war_alerts:
            alert_cols = st.columns(min(len(war_alerts), 3))
            for i, alert in enumerate(war_alerts):
                with alert_cols[i % 3]:
                    render_alert_card(
                        alert["title"], alert["desc"],
                        icon=alert.get("icon", "🔵"),
                        severity=alert.get("severity", "info"),
                        loss_rate=0.0
                    )
            st.markdown("<br>", unsafe_allow_html=True)
        
        # ── KPIs Globales ──────────────────────────────────────────────────
        team_df = filter_team_players(features["participants"])
        match_res = team_df.drop_duplicates("match_id")[["match_id", "result", "duration_minutes"]]
        wr = match_res["result"].mean()
        avg_impact = features["summary"]["avg_impact_score"].mean()
        avg_gd15 = features.get("gold_diff", pd.DataFrame()).get("gold_diff_min15", pd.Series([0])).mean()
        n_games = len(match_res)

        obj_perf = analyze_objective_performance(
            features["objectives"], match_res, df_participants=features["participants"]
        )
        obj_conversion = obj_perf.get("objective_conversion", 0.5)

        k1, k2, k3, k4 = st.columns(4)
        with k1: render_kpi_card("WIN RATE", f"{wr*100:.0f}%", f"{n_games} PARTIDAS")
        with k2: render_kpi_card("IMPACTO AVG", f"{avg_impact:.3f}", "TOTAL EQUIPO")
        with k3: render_kpi_card("GD @ 15", f"{avg_gd15:+.0f}", "EARLY GAME")
        with k4: render_kpi_card("CONVERSIÓN", f"{obj_conversion*100:.0f}%", "OBJ ➔ WIN")

        # ── DUOS + TRÍOS + VISIÓN ─────────────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)

        duo_col, trio_col, vis_col = st.columns([1, 1, 1])

        # ── Datos compartidos para Duos y Tríos ──────────────────────────
        from itertools import combinations

        team_raw = features["participants"].copy()

        duo_counter: dict[tuple, dict] = {}
        trio_counter: dict[tuple, dict] = {}

        for mid, grp in team_raw.groupby("match_id"):
            grp_clean = grp.drop_duplicates(subset=["role"])
            champs = sorted(grp_clean["champion"].unique().tolist())
            if len(champs) < 2:
                continue
            win = bool(grp_clean["result"].iloc[0]) if "result" in grp_clean.columns else False

            for duo in combinations(champs, 2):
                if duo not in duo_counter:
                    duo_counter[duo] = {"n": 0, "wins": 0}
                duo_counter[duo]["n"] += 1
                duo_counter[duo]["wins"] += 1 if win else 0

            if len(champs) >= 3:
                for trio in combinations(champs, 3):
                    if trio not in trio_counter:
                        trio_counter[trio] = {"n": 0, "wins": 0}
                    trio_counter[trio]["n"] += 1
                    trio_counter[trio]["wins"] += 1 if win else 0

        dd_ver = st.session_state.get("ddragon_version", "16.9.1")

        def render_champ_icons(champs: tuple, size: int = 150) -> str:
            icons = ""
            for c in champs:
                url = f"https://ddragon.leagueoflegends.com/cdn/{dd_ver}/img/champion/{c}.png"
                icons += f'<img src="{url}" style="width:{size}px; height:{size}px; border-radius:4px; border:1px solid rgba(255,255,255,0.2); object-fit:cover; margin-right:2px;" title="{c}">'
            return icons

        # ── COLUMNA 1: DUOS ──────────────────────────────────────────────
        with duo_col:
            st.markdown('<div class="cp-table-header">🔹 Duos</div>', unsafe_allow_html=True)
            top_duos = sorted(duo_counter.items(), key=lambda x: x[1]["n"], reverse=True)[:4]
            if top_duos:
                duo_html = '<table class="cp-scoreboard"><tbody>'
                for duo, stats in top_duos:
                    wr = stats["wins"] / max(stats["n"], 1) * 100
                    duo_html += '<tr class="cp-sb-row">'
                    duo_html += f'<td class="cp-sb-cell" style="padding:6px 8px;">{render_champ_icons(duo, 60)}</td>'
                    duo_html += f'<td class="cp-sb-cell cp-sb-metric" style="text-align:right; padding:6px 8px;"><span style="color:#E2E8F0; font-weight:700; font-size:14px;">{wr:.0f}%</span> <span style="color:#64748B; font-size:11px;">({stats["n"]}p)</span></td>'
                    duo_html += '</tr>'
                duo_html += '</tbody></table>'
                st.markdown(duo_html, unsafe_allow_html=True)
            else:
                st.caption("Sin datos")

        # ── COLUMNA 2: TRÍOS ─────────────────────────────────────────────
        with trio_col:
            st.markdown('<div class="cp-table-header">🔸 Tríos</div>', unsafe_allow_html=True)
            top_trios = sorted(trio_counter.items(), key=lambda x: x[1]["n"], reverse=True)[:4]
            if top_trios:
                trio_html = '<table class="cp-scoreboard"><tbody>'
                for trio, stats in top_trios:
                    wr = stats["wins"] / max(stats["n"], 1) * 100
                    trio_html += '<tr class="cp-sb-row">'
                    trio_html += f'<td class="cp-sb-cell" style="padding:6px 8px;">{render_champ_icons(trio, 60)}</td>'
                    trio_html += f'<td class="cp-sb-cell cp-sb-metric" style="text-align:right; padding:6px 8px;"><span style="color:#E2E8F0; font-weight:700; font-size:14px;">{wr:.0f}%</span> <span style="color:#64748B; font-size:11px;">({stats["n"]}p)</span></td>'
                    trio_html += '</tr>'
                trio_html += '</tbody></table>'
                st.markdown(trio_html, unsafe_allow_html=True)
            else:
                st.caption("Sin datos")

        # ── COLUMNA 3: VISIÓN ────────────────────────────────────────────
        vision_data = analyze_vision_control(features["participants"])

        with vis_col:
            st.markdown('<div class="cp-table-header">👁️ Control de Visión</div>', unsafe_allow_html=True)

            # Tabla estilo Scoreboard: VS/min + Pinks por rol con % gap vs Challenger
            # Usamos df_p crudo (session_state) porque features["participants"] puede no tener control_wards
            raw_team = features["participants"].copy()

            # Calcular VS/min y pinks desde columnas crudas
            dur = raw_team["duration_minutes"].clip(lower=0.01)
            raw_team["vision_per_min"] = raw_team["vision_score"] / dur
            raw_team["control_wards"] = raw_team.get("control_wards", 0)

            vis_by_player = raw_team.groupby(["game_name", "role"]).agg(
                avg_vs=("vision_per_min", "mean"),
                avg_pinks=("control_wards", "mean"),
            ).reset_index()

            # Ordenar por rol canónico: TOP → JUNGLE → MID → BOT → SUPPORT
            role_order = {"TOP": 0, "JUNGLE": 1, "MID": 2, "BOT": 3, "SUPPORT": 4}
            vis_by_player["_sort"] = vis_by_player["role"].map(role_order).fillna(99)
            vis_by_player = vis_by_player.sort_values("_sort")

            vis_html = '<table class="cp-scoreboard">'
            vis_html += '<thead><tr style="text-align:left;">'
            vis_html += '<th class="cp-sb-cell cp-table-header">JUGADOR</th>'
            vis_html += '<th class="cp-sb-cell cp-table-header" style="text-align:center;">VS/MIN</th>'
            vis_html += '<th class="cp-sb-cell cp-table-header" style="text-align:center;">PINKS</th></tr></thead>'

            for _, row in vis_by_player.iterrows():
                name = row["game_name"]
                role = row["role"]
                p_vs = row["avg_vs"]
                p_pinks = row["avg_pinks"]

                b_vs, b_pinks = None, None
                if not df_bench.empty:
                    role_bench = df_bench[df_bench["role"].str.strip().str.upper() == role.strip().upper()]
                    if not role_bench.empty:
                        b_vs = role_bench["vision_per_min"].median()
                        b_pinks = role_bench["control_wards"].median() if "control_wards" in role_bench.columns else None

                def fmt_gap(val, bench, decimals=2):
                    if bench is None or bench == 0:
                        return f'<div class="cp-sb-val">{val:.{decimals}f}</div><div class="cp-sb-gap">---</div>'
                    gap_pct = ((val / bench) - 1) * 100
                    cls = "gap-positive" if gap_pct >= 0 else "gap-negative"
                    sign = "+" if gap_pct >= 0 else ""
                    return f'<div class="cp-sb-val">{val:.{decimals}f}</div><div class="cp-sb-gap {cls}">{sign}{gap_pct:.1f}%</div>'

                vis_html += f'<tr class="cp-sb-row">'
                vis_html += f'<td class="cp-sb-cell"><div class="cp-sb-player"><div class="cp-sb-role">{role}</div><div class="cp-sb-name">{name}</div></div></td>'
                vis_html += f'<td class="cp-sb-cell cp-sb-metric" style="text-align:center;">{fmt_gap(p_vs, b_vs)}</td>'
                vis_html += f'<td class="cp-sb-cell cp-sb-metric" style="text-align:center;">{fmt_gap(p_pinks, b_pinks, 1)}</td>'
                vis_html += '</tr>'

            vis_html += '</table>'
            st.markdown(vis_html, unsafe_allow_html=True)

            corr = vision_data.get("correlation_with_win") or 0
            low_n = len(vision_data.get("low_vision_games", []))
            st.markdown(f"""
            <div style="background:rgba(15,23,42,0.6); border-radius:12px; padding:12px; margin-top:8px;">
                <small style="color:#94A3B8;">{vision_data.get('insight', '')}<br>
                Corr. Visión→Win: {corr:+.2f} &nbsp;|&nbsp; Partidas visión crítica: {low_n}</small>
            </div>
            """, unsafe_allow_html=True)

        # ── TEAM TEMPO ──────────────────────────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        tempo = analyze_team_tempo(features["participants"], features.get("gold_diff"))
        if tempo:
            st.markdown('<div class="cp-table-header">⏱️ Team Tempo & Macro</div>', unsafe_allow_html=True)
            t1, t2, t3, t4, t5 = st.columns(5)
            with t1:
                render_kpi_card("FB RATE", f"{tempo.get('fb_rate', 0)*100:.0f}%", "FIRST BLOOD")
            with t2:
                render_kpi_card("WR CON FB", f"{tempo.get('wr_with_fb', 0)*100:.0f}%", "EARLY ADVANTAGE")
            with t3:
                render_kpi_card("WR SIN FB", f"{tempo.get('wr_without_fb', 0)*100:.0f}%", "FROM BEHIND")
            with t4:
                gd15_val = tempo.get('avg_gd15', 0)
                render_kpi_card("GD @ 15", f"{gd15_val:+.0f}", "GOLD DIFF EARLY")
            with t5:
                throw_pct = tempo.get('throw_rate', 0) * 100
                throw_delta = f"{tempo.get('n_throws', 0)} THROWS" if tempo.get('n_throws', 0) > 0 else "CLEAN"
                render_kpi_card("THROW RATE", f"{throw_pct:.0f}%", throw_delta)

        # ── SCOREBOARD + SYNERGY ────────────────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        c1, c2 = st.columns([3, 2])

        with c1:
            st.markdown('<div class="cp-table-header">🎮 Scoreboard vs Challenger Standard</div>', unsafe_allow_html=True)
            name_map = {p["riot_id"].split("#")[0].lower(): p.get("display_name", p["riot_id"]) for p in config.TEAM_PLAYERS}
            render_scoreboard(features["summary"], name_map, df_bench)

        with c2:
            st.markdown('<div class="cp-table-header">🔗 Synergy Matrix (SKP Score)</div>', unsafe_allow_html=True)
            t_syn, b_syn = extract_synergy_dicts(features["summary"], df_bench)
            fig_syn = create_synergy_heatmap(t_syn, b_syn)
            st.plotly_chart(fig_syn, width='stretch', key="syn_war_new")

        # ── WINRATE POR FASE ────────────────────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="cp-table-header">📈 Dominancia por Fase del Juego</div>', unsafe_allow_html=True)
        phase_stats = identify_loss_phase(features["participants"], features["gold_diff"])
        fig_phase = build_phase_winrate_chart(phase_stats["phase_stats"], match_res)
        st.plotly_chart(fig_phase, width='stretch', key="phase_war")

# TAB 2: INDIVIDUAL
with tab2:
    if features:
        df_summary = features["summary"]
        st.subheader("👤 Análisis de Radares vs P50 Challenger (Mediana)")
        radar_cols = st.columns(len(df_summary))
        for i, (idx, p_row) in enumerate(df_summary.iterrows()):
            name = p_row["game_name"]
            role = p_row["role"]
            is_midnexus = "midnexus" in name.lower()
            
            with radar_cols[i % len(radar_cols)]:
                # Card de identificación
                border = "2px solid #EF4444" if is_midnexus else "1px solid rgba(255,255,255,0.1)"
                st.markdown(f"<div style='border:{border}; border-radius:12px; padding:10px; background:rgba(15, 23, 42, 0.4); text-align:center;'><b>{name}</b><br><small>{role}</small></div>", unsafe_allow_html=True)
                
                # Benchmarks para este rol (Promedio Global)
                role_bench_rows = df_bench[df_bench["role"] == role]
                pillar_metrics = ["pilar_combat_efficiency", "pilar_map_pressure", "pilar_tactical_utility", "pilar_team_synergy"]
                
                if not role_bench_rows.empty:
                    role_avg = role_bench_rows.mean(numeric_only=True)
                    perc = {m: {"p50": role_avg[m] if m in role_avg.index else 0.5} for m in pillar_metrics}
                else:
                    perc = {m: {"p50": 0.5} for m in pillar_metrics}

                # Métricas del jugador (Promedios Históricos)
                player_metrics = {m: p_row.get(m, 0) for m in pillar_metrics}
                
                # Radar Ghost Comparative
                fig = plot_ghost_radar(player_metrics, perc, name, role)
                st.plotly_chart(fig, width='stretch', key=f"rad_ghost_ind_{i}")
                
                if st.button(f"Mejora: {name}", key=f"btn_{i}"): st.toast(f"Generando plan para {name}...")

# TAB 3: TEMPO & MACRO
with tab3:
    if features:
        df_gd = features.get("gold_diff", pd.DataFrame())
        st.subheader("📈 Gold Diff Timeline (Wins vs Losses)")
        if not df_gd.empty:
            team_df = filter_team_players(features["participants"])
            res = team_df.drop_duplicates("match_id")[["match_id", "result"]]
            st.plotly_chart(build_gold_timeline_chart(df_gd, res), width='stretch')
        
        st.markdown("---")
        st.subheader("🎯 Control de Objetivos")
        obj_perf = analyze_objective_performance(
            features["objectives"],
            team_df.drop_duplicates("match_id")[["match_id", "result", "duration_minutes"]],
            df_participants=features["participants"],
        )
        st.plotly_chart(build_objectives_chart(obj_perf, features["objectives"]), width='stretch')

# TAB 4: PATRONES
with tab4:
    if features:
        st.subheader("🧠 Patrones de Derrota Críticos")
        detector = PatternDetector(features["participants"], features.get("objectives"), features.get("gold_diff"))
        insights = detector.detect_all()
        for i, ins in enumerate(insights[:4]):
            render_insight_card(ins.title, f"{ins.description}<br><br><b>FIX:</b> Sincronizar timers y priorizar visión.", ins.severity)
        
        st.markdown("---")
        st.markdown("---")
        st.markdown("---")
        st.subheader("⚔️ Densidad de Combate (Minuto a Minuto)")
        
        # Heatmap de Kills
        st.plotly_chart(build_density_heatmap(features["kill_matrix"], "Ejecución de Kills: Cuándo y quién logra las bajas", "Greens"), width='stretch', key="kill_density_war_v2")
        
        st.markdown("---")
        
        # Heatmap de Muertes
        st.plotly_chart(build_density_heatmap(features["death_matrix"], "Vulnerabilidad: Cuándo y quién sufre las muertes", "Reds"), width='stretch', key="death_density_war_v2")

# TAB 5: CHALLENGER GAP
with tab5:
    if features:
        st.subheader("🏆 Challenger Gap Analysis")
        # Tabla de gap consolidada por rol
        gap_data = []
        for _, p_row in features["summary"].iterrows():
            role = p_row["role"]
            role_rows = df_bench[df_bench["role"].str.strip().str.upper() == role.strip().upper()]
            
            # Usamos Mediana (p50) para todas las comparaciones de gap
            if not role_rows.empty:
                bench_impact = role_rows["impact_score"].median()
                bench_cs     = role_rows["cs_per_min"].median()
                bench_dpm    = role_rows["damage_per_min"].median()
            else:
                # Fallbacks razonables si no hay data
                bench_impact, bench_cs, bench_dpm = 0.5, 8.0, 550
            
            def fmt_gap(val, bench):
                ratio = (val / bench) * 100 if bench > 0 else 100
                color = "green" if ratio >= 100 else "red"
                return f":{color}[{ratio:.1f}%]"

            gap_data.append({
                "Jugador": p_row["game_name"], 
                "Rol": role,
                "Impacto vs Chal": fmt_gap(p_row['avg_impact_score'], bench_impact), 
                "CS/min vs Chal":  fmt_gap(p_row['avg_cs_per_min'], bench_cs), 
                "DPM vs Chal":     fmt_gap(p_row['avg_damage_per_min'], bench_dpm)
            })
        st.table(pd.DataFrame(gap_data))

# TAB 6: POR PARTIDA
with tab6:
    st.markdown('<div class="cp-section-title">🔍 Análisis Táctico Individual</div>', unsafe_allow_html=True)
    
    if features:
        m_ids = sorted(list(features["participants"]["match_id"].unique()), reverse=True)
        
        if "selected_match_id" not in st.session_state:
            st.session_state.selected_match_id = None
            
        sel_mid = st.session_state.selected_match_id
        
        st.markdown("### 🎮 Historial de Partidas del Equipo")
        if "history_page" not in st.session_state:
            st.session_state.history_page = 0
        page = st.session_state.history_page
        items_per_page = 5
        max_page = max(0, (len(m_ids) - 1) // items_per_page)
        if page > max_page:
            st.session_state.history_page = max_page
            page = max_page
        start_idx = page * items_per_page
        display_matches = m_ids[start_idx : start_idx + items_per_page]
        c_left, c_cards, c_right = st.columns([0.5, 9, 0.5])
        with c_left:
            st.markdown("<br><br><br>", unsafe_allow_html=True)
            if st.button("◀", disabled=(page == 0), key="btn_prev_hist"):
                st.session_state.history_page -= 1
                st.rerun()
        with c_cards:
            cols = st.columns(5)
            
            import streamlit.components.v1 as components
            # Listener global para atrapar los clicks de los divs sin depender del onclick en el HTML, y evitar recargas
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

            # CSS para ocultar completamente los botones invisibles generados abajo
            st.markdown("""<style>
            div.element-container:has(div[id^="match_card_"]) + div.element-container {
                position: absolute !important;
                opacity: 0 !important;
                pointer-events: none !important;
                height: 0px !important;
                overflow: hidden !important;
            }
            </style>""", unsafe_allow_html=True)

            for i_m, mid in enumerate(display_matches):
                match_df = st.session_state.df_p[st.session_state.df_p["match_id"] == mid].copy()
                from src.analysis import TEAM_GAME_NAMES
                team_counts = match_df[match_df["game_name"].str.lower().isin(TEAM_GAME_NAMES)]["team_id"].value_counts()
                my_team_id_card = team_counts.idxmax() if not team_counts.empty else 100
                team_df = match_df[match_df["team_id"] == my_team_id_card]
                if team_df.empty: team_df = match_df
                row = team_df.iloc[0]
                result_bool = row.get("result", False)
                result_str = "Win" if result_bool else "Loss"
                order = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
                champ_dict = {r.get("role", "UNKNOWN"): r.get("champion", "Unknown") for _, r in team_df.iterrows()}
                champions = [champ_dict.get(r, "Unknown") for r in order]
                duration_min = row.get("duration_minutes", 0)
                dur_mins = int(duration_min)
                dur_secs = int((duration_min - dur_mins) * 60)
                dur_str = f"{dur_mins}:{dur_secs:02d}"
                is_selected = (mid == sel_mid)
                with cols[i_m % 5]:
                    render_match_card(mid, champions, result_str, dur_str, is_selected)
                    if st.button(f"{mid}_HIDDEN", key=f"btn_match_{mid}"):
                        st.session_state.selected_match_id = mid
                        st.rerun()
        with c_right:
            st.markdown("<br><br><br>", unsafe_allow_html=True)
            if st.button("▶", disabled=(page == max_page), key="btn_next_hist"):
                st.session_state.history_page += 1
                st.rerun()
        
        if not sel_mid or sel_mid not in m_ids:
            st.info("👆 Selecciona una partida arriba para ver su análisis táctico completo.")
        else:
            if st.button("⬅️ Cerrar Partida", key="close_match"):
                st.session_state.selected_match_id = None
                st.rerun()

            # Cargar data completa de la partida para visualizaciones tácticas
            with st.spinner("Cargando datos tácticos de la partida..."):
                df_p_raw, df_t, df_e = load_single_match(sel_mid)
            
            # PROCESAR MÉTRICAS: Aplicar motor de inteligencia a los 10 jugadores (Sin filtrar)
            temp_feat = compute_all_features(f"full_{sel_mid}", df_p_raw, df_t, df_e, filter_team=False)
            df_p_full = temp_feat.get("participants", df_p_raw)
            
            match_summary_full = summarize_match(sel_mid, df_p_full, features.get("objectives", pd.DataFrame()))
            
            # Identificar equipo aliado
            # Identificar equipo aliado robusto
            team_names = [p["riot_id"].split("#")[0].lower() for p in config.TEAM_PLAYERS]
            team_counts = {}
            for p in match_summary_full["players"]:
                if p["game_name"].lower() in team_names:
                    team_counts[p["team_id"]] = team_counts.get(p["team_id"], 0) + 1
            my_team_id = max(team_counts, key=team_counts.get) if team_counts else 100
            
            with st.expander("📖 Guía Táctica: ¿Qué miden los 4 Pilares?", expanded=False):
                st.markdown("""
                | Pilar | Componentes Clave | ¿Qué explica? |
                | :--- | :--- | :--- |
                | **Eficiencia Combate** | Damage per Gold, KDA, Kill Part. | Tu capacidad de generar daño y participar en kills optimizando el oro recibido. |
                | **Presión de Mapa** | Dmg to Buildings, Kill Conversion. | Qué tanto transformas tus ventajas en destrucción de estructuras y objetivos. |
                | **Utilidad Táctica** | CC Score, Vision Score, Mitigation/Gold. | Tu aporte al control de mapa, disrupción y supervivencia eficiente. |
                | **Sinergia de Equipo** | SKP Sync Score (JG-SUP, JG-MID, etc.) | Tu nivel de coordinación técnica con los roles clave de la partida. |
                """)
    
            # Pestañas: General + 5 Jugadores
            roles = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
            all_tabs = ["📊 General"] + [f"{render_role_badge(r)} {r}" for r in roles]
            match_tabs = st.tabs(all_tabs)
            
            # --- TAB GENERAL DE LA PARTIDA ---
            with match_tabs[0]:
                st.markdown(f"### 🛡️ Resumen Táctico: {sel_mid}")
                
                # Filtramos los participantes de esta partida específica para el scoreboard (Solo equipo aliado)
                df_match_p = features["participants"][
                    (features["participants"]["match_id"] == sel_mid) & 
                    (features["participants"]["team_id"] == my_team_id)
                ].copy()
                
                # 1. Scoreboard de la Partida (Reusando componente)
                name_map = {p["riot_id"].split("#")[0].lower(): p.get("display_name", p["riot_id"]) for p in config.TEAM_PLAYERS}
                
                df_match_summary = df_match_p.copy()
                df_match_summary["avg_impact_score"] = df_match_summary["impact_score"]
                df_match_summary["avg_kda"] = df_match_summary["kda"]
                df_match_summary["avg_cs_per_min"] = df_match_summary.get("cs_per_min", 0)
                df_match_summary["avg_damage_per_min"] = df_match_summary.get("damage_per_min", 0)
                df_match_summary["avg_vision_per_min"] = df_match_summary["vision_per_min"]
                
                # --- SECCIÓN TÁCTICA PRO ---
                
                st.markdown('<div class="cp-section-title">📊 Análisis Táctico de Élite</div>', unsafe_allow_html=True)
                
                # Fila 1: Momentum y Mapa de Muertes
                col_tactical_1, col_tactical_2 = st.columns([2, 1])
                df_match_t = temp_feat.get("timeline", pd.DataFrame())
                
                with col_tactical_1:
                    if not df_match_t.empty:
                        fig_momentum = plot_match_momentum(df_match_t, my_team_id)
                        st.plotly_chart(fig_momentum, width='stretch', key=f"momentum_{sel_mid}")
                    else:
                        st.warning("Datos de timeline no disponibles para esta partida.")
                
                with col_tactical_2:
                    df_match_e = features["events"][features["events"]["match_id"] == sel_mid]
                    if not df_match_e.empty:
                        # Selector de capa tactica
                        map_view = st.radio(
                            "Visualizacion Tactica:", 
                            ["Muertes", "Influencia"], 
                            horizontal=True, 
                            key=f"map_toggle_{sel_mid}"
                        )
                        
                        my_team_id = int(df_match_p["team_id"].iloc[0]) if not df_match_p.empty else 100
                        
                        if map_view == "Muertes":
                            fig_map = plot_death_map(df_match_e, my_team_id)
                        else:
                            # El Heatmap tactico usa el timeline para densidad y eventos para iconos
                            fig_map = plot_position_heatmap(df_match_t, df_match_e, match_id=sel_mid)
                            
                        st.plotly_chart(fig_map, use_container_width=True, key=f"tactical_map_{sel_mid}")
                    else:
                        st.warning("Datos de eventos no disponibles para esta partida.")
    
                # Fila 2: Dominancia de Línea y Scoreboard
                col_tactical_3, col_tactical_4 = st.columns([1, 1])
                
                with col_tactical_3:
                    if not df_match_t.empty:
                        fig_lane = plot_lane_dominance(df_match_t, my_team_id)
                        st.plotly_chart(fig_lane, width='stretch', key=f"lane_dom_{sel_mid}")
    
                with col_tactical_4:
                    st.markdown('<div class="cp-table-header">🔗 Sinergia en esta Partida</div>', unsafe_allow_html=True)
                    if not df_match_p.empty:
                        df_match_summary_syn = compute_player_impact_summary(df_match_p)
                        # Usamos el summary ad-hoc de esta partida
                        t_syn_match, b_syn_match = extract_synergy_dicts(df_match_summary_syn, df_bench)
                        fig_syn_match = create_synergy_heatmap(t_syn_match, b_syn_match)
                        st.plotly_chart(fig_syn_match, width='stretch', key=f"syn_match_{sel_mid}")
    
                st.markdown("---")
                st.markdown('<div class="cp-table-header">🎮 Desempeño vs Challenger Standard</div>', unsafe_allow_html=True)
                render_scoreboard(df_match_summary, name_map, df_bench)
    
            # --- TABS INDIVIDUALES POR ROL ---
            for i, role in enumerate(roles):
                with match_tabs[i+1]:
                    # Buscar datos del jugador ALIADO en el resumen de esta partida
                    # Filtramos por ROL y por nuestro TEAM_ID para evitar cruzarnos con el rival
                    p_data = next((p for p in match_summary_full["players"] 
                                 if p["role"] == role and p["team_id"] == my_team_id), None)
                    
                    if p_data:
                        champion = p_data["champion"]
                        c1, c2 = st.columns([1, 2])
                        
                        with c1:
                            st.image(f"https://ddragon.leagueoflegends.com/cdn/{dd_ver}/img/champion/{champion}.png", width=100)
                            st.markdown(f"### {p_data['game_name']}")
                            st.markdown(f"**{champion}**")
                            
                            # Benchmark vs Challenger
                            b_champ = df_bench[(df_bench["champion"] == champion) & (df_bench["role"] == role)]
                            if b_champ.empty: b_champ = df_bench[df_bench["role"] == role]
                            
                            metrics_to_show = [("KDA", "kda", ".2f"), ("Impacto", "impact_score", ".3f"), 
                                              ("VS/Min", "vision_per_min", ".2f"), ("Gold/Min", "gold_per_min", ".0f")]
                            
                            for label, key, fmt in metrics_to_show:
                                val = p_data.get(key, 0)
                                bench = b_champ[key].mean() if not b_champ.empty else 0.1
                                diff = (val / bench - 1) * 100 if bench > 0 else 0
                                st.metric(label, f"{val:{fmt}}", f"{diff:+.1f}% vs Chall")
    
                        with c2:
                            # Radar individual de la partida
                            
                            # Preparar métricas para el radar (4 Pilares)
                            current_metrics = {
                                "pilar_combat_efficiency": p_data.get("pilar_combat_efficiency", 0),
                                "pilar_map_pressure":      p_data.get("pilar_map_pressure", 0),
                                "pilar_tactical_utility":  p_data.get("pilar_tactical_utility", 0),
                                "pilar_team_synergy":      p_data.get("pilar_team_synergy", 0)
                            }
                            
                            # Benchmark vs Challenger para visualización
                            pseudo_percentiles = {}
                            for k in current_metrics.keys():
                                if k in b_champ.columns:
                                    m = b_champ[k].mean()
                                    pseudo_percentiles[k] = {"p10": m*0.7, "p50": m, "p90": m*1.3}
                                else:
                                    pseudo_percentiles[k] = {"p10": 0.2, "p50": 0.5, "p90": 0.8}
                            
                            fig_radar = build_radar_chart(current_metrics, pseudo_percentiles, p_data["game_name"], role)
                            st.plotly_chart(fig_radar, width='stretch', key=f"radar_{role}_{sel_mid}")
    
                        # --- SECCIÓN: DESGLOSE TÁCTICO DETALLADO ---
                        st.markdown("---")
                        
                        col_p1, col_p2 = st.columns(2)
                        with col_p1:
                            st.markdown('<div class="cp-table-header">⏳ Cronología de Impacto</div>', unsafe_allow_html=True)
                            fig_p_timeline = plot_individual_timeline(df_e, p_data.get("participant_id"))
                            st.plotly_chart(fig_p_timeline, width='stretch', key=f"p_timeline_{role}_{sel_mid}")
                        
                        with col_p2:
                            st.markdown('<div class="cp-table-header">💰 Duelo de Oro vs Rival</div>', unsafe_allow_html=True)
                            # CAMBIO: Usamos df_match_t (el timeline procesado arriba) en lugar de df_t
                            fig_p_gold = plot_player_gold_diff(df_match_t, role, my_team_id) 
                            st.plotly_chart(fig_p_gold, width='stretch', key=f"p_gold_{role}_{sel_mid}")
                    else:
                        st.info(f"No se encontró información táctica para el rol {role} en esta partida.")
    else:
        st.info("Carga datos para visualizar el análisis por partida.")

# TAB 7: DRAFT LAB
with tab7:
    draft_engine = load_draft_engine_v2()
    render_draft_tab(draft_engine, dd_ver)
