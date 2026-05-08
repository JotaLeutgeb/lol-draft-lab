"""
visualization.py — Gráficos para análisis de partidas LoL.

Todas las funciones devuelven objetos `plotly.graph_objects.Figure`.
Esto las hace compatibles con:
  - Streamlit (st.plotly_chart)
  - Exportación a HTML standalone (fig.write_html)
  - Exportación a PNG con kaleido (fig.write_image)

Paleta de colores: azul (#5B9BD5) para equipo, rojo (#E06C75) para rivales.
"""

from __future__ import annotations

import logging
from typing import Optional, Dict
from src import config

import numpy as np
import os
from PIL import Image
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Paleta y tema base
# ──────────────────────────────────────────────────────────────────

TEAM_COLOR   = "#5B9BD5"   # azul equipo
ENEMY_COLOR  = "#E06C75"   # rojo enemigos
WIN_COLOR    = "#98C379"   # verde victorias
LOSS_COLOR   = "#E06C75"   # rojo derrotas
NEUTRAL_COLOR = "#ABB2BF"

ROLE_COLORS = {
    "TOP":     "#E5C07B",
    "JUNGLE":  "#98C379",
    "MID":     "#61AFEF",
    "BOT":     "#C678DD",
    "SUPPORT": "#56B6C2",
    "UNKNOWN": "#ABB2BF",
}

_BASE_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="#1E2030",
    plot_bgcolor="#1E2030",
    font=dict(family="Inter, sans-serif", color="#CDD6F4"),
    margin=dict(l=40, r=40, t=60, b=40),
)


def _apply_base_layout(fig: go.Figure, title: str = "") -> go.Figure:
    fig.update_layout(title=dict(text=title, font=dict(size=16)), **_BASE_LAYOUT)
    return fig


def _add_map_background(fig: go.Figure, opacity: float = 0.6) -> go.Figure:
    """
    Inyecta la imagen de la Grieta del Invocador perfectamente escalada 
    según las constantes de config.py.
    """
    image_source = "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-minimap/global/default/minimap-rendered.png"
    if os.path.exists("map.png"):
        try:
            # Importamos aquí para evitar dependencias pesadas si no se usa
            from PIL import Image
            image_source = Image.open("map.png")
        except Exception as e:
            logger.warning(f"No se pudo cargar map.png: {e}")

    fig.update_xaxes(
        range=[config.MAP_X_MIN, config.MAP_X_MAX], 
        showticklabels=False, showgrid=False, zeroline=False, visible=False
    )
    fig.update_yaxes(
        range=[config.MAP_Y_MIN, config.MAP_Y_MAX], 
        showticklabels=False, showgrid=False, zeroline=False, visible=False, 
        scaleanchor="x", scaleratio=1
    )

    fig.add_layout_image(
        dict(
            source=image_source,
            xref="x", yref="y",
            x=config.MAP_X_MIN,
            y=config.MAP_Y_MAX,
            sizex=config.MAP_X_MAX - config.MAP_X_MIN,
            sizey=config.MAP_Y_MAX - config.MAP_Y_MIN,
            sizing="stretch",
            opacity=opacity,
            layer="below"
        )
    )
    return fig


# ──────────────────────────────────────────────────────────────────
# 1. Evolución de gold en la partida
# ──────────────────────────────────────────────────────────────────

def plot_gold_timeline(
    df_timeline: pd.DataFrame,
    df_participants: pd.DataFrame,
    match_id: str,
) -> go.Figure:
    """
    Muestra la curva de gold total por jugador a lo largo de la partida.

    Args:
        df_timeline:    DataFrame de frames con total_gold y timestamp_min.
        df_participants: Para mapear participant_id → game_name y role.
        match_id:       ID de la partida a visualizar.

    Returns:
        Figura Plotly con una línea por jugador.
    """
    tl = df_timeline[df_timeline["match_id"] == match_id].copy()
    meta = df_participants[df_participants["match_id"] == match_id][
        ["participant_id", "game_name", "role", "team_id", "result"]
    ].drop_duplicates("participant_id")

    if tl.empty or meta.empty:
        fig = go.Figure()
        return _apply_base_layout(fig, f"Sin datos de timeline para {match_id}")

    merged = tl.merge(meta, on="participant_id", how="left")
    fig = go.Figure()

    for _, player_meta in meta.iterrows():
        pid = player_meta["participant_id"]
        pname = player_meta.get("game_name", f"P{pid}")
        role = player_meta.get("role", "")
        team_id = player_meta.get("team_id", 0)

        player_tl = merged[merged["participant_id"] == pid].sort_values("timestamp_min")
        color = ROLE_COLORS.get(role, NEUTRAL_COLOR) if team_id == 100 else NEUTRAL_COLOR
        dash = "solid" if team_id == 100 else "dash"

        fig.add_trace(go.Scatter(
            x=player_tl["timestamp_min"],
            y=player_tl["total_gold"],
            mode="lines",
            name=f"{pname} ({role})" if role else pname,
            line=dict(color=color, dash=dash, width=2),
            hovertemplate=f"<b>{pname}</b><br>Minuto: %{{x:.1f}}<br>Gold: %{{y:,}}<extra></extra>",
        ))

    # Líneas verticales de fases
    for x_val, label in [(20, "Early/Mid"), (30, "Mid/Late")]:
        fig.add_vline(
            x=x_val, line_dash="dot", line_color=NEUTRAL_COLOR, opacity=0.5,
            annotation_text=label, annotation_position="top right",
            annotation_font=dict(color=NEUTRAL_COLOR, size=10),
        )

    return _apply_base_layout(fig, f"Evolución de Gold — {match_id}")


# ──────────────────────────────────────────────────────────────────
# 2. Gold difference por rol
# ──────────────────────────────────────────────────────────────────

def plot_gold_diff_by_role(
    df_gold_diff: pd.DataFrame,
    snapshot_min: int = 15,
) -> go.Figure:
    """
    Barras de diferencia de gold vs oponente por rol, promediadas
    sobre todas las partidas disponibles.

    Args:
        df_gold_diff: Output de compute_gold_diff().
        snapshot_min: Minuto del snapshot a visualizar (5, 10 o 15).

    Returns:
        Figura de barras con valores positivos/negativos.
    """
    col = f"gold_diff_min{snapshot_min}"
    if col not in df_gold_diff.columns or df_gold_diff.empty:
        fig = go.Figure()
        return _apply_base_layout(fig, f"Sin datos de gold diff para min {snapshot_min}")

    agg = df_gold_diff.groupby("role")[col].mean().reset_index()
    agg.columns = ["role", "avg_gold_diff"]
    agg["color"] = agg["avg_gold_diff"].apply(
        lambda x: WIN_COLOR if x >= 0 else LOSS_COLOR
    )

    # Ordenar por rol canónico
    role_order = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
    agg["_order"] = agg["role"].map({r: i for i, r in enumerate(role_order)}).fillna(99)
    agg = agg.sort_values("_order")

    fig = go.Figure(go.Bar(
        x=agg["role"],
        y=agg["avg_gold_diff"],
        marker_color=agg["color"],
        text=agg["avg_gold_diff"].apply(lambda v: f"{v:+,.0f}"),
        textposition="outside",
        name="Tu Equipo",
        hovertemplate="<b>%{x}</b><br>Gold Diff: %{y:+,.0f}<extra></extra>",
    ))
    fig.add_hline(y=0, line_dash="solid", line_color=NEUTRAL_COLOR, line_width=1)

    return _apply_base_layout(fig, f"Diferencia de Gold por Rol @ Min {snapshot_min}")


# ──────────────────────────────────────────────────────────────────
# 3. Comparativa radar por jugador
# ──────────────────────────────────────────────────────────────────

def plot_player_radars(df_summary: pd.DataFrame) -> dict[str, go.Figure]:
    """
    Lista de Radar charts individuales con métricas normalizadas.
    """
    if df_summary.empty:
        return {}

    metrics = [
        ("pilar_combat_efficiency", "Eficiencia Combate"),
        ("pilar_map_pressure",      "Presión de Mapa"),
        ("pilar_tactical_utility",  "Utilidad Táctica"),
        ("pilar_team_synergy",      "Sinergia de Equipo")
    ]
    # Filtramos las métricas que realmente están en el dataframe
    available = [(col, label) for col, label in metrics if col in df_summary.columns]
    if not available:
        # Fallback si por alguna razón no se calcularon los pilares aún
        return {}

    cols, labels = zip(*available)
    labels = list(labels) + [labels[0]]

    # Los pilares ya vienen normalizados 0-1 por partida, usamos el valor directo
    norm_df = df_summary[list(cols)].fillna(0).copy()

    colors = list(ROLE_COLORS.values())
    figs = {}

    for i, (_, row) in enumerate(df_summary.iterrows()):
        values = [float(norm_df.at[row.name, c]) for c in cols]
        values += [values[0]]
        
        role = row.get("role", "")
        color = ROLE_COLORS.get(role, colors[i % len(colors)])
        player_name = row.get("game_name", f"P{i}")

        fig = go.Figure()
        fig.add_trace(go.Scatterpolar(
            r=values,
            theta=labels,
            fill="toself",
            name=player_name,
            line=dict(color=color, width=2),
            fillcolor=color.replace(")", ", 0.4)").replace("rgb", "rgba") if "rgb" in color else color,
            hovertemplate="<b>%{theta}</b>: %{r:.2f}<extra></extra>",
        ))

        fig.update_layout(
            polar=dict(
                bgcolor="rgba(0,0,0,0)",
                radialaxis=dict(visible=False, range=[0, 1]),
                angularaxis=dict(color="#CDD6F4", tickfont=dict(size=11, family="Outfit", weight="bold")),
            ),
            margin=dict(l=40, r=40, t=30, b=30),
            paper_bgcolor="rgba(15, 23, 42, 0.6)", # Azul profundo traslúcido
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
            height=280
        )
        figs[player_name] = fig

    return figs


def plot_ghost_radar(
    player_metrics: Dict[str, float],
    benchmark_percentiles: Dict[str, Dict[str, float]],
    player_name: str,
    role: str,
    region: str = "KR"
) -> go.Figure:
    """
    Gráfico de radar que superpone el rendimiento del jugador sobre un 
    "Ghost Polygon" (Percentil 90 del Benchmark Challenger).
    """
    # Definición de los 4 Pilares del Radar
    metrics = [
        ("pilar_combat_efficiency", "Eficiencia Combate"),
        ("pilar_map_pressure",      "Presión de Mapa"),
        ("pilar_tactical_utility",  "Utilidad Táctica"),
        ("pilar_team_synergy",      "Sinergia de Equipo")
    ]
    
    labels = [m[1] for m in metrics]
    player_vals = []
    ghost_vals = [] # P50 Benchmark (1.0 por definición de escala relativa)
    
    for m_key, _ in metrics:
        p_val = player_metrics.get(m_key, 0)
        
        # Blindaje para listas/series
        if hasattr(p_val, "iloc"): p_val = p_val.iloc[0]
        elif isinstance(p_val, (list, np.ndarray)) and len(p_val) > 0: p_val = p_val[0]
        
        try: p_val = float(p_val)
        except: p_val = 0.0
        
        # OBTENER EL VALOR REAL DE SUPABASE (Soporta float plano o dict)
        b_data = benchmark_percentiles.get(m_key, 0.5)
        p50 = b_data.get("p50", 0.5) if isinstance(b_data, dict) else float(b_data)
        
        if p50 == 0: p50 = 0.001 
        
        player_vals.append(p_val / p50) 
        ghost_vals.append(1.0)

    # Cerrar polígonos
    labels += [labels[0]]
    player_vals += [player_vals[0]]
    ghost_vals += [ghost_vals[0]]

    fig = go.Figure()

    # 1. Área de Referencia Challenger (El "Ghost" base)
    fig.add_trace(go.Scatterpolar(
        r=ghost_vals,
        theta=labels,
        fill="toself",
        name=f"Standard Challenger {region}",
        line=dict(color="rgba(167, 139, 250, 0.4)", width=2, dash="dot"),
        fillcolor="rgba(167, 139, 250, 0.15)",
        hoverinfo="skip"
    ))

    # 2. Hitos de Excelencia (Puntos en 1.0)
    fig.add_trace(go.Scatterpolar(
        r=[1.0] * len(labels),
        theta=labels,
        mode="markers",
        name="Límite Challenger",
        marker=dict(color="#A78BFA", size=8, symbol="diamond-open"),
        showlegend=False,
        hoverinfo="skip"
    ))

    # 3. Desempeño del Jugador (Neon Glow)
    role_color = ROLE_COLORS.get(role, TEAM_COLOR)
    
    # Capa de Brillo
    fig.add_trace(go.Scatterpolar(
        r=player_vals,
        theta=labels,
        line=dict(color=role_color, width=10),
        opacity=0.15,
        showlegend=False,
        hoverinfo="skip"
    ))
    
    # Línea Principal
    fig.add_trace(go.Scatterpolar(
        r=player_vals,
        theta=labels,
        fill="toself",
        name=f"{player_name} ({role})",
        line=dict(color=role_color, width=4),
        fillcolor=role_color.replace(")", ", 0.4)").replace("rgb", "rgba") if "rgb" in role_color else role_color,
        marker=dict(size=10),
        hovertemplate="<b>%{theta}</b><br>Dominancia: %{r:.2f}x vs Elite<extra></extra>"
    ))

    fig.update_layout(
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(
                visible=True, 
                range=[0, max(max(player_vals), 1.5)], 
                gridcolor="rgba(255,255,255,0.1)",
                tickfont=dict(size=10, color="#64748B"),
                tickvals=[0.5, 1.0, 1.5],
                ticktext=["50%", "CHALL", "150%"]
            ),
            angularaxis=dict(
                gridcolor="rgba(255,255,255,0.1)",
                tickfont=dict(size=13, color="#CDD6F4", family="Outfit", weight="bold")
            )
        ),
        paper_bgcolor="rgba(15, 23, 42, 0.6)", # Azul profundo traslúcido
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=60, r=60, t=30, b=30),
        showlegend=True,
        legend=dict(
            orientation="h", 
            yanchor="bottom", 
            y=-0.25, 
            xanchor="center", 
            x=0.5,
            font=dict(color="#CDD6F4", size=11)
        )
    )

    return fig


# ──────────────────────────────────────────────────────────────────
# 4. Distribución de daño por minuto
# ──────────────────────────────────────────────────────────────────

def plot_damage_distribution(df: pd.DataFrame) -> go.Figure:
    """
    Boxplot de damage_per_min por jugador, separado por victoria/derrota.

    Args:
        df: DataFrame de participantes con damage_per_min, game_name, result.

    Returns:
        Figura de boxplots.
    """
    if "damage_per_min" not in df.columns or df.empty:
        fig = go.Figure()
        return _apply_base_layout(fig, "Sin datos de daño")

    df = df.copy()
    df["Resultado"] = df["result"].map({True: "Victoria", False: "Derrota"})
    df["Jugador"] = df.get("game_name", "Desconocido")

    fig = px.box(
        df,
        x="Jugador",
        y="damage_per_min",
        color="Resultado",
        color_discrete_map={"Victoria": WIN_COLOR, "Derrota": LOSS_COLOR},
        points="all",
        hover_data=["champion"] if "champion" in df.columns else None,
    )
    fig.update_traces(jitter=0.3, pointpos=-1.8)
    return _apply_base_layout(fig, "Distribución de Daño por Minuto")


# ──────────────────────────────────────────────────────────────────
# 5. Control de objetivos por partida
# ──────────────────────────────────────────────────────────────────

def plot_objective_control(
    df_objectives: pd.DataFrame,
    df_participants: pd.DataFrame,
) -> go.Figure:
    """
    Heatmap de objetivos tomados por partida (dragons, barons, heralds, towers).

    Args:
        df_objectives:  Output de compute_objective_control().
        df_participants: Para identificar el team_id del equipo analizado.

    Returns:
        Heatmap Plotly.
    """
    if df_objectives.empty:
        fig = go.Figure()
        return _apply_base_layout(fig, "Sin datos de objetivos")

    obj_cols = ["dragons", "barons", "heralds", "towers"]
    available_cols = [c for c in obj_cols if c in df_objectives.columns]

    if not available_cols:
        fig = go.Figure()
        return _apply_base_layout(fig, "Columnas de objetivos no disponibles")

    # Usar match_id como eje Y, objetivos como eje X
    plot_df = df_objectives[["match_id"] + available_cols].copy()
    plot_df = plot_df.set_index("match_id")[available_cols]

    # Agregar columna de result si disponible
    match_results = df_participants.drop_duplicates("match_id")[["match_id", "result"]]
    plot_df = plot_df.join(
        match_results.set_index("match_id")["result"], how="left"
    )

    # Ordenar por resultado y luego por match
    if "result" in plot_df.columns:
        plot_df = plot_df.sort_values("result", ascending=False)
        y_labels = [
            f"{'✅' if r else '❌'} {mid[:10]}"
            for mid, r in zip(plot_df.index, plot_df["result"])
        ]
        plot_df = plot_df[available_cols]
    else:
        y_labels = [mid[:10] for mid in plot_df.index]

    fig = go.Figure(go.Heatmap(
        z=plot_df.values,
        x=available_cols,
        y=y_labels,
        colorscale="Blues",
        text=plot_df.values,
        texttemplate="%{text}",
        hovertemplate="<b>%{y}</b><br>%{x}: %{z}<extra></extra>",
        showscale=True,
    ))

    return _apply_base_layout(fig, "Control de Objetivos por Partida")


# ──────────────────────────────────────────────────────────────────
# 6. Heatmap de posiciones (coordenadas del mapa)
# ──────────────────────────────────────────────────────────────────

def plot_position_heatmap(
    df_timeline: pd.DataFrame,
    df_events: Optional[pd.DataFrame] = None,
    role: Optional[str] = None,
    participant_id: Optional[int] = None,
    match_id: Optional[str] = None,
    timestamp_range: tuple[float, float] = (0, float("inf")),
) -> go.Figure:
    """
    Heatmap de densidad de posiciones en el mapa de LoL.

    El mapa de LoL va de (0,0) a (14820, 14881) en coordenadas de la API.

    Args:
        df_timeline:     DataFrame con pos_x, pos_y, participant_id.
        role:            Filtrar por rol (si hay merge con meta previamente).
        participant_id:  Filtrar por jugador específico.
        match_id:        Filtrar por partida.
        timestamp_range: Rango de minutos (ej: (0, 15) para early).

    Returns:
        Figura de densidad 2D.
    """
    df = df_timeline.copy()

    if match_id:
        df = df[df["match_id"] == match_id]
    if participant_id:
        df = df[df["participant_id"] == participant_id]

    t_min, t_max = timestamp_range
    df = df[(df["timestamp_min"] >= t_min) & (df["timestamp_min"] <= t_max)]

    if df.empty or "pos_x" not in df.columns:
        fig = go.Figure()
        return _apply_base_layout(fig, "Sin datos de posición")

    # Filtramos posiciones nulas o de spawn (0,0)
    df = df[(df["pos_x"] > 500) | (df["pos_y"] > 500)]

    # ────── INYECCIÓN DE TRAYECTORIA HOMOGÉNEA AL HEATMAP ──────
    # Unimos los "Snapshots" (minuto a minuto) con los "Eventos" (precisión milimétrica)
    # para usar los eventos (Wards, Kills) como anclas de ruta y generar curvas realistas 
    # en lugar de líneas rectas de 1 minuto entero.
    path_nodes = []
    for _, row in df.iterrows():
        path_nodes.append({
            "participant_id": row["participant_id"],
            "timestamp_min": row["timestamp_min"],
            "pos_x": row["pos_x"],
            "pos_y": row["pos_y"],
        })
        
    df_ev_ref = pd.DataFrame()
    if df_events is not None and not df_events.empty:
        df_ev_ref = df_events.copy()
        if match_id:
            df_ev_ref = df_ev_ref[df_ev_ref["match_id"] == match_id]
        df_ev_ref = df_ev_ref[(df_ev_ref["timestamp_min"] >= t_min) & (df_ev_ref["timestamp_min"] <= t_max)]
        
        for _, row in df_ev_ref.iterrows():
            if row["position_x"] > 500 and row["position_y"] > 500:
                path_nodes.append({
                    "participant_id": row["participant_id"],
                    "timestamp_min": row["timestamp_min"],
                    "pos_x": row["position_x"],
                    "pos_y": row["position_y"],
                })

    if path_nodes:
        df_path = pd.DataFrame(path_nodes).sort_values(by=["participant_id", "timestamp_min"])
        interpolated_rows = []
        
        for pid in df_path["participant_id"].unique():
            p_data = df_path[df_path["participant_id"] == pid]
            for i in range(len(p_data) - 1):
                x1, y1 = p_data.iloc[i]["pos_x"], p_data.iloc[i]["pos_y"]
                x2, y2 = p_data.iloc[i+1]["pos_x"], p_data.iloc[i+1]["pos_y"]
                
                dist = ((x2 - x1)**2 + (y2 - y1)**2)**0.5
                if dist > 8000:
                    continue
                    
                steps = 20
                for step in range(1, steps):
                    frac = step / float(steps)
                    interpolated_rows.append({
                        "pos_x": x1 + (x2 - x1) * frac,
                        "pos_y": y1 + (y2 - y1) * frac,
                        "participant_id": pid
                    })
        
        if interpolated_rows:
            df = pd.concat([df, pd.DataFrame(interpolated_rows)], ignore_index=True)


    # Colormap premium: de transparente a neón
    custom_colorscale = [
        [0.0, "rgba(0, 0, 0, 0)"],
        [0.2, "rgba(96, 165, 250, 0.3)"], # Azul suave
        [0.6, "rgba(244, 63, 94, 0.7)"],  # Rose intenso
        [1.0, "rgba(251, 191, 36, 1.0)"], # Ambar neón para puntos de calor máximo
    ]

    fig = go.Figure(go.Histogram2dContour(
        x=df["pos_x"],
        y=df["pos_y"],
        colorscale=custom_colorscale,
        reversescale=False,
        showscale=False,
        nbinsx=100,
        nbinsy=100,
        contours=dict(showlines=False), 
        line=dict(width=0),
        ncontours=30,
        hovertemplate="Zona de Influencia<extra></extra>",
    ))

    # Incorporar Eventos (Kills, Wards, etc.)
    if df_events is not None and not df_events.empty:
        df_ev = df_events.copy()
        if match_id:
            df_ev = df_ev[df_ev["match_id"] == match_id]
            
        df_ev = df_ev[(df_ev["timestamp_min"] >= t_min) & (df_ev["timestamp_min"] <= t_max)]
        df_ev = df_ev[(df_ev["position_x"] > 100) | (df_ev["position_y"] > 100)]
        
        marker_map = {
            "CHAMPION_KILL": {"symbol": "cross", "color": "#F43F5E", "size": 12, "name": "Baja"},
            "ELITE_MONSTER_KILL": {"symbol": "star", "color": "#FBBF24", "size": 15, "name": "Objetivo"},
            "BUILDING_KILL": {"symbol": "square", "color": "#22D3EE", "size": 10, "name": "Estructura"},
            "WARD_PLACED": {"symbol": "circle", "color": "#34D399", "size": 7, "name": "Visión"},
        }

        for ev_type, style in marker_map.items():
            ev_subset = df_ev[df_ev["event_type"] == ev_type]
            if not ev_subset.empty:
                fig.add_trace(go.Scatter(
                    x=ev_subset["position_x"],
                    y=ev_subset["position_y"],
                    mode="markers",
                    name=style["name"],
                    marker=dict(
                        symbol=style["symbol"],
                        color=style["color"],
                        size=style["size"],
                        line=dict(color="white", width=1)
                    ),
                    hovertemplate=f"<b>{style['name']}</b><br>Minuto: %{{customdata}}<extra></extra>",
                    customdata=ev_subset["timestamp_min"].round(1)
                ))

    # Aplicar fondo perfectamente escalado
    fig = _add_map_background(fig, opacity=0.8)

    title = "Mapa de Calor de Posiciones"
    if role:
        title += f" — {role}"
    if match_id:
        title += f" ({match_id[:10]})"
    return _apply_base_layout(fig, title)


# ──────────────────────────────────────────────────────────────────
# 7. Winrate por fase del juego
# ──────────────────────────────────────────────────────────────────

def plot_winrate_by_phase(phase_stats: dict) -> go.Figure:
    """
    Barras de winrate por fase del juego (early / mid / late).

    Args:
        phase_stats: Dict del output de analyze.identify_loss_phase()["phase_stats"].

    Returns:
        Figura de barras.
    """
    if not phase_stats:
        fig = go.Figure()
        return _apply_base_layout(fig, "Sin datos de fases")

    phases = []
    winrates = []
    n_games = []

    for phase in ["early", "mid", "late"]:
        stats = phase_stats.get(phase, {})
        wr = stats.get("win_rate")
        ng = stats.get("n_games", 0)
        if wr is not None and ng > 0:
            phases.append(phase.capitalize())
            winrates.append(wr)
            n_games.append(ng)

    colors = [WIN_COLOR if wr >= 0.5 else LOSS_COLOR for wr in winrates]

    fig = go.Figure(go.Bar(
        x=phases,
        y=winrates,
        marker_color=colors,
        text=[f"{wr*100:.0f}%<br>({ng} partidas)" for wr, ng in zip(winrates, n_games)],
        textposition="inside",
        hovertemplate="<b>%{x}</b><br>Winrate: %{y:.1%}<extra></extra>",
    ))
    fig.add_hline(y=0.5, line_dash="dot", line_color=NEUTRAL_COLOR,
                  annotation_text="50%", annotation_position="right")
    fig.update_yaxes(tickformat=".0%", range=[0, 1])

    return _apply_base_layout(fig, "Winrate por Fase del Juego")


# ──────────────────────────────────────────────────────────────────
# 8. Barras comparativas de métricas por jugador
# ──────────────────────────────────────────────────────────────────

def plot_player_bars(
    df_summary: pd.DataFrame,
    metric: str = "avg_impact_score",
    title: Optional[str] = None,
) -> go.Figure:
    """
    Barras horizontales comparando una métrica entre jugadores.

    Args:
        df_summary: Output de compute_player_impact_summary().
        metric:     Columna a visualizar.
        title:      Título personalizado (por defecto usa el nombre de la métrica).

    Returns:
        Figura de barras horizontales.
    """
    if df_summary.empty or metric not in df_summary.columns:
        fig = go.Figure()
        return _apply_base_layout(fig, "Sin datos")

    df = df_summary.sort_values(metric, ascending=True).copy()
    colors = [ROLE_COLORS.get(r, NEUTRAL_COLOR) for r in df.get("role", ["UNKNOWN"] * len(df))]

    fig = go.Figure(go.Bar(
        y=df["game_name"],
        x=df[metric],
        orientation="h",
        marker_color=colors,
        text=df[metric].apply(lambda v: f"{v:.2f}"),
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>" + metric + ": %{x:.3f}<extra></extra>",
    ))

    return _apply_base_layout(fig, title or f"Comparativa: {metric}")


# ──────────────────────────────────────────────────────────────────
# 9. Professional Synergy Heatmap (Sync Gap)
# ──────────────────────────────────────────────────────────────────

def create_synergy_heatmap(
    team_synergy: dict[str, float],
    benchmark_percentiles: dict[str, dict[str, float]],
    region: str = "KR"
) -> go.Figure:
    """
    Crea un Heatmap divergente que muestra la sinergia del equipo vs Benchmark.
    
    Escala:
    - Rojo: < P50 Challenger (Deficiencia)
    - Blanco: = P50 Challenger (Estándar)
    - Verde: > P50 Challenger (Excelencia)
    """
    roles = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
    n = len(roles)
    
    # Matriz de valores (Gap vs P50)
    z = np.zeros((n, n))
    text_vals = np.full((n, n), "", dtype=object)
    
    # Mapeo de métricas a pares de la matriz
    # Nota: Usamos una matriz simétrica
    # Mapeo de métricas a pares de la matriz
    pair_map = {
        ("JUNGLE", "SUPPORT"): "synergy_jg_sup",
        ("JUNGLE", "MID"):     "synergy_jg_mid",
        ("JUNGLE", "TOP"):     "synergy_jg_top",
        ("JUNGLE", "BOT"):     "synergy_jg_adc",  # <--- Faltaba
        ("BOT",    "SUPPORT"): "synergy_adc_sup",
        ("MID",    "BOT"):     "synergy_mid_bot",
        ("MID",    "TOP"):     "synergy_mid_top",
        ("MID",    "SUPPORT"): "synergy_mid_sup", # <--- Faltaba
        ("TOP",    "BOT"):     "synergy_top_bot",
        ("TOP",    "SUPPORT"): "synergy_top_sup"  # <--- Faltaba
    }
    for (r1, r2), m_key in pair_map.items():
        idx1, idx2 = roles.index(r1), roles.index(r2)
        val = team_synergy.get(m_key, 0)
        
        # Compatibilidad Supabase
        b_data = benchmark_percentiles.get(m_key, 0.5)
        p50 = b_data.get("p50", 0.5) if isinstance(b_data, dict) else float(b_data)
        
        gap = val - p50
        
        z[idx1, idx2] = gap
        z[idx2, idx1] = gap
        text_vals[idx1, idx2] = f"{val:.2f}<br>(P50: {p50:.2f})"
        text_vals[idx2, idx1] = f"{val:.2f}<br>(P50: {p50:.2f})"

    # Configurar escala de colores divergente
    # Usamos RdBu_r (Red-Blue reversed) o PiYG para algo similar a Rojo-Verde
    # Pero lo mejor es definirla manualmente para asegurar el Blanco en 0
    colorscale = [
        [0.0, "#E06C75"], # Rojo (Deficiencia)
        [0.5, "#98C379"], # Amarillo (Estándar/P50)
        [1.0, "#00aae4"]  # Verde (Excelencia)
    ]
    
    # Determinar el rango simétrico para que el 0 sea siempre blanco
    max_gap = max(abs(np.nanmin(z)), abs(np.nanmax(z)), 0.1)

    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=roles,
        y=roles,
        zmin=-max_gap,
        zmax=max_gap,
        colorscale=colorscale,
        text=text_vals,
        texttemplate="%{text}",
        hovertemplate="<b>Sinergia %{y}-%{x}</b><br>Gap vs P50: %{z:+.2f}<extra></extra>",
        showscale=True,
        colorbar=dict(title=dict(text="Gap vs P50", side="right"))
    ))

    fig.update_layout(
        xaxis=dict(side="top"),
        yaxis=dict(autorange="reversed"),
        height=450,
        width=500
    )

    return _apply_base_layout(fig, f"Synergy Sync Gap — vs Challenger {region}")


# ──────────────────────────────────────────────────────────────────
# ANÁLISIS TÁCTICO POR PARTIDA (Nivel Pro)
# ──────────────────────────────────────────────────────────────────

def plot_death_map(df_events: pd.DataFrame, team_id: int) -> go.Figure:
    """
    Mapa de calor de densidad de muertes + marcadores exactos.
    Perfectamente escalado sobre map.png.
    """
    if df_events.empty: return go.Figure()
    
    df = df_events.copy()
    col_map = {"position_x": "x", "position_y": "y", "time": "timestamp"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    if "timestamp" not in df.columns:
        df = df.reset_index().rename(columns={"index": "timestamp"})

    if "victim_team_id" not in df.columns and "victim_id" in df.columns:
        df["victim_team_id"] = df["victim_id"].apply(lambda x: 100 if 1 <= x <= 5 else (200 if 6 <= x <= 10 else 0))

    deaths = df[
        (df["event_type"] == "CHAMPION_KILL") & 
        (df["victim_team_id"] == team_id) &
        (df["x"].notna()) & (df["y"].notna())
    ].copy()
    
    if deaths.empty:
        fig = go.Figure()
        fig.add_annotation(text="Sin muertes aliadas registradas", showarrow=False)
        return fig

    # 1. Capa de Calor (Densidad de muertes)
    fig = go.Figure(go.Histogram2dContour(
        x=deaths["x"], y=deaths["y"],
        colorscale="Reds",
        showscale=False,
        nbinsx=40, nbinsy=40,
        contours=dict(showlines=False),
        line=dict(width=0),
        opacity=0.6,
        hoverinfo="skip"
    ))
    
    # 2. Capa de Marcadores (Puntos exactos)
    fig.add_trace(go.Scatter(
        x=deaths["x"], y=deaths["y"],
        mode='markers',
        marker=dict(
            size=12, color='#F43F5E', symbol='x',
            line=dict(width=1.5, color='white'),
            opacity=0.9
        ),
        text=deaths.apply(lambda r: f"Min {int(r['timestamp']//60000)}: Baja de {r.get('victim_role', 'Jugador')}", axis=1),
        hoverinfo='text',
        name="Puntos de Baja"
    ))

    # 3. Aplicar fondo y escalas del config
    fig = _add_map_background(fig, opacity=0.7)
    fig.update_layout(
        title="<b>MAPA DE CALOR: VULNERABILIDAD</b>",
        height=500, width=500,
        margin=dict(l=0, r=0, t=40, b=0),
        showlegend=False
    )
    
    return fig

def plot_match_momentum(df_timeline: pd.DataFrame, allied_team_id: int) -> go.Figure:
    """
    Gráfico de ventaja de oro acumulada (Momentum) minuto a minuto.
    Siempre se calcula desde la perspectiva de allied_team_id.
    """
    if df_timeline.empty: return go.Figure()
    
    df = df_timeline.copy()
    
    # Normalizar nombres
    col_map = {"time": "timestamp", "gold": "total_gold", "current_gold": "total_gold"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    
    if "timestamp_min" in df.columns:
        df["minuto"] = df["timestamp_min"]
    elif "timestamp" in df.columns:
        df["minuto"] = df["timestamp"].apply(lambda x: x / 60000.0 if x > 1000 else x)
    else:
        df = df.reset_index()
        df["minuto"] = df.index
    
    if "minuto" not in df.columns or "total_gold" not in df.columns: return go.Figure()
    if "team_id" not in df.columns and "participant_id" in df.columns:
        df["team_id"] = df["participant_id"].apply(lambda x: 100 if x <= 5 else 200)
    if "team_id" not in df.columns: return go.Figure()
    
    teams = sorted(df["team_id"].unique())
    if len(teams) < 2: return go.Figure()
    
    # Identificar explícitamente aliados y enemigos
    ally_id = allied_team_id if allied_team_id in teams else teams[0]
    enemy_id = [t for t in teams if t != ally_id][0]
    
    t_ally = df[df["team_id"] == ally_id].groupby("minuto")["total_gold"].sum().reset_index()
    t_enemy = df[df["team_id"] == enemy_id].groupby("minuto")["total_gold"].sum().reset_index()
    
    merged = pd.merge(t_ally, t_enemy, on='minuto', suffixes=('_ally', '_enemy'))
    # La resta ahora siempre es Nosotros - Ellos
    merged['gold_diff'] = merged['total_gold_ally'] - merged['total_gold_enemy']
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=merged['minuto'], y=merged['gold_diff'],
        fill='tozeroy', mode='lines',
        line=dict(width=3, color='#38BDF8'),
        fillcolor='rgba(56, 189, 248, 0.2)',
        name="Lead Equipo",
        hovertemplate="Minuto %{x}: %{y:+} Oro<extra></extra>"
    ))
    
    fig.update_layout(
        template="plotly_dark", title="<b>PULSO DE LA PARTIDA</b> (Ventaja de Oro)",
        xaxis_title="Minuto", yaxis_title="Gold Diff",
        paper_bgcolor='rgba(15, 23, 42, 0.5)', plot_bgcolor='rgba(0,0,0,0)',
        hovermode="x unified", height=350, margin=dict(t=50, b=40, l=40, r=20)
    )
    fig.add_shape(type="line", x0=0, y0=0, x1=merged['minuto'].max(), y1=0, line=dict(color="white", width=1, dash="dash"))
    return fig

def plot_lane_dominance(df_timeline: pd.DataFrame, allied_team_id: int = 100) -> go.Figure:
    """
    Diferencial de oro contra el oponente directo en fases críticas (5, 10, 15, 20, 25 min).
    La ventaja se calcula siempre como Oro_Aliado - Oro_Rival.
    """
    if df_timeline.empty: return go.Figure()
    
    df = df_timeline.copy()
    
    # Asegurar que exista timestamp_min
    if "timestamp_min" not in df.columns:
        if "timestamp" in df.columns:
            df["timestamp_min"] = df["timestamp"] / 60000.0
        elif "time" in df.columns:
            df["timestamp_min"] = df["time"] / 60000.0
        else:
            df = df.reset_index()
            df["timestamp_min"] = df["index"] / 60000.0
            
    if "team_id" not in df.columns and "participant_id" in df.columns:
        df["team_id"] = df["participant_id"].apply(lambda x: 100 if x <= 5 else 200)
    
    if "role" not in df.columns:
        fig = go.Figure()
        fig.add_annotation(text="Mapeo de roles no disponible en Timeline", showarrow=False, font=dict(color="white"))
        fig.update_layout(template="plotly_dark", paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        return fig

    minutes = [5, 10, 15, 20, 25]
    roles = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
    teams = sorted(df["team_id"].unique())
    if len(teams) < 2: return go.Figure()
    
    # Orientación de equipos
    ally_id = allied_team_id if allied_team_id in teams else teams[0]
    enemy_id = [t for t in teams if t != ally_id][0]
    
    data = []
    for role in roles:
        for m in minutes:
            # Buscar el frame más cercano a ese minuto (± 1 minuto de margen)
            snap = df[(df["timestamp_min"] >= m - 0.5) & (df["timestamp_min"] <= m + 0.5) & (df["role"] == role)].copy()
            
            if len(snap) >= 2 and "total_gold" in df.columns:
                # Tomamos el frame más cercano al minuto exacto por cada equipo
                snap["dist"] = abs(snap["timestamp_min"] - m)
                snap = snap.sort_values("dist").drop_duplicates(["team_id"])
                
                if len(snap) == 2:
                    g_ally = snap[snap["team_id"] == ally_id]["total_gold"].values[0]
                    g_enemy = snap[snap["team_id"] == enemy_id]["total_gold"].values[0]
                    data.append({"Rol": role, "Fase": f"Min {m}", "Ventaja": int(g_ally - g_enemy)})
    
    if not data: return go.Figure()
    
    import plotly.express as px
    df_plot = pd.DataFrame(data)
    fig = px.bar(
        df_plot, x="Rol", y="Ventaja", color="Fase",
        barmode="group",
        color_discrete_sequence=['#DBEAFE', '#93C5FD', '#3B82F6', '#1E40AF', '#1E3A8A'],
        title="<b>DOMINANCIA POR ROL</b> (Lead vs Oponente Directo)",
        category_orders={"Fase": [f"Min {m}" for m in minutes]}
    )
    
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor='rgba(15, 23, 42, 0.5)',
        plot_bgcolor='rgba(0,0,0,0)',
        yaxis_title="Gold Diff",
        height=400,
        margin=dict(t=60, b=40, l=40, r=20)
    )
    return fig
    

def plot_player_gold_diff(df_timeline: pd.DataFrame, role: str, allied_team_id: int) -> go.Figure:
    """
    Diferencial de oro individual con filtrado insensible y fallback de IDs.
    """
    if df_timeline.empty or not role: return go.Figure()
    
    df = df_timeline.copy()
    # 1. Normalización total
    col_map = {"time": "timestamp", "gold": "total_gold", "current_gold": "total_gold"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if "timestamp" not in df.columns: 
        df = df.reset_index().rename(columns={"index": "timestamp"})
    
    # 2. Asegurar que team_id existe (heurística si falta)
    if "team_id" not in df.columns and "participant_id" in df.columns:
        df["team_id"] = df["participant_id"].apply(lambda x: 100 if x <= 5 else 200)

    # 3. Filtrado Insensible de Rol
    df["role_upper"] = df["role"].astype(str).str.upper() if "role" in df.columns else ""
    target_role = str(role).upper()
    role_df = df[df["role_upper"] == target_role].copy()

    # Fallback: Si no hay roles en el timeline, usar IDs nativos por posición
    if role_df.empty and "participant_id" in df.columns:
        role_to_idx = {"TOP": 1, "JUNGLE": 2, "MID": 3, "BOT": 4, "SUPPORT": 5}
        idx = role_to_idx.get(target_role)
        if idx:
            # Seleccionar ID idx (equipo 1) y idx+5 (equipo 2)
            role_df = df[df["participant_id"].isin([idx, idx+5])].copy()

    if role_df.empty:
        fig = go.Figure()
        found = df["role"].unique() if "role" in df.columns else "Ninguno"
        fig.add_annotation(text=f"No se halló el rol {target_role}. Encontrados: {found}", showarrow=False)
        return fig

    # 4. Agregación y Orientación
    teams = sorted(role_df["team_id"].unique())
    
    if len(teams) < 2:
        fig = go.Figure()
        fig.add_annotation(text=f"No se detectó al RIVAL para el rol {target_role}", showarrow=False, font=dict(color="white"))
        fig.update_layout(template="plotly_dark", paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        return fig

    ally_id = allied_team_id if allied_team_id in teams else teams[0]
    enemy_id = [t for t in teams if t != ally_id][0]

    if "timestamp_min" in role_df.columns:
        role_df["minuto"] = role_df["timestamp_min"]
    elif "timestamp" in role_df.columns:
        role_df["minuto"] = role_df["timestamp"].apply(lambda x: x / 60000.0 if x > 1000 else x)
    else:
        role_df = role_df.reset_index()
        role_df["minuto"] = role_df.index

    t_ally = role_df[role_df["team_id"] == ally_id].groupby("minuto")["total_gold"].sum().reset_index()
    t_enemy = role_df[role_df["team_id"] == enemy_id].groupby("minuto")["total_gold"].sum().reset_index()
    
    merged = pd.merge(t_ally, t_enemy, on='minuto', suffixes=('_ally', '_enemy'))
    merged['diff'] = merged['total_gold_ally'] - merged['total_gold_enemy']
    merged = merged.sort_values("minuto")
    
    if merged.empty: return go.Figure()
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=merged['minuto'], y=merged['diff'],
        mode='lines',
        line=dict(color='#F59E0B', width=3),
        fill='tozeroy',
        fillcolor='rgba(245, 158, 11, 0.2)',
        name=f"Lead {role}",
        hovertemplate="Min %{x}: %{y:+} Oro<extra></extra>"
    ))
    
    fig.update_layout(
        template="plotly_dark",
        title=f"<b>DUELO DE ORO: {role}</b>",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        xaxis_title="Minuto",
        yaxis_title="Diferencia de Oro",
        height=300,
        margin=dict(t=50, b=40, l=40, r=20)
    )
    fig.add_shape(type="line", x0=0, y0=0, x1=merged['minuto'].max(), y1=0, 
                  line=dict(color="white", width=1, dash="dash"))
    
    return fig

def plot_individual_timeline(df_events: pd.DataFrame, participant_id: int) -> go.Figure:
    """
    Línea de tiempo de eventos clave del jugador (Kills, Deaths, Assists).
    """
    if df_events.empty: return go.Figure()
    
    df = df_events.copy()
    # Normalizar tiempo
    if "timestamp" not in df.columns:
        df = df.rename(columns={"time": "timestamp"})
        if "timestamp" not in df.columns: df = df.reset_index().rename(columns={"index": "timestamp"})

    # Filtrar eventos del jugador
    # - Kill: participant_id == killer
    # - Death: participant_id == victim
    # - Assist: participant_id in assisting_ids
    
    def is_involved(row):
        if row["participant_id"] == participant_id: return "Kill"
        if row.get("victim_id") == participant_id: return "Death"
        assists = str(row.get("assisting_ids", ""))
        if str(participant_id) in assists: return "Assist"
        return None

    df["involvement"] = df.apply(is_involved, axis=1)
    player_events = df[df["involvement"].notna()].copy()
    player_events["minuto"] = player_events["timestamp"] / 60000
    
    if player_events.empty: return go.Figure()
    
    colors = {"Kill": "#10B981", "Death": "#EF4444", "Assist": "#3B82F6"}
    
    fig = go.Figure()
    for ev_type, color in colors.items():
        sub = player_events[player_events["involvement"] == ev_type]
        fig.add_trace(go.Scatter(
            x=sub["minuto"], y=[1]*len(sub),
            mode='markers',
            marker=dict(size=15, color=color, symbol='diamond'),
            name=ev_type,
            text=sub.apply(lambda r: f"Min {int(r['minuto'])}: {ev_type}", axis=1),
            hoverinfo='text'
        ))
    
    fig.update_layout(
        template="plotly_dark",
        title="Eventos Clave",
        showlegend=True,
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(title="Minuto", showgrid=False),
        yaxis=dict(showgrid=False, zeroline=False, visible=False),
        height=150,
        margin=dict(t=40, b=20, l=40, r=20)
    )
    return fig
