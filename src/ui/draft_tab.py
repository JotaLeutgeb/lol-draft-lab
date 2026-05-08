"""
draft_tab.py — UI del Draft Lab para Challenger Protocol con optimizaciones de alta velocidad.
"""
from __future__ import annotations
from typing import Optional
import streamlit as st
import requests

from src.draft_engine import DraftEngine, DraftState, normalize_champ

ROLE_ORDER  = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
ROLE_ICONS  = {"TOP": "🛡️", "JUNGLE": "🌿", "MID": "⚡", "BOT": "🏹", "SUPPORT": "💎"}
QUALITY_LEAGUES = ["LCK", "LPL", "LCS", "LEC", "CBLOL"]


@st.cache_data(ttl=3600, show_spinner=False)
def _get_all_champions(dd_ver: str) -> list[str]:
    """Carga y cachea la lista de campeones desde DDragon para acceso instantáneo."""
    try:
        url = f"https://ddragon.leagueoflegends.com/cdn/{dd_ver}/data/en_US/champion.json"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return sorted(r.json()["data"].keys())
    except Exception:
        pass
    return []


def _champ_icon_url(champ: str, dd_ver: str) -> str:
    return f"https://ddragon.leagueoflegends.com/cdn/{dd_ver}/img/champion/{champ}.png"


def _init_state():
    if "draft_state" not in st.session_state:
        st.session_state.draft_state = DraftState()
    if "draft_our_side" not in st.session_state:
        st.session_state.draft_our_side = "blue"
    if "draft_leagues" not in st.session_state:
        st.session_state.draft_leagues = list(QUALITY_LEAGUES)


def _render_side_board(side: str, state: DraftState, all_champs: list[str], dd_ver: str):
    is_our = side == st.session_state.draft_our_side
    color  = "#3B82F6" if side == "blue" else "#EF4444"
    bg_color = "rgba(59,130,246,0.08)" if side == "blue" else "rgba(239,68,68,0.08)"
    label  = ("🔵 BLUE SIDE" if side == "blue" else "🔴 RED SIDE") + (" ← TU EQUIPO" if is_our else "")

    picks = state.blue_picks if side == "blue" else state.red_picks

    st.markdown(
        f'<div style="background:rgba(255,255,255,0.02);border:1px solid {color}22;'
        f'border-radius:14px;padding:16px;margin-bottom:12px;">'
        f'<div style="font-size:13px;font-weight:800;color:{color};letter-spacing:2px;'
        f'text-transform:uppercase;margin-bottom:16px;">{label}</div>',
        unsafe_allow_html=True,
    )

    # Picks por rol
    for role in ROLE_ORDER:
        champ = picks.get(role)
        current_val = champ or "(Vacío)"
        icon = ROLE_ICONS.get(role, "")

        # Opciones para el selectbox (Filtrar deshabilitados de Fearless Mode)
        disabled_pool = st.session_state.get("disabled_champions", set())
        champs_available = [c for c in all_champs if (c not in disabled_pool or c == champ)]
        options = ["(Vacío)"] + champs_available
        try:
            idx = options.index(current_val)
        except ValueError:
            idx = 0

        # Inicializar llave de reset dinámica
        reset_key = f"reset_{side}_{role}"
        if reset_key not in st.session_state:
            st.session_state[reset_key] = 0

        col_icon, col_select, col_clear = st.columns([1, 3.8, 0.6])
        
        with col_icon:
            if champ:
                url = _champ_icon_url(champ, dd_ver)
                st.markdown(
                    f'<div style="display:flex;flex-direction:column;align-items:center;margin-top:2px;" title="{champ}">'
                    f'<img src="{url}" style="width:42px;height:42px;border-radius:8px;'
                    f'border:2px solid {color};object-fit:cover;" />'
                    f'<span style="font-size:9px;color:#94A3B8;margin-top:2px;font-weight:700;">{role}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div style="display:flex;flex-direction:column;align-items:center;margin-top:2px;" title="Vacío">'
                    f'<div style="width:42px;height:42px;border-radius:8px;'
                    f'border:2px dashed rgba(255,255,255,0.15);background:{bg_color};'
                    f'display:flex;align-items:center;justify-content:center;font-size:16px;color:#64748B;">?</div>'
                    f'<span style="font-size:9px;color:#475569;margin-top:2px;font-weight:700;">{role}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        with col_select:
            st.markdown(f'<div style="padding-top:4px;font-size:11px;color:#64748B;font-weight:700;">{icon} Seleccionar {role}</div>', unsafe_allow_html=True)
            selected_champ = st.selectbox(
                label=f"select_{side}_{role}",
                options=options,
                index=idx,
                key=f"sb_{side}_{role}_{st.session_state[reset_key]}",
                label_visibility="collapsed",
            )
            
            if selected_champ != current_val:
                new_val = None if selected_champ == "(Vacío)" else selected_champ
                if side == "blue":
                    st.session_state.draft_state.blue_picks[role] = new_val
                else:
                    st.session_state.draft_state.red_picks[role] = new_val
                st.rerun()

        with col_clear:
            if champ:
                st.markdown("<div style='padding-top:24px;'>", unsafe_allow_html=True)
                if st.button("✕", key=f"clear_{side}_{role}", help=f"Limpiar {role}"):
                    if side == "blue":
                        st.session_state.draft_state.blue_picks[role] = None
                    else:
                        st.session_state.draft_state.red_picks[role] = None
                    st.session_state[reset_key] += 1
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


def render_draft_tab(engine: DraftEngine, dd_ver: str):
    _init_state()
    state = st.session_state.draft_state

    # ── HEADER ──────────────────────────────────────────────────────
    st.markdown(
        '<div style="background:linear-gradient(90deg,#1e3a5f,#1a1f2e);'
        'border-radius:16px;padding:20px 28px;margin-bottom:20px;'
        'border:1px solid rgba(59,130,246,0.3);">'
        '<h2 style="margin:0;color:#FFF;font-size:26px;font-weight:800;">🎯 Draft Lab</h2>'
        '<p style="margin:4px 0 0;color:#64748B;font-size:13px;letter-spacing:1px;">'
        'SIMULADOR DE DRAFT · ANÁLISIS CON DATA PROFESIONAL</p>'
        '</div>',
        unsafe_allow_html=True,
    )

    if not engine.is_ready():
        st.warning(
            "⚠️ No hay datos de drafts pro. Ejecuta primero: "
            "`python sync_pro_drafts.py`",
            icon="📥",
        )
        return

    # Carga de campeones optimizada (instantánea vía caché)
    all_champs = _get_all_champions(dd_ver)

    # ── CONTROLES ────────────────────────────────────────────────────
    ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([2, 2, 2, 2])

    with ctrl1:
        our_side = st.radio(
            "Tu equipo juega:",
            ["blue", "red"],
            format_func=lambda x: "🔵 Blue Side" if x == "blue" else "🔴 Red Side",
            horizontal=True,
            key="draft_our_side_radio",
        )
        st.session_state.draft_our_side = our_side
        
        first_pick_side = st.radio(
            "Tiene First Pick:",
            ["blue", "red"],
            index=1,
            format_func=lambda x: "🔵 Blue Side" if x == "blue" else "🔴 Red Side",
            horizontal=True,
            key="draft_first_pick_radio",
        )
        st.session_state.draft_first_pick = first_pick_side

    with ctrl2:
        available_patches = engine.available_patches()
        selected_patches = st.multiselect(
            "Patches",
            options=available_patches,
            default=available_patches[:1] if available_patches else [],
            key="draft_patch_select_ms",
        )
        patch_filter = selected_patches if selected_patches else None

    with ctrl3:
        available_leagues = engine.available_leagues()
        selected_leagues = st.multiselect(
            "Ligas",
            options=available_leagues,
            default=[l for l in QUALITY_LEAGUES if l in available_leagues],
            key="draft_leagues_ms",
        )
        st.session_state.draft_leagues = selected_leagues or available_leagues

    with ctrl4:
        st.markdown("<br>", unsafe_allow_html=True)
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("🔄 Reset", key="draft_reset", use_container_width=True, help="Limpia el draft actual sin tocar el cajón Fearless"):
                st.session_state.draft_state = DraftState()
                for s in ["blue", "red"]:
                    for r in ROLE_ORDER:
                        rk = f"reset_{s}_{r}"
                        st.session_state[rk] = st.session_state.get(rk, 0) + 1
                    for i in range(5):
                        rbk = f"reset_ban_{s}_{i}"
                        st.session_state[rbk] = st.session_state.get(rbk, 0) + 1
                st.rerun()
        with col_btn2:
            if st.button("⏭️ Next Game", key="draft_next_game", use_container_width=True, help="Siguiente Partida: Envía picks al cajón Fearless y reinicia el draft"):
                active_picks = [c for c in list(state.blue_picks.values()) + list(state.red_picks.values()) if c]
                if "disabled_champions" not in st.session_state:
                    st.session_state.disabled_champions = set()
                st.session_state.disabled_champions.update(active_picks)
                st.session_state.draft_state = DraftState()
                for s in ["blue", "red"]:
                    for r in ROLE_ORDER:
                        rk = f"reset_{s}_{r}"
                        st.session_state[rk] = st.session_state.get(rk, 0) + 1
                    for i in range(5):
                        rbk = f"reset_ban_{s}_{i}"
                        st.session_state[rbk] = st.session_state.get(rbk, 0) + 1
                st.rerun()

    st.markdown("---")

    # ── CAJÓN FEARLESS ───────────────────────────────────────────────
    if "disabled_champions" not in st.session_state:
        st.session_state.disabled_champions = set()

    disabled = st.session_state.disabled_champions
    n_dis = len(disabled)

    with st.expander(f"🚫 CAJÓN FEARLESS — Campeones Deshabilitados ({n_dis})", expanded=(n_dis > 0)):
        if not disabled:
            st.markdown(
                '<div style="font-size:12px;color:#64748B;">'
                'No hay campeones deshabilitados en esta serie. Cuando termines un mapa, '
                'presiona <b>"⏭️ Next Game"</b> para enviar todos los picks jugados a este cajón '
                'y bloquearlos para el resto de la serie (Bo3/Bo5 Fearless Mode).'
                '</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                '<div style="font-size:12px;color:#94A3B8;margin-bottom:12px;">'
                'Los siguientes campeones han sido bloqueados para el resto de la serie (no pueden seleccionarse ni serán sugeridos):'
                '</div>',
                unsafe_allow_html=True
            )
            
            tag_cols = st.columns(5)
            for idx, c in enumerate(sorted(list(disabled))):
                with tag_cols[idx % 5]:
                    if st.button(f"✕ {c}", key=f"enable_{c}", use_container_width=True, help=f"Habilitar {c} nuevamente"):
                        st.session_state.disabled_champions.remove(c)
                        st.rerun()
            
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🧹 Vaciar Cajón (Reiniciar Serie Bo3/Bo5)", key="clear_fearless", use_container_width=True):
                st.session_state.disabled_champions = set()
                st.rerun()

    st.markdown("---")

    # ── PRO DRAFT TIMELINE PREDICTOR ──────────────────────────────────
    first_pick_side = st.session_state.get("draft_first_pick", "blue")
    
    st.markdown(
        '<div style="font-size:13px;font-weight:800;color:#F59E0B;letter-spacing:1px;margin:15px 0 8px;">'
        '🧠 PRO DRAFT TIMELINE PREDICTOR — Densidad de Elección Histórica</div>',
        unsafe_allow_html=True
    )
    
    with st.container():
        timeline_data = engine.predict_timeline_picks(
            state=state,
            first_pick_side=first_pick_side,
            leagues=st.session_state.draft_leagues,
            patch=patch_filter
        )
        if timeline_data:
            cols = st.columns(10)
            for i, step in enumerate(timeline_data):
                with cols[i]:
                    side = step["side"]
                    color_border = "#3B82F6" if side == "blue" else "#EF4444"
                    bg_color = "rgba(59,130,246,0.03)" if side == "blue" else "rgba(239,68,68,0.03)"
                    
                    # Empezar a construir el HTML completo del card
                    card_html = (
                        f'<div style="background:{bg_color};border:1px solid {color_border}44;'
                        f'border-radius:10px;padding:8px;text-align:center;min-height:175px;'
                        f'box-shadow:0 4px 6px -1px rgba(0,0,0,0.1);height:100%;display:flex;flex-direction:column;justify-content:space-between;">'
                        f'<div>'
                        f'<div style="font-size:10px;font-weight:800;color:#94A3B8;text-transform:uppercase;">Step {i+1}</div>'
                        f'<div style="font-size:11px;font-weight:700;color:{color_border};margin-bottom:6px;">{step["step_name"]}</div>'
                    )
                    
                    if step["status"] == "chosen":
                        champ = step["champion"]
                        url = _champ_icon_url(champ, dd_ver)
                        card_html += (
                            f'<div style="display:flex;justify-content:center;align-items:center;height:100%;margin-top:15px;margin-bottom:10px;">'
                            f'<img src="{url}" style="width:64px;height:64px;border-radius:10px;border:3px solid {color_border};box-shadow:0 0 12px {color_border}66;" title="{champ}" />'
                            f'</div>'
                        )
                    else:
                        preds = step["predictions"]
                        if preds:
                            top_role = preds[0]["role"]
                            card_html += (
                                f'<div style="display:flex;justify-content:center;margin-top:8px;margin-bottom:10px;">'
                                f'<div style="border:1px dashed {color_border}88;border-radius:6px;padding:4px 12px;font-size:12px;font-weight:800;color:{color_border};background:rgba(255,255,255,0.02);letter-spacing:0.5px;">'
                                f'{top_role}'
                                f'</div>'
                                f'</div>'
                            )
                            
                            card_html += '<div style="text-align:left;font-size:9px;line-height:1.2;margin-top:4px;">'
                            for idx, p in enumerate(preds[:3]):
                                p_role = p["role"]
                                p_pct = p["percentage"] * 100
                                highlight = "color:#4ADE80;font-weight:700;" if idx == 0 else "color:#94A3B8;"
                                card_html += (
                                    f'<div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px;" title="{p_role} ({p_pct:.0f}%)">'
                                    f'<span style="{highlight}">{idx+1}. {p_role}</span> '
                                    f'<span style="color:#64748B;font-size:8px;">{p_pct:.0f}%</span>'
                                    f'</div>'
                                )
                            card_html += '</div>'
                        else:
                            card_html += f'<div style="font-size:9px;color:#475569;margin-top:16px;">Sin datos</div>'
                            
                    card_html += '</div></div>'
                    st.markdown(card_html, unsafe_allow_html=True)

    # ── BOARD ────────────────────────────────────────────────────────
    board_blue, board_red = st.columns(2)

    with board_blue:
        _render_side_board("blue", state, all_champs, dd_ver)

    with board_red:
        _render_side_board("red", state, all_champs, dd_ver)

    # ── BANS ROW ─────────────────────────────────────────────────────
    st.markdown('<div style="font-size:13px;font-weight:800;color:#94A3B8;letter-spacing:1px;margin-bottom:8px;">❌ CONFIGURACIÓN DE BANS (3+2 FASES)</div>', unsafe_allow_html=True)
    ban_b_col, ban_r_col = st.columns(2)

    with ban_b_col:
        st.markdown('<div style="font-size:11px;color:#3B82F6;font-weight:700;margin-bottom:4px;">Bans Blue Side</div>', unsafe_allow_html=True)
        cols_b = st.columns(5)
        for i, col in enumerate(cols_b):
            current_ban = state.blue_bans[i] or "(Vacío)"
            options = ["(Vacío)"] + all_champs
            try:
                idx = options.index(current_ban)
            except ValueError:
                idx = 0

            reset_ban_key = f"reset_ban_blue_{i}"
            if reset_ban_key not in st.session_state:
                st.session_state[reset_ban_key] = 0

            with col:
                if state.blue_bans[i]:
                    url = _champ_icon_url(state.blue_bans[i], dd_ver)
                    st.markdown(f'<div style="text-align:center;margin-bottom:2px;"><img src="{url}" style="width:32px;height:32px;border-radius:4px;border:1px solid #3B82F6;object-fit:cover;" /></div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div style="text-align:center;margin-bottom:2px;"><div style="width:32px;height:32px;border-radius:4px;border:1px dashed rgba(255,255,255,0.1);display:flex;align-items:center;justify-content:center;font-size:9px;color:#475569;margin:0 auto;">B{i+1}</div></div>', unsafe_allow_html=True)
                
                selected_ban = st.selectbox(
                    label=f"sb_ban_blue_{i}",
                    options=options,
                    index=idx,
                    key=f"sb_ban_blue_{i}_widget_{st.session_state[reset_ban_key]}",
                    label_visibility="collapsed"
                )
                if selected_ban != current_ban:
                    st.session_state.draft_state.blue_bans[i] = None if selected_ban == "(Vacío)" else selected_ban
                    st.rerun()

    with ban_r_col:
        st.markdown('<div style="font-size:11px;color:#EF4444;font-weight:700;margin-bottom:4px;">Bans Red Side</div>', unsafe_allow_html=True)
        cols_r = st.columns(5)
        for i, col in enumerate(cols_r):
            current_ban = state.red_bans[i] or "(Vacío)"
            options = ["(Vacío)"] + all_champs
            try:
                idx = options.index(current_ban)
            except ValueError:
                idx = 0

            reset_ban_key = f"reset_ban_red_{i}"
            if reset_ban_key not in st.session_state:
                st.session_state[reset_ban_key] = 0

            with col:
                if state.red_bans[i]:
                    url = _champ_icon_url(state.red_bans[i], dd_ver)
                    st.markdown(f'<div style="text-align:center;margin-bottom:2px;"><img src="{url}" style="width:32px;height:32px;border-radius:4px;border:1px solid #EF4444;object-fit:cover;" /></div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div style="text-align:center;margin-bottom:2px;"><div style="width:32px;height:32px;border-radius:4px;border:1px dashed rgba(255,255,255,0.1);display:flex;align-items:center;justify-content:center;font-size:9px;color:#475569;margin:0 auto;">B{i+1}</div></div>', unsafe_allow_html=True)
                
                selected_ban = st.selectbox(
                    label=f"sb_ban_red_{i}",
                    options=options,
                    index=idx,
                    key=f"sb_ban_red_{i}_widget_{st.session_state[reset_ban_key]}",
                    label_visibility="collapsed"
                )
                if selected_ban != current_ban:
                    st.session_state.draft_state.red_bans[i] = None if selected_ban == "(Vacío)" else selected_ban
                    st.rerun()

    st.markdown("---")

    # ── ANÁLISIS ─────────────────────────────────────────────────────
    leagues     = st.session_state.draft_leagues
    our_picks   = state.known_picks(our_side)
    rival_picks = state.known_picks("red" if our_side == "blue" else "blue")

    active_bans = [b for b in state.blue_bans + state.red_bans if b]
    has_any_pick_or_ban = bool(our_picks or rival_picks or active_bans)

    if not has_any_pick_or_ban:
        st.info("Asigná al menos un pick o ban para ver el análisis contextual.", icon="🎯")
        return

    with st.spinner("Consultando base de datos pro..."):
        result = engine.query(
            state=state,
            our_side=our_side,
            leagues=leagues,
            patch=patch_filter,
            patch_tolerance=1,
            min_games=3,
            top_n=10,
        )

    # KPIs
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Partidas similares", result.n_matching_games)
    with k2:
        wr_str = f"{result.wr_compo_completa*100:.0f}%" if result.wr_compo_completa is not None else "—"
        st.metric("WR compo completa", wr_str)
    with k3:
        top_league = max(result.leagues_distribution, key=result.leagues_distribution.get, default="—")
        st.metric("Liga predominante", top_league)
    with k4:
        patches_str = ", ".join(result.patches_found[:3]) if result.patches_found else "—"
        st.metric("Patches", patches_str)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── PERFIL DE PICK ORDER (BLIND VS COUNTER & MATCHUP) ──────────────────────
    blue_selected = {r: c for r, c in state.blue_picks.items() if c}
    red_selected = {r: c for r, c in state.red_picks.items() if c}

    if blue_selected or red_selected:
        st.markdown(
            '<div style="background:linear-gradient(90deg,#0f172a,#1e293b);border:1px solid rgba(251,191,36,0.25);'
            'border-radius:12px;padding:16px;margin-bottom:20px;">'
            '<div style="font-size:14px;font-weight:800;color:#FBBF24;letter-spacing:1px;'
            'margin-bottom:12px;">⚡ PERFIL TÁCTICO DE PICKS (Riesgo Blind vs Enfrentamiento)</div>',
            unsafe_allow_html=True
        )

        col_b_p, col_r_p = st.columns(2)

        with col_b_p:
            st.markdown('<div style="font-size:11px;font-weight:700;color:#3B82F6;margin-bottom:8px;">Picks Aliados / Blue</div>', unsafe_allow_html=True)
            if not blue_selected:
                st.caption("Sin picks seleccionados en Blue Side.")
            for r, champ in blue_selected.items():
                stats = engine.get_champion_pick_order_stats(champ, r, leagues, patch=patch_filter)
                if stats:
                    wr_blind = stats["win_rate_blind"]
                    n_b = stats["n_blind"]
                    color_b_wr = "#4ADE80" if wr_blind >= 0.5 else "#F87171"

                    rival_champ = red_selected.get(r)
                    matchup = engine.get_champion_matchup_stats(champ, rival_champ, r, leagues, patch=patch_filter) if rival_champ else None
                    
                    if matchup and matchup["win_rate"] is not None:
                        wr_m = matchup["win_rate"]
                        n_m = matchup["n_games"]
                        color_m_wr = "#4ADE80" if wr_m >= 0.5 else "#F87171"
                        matchup_html = f'<div><span style="color:#94A3B8;">Vs {rival_champ}:</span> <span style="font-weight:700;color:{color_m_wr};">{wr_m*100:.0f}%</span> <span style="font-size:9px;color:#475569;">({n_m}p)</span></div>'
                    else:
                        matchup_html = f'<div><span style="color:#94A3B8;">Vs Rival:</span> <span style="font-weight:700;color:#64748B;">—</span></div>'

                    url = _champ_icon_url(champ, dd_ver)
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:12px;background:rgba(255,255,255,0.02);'
                        f'border:1px solid rgba(255,255,255,0.05);border-radius:8px;padding:8px 12px;margin-bottom:6px;">'
                        f'<img src="{url}" style="width:36px;height:36px;border-radius:6px;border:1px solid #3B82F6;">'
                        f'<div style="flex:1;">'
                        f'  <div style="font-size:13px;font-weight:700;color:#F1F5F9;">{champ} <span style="font-size:9px;color:#64748B;">{r}</span></div>'
                        f'  <div style="display:flex;gap:10px;font-size:11px;margin-top:2px;">'
                        f'    <div><span style="color:#94A3B8;">Como Blind:</span> <span style="font-weight:700;color:{color_b_wr};">{wr_blind*100:.0f}%</span> <span style="font-size:9px;color:#475569;">({n_b}p)</span></div>'
                        f'    {matchup_html}'
                        f'  </div>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

        with col_r_p:
            st.markdown('<div style="font-size:11px;font-weight:700;color:#EF4444;margin-bottom:8px;">Picks Enemigos / Red</div>', unsafe_allow_html=True)
            if not red_selected:
                st.caption("Sin picks seleccionados en Red Side.")
            for r, champ in red_selected.items():
                stats = engine.get_champion_pick_order_stats(champ, r, leagues, patch=patch_filter)
                if stats:
                    wr_blind = stats["win_rate_blind"]
                    n_b = stats["n_blind"]
                    color_b_wr = "#4ADE80" if wr_blind >= 0.5 else "#F87171"

                    rival_champ = blue_selected.get(r)
                    matchup = engine.get_champion_matchup_stats(champ, rival_champ, r, leagues, patch=patch_filter) if rival_champ else None
                    
                    if matchup and matchup["win_rate"] is not None:
                        wr_m = matchup["win_rate"]
                        n_m = matchup["n_games"]
                        color_m_wr = "#4ADE80" if wr_m >= 0.5 else "#F87171"
                        matchup_html = f'<div><span style="color:#94A3B8;">Vs {rival_champ}:</span> <span style="font-weight:700;color:{color_m_wr};">{wr_m*100:.0f}%</span> <span style="font-size:9px;color:#475569;">({n_m}p)</span></div>'
                    else:
                        matchup_html = f'<div><span style="color:#94A3B8;">Vs Rival:</span> <span style="font-weight:700;color:#64748B;">—</span></div>'

                    url = _champ_icon_url(champ, dd_ver)
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:12px;background:rgba(255,255,255,0.02);'
                        f'border:1px solid rgba(255,255,255,0.05);border-radius:8px;padding:8px 12px;margin-bottom:6px;">'
                        f'<img src="{url}" style="width:36px;height:36px;border-radius:6px;border:1px solid #EF4444;">'
                        f'<div style="flex:1;">'
                        f'  <div style="font-size:13px;font-weight:700;color:#F1F5F9;">{champ} <span style="font-size:9px;color:#64748B;">{r}</span></div>'
                        f'  <div style="display:flex;gap:10px;font-size:11px;margin-top:2px;">'
                        f'    <div><span style="color:#94A3B8;">Como Blind:</span> <span style="font-weight:700;color:{color_b_wr};">{wr_blind*100:.0f}%</span> <span style="font-size:9px;color:#475569;">({n_b}p)</span></div>'
                        f'    {matchup_html}'
                        f'  </div>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

    # Paneles de análisis (Swapped columns! Left: NEXT PICKS, Right: RESPUESTAS PRO)
    left_panel, right_panel = st.columns(2)

    with left_panel:
        color_our = "🔵" if our_side == "blue" else "🔴"
        st.markdown(
            f'<div style="font-size:13px;font-weight:800;color:#3B82F6;'
            f'letter-spacing:2px;text-transform:uppercase;margin-bottom:10px;">'
            f'{color_our} NEXT PICKS — Sugerencias para tus líneas</div>',
            unsafe_allow_html=True,
        )
        empty_roles = state.empty_roles(our_side)
        if not empty_roles:
            st.success("✅ Draft completo en tu lado.", icon="🎉")
        elif result.next_picks:
            for role in empty_roles:
                raw_s = result.next_picks.get(role, [])
                suggestions = [s for s in raw_s if s.champion not in disabled]
                if not suggestions:
                    continue
                icon = ROLE_ICONS.get(role, "")
                st.markdown(
                    f'<div style="font-size:12px;font-weight:800;color:#94A3B8;'
                    f'letter-spacing:1px;margin:10px 0 4px;">{icon} {role}</div>',
                    unsafe_allow_html=True,
                )
                
                scroll_html = (
                    f'<div style="max-height:210px; overflow-y:auto; padding-right:6px; '
                    f'margin-bottom:12px; display:flex; flex-direction:column; gap:4px; '
                    f'scrollbar-width: thin; scrollbar-color: rgba(255,255,255,0.15) transparent;">'
                )
                for s in suggestions:
                    wr_color = "#4ADE80" if s.win_rate >= 0.5 else "#F87171"
                    champ_url = _champ_icon_url(s.champion, dd_ver)
                    top_lg = max(s.leagues, key=s.leagues.get, default="") if s.leagues else ""
                    
                    blind_stats = engine.get_champion_pick_order_stats(s.champion, role, leagues, patch=patch_filter)
                    blind_wr_str = f"{blind_stats['win_rate_blind']*100:.0f}%" if (blind_stats and "win_rate_blind" in blind_stats and blind_stats['n_blind'] > 0) else "—"
                    
                    scroll_html += (
                        f'<div style="display:flex;align-items:center;gap:8px;'
                        f'background:rgba(59,130,246,0.05);border-radius:8px;'
                        f'padding:6px 10px; flex-shrink:0;">'
                        f'<img src="{champ_url}" style="width:30px;height:30px;border-radius:4px;">'
                        f'<div style="flex:1;font-size:13px;font-weight:700;color:#E2E8F0;">{s.champion}</div>'
                        f'<div style="font-size:10px;background:rgba(255,255,255,0.05);border-radius:4px;padding:2px 6px;color:#94A3B8;white-space:nowrap;font-weight:600;" title="Win Rate como Blind Pick">B: {blind_wr_str}</div>'
                        f'<div style="font-size:13px;font-weight:800;color:{wr_color};">{s.win_rate*100:.0f}%</div>'
                        f'<div style="font-size:11px;color:#475569;">{s.n_games}p</div>'
                        f'<div style="font-size:10px;color:#334155;white-space:nowrap;">{top_lg}</div>'
                        f'</div>'
                    )
                scroll_html += '</div>'
                st.markdown(scroll_html, unsafe_allow_html=True)
        else:
            st.caption("Sin datos para sugerencias con este estado de draft.")

    with right_panel:
        rival_side = "red" if our_side == "blue" else "blue"
        color_rival = "🔴" if rival_side == "red" else "🔵"
        st.markdown(
            f'<div style="font-size:13px;font-weight:800;color:#F59E0B;'
            f'letter-spacing:2px;text-transform:uppercase;margin-bottom:10px;">'
            f'{color_rival} RESPUESTAS PRO — Qué pickeó el rival</div>',
            unsafe_allow_html=True,
        )
        
        # Omitimos roles que ya fueron seleccionados por el rival
        rival_picks = state.known_picks(rival_side)
        empty_rival_roles = [r for r in ROLE_ORDER if r not in rival_picks]
        
        if not empty_rival_roles:
            st.success("✅ Rival completó todos sus picks.", icon="🎉")
        elif result.counter_picks:
            for role in empty_rival_roles:
                raw_s = result.counter_picks.get(role, [])
                suggestions = [cp for cp in raw_s if cp.champion not in disabled]
                if not suggestions:
                    continue
                icon = ROLE_ICONS.get(role, "")
                st.markdown(
                    f'<div style="font-size:12px;font-weight:800;color:#94A3B8;'
                    f'letter-spacing:1px;margin:10px 0 4px;">{icon} {role}</div>',
                    unsafe_allow_html=True,
                )
                
                scroll_html = (
                    f'<div style="max-height:210px; overflow-y:auto; padding-right:6px; '
                    f'margin-bottom:12px; display:flex; flex-direction:column; gap:4px; '
                    f'scrollbar-width: thin; scrollbar-color: rgba(255,255,255,0.15) transparent;">'
                )
                for cp in suggestions:
                    wr_color = "#4ADE80" if cp.win_rate >= 0.5 else "#F87171"
                    champ_url = _champ_icon_url(cp.champion, dd_ver)
                    top_lg = max(cp.leagues, key=cp.leagues.get, default="") if cp.leagues else ""
                    
                    blind_stats = engine.get_champion_pick_order_stats(cp.champion, role, leagues, patch=patch_filter)
                    blind_wr_str = f"{blind_stats['win_rate_blind']*100:.0f}%" if (blind_stats and "win_rate_blind" in blind_stats and blind_stats['n_blind'] > 0) else "—"
                    
                    scroll_html += (
                        f'<div style="display:flex;align-items:center;gap:8px;'
                        f'background:rgba(251,191,36,0.05);border-radius:8px;'
                        f'padding:6px 10px; flex-shrink:0;">'
                        f'<img src="{champ_url}" style="width:30px;height:30px;border-radius:4px;">'
                        f'<div style="flex:1;font-size:13px;font-weight:700;color:#E2E8F0;">{cp.champion}</div>'
                        f'<div style="font-size:10px;background:rgba(255,255,255,0.05);border-radius:4px;padding:2px 6px;color:#94A3B8;white-space:nowrap;font-weight:600;" title="Win Rate como Blind Pick">B: {blind_wr_str}</div>'
                        f'<div style="font-size:13px;font-weight:800;color:{wr_color};">{cp.win_rate*100:.0f}%</div>'
                        f'<div style="font-size:11px;color:#475569;">{cp.n_games}p</div>'
                        f'<div style="font-size:10px;color:#334155;white-space:nowrap;">{top_lg}</div>'
                        f'</div>'
                    )
                scroll_html += '</div>'
                st.markdown(scroll_html, unsafe_allow_html=True)
        else:
            st.caption("Sin datos suficientes para este estado de draft.")
