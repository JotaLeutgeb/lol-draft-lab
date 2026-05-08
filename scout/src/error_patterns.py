"""
error_patterns.py — Detección de anti-patrones y errores recurrentes.

Identifica patrones de error específicos:
  1. EARLY_SOLO_DEATHS: Muertes 1v1 pre-15min sin assists enemigos
  2. GANK_DEATHS: Muertes con 2+ enemigos cerca
  3. OBJECTIVE_THROWS: Muertes en Baron/Drake pit que resultan en pérdida del objetivo
  4. VISION_GAPS: Muertes en zonas sin wards propios
  5. RECALL_TIMING: Recalls con >1500g unspent
  6. OVEREXTENSION: Muertes >2000 units de torre más cercana
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Constantes
EARLY_GAME_THRESHOLD = 15  # minutos
GANK_ASSIST_THRESHOLD = 2  # 2+ assists = gank
HIGH_UNSPENT_GOLD = 1500  # gold
OVEREXTENSION_DISTANCE = 2000  # units desde torre

# Posiciones aproximadas de torres (Summoner's Rift)
TOWER_POSITIONS = [
    # Blue side
    {"x": 1512, "y": 6699, "team": 100, "name": "blue_top_outer"},
    {"x": 5048, "y": 10504, "team": 100, "name": "blue_top_inner"},
    {"x": 981, "y": 10441, "team": 100, "name": "blue_top_inhib"},
    {"x": 10504, "y": 1029, "team": 100, "name": "blue_bot_outer"},
    {"x": 6919, "y": 1483, "team": 100, "name": "blue_bot_inner"},
    {"x": 10441, "y": 1051, "team": 100, "name": "blue_bot_inhib"},
    {"x": 5846, "y": 6396, "team": 100, "name": "blue_mid_outer"},
    {"x": 5048, "y": 4812, "team": 100, "name": "blue_mid_inner"},
    {"x": 3651, "y": 3696, "team": 100, "name": "blue_mid_inhib"},
    # Red side
    {"x": 13866, "y": 8005, "team": 200, "name": "red_top_outer"},
    {"x": 10481, "y": 13650, "team": 200, "name": "red_top_inner"},
    {"x": 13866, "y": 4505, "team": 200, "name": "red_bot_outer"},
    {"x": 8955, "y": 8510, "team": 200, "name": "red_mid_outer"},
    {"x": 11134, "y": 11207, "team": 200, "name": "red_mid_inner"},
]

# Zonas de objetivos (Baron/Drake)
BARON_PIT = {"x": (4500, 6000), "y": (9500, 11500)}
DRAKE_PIT = {"x": (9000, 10500), "y": (3500, 5000)}


def detect_error_patterns(
    df_player: pd.DataFrame,
    df_events: pd.DataFrame,
    df_timeline: pd.DataFrame,
    df_bench: pd.DataFrame,
    profile,
) -> list[dict]:
    """
    Detecta patrones de error recurrentes en las partidas del jugador.
    
    Args:
        df_player: DataFrame con partidas del jugador (filtrado)
        df_events: DataFrame con eventos de todas las partidas
        df_timeline: DataFrame con timeline
        df_bench: DataFrame con benchmarks Challenger
        profile: PlayerProfile
    
    Returns:
        Lista de dicts con:
        - pattern_name: str
        - frequency: int (# de ocurrencias)
        - frequency_pct: float (% de partidas afectadas)
        - impact_on_winrate: float (correlación con derrotas)
        - example_matches: list[str] (match_ids de ejemplo)
        - drill_suggestion: str (qué practicar)
        - severity: "critical" | "high" | "medium"
    """
    patterns = []
    
    if df_player.empty or df_events.empty:
        return patterns
    
    # Obtener participant_ids del jugador por partida
    player_pids = df_player.set_index("match_id")["participant_id"].to_dict()
    
    # 1. EARLY SOLO DEATHS
    early_solo = _detect_early_solo_deaths(df_events, df_player, player_pids)
    if early_solo:
        patterns.append(early_solo)
    
    # 2. GANK DEATHS
    gank_deaths = _detect_gank_deaths(df_events, df_player, player_pids)
    if gank_deaths:
        patterns.append(gank_deaths)
    
    # 3. OBJECTIVE THROWS
    obj_throws = _detect_objective_throws(df_events, df_player, player_pids)
    if obj_throws:
        patterns.append(obj_throws)
    
    # 4. OVEREXTENSION
    overextension = _detect_overextension(df_events, df_player, player_pids)
    if overextension:
        patterns.append(overextension)
    
    # 5. RECALL TIMING (requiere timeline)
    if not df_timeline.empty:
        recall_timing = _detect_poor_recall_timing(df_timeline, df_player, player_pids)
        if recall_timing:
            patterns.append(recall_timing)
    
    # 6. VISION GAPS (requiere ward data)
    vision_gaps = _detect_vision_gaps(df_events, df_player, player_pids, df_bench)
    if vision_gaps:
        patterns.append(vision_gaps)
    
    # Ordenar por severity
    severity_order = {"critical": 0, "high": 1, "medium": 2}
    patterns.sort(key=lambda p: severity_order.get(p["severity"], 3))
    
    return patterns


def _detect_early_solo_deaths(
    df_events: pd.DataFrame,
    df_player: pd.DataFrame,
    player_pids: dict,
) -> Optional[dict]:
    """
    Detecta muertes 1v1 en early game (pre-15min) sin assists enemigos.
    Indica problemas de trading, matchup knowledge o positioning.
    """
    early_deaths = []
    affected_matches = set()
    
    for match_id, pid in player_pids.items():
        match_events = df_events[df_events["match_id"] == match_id]
        
        deaths = match_events[
            (match_events["event_type"] == "CHAMPION_KILL") &
            (match_events["victim_id"] == pid) &
            (match_events["timestamp_min"] < EARLY_GAME_THRESHOLD)
        ].copy()
        
        for _, death in deaths.iterrows():
            assists_str = str(death.get("assisting_ids", ""))
            assists = []
            if assists_str and assists_str != "nan":
                try:
                    assists = [int(x) for x in assists_str.split(",") if x.strip().isdigit()]
                except:
                    pass
            
            # Solo death = sin assists
            if len(assists) == 0:
                early_deaths.append(match_id)
                affected_matches.add(match_id)
    
    if not early_deaths:
        return None
    
    total_matches = len(df_player)
    frequency = len(early_deaths)
    frequency_pct = round(len(affected_matches) / total_matches * 100, 1)
    
    # Correlación con derrotas
    affected_results = df_player[df_player["match_id"].isin(affected_matches)]["result"]
    win_rate_affected = affected_results.mean() if len(affected_results) > 0 else 0.5
    overall_wr = df_player["result"].mean()
    impact = round((overall_wr - win_rate_affected) * 100, 1)
    
    severity = "critical" if frequency_pct > 40 else "high" if frequency_pct > 25 else "medium"
    
    return {
        "pattern_name": "EARLY_SOLO_DEATHS",
        "title": f"Muertes Solo Pre-{EARLY_GAME_THRESHOLD}min",
        "frequency": frequency,
        "frequency_pct": frequency_pct,
        "impact_on_winrate": impact,
        "example_matches": list(affected_matches)[:3],
        "drill_suggestion": "Practica trading stance en Practice Tool. Estudia powerspikes de campeones enemigos. Revisa replays de tus primeras muertes.",
        "severity": severity,
        "description": f"{frequency} muertes solo en early game ({frequency_pct}% de partidas). WR en partidas afectadas: {win_rate_affected*100:.1f}% vs {overall_wr*100:.1f}% global.",
    }


def _detect_gank_deaths(
    df_events: pd.DataFrame,
    df_player: pd.DataFrame,
    player_pids: dict,
) -> Optional[dict]:
    """
    Detecta muertes con 2+ assists enemigos (ganks recibidos).
    Indica problemas de ward coverage o map awareness.
    """
    # Validate schema
    required_cols = ["event_type", "victim_id", "assisting_ids"]
    missing = [c for c in required_cols if c not in df_events.columns]
    if missing:
        logger.warning(f"Missing columns in df_events for gank detection: {missing}")
        return None
    
    gank_deaths = []
    affected_matches = set()
    
    for match_id, pid in player_pids.items():
        match_events = df_events[df_events["match_id"] == match_id]
        
        deaths = match_events[
            (match_events["event_type"] == "CHAMPION_KILL") &
            (match_events["victim_id"] == pid)
        ].copy()
        
        for _, death in deaths.iterrows():
            assists_str = str(death.get("assisting_ids", ""))
            assists = []
            if assists_str and assists_str != "nan":
                try:
                    assists = [int(x) for x in assists_str.split(",") if x.strip().isdigit()]
                except:
                    pass
            
            # Gank = 2+ assists
            if len(assists) >= GANK_ASSIST_THRESHOLD:
                gank_deaths.append(match_id)
                affected_matches.add(match_id)
    
    if not gank_deaths:
        return None
    
    total_matches = len(df_player)
    frequency = len(gank_deaths)
    frequency_pct = round(len(affected_matches) / total_matches * 100, 1)
    
    affected_results = df_player[df_player["match_id"].isin(affected_matches)]["result"]
    win_rate_affected = affected_results.mean() if len(affected_results) > 0 else 0.5
    overall_wr = df_player["result"].mean()
    impact = round((overall_wr - win_rate_affected) * 100, 1)
    
    severity = "high" if frequency_pct > 50 else "medium"
    
    return {
        "pattern_name": "GANK_DEATHS",
        "title": "Muertes por Gank (2+ enemigos)",
        "frequency": frequency,
        "frequency_pct": frequency_pct,
        "impact_on_winrate": impact,
        "example_matches": list(affected_matches)[:3],
        "drill_suggestion": "Mejora ward coverage en jungle. Practica mirar minimap cada 3-5s. Estudia timings de ganks enemigos.",
        "severity": severity,
        "description": f"{frequency} muertes por gank ({frequency_pct}% de partidas). Avg por partida afectada: {frequency/len(affected_matches):.1f}.",
    }


def _detect_objective_throws(
    df_events: pd.DataFrame,
    df_player: pd.DataFrame,
    player_pids: dict,
) -> Optional[dict]:
    """
    Detecta muertes en Baron/Drake pit que resultan en pérdida del objetivo.
    """
    throws = []
    affected_matches = set()
    
    for match_id, pid in player_pids.items():
        match_events = df_events[df_events["match_id"] == match_id]
        player_row = df_player[df_player["match_id"] == match_id].iloc[0]
        team_id = player_row["team_id"]
        
        deaths = match_events[
            (match_events["event_type"] == "CHAMPION_KILL") &
            (match_events["victim_id"] == pid)
        ].copy()
        
        objectives = match_events[
            (match_events["event_type"] == "ELITE_MONSTER_KILL") &
            (match_events["monster_type"].isin(["baron", "dragon"]))
        ].copy()
        
        for _, death in deaths.iterrows():
            x = death.get("position_x", 0)
            y = death.get("position_y", 0)
            t = death.get("timestamp_min", 0)
            
            # Check si murió en pit
            in_baron = BARON_PIT["x"][0] <= x <= BARON_PIT["x"][1] and BARON_PIT["y"][0] <= y <= BARON_PIT["y"][1]
            in_drake = DRAKE_PIT["x"][0] <= x <= DRAKE_PIT["x"][1] and DRAKE_PIT["y"][0] <= y <= DRAKE_PIT["y"][1]
            
            if not (in_baron or in_drake):
                continue
            
            # Check si el equipo enemigo tomó el objetivo en los próximos 30s
            nearby_objs = objectives[
                (objectives["timestamp_min"] >= t) &
                (objectives["timestamp_min"] <= t + 0.5) &
                (objectives["team_id"] != team_id)
            ]
            
            if not nearby_objs.empty:
                throws.append(match_id)
                affected_matches.add(match_id)
    
    if not throws:
        return None
    
    total_matches = len(df_player)
    frequency = len(throws)
    frequency_pct = round(len(affected_matches) / total_matches * 100, 1)
    
    severity = "critical" if frequency_pct > 20 else "high"
    
    return {
        "pattern_name": "OBJECTIVE_THROWS",
        "title": "Throws en Baron/Drake Pit",
        "frequency": frequency,
        "frequency_pct": frequency_pct,
        "impact_on_winrate": 15.0,  # Estimado alto
        "example_matches": list(affected_matches)[:3],
        "drill_suggestion": "Practica objective setup: ward coverage, positioning, engage timing. Nunca fightees Baron/Drake sin vision advantage.",
        "severity": severity,
        "description": f"{frequency} muertes en objective pits que resultaron en pérdida del objetivo ({frequency_pct}% de partidas).",
    }


def _detect_overextension(
    df_events: pd.DataFrame,
    df_player: pd.DataFrame,
    player_pids: dict,
) -> Optional[dict]:
    """
    Detecta muertes lejos de torres aliadas (>2000 units).
    """
    overextensions = []
    affected_matches = set()
    
    for match_id, pid in player_pids.items():
        match_events = df_events[df_events["match_id"] == match_id]
        player_row = df_player[df_player["match_id"] == match_id].iloc[0]
        team_id = player_row["team_id"]
        
        deaths = match_events[
            (match_events["event_type"] == "CHAMPION_KILL") &
            (match_events["victim_id"] == pid)
        ].copy()
        
        for _, death in deaths.iterrows():
            x = death.get("position_x", 0)
            y = death.get("position_y", 0)
            
            if x == 0 or y == 0:
                continue
            
            # Calcular distancia a torre aliada más cercana
            min_dist = float('inf')
            for tower in TOWER_POSITIONS:
                if tower["team"] == team_id:
                    dist = np.sqrt((x - tower["x"])**2 + (y - tower["y"])**2)
                    min_dist = min(min_dist, dist)
            
            if min_dist > OVEREXTENSION_DISTANCE:
                overextensions.append(match_id)
                affected_matches.add(match_id)
    
    if not overextensions:
        return None
    
    total_matches = len(df_player)
    frequency = len(overextensions)
    frequency_pct = round(len(affected_matches) / total_matches * 100, 1)
    
    severity = "high" if frequency_pct > 30 else "medium"
    
    return {
        "pattern_name": "OVEREXTENSION",
        "title": "Overextension (Lejos de Torres)",
        "frequency": frequency,
        "frequency_pct": frequency_pct,
        "impact_on_winrate": 10.0,
        "example_matches": list(affected_matches)[:3],
        "drill_suggestion": "Practica wave management. Mantén vision antes de pushear profundo. Trackea jungle enemigo.",
        "severity": severity,
        "description": f"{frequency} muertes >2000 units de torre aliada ({frequency_pct}% de partidas).",
    }


def _detect_poor_recall_timing(
    df_timeline: pd.DataFrame,
    df_player: pd.DataFrame,
    player_pids: dict,
) -> Optional[dict]:
    """
    Detecta recalls con >1500g unspent (ineficiencia de recursos).
    """
    poor_recalls = []
    affected_matches = set()
    
    for match_id, pid in player_pids.items():
        player_timeline = df_timeline[
            (df_timeline["match_id"] == match_id) &
            (df_timeline["participant_id"] == pid)
        ].copy()
        
        if player_timeline.empty or "total_gold" not in player_timeline.columns:
            continue
        
        # Ordenar por timestamp
        player_timeline = player_timeline.sort_values("timestamp_min")
        
        # Detectar recalls (gold sube significativamente sin CS)
        player_timeline["gold_diff"] = player_timeline["total_gold"].diff()
        player_timeline["cs_diff"] = player_timeline.get("cs", pd.Series(0, index=player_timeline.index)).diff()
        
        recalls = player_timeline[
            (player_timeline["gold_diff"].abs() > 500) &
            (player_timeline["cs_diff"].abs() < 5)
        ].copy()
        
        for _, recall in recalls.iterrows():
            unspent = recall["total_gold"]
            if unspent > HIGH_UNSPENT_GOLD:
                poor_recalls.append(match_id)
                affected_matches.add(match_id)
    
    if not poor_recalls:
        return None
    
    total_matches = len(df_player)
    frequency = len(poor_recalls)
    frequency_pct = round(len(affected_matches) / total_matches * 100, 1)
    
    return {
        "pattern_name": "POOR_RECALL_TIMING",
        "title": f"Recalls con >{HIGH_UNSPENT_GOLD}g Unspent",
        "frequency": frequency,
        "frequency_pct": frequency_pct,
        "impact_on_winrate": 5.0,
        "example_matches": list(affected_matches)[:3],
        "drill_suggestion": "Planea recalls con gold breakpoints (1300g, 1600g, 3200g). Evita recalls con >1500g sin comprar item completo.",
        "severity": "medium",
        "description": f"{frequency} recalls ineficientes ({frequency_pct}% de partidas). Pierdes powerspikes por mal timing.",
    }


def _detect_vision_gaps(
    df_events: pd.DataFrame,
    df_player: pd.DataFrame,
    player_pids: dict,
    df_bench: pd.DataFrame,
) -> Optional[dict]:
    """
    Detecta vision score bajo comparado con Challenger benchmark.
    """
    if df_player.empty or "vision_per_min" not in df_player.columns:
        return None
    
    avg_vision = df_player["vision_per_min"].mean()
    
    # Benchmark Challenger
    chall_vision = 1.5  # Default
    if not df_bench.empty and "vision_per_min" in df_bench.columns:
        role = df_player["role"].mode().iloc[0] if "role" in df_player.columns else None
        if role:
            role_bench = df_bench[df_bench["role"] == role]
            if not role_bench.empty:
                chall_vision = role_bench["vision_per_min"].median()
    
    gap_pct = ((avg_vision / chall_vision) - 1) * 100
    
    if gap_pct > -20:  # Solo reportar si está <20% por debajo
        return None
    
    low_vision_matches = df_player[df_player["vision_per_min"] < chall_vision * 0.7]["match_id"].tolist()
    
    return {
        "pattern_name": "VISION_GAPS",
        "title": "Vision Score Bajo vs Challenger",
        "frequency": len(low_vision_matches),
        "frequency_pct": round(len(low_vision_matches) / len(df_player) * 100, 1),
        "impact_on_winrate": 8.0,
        "example_matches": low_vision_matches[:3],
        "drill_suggestion": "Compra 2 control wards por back. Coloca wards en jungle enemigo pre-objectives. Usa trinket en CD.",
        "severity": "high" if gap_pct < -30 else "medium",
        "description": f"Vision/min: {avg_vision:.2f} vs Challenger: {chall_vision:.2f} ({gap_pct:+.1f}%). {len(low_vision_matches)} partidas críticas.",
    }
