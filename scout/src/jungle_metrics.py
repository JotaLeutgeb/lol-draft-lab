"""
jungle_metrics.py — Métricas específicas para análisis de jungle.

Calcula KPIs únicos para el rol de jungle:
  - Gank efficiency (ganks exitosos / ganks totales)
  - Objective control (% de drakes/barons/heralds asegurados)
  - Counter-jungle CS (CS robado del jungle enemigo)
  - Scuttle control (% de scuttles asegurados en early game)
  - Early pressure (First blood participation + ganks pre-10min)
  - Pathing efficiency (tiempo por cuadrante, clear speed)
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Constantes del mapa (Summoner's Rift)
MAP_WIDTH = 14820
MAP_HEIGHT = 14881
MAP_CENTER_X = MAP_WIDTH / 2
MAP_CENTER_Y = MAP_HEIGHT / 2

# Definición de cuadrantes del mapa
QUADRANTS = {
    "TOP_BLUE": {"x": (0, MAP_CENTER_X), "y": (MAP_CENTER_Y, MAP_HEIGHT)},
    "TOP_RED": {"x": (MAP_CENTER_X, MAP_WIDTH), "y": (MAP_CENTER_Y, MAP_HEIGHT)},
    "BOT_BLUE": {"x": (0, MAP_CENTER_X), "y": (0, MAP_CENTER_Y)},
    "BOT_RED": {"x": (MAP_CENTER_X, MAP_WIDTH), "y": (0, MAP_CENTER_Y)},
}

# Posiciones aproximadas de jungles (para detectar counter-jungling)
BLUE_JUNGLE_ZONES = [
    {"x": (1500, 5500), "y": (8500, 12500), "name": "blue_topside"},
    {"x": (1500, 5500), "y": (2000, 6000), "name": "blue_botside"},
]

RED_JUNGLE_ZONES = [
    {"x": (9000, 13500), "y": (8500, 12500), "name": "red_topside"},
    {"x": (9000, 13500), "y": (2000, 6000), "name": "red_botside"},
]


def compute_jungle_metrics(
    df_player: pd.DataFrame,
    df_events: pd.DataFrame,
    df_timeline: pd.DataFrame,
    profile,
) -> pd.DataFrame:
    """
    Calcula métricas específicas de jungle para cada partida del jugador.
    
    Args:
        df_player: DataFrame con partidas del jugador (filtrado)
        df_events: DataFrame con eventos de todas las partidas
        df_timeline: DataFrame con timeline de todas las partidas
        profile: PlayerProfile con game_name
    
    Returns:
        DataFrame con columnas adicionales:
        - gank_success_rate: % de ganks que resultan en kill
        - objective_control_pct: % de objetivos mayores asegurados
        - counter_jungle_cs: CS robado del jungle enemigo (estimado)
        - scuttle_control: % de scuttles asegurados (primeros 2)
        - early_pressure_score: FB participation + ganks pre-10min
        - avg_clear_speed: CS/min en jungle propio
    """
    if df_player.empty or df_events.empty:
        logger.warning("compute_jungle_metrics: DataFrames vacíos")
        return df_player
    
    # Validate required columns
    required_cols = ["match_id", "participant_id", "team_id"]
    missing = [c for c in required_cols if c not in df_player.columns]
    if missing:
        logger.error(f"Missing columns in df_player: {missing}")
        return df_player
    
    result_rows = []
    
    for _, player_row in df_player.iterrows():
        match_id = player_row["match_id"]
        participant_id = player_row["participant_id"]
        team_id = player_row["team_id"]
        
        match_events = df_events[df_events["match_id"] == match_id]
        
        # 1. GANK SUCCESS RATE
        gank_success = _compute_gank_success(match_events, participant_id, team_id)
        
        # 2. OBJECTIVE CONTROL
        obj_control = _compute_objective_control_pct(match_events, team_id)
        
        # 3. SCUTTLE CONTROL
        scuttle_pct = _compute_scuttle_control(match_events, team_id)
        
        # 4. EARLY PRESSURE
        early_pressure = _compute_early_pressure(match_events, participant_id, team_id)
        
        # 5. COUNTER JUNGLE CS (requiere timeline con posiciones)
        counter_cs = 0  # Placeholder - requiere position data
        
        # 6. CLEAR SPEED (CS/min en jungle propio)
        clear_speed = player_row.get("cs_per_min", 0)
        
        result_rows.append({
            "match_id": match_id,
            "participant_id": participant_id,
            "gank_success_rate": gank_success,
            "objective_control_pct": obj_control,
            "scuttle_control": scuttle_pct,
            "early_pressure_score": early_pressure,
            "counter_jungle_cs": counter_cs,
            "avg_clear_speed": clear_speed,
        })
    
    df_jungle = pd.DataFrame(result_rows)
    
    # Merge con df_player
    df_result = df_player.merge(
        df_jungle,
        on=["match_id", "participant_id"],
        how="left"
    )
    
    return df_result


def _compute_gank_success(df_events: pd.DataFrame, participant_id: int, team_id: int) -> float:
    """
    Calcula gank success rate: kills en lanes enemigas / total de apariciones en lanes.
    
    Un gank exitoso es un CHAMPION_KILL donde:
    - El jugador es killer o assister
    - La víctima es del equipo contrario
    - Ocurre en una lane (no en jungle)
    - Timestamp < 20min (early/mid game)
    """
    kills = df_events[
        (df_events["event_type"] == "CHAMPION_KILL") &
        (df_events["timestamp_min"] < 20)
    ].copy()
    
    if kills.empty:
        return 0.0
    
    def parse_assists(a_str):
        if not a_str or pd.isna(a_str) or str(a_str) == "nan":
            return []
        return [int(x) for x in str(a_str).split(",") if x.strip().isdigit()]
    
    successful_ganks = 0
    total_ganks = 0
    
    for _, kill in kills.iterrows():
        killer = int(kill["participant_id"])
        assists = parse_assists(kill.get("assisting_ids", ""))
        victim_team = int(kill.get("victim_team_id", 0))
        
        # Check si el jugador participó
        involved = [killer] + assists
        if participant_id not in involved:
            continue
        
        # Check si es un gank (víctima del equipo contrario)
        if victim_team != team_id and victim_team > 0:
            # Check si es en lane (posición fuera de jungle)
            pos_x = kill.get("position_x", 0)
            pos_y = kill.get("position_y", 0)
            
            if pos_x > 0 and pos_y > 0:  # Tiene posición válida
                is_in_lane = not _is_in_jungle(pos_x, pos_y, team_id)
                if is_in_lane:
                    total_ganks += 1
                    if killer == participant_id or participant_id in assists:
                        successful_ganks += 1
    
    return round(successful_ganks / total_ganks, 3) if total_ganks > 0 else 0.0


def _compute_objective_control_pct(df_events: pd.DataFrame, team_id: int) -> float:
    """
    Calcula % de objetivos mayores asegurados por el equipo.
    Objetivos mayores: Drake, Baron, Herald.
    """
    objectives = df_events[
        (df_events["event_type"] == "ELITE_MONSTER_KILL") &
        (df_events["monster_type"].isin(["dragon", "baron", "herald"]))
    ].copy()
    
    if objectives.empty:
        return 0.5  # Neutral si no hay objetivos
    
    total_objs = len(objectives)
    team_objs = len(objectives[objectives["team_id"] == team_id])
    
    return round(team_objs / total_objs, 3)


def _compute_scuttle_control(df_events: pd.DataFrame, team_id: int) -> float:
    """
    Calcula % de scuttles asegurados en early game (primeros 2 spawns).
    Scuttles spawnan a 3:15 y 5:15 aproximadamente.
    """
    scuttles = df_events[
        (df_events["event_type"] == "ELITE_MONSTER_KILL") &
        (df_events["monster_type"] == "voidgrub") &  # En versiones nuevas
        (df_events["timestamp_min"] < 8)  # Early game
    ].copy()
    
    if scuttles.empty:
        return 0.5  # Neutral si no hay datos
    
    total_scuttles = len(scuttles)
    team_scuttles = len(scuttles[scuttles["team_id"] == team_id])
    
    return round(team_scuttles / total_scuttles, 3)


def _compute_early_pressure(df_events: pd.DataFrame, participant_id: int, team_id: int) -> float:
    """
    Calcula early pressure score:
    - First blood participation: +1.0
    - Ganks pre-10min: +0.2 cada uno
    - Objetivos pre-10min: +0.3 cada uno
    
    Escala: 0-3 típicamente.
    """
    score = 0.0
    
    # First blood
    fb_kills = df_events[
        (df_events["event_type"] == "CHAMPION_KILL") &
        (df_events["timestamp_min"] < 5)
    ].copy()
    
    if not fb_kills.empty:
        first_kill = fb_kills.iloc[0]
        killer = int(first_kill["participant_id"])
        assists_str = str(first_kill.get("assisting_ids", ""))
        assists = []
        if assists_str and assists_str != "nan":
            try:
                assists = [int(x) for x in assists_str.split(",") if x.strip().isdigit()]
            except:
                pass
        
        if participant_id == killer or participant_id in assists:
            score += 1.0
    
    # Ganks pre-10min
    early_kills = df_events[
        (df_events["event_type"] == "CHAMPION_KILL") &
        (df_events["timestamp_min"] < 10) &
        (df_events["victim_team_id"] != team_id)
    ].copy()
    
    for _, kill in early_kills.iterrows():
        killer = int(kill["participant_id"])
        assists_str = str(kill.get("assisting_ids", ""))
        assists = []
        if assists_str and assists_str != "nan":
            try:
                assists = [int(x) for x in assists_str.split(",") if x.strip().isdigit()]
            except:
                pass
        
        if participant_id == killer or participant_id in assists:
            score += 0.2
    
    # Objetivos pre-10min
    early_objs = df_events[
        (df_events["event_type"] == "ELITE_MONSTER_KILL") &
        (df_events["timestamp_min"] < 10) &
        (df_events["team_id"] == team_id)
    ].copy()
    
    score += len(early_objs) * 0.3
    
    return round(score, 2)


def _is_in_jungle(x: int, y: int, team_id: int) -> bool:
    """
    Determina si una posición está dentro del jungle (blue o red).
    Usa zonas aproximadas definidas en constantes.
    """
    zones = BLUE_JUNGLE_ZONES if team_id == 100 else RED_JUNGLE_ZONES
    
    for zone in zones:
        x_range = zone["x"]
        y_range = zone["y"]
        if x_range[0] <= x <= x_range[1] and y_range[0] <= y <= y_range[1]:
            return True
    
    return False


def compute_pathing_efficiency(
    df_timeline: pd.DataFrame,
    df_player: pd.DataFrame,
    profile,
) -> pd.DataFrame:
    """
    Analiza rutas de jungle usando position_nodes del timeline.
    
    Calcula:
    - time_per_quadrant: Tiempo en cada cuadrante del mapa
    - clear_speed: CS/min en jungle propio
    - invasion_frequency: # de frames en jungle enemigo
    - recall_timing: Unspent gold promedio al hacer recall
    
    Args:
        df_timeline: DataFrame con pos_x, pos_y, total_gold
        df_player: DataFrame con partidas del jugador
        profile: PlayerProfile
    
    Returns:
        DataFrame con métricas de pathing por partida
    """
    if df_timeline.empty or df_player.empty:
        return df_player
    
    result_rows = []
    
    for _, player_row in df_player.iterrows():
        match_id = player_row["match_id"]
        participant_id = player_row["participant_id"]
        team_id = player_row["team_id"]
        
        player_timeline = df_timeline[
            (df_timeline["match_id"] == match_id) &
            (df_timeline["participant_id"] == participant_id)
        ].copy()
        
        if player_timeline.empty:
            continue
        
        # Calcular tiempo por cuadrante
        quadrant_times = _compute_quadrant_times(player_timeline)
        
        # Calcular invasiones (frames en jungle enemigo)
        invasions = _compute_invasions(player_timeline, team_id)
        
        # Recall timing (unspent gold)
        avg_unspent = _compute_recall_timing(player_timeline)
        
        result_rows.append({
            "match_id": match_id,
            "participant_id": participant_id,
            "time_topside_pct": quadrant_times.get("topside", 0),
            "time_botside_pct": quadrant_times.get("botside", 0),
            "invasion_count": invasions,
            "avg_unspent_gold": avg_unspent,
        })
    
    df_pathing = pd.DataFrame(result_rows)
    
    if df_pathing.empty:
        return df_player
    
    df_result = df_player.merge(
        df_pathing,
        on=["match_id", "participant_id"],
        how="left"
    )
    
    return df_result


def _compute_quadrant_times(df_timeline: pd.DataFrame) -> dict:
    """
    Calcula % de tiempo en cada lado del mapa (topside vs botside).
    """
    if "pos_y" not in df_timeline.columns or df_timeline.empty:
        return {"topside": 0.5, "botside": 0.5}
    
    total_frames = len(df_timeline)
    topside_frames = len(df_timeline[df_timeline["pos_y"] > MAP_CENTER_Y])
    
    topside_pct = round(topside_frames / total_frames, 3) if total_frames > 0 else 0.5
    botside_pct = round(1.0 - topside_pct, 3)
    
    return {"topside": topside_pct, "botside": botside_pct}


def _compute_invasions(df_timeline: pd.DataFrame, team_id: int) -> int:
    """
    Cuenta frames donde el jugador está en jungle enemigo.
    """
    if "pos_x" not in df_timeline.columns or "pos_y" not in df_timeline.columns:
        return 0
    
    enemy_zones = RED_JUNGLE_ZONES if team_id == 100 else BLUE_JUNGLE_ZONES
    invasions = 0
    
    for _, frame in df_timeline.iterrows():
        x = frame.get("pos_x", 0)
        y = frame.get("pos_y", 0)
        
        if x == 0 or y == 0:
            continue
        
        for zone in enemy_zones:
            x_range = zone["x"]
            y_range = zone["y"]
            if x_range[0] <= x <= x_range[1] and y_range[0] <= y <= y_range[1]:
                invasions += 1
                break
    
    return invasions


def _compute_recall_timing(df_timeline: pd.DataFrame) -> float:
    """
    Calcula unspent gold promedio al hacer recall.
    Un recall se detecta como un salto grande en gold sin cambio en CS.
    """
    if df_timeline.empty or "total_gold" not in df_timeline.columns:
        return 0.0
    
    # Ordenar por timestamp
    df = df_timeline.sort_values("timestamp_min").copy()
    
    # Detectar recalls (gold sube pero CS no cambia significativamente)
    df["gold_diff"] = df["total_gold"].diff()
    df["cs_diff"] = df.get("cs", pd.Series(0, index=df.index)).diff()
    
    # Recall = gold_diff < -500 (gastó oro) o gold_diff > 1000 (volvió a base)
    recalls = df[
        (df["gold_diff"].abs() > 500) &
        (df["cs_diff"].abs() < 5)
    ].copy()
    
    if recalls.empty:
        return 0.0
    
    # Unspent gold = total_gold antes del recall
    avg_unspent = recalls["total_gold"].mean()
    
    return round(float(avg_unspent), 1)
