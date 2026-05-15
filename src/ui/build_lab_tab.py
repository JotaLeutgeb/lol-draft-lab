"""
build_lab_tab.py — UI del Build Lab para Challenger Protocol.

Herramienta standalone que consulta data de builds profesionales (gol.gg)
sin cruzar con datos del equipo propio. Solo fuente pro.
"""
from __future__ import annotations

import streamlit as st
import requests

from src.build_engine import BuildEngine, BuildQueryResult, STARTER_OR_CONSUMABLE

ROLE_ORDER   = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
ROLE_ICONS   = {"TOP": "🛡️", "JUNGLE": "🌿", "MID": "⚡", "BOT": "🏹", "SUPPORT": "💎"}
QUALITY_LEAGUES = ["LCK", "LES", "LCS", "LEC", "CBLOL", "VCS", "PCS", "LLA"]

WR_GREEN = "#4ADE80"
WR_RED   = "#F87171"


@st.cache_data(ttl=3600, show_spinner=False)
def _get_all_champions(dd_ver: str) -> list[str]:
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


@st.cache_data(ttl=3600, show_spinner=False)
def _item_icon_url(item_name: str) -> str:
    """
    Intenta construir URL del ícono del ítem desde DDragon.
    Los ítems no tienen un slug fácil → usamos un placeholder si no tenemos el ID.
    """
    # DDragon sirve ítems por ID numérico, no por nombre.
    # Para esto usaríamos el endpoint items.json que mapea nombre→ID.
    # Por simplicidad mostramos texto con ícono genérico de ítem.
    return ""


@st.cache_data(ttl=7200, show_spinner=False)
def _get_item_id_map(dd_ver: str) -> dict[str, str]:
    """Descarga y cachea el mapa nombre de ítem → ID para construir URLs de íconos."""
    try:
        url = f"https://ddragon.leagueoflegends.com/cdn/{dd_ver}/data/en_US/item.json"
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", {})
            # key = ID numérico, value.name = nombre
            return {v["name"]: k for k, v in data.items()}
    except Exception:
        pass
    return {}


def _item_ddragon_url(item_name: str, item_map: dict[str, str], dd_ver: str) -> str:
    item_id = item_map.get(item_name)
    if item_id:
        return f"https://ddragon.leagueoflegends.com/cdn/{dd_ver}/img/item/{item_id}.png"
    return ""


def _wr_color(wr: float) -> str:
    return WR_GREEN if wr >= 0.5 else WR_RED


def _init_state():
    defaults = {
        "bl_champion": None,
        "bl_leagues": list(QUALITY_LEAGUES),
        "bl_patches": [],
        "bl_role": "Todos",
        "bl_allies": [],
        "bl_rivals": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ─────────────────────────────────────────────────────────────────────
# RENDER PRINCIPAL
# ─────────────────────────────────────────────────────────────────────

def render_build_lab(engine: BuildEngine, dd_ver: str):
    _init_state()

    # ── HEADER ──────────────────────────────────────────────────────
    st.markdown(
        '<div style="background:linear-gradient(90deg,#1a1f2e,#1e2d1a);'
        'border-radius:16px;padding:20px 28px;margin-bottom:20px;'
        'border:1px solid rgba(74,222,128,0.3);">'
        '<h2 style="margin:0;color:#FFF;font-size:26px;font-weight:800;">🛠️ Build Lab</h2>'
        '<p style="margin:4px 0 0;color:#64748B;font-size:13px;letter-spacing:1px;">'
        'BUILDS PROFESIONALES · SOLO DATA PRO (gol.gg)</p>'
        '</div>',
        unsafe_allow_html=True,
    )

    if not engine.is_ready():
        st.warning(
            "⚠️ No hay datos de builds pro. Ejecuta primero: "
            "`python sync_pro_builds.py`",
            icon="📥",
        )
        return

    all_champs = _get_all_champions(dd_ver)
    item_map   = _get_item_id_map(dd_ver)

    # Completar con campeones que están en el parquet pero no en DDragon (por si acaso)
    engine_champs = engine.available_champions()
    all_champs_merged = sorted(set(all_champs) | set(engine_champs))

    # ── CONTROLES ────────────────────────────────────────────────────
    ctrl1, ctrl2, ctrl3 = st.columns([3, 2, 2])

    with ctrl1:
        champion_options = ["(Seleccionar campeón)"] + all_champs_merged
        current_champ = st.session_state.bl_champion or "(Seleccionar campeón)"
        try:
            champ_idx = champion_options.index(current_champ)
        except ValueError:
            champ_idx = 0

        selected_champ = st.selectbox(
            "⚔️ Campeón",
            options=champion_options,
            index=champ_idx,
            key="bl_champ_select",
        )
        st.session_state.bl_champion = None if selected_champ == "(Seleccionar campeón)" else selected_champ

    with ctrl2:
        available_leagues = engine.available_leagues()
        sel_leagues = st.multiselect(
            "🏆 Ligas",
            options=available_leagues,
            default=[l for l in QUALITY_LEAGUES if l in available_leagues],
            key="bl_leagues_ms",
        )
        st.session_state.bl_leagues = sel_leagues or available_leagues

    with ctrl3:
        available_patches = engine.available_patches()
        sel_patches = st.multiselect(
            "🔖 Patches",
            options=available_patches,
            default=[],
            key="bl_patches_ms",
            placeholder="Todos los patches",
        )
        st.session_state.bl_patches = sel_patches or None

    # Segunda fila de controles
    ctrl4, ctrl5, ctrl6 = st.columns([1, 2.5, 2.5])

    with ctrl4:
        # Calcular roles disponibles y default para el campeón seleccionado
        if st.session_state.bl_champion:
            # Query rápido para ver roles
            c_lower = st.session_state.bl_champion.lower()
            df_c = engine.df[engine.df["champion"].str.lower() == c_lower]
            if st.session_state.bl_leagues:
                df_c = df_c[df_c["league"].isin(st.session_state.bl_leagues)]
            
            role_counts = df_c["role"].str.upper().value_counts().to_dict()
            # Solo roles con games
            available_roles_for_champ = [r for r in ROLE_ORDER if r in role_counts]
            
            if not available_roles_for_champ:
                role_options = ["Todos"]
                default_idx = 0
            else:
                role_options = available_roles_for_champ
                # El más frecuente
                top_role = max(role_counts, key=role_counts.get)
                try:
                    default_idx = role_options.index(top_role)
                except ValueError:
                    default_idx = 0
        else:
            role_options = ["Todos"] + ROLE_ORDER
            default_idx = 0

        # Si el rol guardado no está en las opciones, resetear al default
        current_sel_role = st.session_state.get("bl_role", "Todos")
        if current_sel_role not in role_options:
            st.session_state.bl_role = role_options[default_idx]

        sel_role = st.selectbox(
            "📍 Rol",
            options=role_options,
            index=role_options.index(st.session_state.bl_role) if st.session_state.bl_role in role_options else default_idx,
            key="bl_role_selectbox",
        )
        st.session_state.bl_role = sel_role

    with ctrl5:
        ally_options = [c for c in all_champs_merged if c != st.session_state.bl_champion]
        sel_allies = st.multiselect(
            "🤝 Aliados (mismo equipo)",
            options=ally_options,
            default=st.session_state.bl_allies,
            max_selections=4,
            key="bl_allies_ms",
            placeholder="Agregar aliado...",
        )
        st.session_state.bl_allies = sel_allies

    with ctrl6:
        rival_options = [c for c in all_champs_merged if c != st.session_state.bl_champion]
        sel_rivals = st.multiselect(
            "⚔️ Rivales (equipo contrario)",
            options=rival_options,
            default=st.session_state.bl_rivals,
            max_selections=5,
            key="bl_rivals_ms",
            placeholder="Agregar rival...",
        )
        st.session_state.bl_rivals = sel_rivals

    st.markdown("---")

    # ── VALIDACIÓN ───────────────────────────────────────────────────
    champion = st.session_state.bl_champion
    if not champion:
        st.info("👆 Seleccioná un campeón para ver sus builds profesionales.", icon="⚔️")
        return

    # ── CONSULTA ─────────────────────────────────────────────────────
    role_filter = None if sel_role == "Todos" else sel_role

    with st.spinner(f"Consultando builds de {champion} en data pro..."):
        result: BuildQueryResult = engine.query_champion(
            champion=champion,
            leagues=st.session_state.bl_leagues,
            patches=st.session_state.bl_patches,
            role=role_filter,
            allies=st.session_state.bl_allies or None,
            rivals=st.session_state.bl_rivals or None,
            top_builds=5,
            top_context=15,
        )

    # ── HEADER DE RESULTADOS ──────────────────────────────────────────
    _render_champion_header(champion, result, dd_ver)

    if result.n_games == 0:
        st.warning(
            f"Sin datos para {champion} con los filtros seleccionados. "
            "Probá ampliar los patches o ligas, o reducir los filtros de aliados/rivales.",
            icon="🔍"
        )
        return

    # ── PANEL DE BUILDS ─────────────────────────────────────────────
    _render_builds_panel(result, item_map, dd_ver)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("---")
    
    # ── BLOQUE DE CONTEXTO TÁCTICO (ANCHO COMPLETO) ──────────────────
    _render_tactical_context_block(result, dd_ver)


# ─────────────────────────────────────────────────────────────────────
# COMPONENTES
# ─────────────────────────────────────────────────────────────────────

def _render_champion_header(champion: str, result: BuildQueryResult, dd_ver: str):
    """Renderiza el header con ícono del campeón y KPIs principales."""
    wr = result.win_rate
    wr_color = _wr_color(wr)

    # Rol más frecuente
    top_role = max(result.role_dist, key=result.role_dist.get, default="?") if result.role_dist else "?"
    role_icon = ROLE_ICONS.get(top_role, "")

    # Ligas con más data
    top_leagues = sorted(result.leagues_dist.items(), key=lambda x: x[1], reverse=True)[:3]
    leagues_str = " · ".join(f"{l} ({n})" for l, n in top_leagues) if top_leagues else "—"

    champ_url = _champ_icon_url(champion, dd_ver)

    st.markdown(
        f'<div style="background:rgba(255,255,255,0.02);border:1px solid rgba(74,222,128,0.2);'
        f'border-radius:14px;padding:16px 20px;margin-bottom:20px;display:flex;align-items:center;gap:20px;">'
        f'<img src="{champ_url}" style="width:72px;height:72px;border-radius:12px;'
        f'border:2px solid rgba(74,222,128,0.5);object-fit:cover;">'
        f'<div style="flex:1;">'
        f'  <div style="font-size:22px;font-weight:800;color:#F1F5F9;">{champion}</div>'
        f'  <div style="font-size:13px;color:#64748B;margin-top:2px;">'
        f'    {role_icon} {top_role} · {leagues_str}'
        f'  </div>'
        f'</div>'
        f'<div style="display:flex;gap:24px;">'
        f'  <div style="text-align:center;">'
        f'    <div style="font-size:28px;font-weight:900;color:{wr_color};">{wr*100:.0f}%</div>'
        f'    <div style="font-size:10px;color:#64748B;font-weight:700;letter-spacing:1px;">WIN RATE</div>'
        f'  </div>'
        f'  <div style="text-align:center;">'
        f'    <div style="font-size:28px;font-weight:900;color:#F1F5F9;">{result.n_games}</div>'
        f'    <div style="font-size:10px;color:#64748B;font-weight:700;letter-spacing:1px;">PARTIDAS</div>'
        f'  </div>'
        f'  <div style="text-align:center;">'
        f'    <div style="font-size:28px;font-weight:900;color:#F1F5F9;">{len(result.leagues_dist)}</div>'
        f'    <div style="font-size:10px;color:#64748B;font-weight:700;letter-spacing:1px;">LIGAS</div>'
        f'  </div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Distribución de roles (si el campeón se juega en varios roles)
    if len(result.role_dist) > 1:
        total = sum(result.role_dist.values())
        role_html = '<div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;">'
        for role, n in sorted(result.role_dist.items(), key=lambda x: x[1], reverse=True):
            pct = n / total * 100
            icon = ROLE_ICONS.get(role, "")
            role_html += (
                f'<div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);'
                f'border-radius:8px;padding:4px 12px;font-size:12px;font-weight:700;color:#94A3B8;">'
                f'{icon} {role} <span style="color:#F1F5F9;">{pct:.0f}%</span>'
                f'</div>'
            )
        role_html += '</div>'
        st.markdown(role_html, unsafe_allow_html=True)

def _render_role_scrollable_list(role: str, champs_freq: list, dd_ver: str, color: str, side_label: str):
    """Renderiza una lista scrolleable al estilo Draft Lab para un rol específico."""
    icon = ROLE_ICONS.get(role, "")
    st.markdown(
        f'<div style="font-size:12px;font-weight:800;color:#94A3B8;'
        f'letter-spacing:1px;margin:12px 0 6px;">{icon} {role} {side_label}</div>',
        unsafe_allow_html=True,
    )
    
    if not champs_freq:
        st.markdown(
            '<div style="font-size:11px;color:#475569;padding:10px;background:rgba(255,255,255,0.01);'
            'border-radius:8px;border:1px dashed rgba(255,255,255,0.05);">Sin datos</div>',
            unsafe_allow_html=True
        )
        return

    scroll_html = (
        f'<div style="max-height:200px; overflow-y:auto; padding-right:6px; '
        f'margin-bottom:12px; display:flex; flex-direction:column; gap:4px; '
        f'scrollbar-width: thin; scrollbar-color: {color}44 transparent;">'
    )
    
    for champ, stats in champs_freq:
        wr_color = _wr_color(stats["wr"])
        champ_url = _champ_icon_url(champ, dd_ver)
        
        scroll_html += (
            f'<div style="display:flex;align-items:center;gap:8px;'
            f'background:rgba({("59,130,246" if color=="#3B82F6" else "239,68,68")},0.05);border-radius:8px;'
            f'padding:6px 10px; flex-shrink:0; border:1px solid {color}11;">'
            f'<img src="{champ_url}" style="width:28px;height:28px;border-radius:4px;border:1px solid {color}22;">'
            f'<div style="flex:1;font-size:12px;font-weight:700;color:#E2E8F0;">{champ}</div>'
            f'<div style="font-size:12px;font-weight:800;color:{wr_color};">{stats["wr"]*100:.0f}%</div>'
            f'<div style="font-size:10px;color:#475569;">{stats["n"]}p</div>'
            f'</div>'
        )
    scroll_html += '</div>'
    st.markdown(scroll_html, unsafe_allow_html=True)


def _aggregate_role_freq(comps: list[dict]):
    """Agrupa frecuencias por rol de una lista de composiciones."""
    role_data = {role: {} for role in ROLE_ORDER}
    for comp in comps:
        win = comp.get("win", False)
        for role in ROLE_ORDER:
            champ = comp.get(role)
            if not champ or "unknown" in champ.lower() or champ == "None":
                continue
            if champ not in role_data[role]:
                role_data[role][champ] = {"n": 0, "wins": 0}
            role_data[role][champ]["n"] += 1
            if win:
                role_data[role][champ]["wins"] += 1
    
    # Calcular WR y ordenar
    result = {}
    for role in ROLE_ORDER:
        sorted_champs = []
        for champ, stats in role_data[role].items():
            stats["wr"] = stats["wins"] / stats["n"]
            sorted_champs.append((champ, stats))
        result[role] = sorted(sorted_champs, key=lambda x: x[1]["n"], reverse=True)[:15]
    return result


def _render_tactical_context_block(result: BuildQueryResult, dd_ver: str):
    """Bloque principal de contexto táctico con 2 columnas (Aliados vs Rivales)."""
    
    selected_rank = st.session_state.get("bl_selected_build")
    
    st.markdown(
        f'<div style="background:linear-gradient(90deg, rgba(59,130,246,0.05), rgba(239,68,68,0.05));'
        f'border-radius:16px; padding:24px; border:1px solid rgba(255,255,255,0.05);">'
        f'<div style="font-size:16px; font-weight:800; color:#F1F5F9; letter-spacing:1px; margin-bottom:20px; text-transform:uppercase;">'
        f'🧠 CONTEXTO TÁCTICO {"DE LA BUILD #"+str(selected_rank) if selected_rank else "GLOBAL"}</div>',
        unsafe_allow_html=True
    )
    
    col_allies, col_enemies = st.columns(2)
    
    if selected_rank:
        build = next((b for b in result.builds if b.rank == selected_rank), None)
        if build:
            # Procesar datos por rol para esta build específica
            ally_role_freq = _aggregate_role_freq(build.ally_comps)
            enemy_role_freq = _aggregate_role_freq(build.enemy_comps)
        else:
            st.error("Error al recuperar los datos de la build.")
            return
    else:
        # Modo GLOBAL: Usamos los datos pre-agregados por el engine
        ally_role_freq = result.ally_role_freq
        enemy_role_freq = result.enemy_role_freq

    with col_allies:
        st.markdown('<div style="color:#3B82F6; font-weight:800; font-size:14px; margin-bottom:10px;">🤝 ALIADOS SUGERIDOS</div>', unsafe_allow_html=True)
        for role in ROLE_ORDER:
            # Ocultar la línea seleccionada en aliados
            if role.upper() == st.session_state.bl_role.upper():
                continue
            _render_role_scrollable_list(role, ally_role_freq.get(role, []), dd_ver, "#3B82F6", "Aliados")
            
    with col_enemies:
        st.markdown('<div style="color:#EF4444; font-weight:800; font-size:14px; margin-bottom:10px;">⚔️ RIVALES FRECUENTES (COUNTERS)</div>', unsafe_allow_html=True)
        for role in ROLE_ORDER:
            _render_role_scrollable_list(role, enemy_role_freq.get(role, []), dd_ver, "#EF4444", "Rivales")
            
    st.markdown('</div>', unsafe_allow_html=True)


def _render_builds_panel(result: BuildQueryResult, item_map: dict, dd_ver: str):
    """Renderiza el panel de builds ordenadas por frecuencia."""
    
    import streamlit.components.v1 as components
    # 1. Listener global (Patrón "Por Partida") para capturar clicks en los divs y disparar botones ocultos
    components.html("""
    <script>
    if (!window.parent.window.buildLabListenerAttached) {
        window.parent.document.addEventListener('click', function(e) {
            let card = e.target.closest('div[id^="build_card_"]');
            if (card) {
                let rankId = card.id.replace('build_card_', '');
                let btns = window.parent.document.querySelectorAll('[data-testid="stButton"] button');
                for (let b of btns) {
                    if (b.innerText.trim() === rankId + "_BL_HIDDEN") {
                        b.click();
                        break;
                    }
                }
            }
        });
        window.parent.window.buildLabListenerAttached = true;
    }
    </script>
    """, height=0)

    # 2. CSS para ocultar botones de trigger y estilo de selección (Patrón "Por Partida")
    st.markdown("""<style>
    /* Ocultar el contenedor del botón que sigue inmediatamente al div de la card */
    div.element-container:has(div[id^="build_card_"]) + div.element-container {
        position: absolute !important;
        opacity: 0 !important;
        pointer-events: none !important;
        height: 0px !important;
        overflow: hidden !important;
    }
    div[id^="build_card_"] {
        cursor: pointer;
        transition: all 0.2s ease-in-out;
    }
    div[id^="build_card_"]:hover { 
        background: rgba(255,255,255,0.06) !important; 
        border-color: rgba(255,255,255,0.15) !important; 
    }
    div[id^="build_card_"].selected { 
        border-color: #4ADE80 !important; 
        background: rgba(74, 222, 128, 0.1) !important; 
        box-shadow: 0 0 15px rgba(74, 222, 128, 0.1);
    }
    </style>""", unsafe_allow_html=True)

    st.markdown(
        '<div style="font-size:13px;font-weight:800;color:#4ADE80;'
        'letter-spacing:1px;margin-bottom:12px;text-transform:uppercase;">'
        '🧱 BUILDS PRO — Ordenadas por frecuencia</div>',
        unsafe_allow_html=True,
    )

    if not result.builds:
        st.caption("Sin builds registradas para esta combinación de filtros.")
        return

    total_games = result.n_games

    for build in result.builds:
        wr = build.win_rate
        wr_color = _wr_color(wr)
        freq_pct = build.n_games / total_games * 100 if total_games > 0 else 0

        # Badge de rank
        rank_colors = {1: "#F59E0B", 2: "#94A3B8", 3: "#CD7F32"}
        rank_color = rank_colors.get(build.rank, "#475569")

        is_selected = st.session_state.get("bl_selected_build") == build.rank
        sel_class = "selected" if is_selected else ""

        # Container de la build
        build_html = (
            f'<div id="build_card_{build.rank}" class="{sel_class}" style="background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);'
            f'border-radius:12px;padding:14px 16px;margin-bottom:10px;">'
            # Header de la build
            f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">'
            f'  <div style="display:flex;align-items:center;gap:10px;">'
            f'    <div style="background:{rank_color};color:#000;font-size:11px;font-weight:900;'
            f'    border-radius:50%;width:22px;height:22px;display:flex;align-items:center;'
            f'    justify-content:center;">#{build.rank}</div>'
            f'    <div style="font-size:12px;font-weight:700;color:#94A3B8;">'
            f'    {build.n_games} partidas · {freq_pct:.0f}% del total</div>'
            f'  </div>'
            f'  <div style="font-size:18px;font-weight:800;color:{wr_color};">{wr*100:.0f}% WR</div>'
            f'</div>'
        )

        # Ítems en secuencia horizontal
        build_html += '<div style="display:flex;align-items:flex-end;gap:6px;flex-wrap:wrap;">'

        for i, item_step in enumerate(build.core_items):
            if not item_step.name:
                continue

            item_url = _item_ddragon_url(item_step.name, item_map, dd_ver)
            min_str = f"{item_step.avg_min:.0f}min" if item_step.avg_min > 0 else ""
            border_color = "#4ADE80" if i < 4 else "rgba(255,255,255,0.12)"

            if item_url:
                img_html = (
                    f'<img src="{item_url}" title="{item_step.name}" '
                    f'style="width:40px;height:40px;border-radius:6px;'
                    f'border:2px solid {border_color};object-fit:cover;">'
                )
            else:
                # Fallback: nombre abreviado como texto
                short_name = item_step.name[:8] + ".." if len(item_step.name) > 10 else item_step.name
                img_html = (
                    f'<div title="{item_step.name}" '
                    f'style="width:40px;height:40px;border-radius:6px;border:2px solid {border_color};'
                    f'background:rgba(255,255,255,0.06);display:flex;align-items:center;'
                    f'justify-content:center;font-size:7px;color:#94A3B8;text-align:center;padding:2px;">'
                    f'{short_name}</div>'
                )

            build_html += (
                f'<div style="display:flex;flex-direction:column;align-items:center;gap:2px;">'
                f'  {img_html}'
                f'  <div style="font-size:9px;color:#475569;white-space:nowrap;">{min_str}</div>'
                f'</div>'
            )

            # Flecha entre ítems core
            if i < len(build.core_items) - 1:
                build_html += (
                    '<div style="font-size:14px;color:#334155;padding-bottom:14px;">→</div>'
                )

        # ── SEPARADOR Y BOTA ──
        if getattr(build, "boot_item", None) and build.boot_item.name:
            # Línea divisoria
            build_html += '<div style="width:1px;height:30px;background:rgba(255,255,255,0.1);margin:0 4px;margin-bottom:14px;"></div>'
            
            b_step = build.boot_item
            b_url = _item_ddragon_url(b_step.name, item_map, dd_ver)
            b_min_str = f"{b_step.avg_min:.0f}min" if b_step.avg_min > 0 else ""
            b_border = "#FBBF24" # Amarillo para destacar la bota final
            
            if b_url:
                b_img_html = f'<img src="{b_url}" title="{b_step.name}" style="width:40px;height:40px;border-radius:6px;border:2px solid {b_border};object-fit:cover;">'
            else:
                b_short = b_step.name[:8] + ".." if len(b_step.name) > 10 else b_step.name
                b_img_html = f'<div title="{b_step.name}" style="width:40px;height:40px;border-radius:6px;border:2px solid {b_border};background:rgba(255,255,255,0.06);display:flex;align-items:center;justify-content:center;font-size:7px;color:#94A3B8;text-align:center;padding:2px;">{b_short}</div>'

            build_html += (
                f'<div style="display:flex;flex-direction:column;align-items:center;gap:2px;">'
                f'  {b_img_html}'
                f'  <div style="font-size:9px;color:#475569;white-space:nowrap;">{b_min_str}</div>'
                f'</div>'
            )

        build_html += '</div></div>'
        
        st.markdown(build_html, unsafe_allow_html=True)
        if st.button(f"{build.rank}_BL_HIDDEN", key=f"btn_build_{build.rank}"):
            if st.session_state.get("bl_selected_build") == build.rank:
                st.session_state.bl_selected_build = None
            else:
                st.session_state.bl_selected_build = build.rank
            st.rerun()




def _render_context_list(entries, dd_ver: str, color: str):
    """Renderiza lista de campeones con ícono, n_games y WR."""
    list_html = '<div style="display:flex;flex-direction:column;gap:4px;">'
    max_n = max(e.n_games for e in entries) if entries else 1

    for entry in entries:
        champ_url = _champ_icon_url(entry.champion, dd_ver)
        wr_color = _wr_color(entry.win_rate)
        bar_width = int(entry.n_games / max_n * 100)

        list_html += (
            f'<div style="display:flex;align-items:center;gap:8px;'
            f'background:rgba(255,255,255,0.02);border-radius:8px;padding:5px 8px;">'
            f'<img src="{champ_url}" style="width:28px;height:28px;border-radius:4px;'
            f'border:1px solid {color}44;object-fit:cover;">'
            f'<div style="flex:1;">'
            f'  <div style="font-size:12px;font-weight:700;color:#E2E8F0;">{entry.champion}</div>'
            f'  <div style="height:3px;background:rgba(255,255,255,0.05);border-radius:2px;margin-top:2px;">'
            f'    <div style="width:{bar_width}%;height:100%;background:{color}66;border-radius:2px;"></div>'
            f'  </div>'
            f'</div>'
            f'<div style="text-align:right;white-space:nowrap;">'
            f'  <div style="font-size:12px;font-weight:800;color:{wr_color};">{entry.win_rate*100:.0f}%</div>'
            f'  <div style="font-size:10px;color:#475569;">{entry.n_games}p</div>'
            f'</div>'
            f'</div>'
        )

    list_html += '</div>'
    st.markdown(list_html, unsafe_allow_html=True)
