"""
visualization_scout.py — Gráficos específicos para análisis individual.

Incluye:
  - Heatmaps de muerte y posicionamiento
  - Timelines de gold diff individual
  - Radares de pilares vs Challenger
  - Gráficos de jungle pathing
  - Evolución de impact score
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import gaussian_kde

logger = logging.getLogger(__name__)

# Paleta de colores
WIN_COLOR = "#4ADE80"
LOSS_COLOR = "#F87171"
NEUTRAL_COLOR = "#94A3B8"
PLAYER_COLOR = "#A855F7"
CHALLENGER_COLOR = "#F59E0B"

# Layout base
BASE_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Outfit, sans-serif", color="#E2E8F0"),
    margin=dict(l=20, r=20, t=40, b=20),
)

# Constantes del mapa
MAP_X_MIN, MAP_X_MAX = 0, 14820
MAP_Y_MIN, MAP_Y_MAX = 0, 14881


def plot_death_heatmap(df_deaths: pd.DataFrame, profile) -> go.Figure:
    """
    Heatmap de densidad de muertes del jugador sobre el mapa de SR.
    
    Args:
        df_deaths: DataFrame con eventos de muerte (position_x, position_y)
        profile: PlayerProfile
    
    Returns:
        Figura Plotly con heatmap sobre mapa
    """
    from pathlib import Path
    from PIL import Image
    import os
    
    fig = go.Figure()
    
    if df_deaths.empty or "position_x" not in df_deaths.columns:
        fig.add_annotation(
            text="No hay datos de posición de muertes",
            showarrow=False,
            font=dict(size=14, color=NEUTRAL_COLOR)
        )
        fig.update_layout(**BASE_LAYOUT, title="Death Heatmap")
        return fig
    
    # Filtrar posiciones válidas
    deaths = df_deaths[
        (df_deaths["position_x"] > 0) &
        (df_deaths["position_y"] > 0)
    ].copy()
    
    if len(deaths) < 3:
        fig.add_annotation(
            text=f"Datos insuficientes: {len(deaths)} muertes con posición",
            showarrow=False,
            font=dict(size=14, color=NEUTRAL_COLOR)
        )
        fig.update_layout(**BASE_LAYOUT, title="Death Heatmap")
        return fig
    
    x = deaths["position_x"].values
    y = deaths["position_y"].values
    
    # Cargar imagen del mapa
    map_path = Path(__file__).parent.parent / "map.png"
    map_img = None
    if map_path.exists():
        try:
            map_img = Image.open(map_path)
        except Exception as e:
            logger.warning(f"No se pudo cargar map.png: {e}")
    
    # Crear grid para heatmap
    xi = np.linspace(MAP_X_MIN, MAP_X_MAX, 100)
    yi = np.linspace(MAP_Y_MIN, MAP_Y_MAX, 100)
    xi, yi = np.meshgrid(xi, yi)
    
    # Gaussian KDE para suavizar
    try:
        positions = np.vstack([x, y])
        kernel = gaussian_kde(positions)
        zi = kernel(np.vstack([xi.flatten(), yi.flatten()]))
        zi = zi.reshape(xi.shape)
    except Exception as e:
        logger.error(f"Error en KDE: {e}")
        # Fallback: histogram2d
        zi, _, _ = np.histogram2d(x, y, bins=[100, 100], range=[[MAP_X_MIN, MAP_X_MAX], [MAP_Y_MIN, MAP_Y_MAX]])
        zi = zi.T
    
    # Heatmap
    fig.add_trace(go.Heatmap(
        x=xi[0],
        y=yi[:, 0],
        z=zi,
        colorscale="Reds",
        opacity=0.6,
        showscale=True,
        colorbar=dict(title=dict(text="Densidad", side="right")),
        hovertemplate="X: %{x}<br>Y: %{y}<br>Densidad: %{z:.3f}<extra></extra>",
    ))
    
    # Scatter de muertes individuales
    fig.add_trace(go.Scatter(
        x=x,
        y=y,
        mode="markers",
        marker=dict(size=8, color=LOSS_COLOR, symbol="x", line=dict(width=1, color="white")),
        name="Muertes",
        hovertemplate="Muerte<br>X: %{x}<br>Y: %{y}<extra></extra>",
    ))
    
    # Configurar ejes para mapa
    fig.update_xaxes(
        range=[MAP_X_MIN, MAP_X_MAX],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        visible=False,
    )
    fig.update_yaxes(
        range=[MAP_Y_MIN, MAP_Y_MAX],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        visible=False,
        scaleanchor="x",
        scaleratio=1,
    )
    
    # Agregar imagen de fondo si existe
    layout_images = []
    if map_img is not None:
        layout_images.append(dict(
            source=map_img,
            xref="x",
            yref="y",
            x=MAP_X_MIN,
            y=MAP_Y_MAX,
            sizex=MAP_X_MAX - MAP_X_MIN,
            sizey=MAP_Y_MAX - MAP_Y_MIN,
            sizing="stretch",
            opacity=0.5,
            layer="below"
        ))
    
    fig.update_layout(
        **BASE_LAYOUT,
        title=f"Death Heatmap - {profile.display_name} ({len(deaths)} muertes)",
        height=600,
        width=600,
        images=layout_images,
    )
    
    return fig


def plot_gold_diff_timeline_individual(
    df_timeline: pd.DataFrame,
    df_player: pd.DataFrame,
    match_id: str,
    profile,
) -> go.Figure:
    """
    Gold diff del jugador vs su oponente directo (mismo rol, equipo contrario).
    
    Args:
        df_timeline: DataFrame con timeline de la partida
        df_player: DataFrame con info del jugador
        match_id: ID de la partida
        profile: PlayerProfile
    
    Returns:
        Figura Plotly con timeline de gold diff
    """
    fig = go.Figure()
    
    if df_timeline.empty or df_player.empty:
        fig.add_annotation(
            text="No hay datos de timeline",
            showarrow=False,
            font=dict(size=14, color=NEUTRAL_COLOR)
        )
        fig.update_layout(**BASE_LAYOUT, title="Gold Diff Timeline")
        return fig
    
    # Filtrar timeline de esta partida
    tl = df_timeline[df_timeline["match_id"] == match_id].copy()
    player_row = df_player[df_player["match_id"] == match_id]
    
    if tl.empty or player_row.empty:
        fig.add_annotation(
            text=f"No hay datos para partida {match_id}",
            showarrow=False,
            font=dict(size=14, color=NEUTRAL_COLOR)
        )
        fig.update_layout(**BASE_LAYOUT, title="Gold Diff Timeline")
        return fig
    
    player_pid = player_row.iloc[0]["participant_id"]
    player_team = player_row.iloc[0]["team_id"]
    player_role = player_row.iloc[0].get("role", "UNKNOWN")
    result = player_row.iloc[0]["result"]
    
    # Verificar que tl tenga las columnas necesarias
    if "role" not in tl.columns or "team_id" not in tl.columns:
        fig.add_annotation(
            text="Datos de timeline incompletos (falta role/team_id)",
            showarrow=False,
            font=dict(size=14, color=NEUTRAL_COLOR)
        )
        fig.update_layout(**BASE_LAYOUT, title="Gold Diff Timeline")
        return fig
    
    # Timeline del jugador
    player_tl = tl[tl["participant_id"] == player_pid].sort_values("timestamp_min")
    
    # Timeline del oponente (mismo rol, equipo contrario)
    opponent_tl = tl[
        (tl["role"] == player_role) &
        (tl["team_id"] != player_team)
    ].sort_values("timestamp_min")
    
    if player_tl.empty or opponent_tl.empty:
        fig.add_annotation(
            text="No se encontró oponente directo",
            showarrow=False,
            font=dict(size=14, color=NEUTRAL_COLOR)
        )
        fig.update_layout(**BASE_LAYOUT, title="Gold Diff Timeline")
        return fig
    
    # Merge por timestamp (aproximado)
    merged = pd.merge_asof(
        player_tl[["timestamp_min", "total_gold"]].rename(columns={"total_gold": "player_gold"}),
        opponent_tl[["timestamp_min", "total_gold"]].rename(columns={"total_gold": "opponent_gold"}),
        on="timestamp_min",
        direction="nearest",
        tolerance=0.5,
    )
    
    merged["gold_diff"] = merged["player_gold"] - merged["opponent_gold"]
    
    # Línea de gold diff
    color = WIN_COLOR if result else LOSS_COLOR
    fig.add_trace(go.Scatter(
        x=merged["timestamp_min"],
        y=merged["gold_diff"],
        mode="lines",
        name="Gold Diff",
        line=dict(color=color, width=3),
        fill="tozeroy",
        fillcolor=f"rgba{tuple(list(int(color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)) + [0.2])}",
        hovertemplate="Min: %{x:.1f}<br>Gold Diff: %{y:+,}<extra></extra>",
    ))
    
    # Línea de referencia en 0
    fig.add_hline(y=0, line_dash="dash", line_color=NEUTRAL_COLOR, line_width=1)
    
    # Zonas de ventaja/desventaja
    fig.add_hrect(y0=1000, y1=10000, fillcolor="green", opacity=0.1, line_width=0)
    fig.add_hrect(y0=-10000, y1=-1000, fillcolor="red", opacity=0.1, line_width=0)
    
    result_text = "VICTORIA" if result else "DERROTA"
    fig.update_layout(
        **BASE_LAYOUT,
        title=f"Gold Diff vs Oponente ({player_role}) - {result_text}",
        xaxis_title="Minuto",
        yaxis_title="Gold Diff",
        height=400,
    )
    
    return fig


def plot_impact_score_evolution(df_player: pd.DataFrame, profile, df_bench: pd.DataFrame = None) -> go.Figure:
    """
    Scatter + línea de tendencia de impact_score a lo largo de las partidas.
    
    Args:
        df_player: DataFrame con partidas del jugador ordenadas cronológicamente
        profile: PlayerProfile
    
    Returns:
        Figura Plotly con evolución de impact score
    """
    fig = go.Figure()
    
    if df_player.empty or "impact_score" not in df_player.columns:
        fig.add_annotation(
            text="No hay datos de impact score",
            showarrow=False,
            font=dict(size=14, color=NEUTRAL_COLOR)
        )
        fig.update_layout(**BASE_LAYOUT, title="Impact Score Evolution")
        return fig
    
    df = df_player.copy()
    df = df.sort_values("match_id").reset_index(drop=True)
    df["game_index"] = df.index
    
    # Scatter por resultado
    wins = df[df["result"] == True]
    losses = df[df["result"] == False]
    
    if not wins.empty:
        fig.add_trace(go.Scatter(
            x=wins["game_index"],
            y=wins["impact_score"],
            mode="markers",
            name="Victoria",
            marker=dict(size=10, color=WIN_COLOR, symbol="circle"),
            hovertemplate="Partida %{x}<br>Impact: %{y:.3f}<br>Victoria<extra></extra>",
        ))
    
    if not losses.empty:
        fig.add_trace(go.Scatter(
            x=losses["game_index"],
            y=losses["impact_score"],
            mode="markers",
            name="Derrota",
            marker=dict(size=10, color=LOSS_COLOR, symbol="circle"),
            hovertemplate="Partida %{x}<br>Impact: %{y:.3f}<br>Derrota<extra></extra>",
        ))
    
    # Línea de tendencia (regresión lineal)
    if len(df) >= 5:
        x = df["game_index"].values
        y = df["impact_score"].values
        
        # Regresión lineal
        slope, intercept = np.polyfit(x, y, 1)
        trend_line = slope * x + intercept
        
        fig.add_trace(go.Scatter(
            x=df["game_index"],
            y=trend_line,
            mode="lines",
            name="Tendencia",
            line=dict(color=PLAYER_COLOR, width=2, dash="dash"),
            hovertemplate="Tendencia: %{y:.3f}<extra></extra>",
        ))
        
        # Banda de confianza (±1 std)
        std = df["impact_score"].std()
        fig.add_trace(go.Scatter(
            x=df["game_index"].tolist() + df["game_index"].tolist()[::-1],
            y=(trend_line + std).tolist() + (trend_line - std).tolist()[::-1],
            fill="toself",
            fillcolor=f"rgba(168, 85, 247, 0.1)",
            line=dict(color="rgba(255,255,255,0)"),
            showlegend=False,
            hoverinfo="skip",
        ))
        
        trend_text = "📈 MEJORANDO" if slope > 0 else "📉 DECLINANDO" if slope < 0 else "➡️ ESTABLE"
        trend_annotation = f"{trend_text} (slope: {slope:+.4f})"
    else:
        trend_annotation = "Datos insuficientes para tendencia"
    
    # Línea benchmark Challenger
    if df_bench is not None and not df_bench.empty and "impact_score" in df_bench.columns:
        role = profile.get("primary_role", "") if isinstance(profile, dict) else getattr(profile, "primary_role", "")
        role_bench = df_bench[df_bench["role"].str.strip().str.upper() == role.strip().upper()] if role else df_bench
        if not role_bench.empty:
            chall_median = role_bench["impact_score"].median()
            x_range = [df["game_index"].min(), df["game_index"].max()] if not df.empty else [0, 1]
            fig.add_trace(go.Scatter(
                x=x_range,
                y=[chall_median, chall_median],
                mode="lines",
                name=f"Challenger Median ({chall_median:.3f})",
                line=dict(color=CHALLENGER_COLOR, width=2, dash="dot"),
                hovertemplate=f"Challenger median: {chall_median:.3f}<extra></extra>",
            ))

    player_name = profile.get("display_name", "Jugador") if isinstance(profile, dict) else getattr(profile, "display_name", "Jugador")
    fig.update_layout(
        **BASE_LAYOUT,
        title=f"Evolución de Impact Score - {player_name}",
        xaxis_title="Partida #",
        yaxis_title="Impact Score",
        height=400,
        annotations=[dict(
            text=trend_annotation,
            xref="paper", yref="paper",
            x=0.5, y=1.05,
            showarrow=False,
            font=dict(size=12, color=PLAYER_COLOR),
        )],
    )
    
    return fig


def plot_pillar_radar_vs_challenger(
    df_player: pd.DataFrame,
    df_bench: pd.DataFrame,
    champion: str,
    role: str,
    profile,
) -> go.Figure:
    """
    Radar de 8 dimensiones comparando jugador vs Challenger benchmark.
    
    Dimensiones:
    1. Combat Efficiency
    2. Map Pressure
    3. Tactical Utility
    4. Consistency
    5. CS Efficiency
    6. Gold Efficiency
    7. Kill Conversion
    8. Early Game
    
    Args:
        df_player: DataFrame con partidas del jugador
        df_bench: DataFrame con benchmarks Challenger
        champion: Campeón a comparar
        role: Rol a comparar
        profile: PlayerProfile
    
    Returns:
        Figura Plotly con radar chart
    """
    fig = go.Figure()
    
    if df_player.empty:
        fig.add_annotation(
            text="No hay datos del jugador",
            showarrow=False,
            font=dict(size=14, color=NEUTRAL_COLOR)
        )
        fig.update_layout(**BASE_LAYOUT, title="Pillar Radar")
        return fig
    
    # Métricas del jugador (promedio)
    player_metrics = {
        "Combat Efficiency": df_player.get("pilar_combat_efficiency", pd.Series([0])).mean(),
        "Map Pressure": df_player.get("pilar_map_pressure", pd.Series([0])).mean(),
        "Tactical Utility": df_player.get("pilar_tactical_utility", pd.Series([0])).mean(),
        "Consistency": df_player.get("pilar_consistency", pd.Series([0])).mean(),
        "CS/min": df_player.get("cs_per_min", pd.Series([0])).mean(),
        "Gold/min": df_player.get("gold_per_min", pd.Series([0])).mean(),
        "Kill Conversion": df_player.get("kill_conversion", pd.Series([0])).mean(),
        "Vision/min": df_player.get("vision_per_min", pd.Series([0])).mean(),
    }
    
    # Benchmarks Challenger
    chall_metrics = {}
    if not df_bench.empty:
        bench_filtered = df_bench[
            (df_bench["champion"] == champion) &
            (df_bench["role"] == role) &
            (df_bench["result"] == True)  # Solo victorias
        ]
        
        if bench_filtered.empty:
            bench_filtered = df_bench[df_bench["role"] == role]
        
        if not bench_filtered.empty:
            chall_metrics = {
                "Combat Efficiency": bench_filtered.get("pilar_combat_efficiency", pd.Series([0.5])).median(),
                "Map Pressure": bench_filtered.get("pilar_map_pressure", pd.Series([0.5])).median(),
                "Tactical Utility": bench_filtered.get("pilar_tactical_utility", pd.Series([0.5])).median(),
                "Consistency": 1 - min(bench_filtered["impact_score"].std() / bench_filtered["impact_score"].mean(), 1) if "impact_score" in bench_filtered.columns and len(bench_filtered) >= 3 and bench_filtered["impact_score"].mean() > 0 else 0.7,
                "CS/min": bench_filtered.get("cs_per_min", pd.Series([5])).median(),
                "Gold/min": bench_filtered.get("gold_per_min", pd.Series([400])).median(),
                "Kill Conversion": bench_filtered.get("kill_conversion", pd.Series([0.5])).median(),
                "Vision/min": bench_filtered.get("vision_per_min", pd.Series([1.5])).median(),
            }
    
    # Normalizar métricas (escala 0-1 relativa a Challenger)
    categories = list(player_metrics.keys())
    player_values = []
    chall_values = []
    
    for cat in categories:
        p_val = player_metrics[cat]
        c_val = chall_metrics.get(cat, 1.0)
        
        # Normalizar: player / challenger (1.0 = paridad)
        if c_val > 0:
            normalized = min(p_val / c_val, 2.0)  # Cap en 2.0
        else:
            normalized = 0.5
        
        player_values.append(normalized)
        chall_values.append(1.0)  # Challenger siempre en 1.0
    
    # Cerrar el polígono
    categories_closed = categories + [categories[0]]
    player_values_closed = player_values + [player_values[0]]
    chall_values_closed = chall_values + [chall_values[0]]
    
    # Radar del jugador
    fig.add_trace(go.Scatterpolar(
        r=player_values_closed,
        theta=categories_closed,
        fill="toself",
        fillcolor=f"rgba(168, 85, 247, 0.3)",
        line=dict(color=PLAYER_COLOR, width=2),
        name=profile.display_name if hasattr(profile, "display_name") else profile.get("display_name", "Jugador"),
        hovertemplate="%{theta}<br>Ratio: %{r:.2f}x Challenger<extra></extra>",
    ))
    
    # Radar Challenger (referencia)
    fig.add_trace(go.Scatterpolar(
        r=chall_values_closed,
        theta=categories_closed,
        fill="toself",
        fillcolor=f"rgba(245, 158, 11, 0.1)",
        line=dict(color=CHALLENGER_COLOR, width=2, dash="dash"),
        name="Challenger",
        hovertemplate="%{theta}<br>Baseline: 1.0x<extra></extra>",
    ))
    
    fig.update_layout(
        **BASE_LAYOUT,
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 1.5],
                tickvals=[0.5, 1.0, 1.5],
                ticktext=["50%", "100%", "150%"],
                gridcolor=NEUTRAL_COLOR,
            ),
            angularaxis=dict(
                gridcolor=NEUTRAL_COLOR,
            ),
        ),
        title=f"Pillar Radar - {champion} ({role})",
        height=500,
        showlegend=True,
    )
    
    return fig


def plot_jungle_pathing(
    df_timeline: pd.DataFrame,
    match_id: str,
    profile,
) -> go.Figure:
    """
    Visualiza ruta de jungle en una partida específica.
    
    Args:
        df_timeline: DataFrame con timeline (pos_x, pos_y)
        match_id: ID de la partida
        profile: PlayerProfile
    
    Returns:
        Figura Plotly con ruta de movimiento
    """
    fig = go.Figure()
    
    if df_timeline.empty:
        fig.add_annotation(
            text="No hay datos de timeline",
            showarrow=False,
            font=dict(size=14, color=NEUTRAL_COLOR)
        )
        fig.update_layout(**BASE_LAYOUT, title="Jungle Pathing")
        return fig
    
    # Filtrar timeline de esta partida
    tl = df_timeline[df_timeline["match_id"] == match_id].copy()
    
    if tl.empty or "pos_x" not in tl.columns or "pos_y" not in tl.columns:
        fig.add_annotation(
            text="No hay datos de posición",
            showarrow=False,
            font=dict(size=14, color=NEUTRAL_COLOR)
        )
        fig.update_layout(**BASE_LAYOUT, title="Jungle Pathing")
        return fig
    
    # Filtrar posiciones válidas
    tl = tl[(tl["pos_x"] > 0) & (tl["pos_y"] > 0)].sort_values("timestamp_min")
    
    if len(tl) < 2:
        fig.add_annotation(
            text="Datos insuficientes de posición",
            showarrow=False,
            font=dict(size=14, color=NEUTRAL_COLOR)
        )
        fig.update_layout(**BASE_LAYOUT, title="Jungle Pathing")
        return fig
    
    # Línea de movimiento
    fig.add_trace(go.Scatter(
        x=tl["pos_x"],
        y=tl["pos_y"],
        mode="lines+markers",
        line=dict(color=PLAYER_COLOR, width=2),
        marker=dict(size=4, color=PLAYER_COLOR),
        name="Ruta",
        hovertemplate="Min: %{text}<br>X: %{x}<br>Y: %{y}<extra></extra>",
        text=tl["timestamp_min"].round(1).astype(str),
    ))
    
    # Marcar inicio y fin
    fig.add_trace(go.Scatter(
        x=[tl.iloc[0]["pos_x"]],
        y=[tl.iloc[0]["pos_y"]],
        mode="markers",
        marker=dict(size=15, color=WIN_COLOR, symbol="star"),
        name="Inicio",
        hovertemplate="Inicio<extra></extra>",
    ))
    
    fig.add_trace(go.Scatter(
        x=[tl.iloc[-1]["pos_x"]],
        y=[tl.iloc[-1]["pos_y"]],
        mode="markers",
        marker=dict(size=15, color=LOSS_COLOR, symbol="square"),
        name="Fin",
        hovertemplate="Fin<extra></extra>",
    ))
    
    # Configurar ejes para mapa
    fig.update_xaxes(
        range=[MAP_X_MIN, MAP_X_MAX],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        visible=False,
    )
    fig.update_yaxes(
        range=[MAP_Y_MIN, MAP_Y_MAX],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        visible=False,
        scaleanchor="x",
        scaleratio=1,
    )
    
    fig.update_layout(
        **BASE_LAYOUT,
        title=f"Jungle Pathing - Match {match_id[-8:]}",
        height=600,
    )
    
    return fig


# ──────────────────────────────────────────────────────────────────
# Synergy Heatmap (port del war room)
# ──────────────────────────────────────────────────────────────────

def create_synergy_heatmap(
    team_synergy: dict,
    benchmark_percentiles: dict,
) -> go.Figure:
    """
    Heatmap divergente de sinergia del jugador vs Challenger P50.
    Verde = mejor que Challenger, Rojo = peor.
    """
    roles = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
    n = len(roles)

    z = np.zeros((n, n))
    text_vals = np.full((n, n), "", dtype=object)

    pair_map = {
        ("JUNGLE", "SUPPORT"): "synergy_jg_sup",
        ("JUNGLE", "MID"):     "synergy_jg_mid",
        ("JUNGLE", "TOP"):     "synergy_jg_top",
        ("JUNGLE", "BOT"):     "synergy_jg_adc",
        ("BOT",    "SUPPORT"): "synergy_adc_sup",
        ("MID",    "BOT"):     "synergy_mid_bot",
        ("MID",    "TOP"):     "synergy_mid_top",
        ("MID",    "SUPPORT"): "synergy_mid_sup",
        ("TOP",    "BOT"):     "synergy_top_bot",
        ("TOP",    "SUPPORT"): "synergy_top_sup",
    }

    for (r1, r2), m_key in pair_map.items():
        idx1, idx2 = roles.index(r1), roles.index(r2)
        val = team_synergy.get(m_key, 0)
        b_data = benchmark_percentiles.get(m_key, 0.5)
        p50 = b_data.get("p50", 0.5) if isinstance(b_data, dict) else float(b_data)
        gap = val - p50
        z[idx1, idx2] = gap
        z[idx2, idx1] = gap
        text_vals[idx1, idx2] = f"{val:.2f}<br>P50:{p50:.2f}"
        text_vals[idx2, idx1] = f"{val:.2f}<br>P50:{p50:.2f}"

    colorscale = [
        [0.0, "#F87171"],
        [0.5, "#A855F7"],
        [1.0, "#4ADE80"],
    ]
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
        hovertemplate="<b>%{y}-%{x}</b><br>Gap vs P50: %{z:+.2f}<extra></extra>",
        showscale=True,
        colorbar=dict(title=dict(text="Gap vs P50", side="right"), thickness=12),
    ))

    fig.update_layout(
        **BASE_LAYOUT,
        xaxis=dict(side="top"),
        yaxis=dict(autorange="reversed"),
        height=380,
        title="Sync Gap vs Challenger P50",
    )
    return fig


def plot_match_momentum(df_timeline: pd.DataFrame, allied_team_id: int) -> go.Figure:
    """
    Ventaja de oro acumulada minuto a minuto (Momentum).
    """
    if df_timeline.empty:
        return go.Figure()

    df = df_timeline.copy()
    if "timestamp_min" not in df.columns:
        return go.Figure()
    if "team_id" not in df.columns and "participant_id" in df.columns:
        df["team_id"] = df["participant_id"].apply(lambda x: 100 if x <= 5 else 200)
    if "team_id" not in df.columns:
        return go.Figure()

    gold_by_team = (
        df.groupby(["timestamp_min", "team_id"])["total_gold"].sum().reset_index()
    )
    pivot = gold_by_team.pivot(index="timestamp_min", columns="team_id", values="total_gold").fillna(0)

    teams = sorted(pivot.columns.tolist())
    if len(teams) < 2:
        return go.Figure()

    enemy_id = [t for t in teams if t != allied_team_id]
    if not enemy_id:
        return go.Figure()
    enemy_id = enemy_id[0]

    pivot["advantage"] = pivot[allied_team_id] - pivot[enemy_id]
    pivot = pivot.reset_index()

    colors = pivot["advantage"].apply(lambda v: WIN_COLOR if v >= 0 else LOSS_COLOR).tolist()

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=pivot["timestamp_min"],
        y=pivot["advantage"],
        marker_color=colors,
        name="Gold Advantage",
        hovertemplate="Min %{x:.0f}: %{y:+,.0f}g<extra></extra>",
    ))
    fig.add_hline(y=0, line_color="#475569", line_width=1)
    fig.add_vline(x=15, line_dash="dot", line_color="#64748B", opacity=0.5,
                  annotation_text="15min", annotation_font=dict(size=10, color="#64748B"))

    fig.update_layout(
        **BASE_LAYOUT,
        title="Momentum: Ventaja de Oro vs Enemigo",
        xaxis_title="Minuto",
        yaxis_title="Gold Diff",
        bargap=0,
        height=300,
    )
    return fig
