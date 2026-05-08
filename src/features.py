"""
features.py — Feature engineering para análisis de partidas LoL.

Todas las funciones reciben DataFrames normalizados (output de data_loader)
y devuelven DataFrames con columnas adicionales. Diseño funcional puro:
no modifican el DataFrame original, devuelven copias/nuevos DFs.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import StandardScaler

from src.config import (
    GAME_PHASES,
    GOLD_DIFF_SNAPSHOTS_MIN,
    IMPACT_WEIGHTS,
    OBJECTIVE_MONSTER_TYPES,
    ROLE_ORDER,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Constantes para Position Nodes v2
# ──────────────────────────────────────────────────────────────────

# Passive gold rate: ~122.4 gold/min = ~2.04 gold/s (base, no runes/items)
_PASSIVE_GOLD_PER_SECOND: float = 2.04

# Approximate item costs for ITEM_PURCHASED subtraction.
# In production, replace with a full Data Dragon lookup.
_ITEM_COST_MAP: dict[int, int] = {
    1001: 300, 1004: 350, 1006: 450, 1011: 1100, 1018: 875,
    1026: 1250, 1027: 350, 1028: 400, 1029: 350, 1031: 1100,
    1033: 450, 1036: 850, 1037: 875, 1038: 1300, 1042: 250,
    1043: 875, 1052: 435, 1053: 800, 1054: 350, 1055: 450,
    1056: 400, 1058: 435, 1082: 350, 1083: 350, 2003: 500,
    2031: 500, 2055: 350, 2138: 350, 2139: 500, 2140: 500,
    2420: 3600, 3006: 1100, 3020: 1100, 3031: 3400, 3047: 1100,
    3078: 3333, 3089: 3600, 3111: 1100, 3157: 2600, 3508: 3300,
    3814: 2400, 4005: 2300, 4633: 3000, 4644: 3000, 6653: 3000,
    6655: 3000, 6656: 3000, 6662: 3300, 6664: 3200, 6671: 3400,
    6672: 3400, 6673: 3400, 6675: 3300, 6676: 3300, 6691: 3400,
    6692: 3200, 6693: 3200, 6694: 3400, 7000: 3000,
}

_POSITION_EVENT_TYPES: set[str] = {
    "CHAMPION_KILL", "WARD_PLACED", "WARD_KILL",
    "ITEM_PURCHASED", "BUILDING_KILL", "ELITE_MONSTER_KILL",
}


# ──────────────────────────────────────────────────────────────────
# Métricas por jugador × partida
# ──────────────────────────────────────────────────────────────────

def compute_player_metrics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    dur = df["duration_minutes"].clip(lower=0.01)

    if "damage_mitigated" not in df.columns:
        df["damage_mitigated"] = 0.0

    # Métricas base crudas
    df["damage_per_gold"] = df["total_damage"].astype(float) / df["gold_earned"].clip(lower=1).astype(float)
    df["mitigation_per_gold"] = df["damage_mitigated"].astype(float) / df["gold_earned"].clip(lower=1).astype(float)
    
    # damage_buildings ya viene normalizado desde data_loader
    if "damage_buildings" not in df.columns:
        df["damage_buildings"] = 0
    
    df["kda"] = (df["kills"] + df["assists"]) / df["deaths"].clip(lower=1)
    df["cs_per_min"] = df.get("cs", 0) / dur
    df["gold_per_min"] = df["gold_earned"] / dur
    df["damage_per_min"] = df["total_damage"] / dur
    df["vision_per_min"] = df["vision_score"] / dur
    df["cc_per_min"] = df.get("time_cc", 0) / dur
    df["damage_taken_per_min"] = df["damage_taken"] / dur

    # Kill participation y Gold Efficiency se mantienen igual...
    team_gold = df.groupby(["match_id", "team_id"])["gold_earned"].transform("sum").clip(lower=1)
    team_damage = df.groupby(["match_id", "team_id"])["total_damage"].transform("sum").clip(lower=1)
    team_vision = df.groupby(["match_id", "team_id"])["vision_score"].transform("sum").clip(lower=1)

    gold_share = df["gold_earned"] / team_gold
    damage_share = df["total_damage"] / team_damage
    vision_share = df["vision_score"] / team_vision

    team_kills = df.groupby(["match_id", "team_id"])["kills"].transform("sum").clip(lower=1)
    df["kill_participation"] = (df["kills"] + df["assists"]) / team_kills

    contribution = (0.5 * damage_share) + (0.3 * df["kill_participation"]) + (0.2 * vision_share)
    df["gold_efficiency"] = contribution / gold_share.clip(lower=0.01)
    df["utility_score"] = (
    df.get("total_heal", 0) + 
    (df.get("control_wards", 0) * 500) + 
    (df.get("wards_killed", 0) * 200)
) / dur

    return df



# ──────────────────────────────────────────────────────────────────
# Gold difference vs oponente por rol
# ──────────────────────────────────────────────────────────────────

def compute_gold_diff(
    df_timeline: pd.DataFrame,
    df_participants: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calcula la diferencia de gold vs el jugador del mismo rol en el equipo
    contrario, a los minutos definidos en GOLD_DIFF_SNAPSHOTS_MIN.

    Requiere que df_participants tenga columnas: match_id, participant_id, role, team_id.
    Requiere que df_timeline tenga columnas: match_id, participant_id, timestamp_min, total_gold.

    Retorna DataFrame con columnas:
      match_id, participant_id, role, gold_diff_min5, gold_diff_min10, gold_diff_min15
      (los minutos disponibles según GOLD_DIFF_SNAPSHOTS_MIN)
    """
    if df_timeline.empty or df_participants.empty:
        logger.warning("compute_gold_diff: DataFrames vacíos, retornando vacío")
        return pd.DataFrame()

    # 1. Limpieza absoluta: borrar columnas de metadatos si ya existen en el timeline
    tl = df_timeline.copy()
    for col in ["role", "team_id", "game_name"]:
        if col in tl.columns:
            tl = tl.drop(columns=[col])
    
    # 2. Inyección fresca desde participantes (Cruce Numérico)
    meta = df_participants[["match_id", "participant_id", "role", "team_id", "game_name"]].drop_duplicates().copy()
    
    # Estandarizar IDs: match_id como string, participant_id como entero
    meta["match_id"] = meta["match_id"].astype(str)
    meta["participant_id"] = pd.to_numeric(meta["participant_id"], downcast="integer", errors="coerce").fillna(0).astype(int)
    
    tl["match_id"] = tl["match_id"].astype(str)
    tl["participant_id"] = pd.to_numeric(tl["participant_id"], downcast="integer", errors="coerce").fillna(0).astype(int)
    
    tl = tl.merge(meta, on=["match_id", "participant_id"], how="left")
    
    # 3. Verificación de pánico: si aún no está 'role', algo muy raro pasa con el merge
    if "role" not in tl.columns:
        # Fallback de emergencia: reindexar con NaNs para evitar el crash
        for col in ["role", "team_id", "game_name"]:
            if col not in tl.columns: tl[col] = None

    # Referencia al roster de equipo config.py
    from src.config import TEAM_PLAYER_ROLE_MAP
    team_roster = {k.lower() for k in TEAM_PLAYER_ROLE_MAP.keys()}

    result_rows: list[dict] = []

    for snap_min in GOLD_DIFF_SNAPSHOTS_MIN:
        # Obtener el frame más cercano al minuto objetivo (±1min)
        snap = tl[
            (tl["timestamp_min"] >= snap_min - 1) &
            (tl["timestamp_min"] <= snap_min + 1)
        ].copy()

        if snap.empty:
            continue

        # Por partida y jugador, tomar el frame más cercano al snapshot
        snap["dist"] = abs(snap["timestamp_min"] - snap_min)
        snap = snap.sort_values("dist").drop_duplicates(["match_id", "participant_id"])

        # Calcular gold del oponente del mismo rol (equipo contrario)
        # Hacemos un self-join sobre partida y rol, filtrando team_id distinto
        snap_a = snap.rename(columns={"total_gold": "gold_self",   "team_id": "team_a"})
        snap_b = snap.rename(columns={"total_gold": "gold_opp",    "team_id": "team_b",
                                      "participant_id": "opp_participant_id"})

        # Usar reindex para evitar KeyError si falta alguna columna
        cols_b = ["match_id", "role", "team_b", "gold_opp"]
        merged = snap_a.merge(
            snap_b.reindex(columns=cols_b),
            on=["match_id", "role"],
        )
        # Filtrar: solo pares de equipos distintos
        merged = merged[merged["team_a"] != merged["team_b"]].copy()

        # Filtrar: conservar solo los calculos base referidos a NUESTRO equipo.
        # Si no borramos los del equipo rival, la diferencia oro X sumada al rival (-X) promedia == 0.
        merged["_gn"] = merged["game_name"].str.lower()
        merged = merged[merged["_gn"].isin(team_roster)].copy()

        if merged.empty:
            continue

        merged[f"gold_diff_min{snap_min}"] = merged["gold_self"] - merged["gold_opp"]

        keep_cols = ["match_id", "participant_id", "role", f"gold_diff_min{snap_min}"]
        result_rows.append(merged.reindex(columns=keep_cols))

    if not result_rows:
        logger.warning("compute_gold_diff: sin snapshots disponibles")
        return pd.DataFrame()

    # Combinar todos los snapshots en un DF único por jugador×partida
    from functools import reduce
    df_diff = reduce(
        lambda left, right: pd.merge(left, right, on=["match_id", "participant_id", "role"], how="outer"),
        result_rows,
    )
    return df_diff


# ──────────────────────────────────────────────────────────────────
# Detección de Gank Deaths vs Solo Lane Deaths
# ──────────────────────────────────────────────────────────────────

def compute_gank_deaths(
    df_events: pd.DataFrame,
    df_participants: pd.DataFrame,
) -> pd.DataFrame:
    """
    Detecta para cada jugador del equipo cuántas muertes fueron provocadas
    por el jungler enemigo (ganks) vs muertes en 1v1 de línea (solo).

    Lógica:
      - Para cada CHAMPION_KILL donde la víctima es un titular:
        - Si el jungler rival aparece como killer O como asistente → gank_death
        - Si no → solo_death

    Args:
        df_events:      DataFrame de eventos con assisting_ids y event_type.
        df_participants: DataFrame de participantes con role, team_id, game_name.

    Returns:
        DataFrame con columnas:
          game_name, role, avg_gank_deaths, avg_solo_deaths
          (promedios por partida jugada)
    """
    if df_events.empty or df_participants.empty:
        return pd.DataFrame()

    from src.config import TEAM_PLAYER_ROLE_MAP
    team_names = set(TEAM_PLAYER_ROLE_MAP.keys())

    kills = df_events[df_events["event_type"] == "CHAMPION_KILL"].copy()
    if kills.empty:
        return pd.DataFrame()

    meta = df_participants[
        ["match_id", "participant_id", "role", "team_id", "game_name"]
    ].drop_duplicates()

    rows: list[dict] = []

    for match_id, match_kills in kills.groupby("match_id"):
        match_meta = meta[meta["match_id"] == match_id]

        # Jugadores del equipo en esta partida
        team_meta = match_meta[match_meta["game_name"].str.lower().isin(team_names)]
        if team_meta.empty:
            continue

        team_p_ids = set(team_meta["participant_id"].tolist())
        team_team_id = int(team_meta["team_id"].mode().iloc[0])

        # Jungler enemigo (rol JUNGLE, equipo contrario)
        enemy_meta = match_meta[match_meta["team_id"] != team_team_id]
        enemy_junglers = enemy_meta[enemy_meta["role"] == "JUNGLE"]["participant_id"].tolist()
        enemy_jungler_id = enemy_junglers[0] if enemy_junglers else None

        # Solo muertes donde la víctima es uno de nuestros jugadores
        our_deaths = match_kills[match_kills["victim_id"].isin(team_p_ids)]

        for _, death in our_deaths.iterrows():
            victim_pid = int(death["victim_id"])
            killer_pid = int(death["participant_id"])

            # Parsear asistentes
            assists_str = str(death.get("assisting_ids", ""))
            assist_ids: set[int] = set()
            if assists_str and assists_str not in ("", "nan"):
                try:
                    assist_ids = {int(x) for x in assists_str.split(",") if x.strip()}
                except ValueError:
                    pass

            player_row = team_meta[team_meta["participant_id"] == victim_pid]
            if player_row.empty:
                continue

            ts_min = death["timestamp_min"]
            if ts_min >= 15:
                continue

            victim_role = player_row.iloc[0]["role"]
            
            if victim_role == "JUNGLE":
                is_gank = float("nan")
                is_solo = float("nan")
            else:
                has_enemy_jungle = (
                    enemy_jungler_id is not None
                    and (killer_pid == enemy_jungler_id or enemy_jungler_id in assist_ids)
                )
                is_gank = has_enemy_jungle
                is_solo = not has_enemy_jungle

            rows.append({
                "match_id":      match_id,
                "game_name":     player_row.iloc[0]["game_name"],
                "role":          victim_role,
                "is_gank_death": is_gank,
                "is_solo_death": is_solo,
            })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["is_gank_death"] = pd.to_numeric(df["is_gank_death"], errors="coerce")
    df["is_solo_death"] = pd.to_numeric(df["is_solo_death"], errors="coerce")

    # Contar por jugador y partida para calcular el promedio por partida real
    n_games = df.groupby("game_name")["match_id"].nunique().rename("n_matches")
    agg = df.groupby(["game_name", "role"]).agg(
        gank_deaths=("is_gank_death", "sum"),
        solo_deaths=("is_solo_death", "sum"),
    ).reset_index()

    agg = agg.merge(n_games, on="game_name", how="left")
    
    # Promediar por la cantidad de partidas que jugó el equipo
    agg["avg_gank_deaths"] = (agg["gank_deaths"] / agg["n_matches"]).round(2)
    agg["avg_solo_deaths"] = (agg["solo_deaths"] / agg["n_matches"]).round(2)

    logger.debug(f"compute_gank_deaths: {len(agg)} jugadores procesados")
    return agg[["game_name", "role", "avg_gank_deaths", "avg_solo_deaths"]]


# ──────────────────────────────────────────────────────────────────
# Control de objetivos
# ──────────────────────────────────────────────────────────────────

def compute_objective_control(df_events: pd.DataFrame, df_participants: pd.DataFrame = None) -> pd.DataFrame:
    """
    Calcula el control de objetivos a nivel individual.
    Siempre retorna las columnas base para evitar KeyErrors posteriores.
    """
    # Estructura base garantizada
    empty_df = pd.DataFrame(columns=["match_id", "participant_id", "objective_score_raw", "team_id"])
    
    if df_events.empty:
        return empty_df

    # Filtrar solo eventos relevantes
    objectives = df_events[df_events["event_type"].isin(["ELITE_MONSTER_KILL", "BUILDING_KILL"])].copy()
    if objectives.empty:
        return empty_df

    rows = []
    
    # Pesos balanceados para los objetivos
    weights = {
        "baron": 2.5,
        "herald": 1.5,
        "dragon": 1.0,
        "voidgrub": 0.5,
        "tower": 1.0,
        "inhibitor": 1.5
    }

    # Analizar cada partida
    for match_id, match_objs in objectives.groupby("match_id"):
        # Diccionario para ir sumando el puntaje por jugador en esta partida
        player_scores = {}
        
        for _, obj in match_objs.iterrows():
            killer_id = obj["participant_id"]
            
            # Extraer asistentes de forma segura
            assists_str = str(obj.get("assisting_ids", ""))
            assist_ids = []
            if assists_str and assists_str not in ("nan", ""):
                try:
                    assist_ids = [int(x.strip()) for x in assists_str.split(",") if x.strip()]
                except ValueError:
                    pass
            
            # Determinar el tipo y peso del objetivo
            obj_type = obj.get("monster_type") if obj["event_type"] == "ELITE_MONSTER_KILL" else obj.get("building_type")
            weight = weights.get(obj_type, 0)
            
            if weight == 0:
                continue
                
            # Asignar puntos al killer (100% del peso)
            if killer_id > 0:
                player_scores[killer_id] = player_scores.get(killer_id, 0.0) + weight
                
            # Asignar puntos a los asistentes (100% del peso, premiamos la rotación grupal)
            for ast_id in assist_ids:
                if ast_id > 0:
                    player_scores[ast_id] = player_scores.get(ast_id, 0.0) + weight

        # Formatear la salida
        for pid, score in player_scores.items():
            rows.append({
                "match_id": match_id,
                "participant_id": pid,
                "objective_score_raw": score
            })

    df = pd.DataFrame(rows)
    
    # Inyectar team_id si hay metadatos disponibles
    if df_participants is not None and not df_participants.empty and not df.empty:
        meta = df_participants[["match_id", "participant_id", "team_id"]].drop_duplicates()
        df = df.merge(meta, on=["match_id", "participant_id"], how="left")
        
    return df


# ──────────────────────────────────────────────────────────────────
# Estadísticas por fase del juego
# ──────────────────────────────────────────────────────────────────

def compute_phase_stats(df_timeline: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega métricas del timeline por jugador y fase del juego.

    Fases: early (0–15 min), mid (15–25 min), late (25+ min).

    Columnas de salida:
      match_id, participant_id, phase,
      avg_gold_per_min, avg_cs_per_min, gold_at_end_of_phase

    Args:
        df_timeline: DataFrame de normalize_timeline (columna timestamp_min).

    Returns:
        DataFrame con 1 fila por (match_id, participant_id, phase).
    """
    if df_timeline.empty:
        return pd.DataFrame()

    df = df_timeline.copy()
    rows: list[dict] = []

    for phase_name, (t_start, t_end) in GAME_PHASES.items():
        phase_mask = (df["timestamp_min"] >= t_start) & (df["timestamp_min"] < t_end)
        phase_df = df[phase_mask]

        if phase_df.empty:
            continue

        # Gold por minuto aproximado dentro de la fase
        grouped = phase_df.groupby(["match_id", "participant_id"]).agg(
            gold_start=("total_gold", "first"),
            gold_end=("total_gold", "last"),
            cs_start=("cs", "first"),
            cs_end=("cs", "last"),
            ts_start=("timestamp_min", "first"),
            ts_end=("timestamp_min", "last"),
        ).reset_index()

        grouped["phase_duration"] = (grouped["ts_end"] - grouped["ts_start"]).clip(lower=0.01)
        grouped["avg_gold_per_min"] = (
            (grouped["gold_end"] - grouped["gold_start"]) / grouped["phase_duration"]
        )
        grouped["avg_cs_per_min"] = (
            (grouped["cs_end"] - grouped["cs_start"]) / grouped["phase_duration"]
        )
        grouped["phase"] = phase_name
        grouped["gold_at_end_of_phase"] = grouped["gold_end"]

        rows.append(grouped[[
            "match_id", "participant_id", "phase",
            "avg_gold_per_min", "avg_cs_per_min", "gold_at_end_of_phase"
        ]])

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# ──────────────────────────────────────────────────────────────────
# Impact Score compuesto por jugador
# ──────────────────────────────────────────────────────────────────

# Reemplazar en src/features.py
def compute_impact_score(
    df_participants: pd.DataFrame,
    df_objectives:   pd.DataFrame,
    df_bench:        pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Calcula el impacto profesional basado en 4 pilares equilibrados (25% c/u).
    """
    if df_participants.empty:
        return df_participants

    df = df_participants.copy()

    # 1. Añadir objective_control
    # Limpiamos columnas de impacto/pilares si ya existen para evitar duplicados
    cols_to_clean = [
        "objective_score_raw", "objective_control", "impact_score",
        "pilar_combat_efficiency", "pilar_map_pressure", 
        "pilar_tactical_utility", "pilar_team_synergy"
    ]
    df = df.drop(columns=[c for c in cols_to_clean if c in df.columns])

    if not df_objectives.empty and "objective_score_raw" in df_objectives.columns:
        df = df.merge(
            df_objectives[["match_id", "participant_id", "objective_score_raw"]],
            on=["match_id", "participant_id"],
            how="left",
        )
        df["objective_control"] = df.get("objective_score_raw", pd.Series(0)).fillna(0)
    else:
        df["objective_control"] = 0.0

    # 2. Lógica Dinámica vs Benchmark
    if df_bench is not None and not df_bench.empty:
        # Pre-calcular medianas para acceso rápido O(1)
        # Aseguramos que columnas existan para evitar KeyErrors
        numeric_cols = df_bench.select_dtypes(include="number").columns.tolist()
        bench_champ_role = df_bench.groupby(['champion', 'role'])[numeric_cols].median()
        bench_role = df_bench.groupby('role')[numeric_cols].median()

        def calculate_dynamic_ratios(row):
            champ, role = row.get('champion'), row.get('role')
            idx = (champ, role)
            
            # Buscar estándar de comparación (Campeón específico o Fallback al Rol)
            if idx in bench_champ_role.index:
                b = bench_champ_role.loc[idx]
            elif role in bench_role.index:
                b = bench_role.loc[role]
            else:
                return pd.Series({"ratio_dpm": 1.0, "ratio_mitigation": 1.0, "ratio_survival": 1.0, "ratio_vision": 1.0})

            # Generar Ratios (Valor Jugador / Mediana Challenger)
            # Para supervivencia, es inverso: menos muertes = mayor ratio
            r_dpm = row.get('damage_per_min', 0) / max(b.get('damage_per_min', 1), 1)
            r_mit = row.get('damage_mitigated', 0) / max(b.get('damage_mitigated', 1), 1)
            r_surv = max(b.get('deaths', 1), 0.1) / max(row.get('deaths', 1), 0.1) 
            r_vis = row.get('vision_per_min', 0) / max(b.get('vision_per_min', 0.1), 0.1)

            # Capamos los ratios en 2.0 para evitar outliers extremos distorsionando el score
            return pd.Series({
                "ratio_dpm": min(r_dpm, 2.0), 
                "ratio_mitigation": min(r_mit, 2.0), 
                "ratio_survival": min(r_surv, 2.0), 
                "ratio_vision": min(r_vis, 2.0)
            })

        # Aplicar vectorización y fusionar
        df_ratios = df.apply(calculate_dynamic_ratios, axis=1)
        df = pd.concat([df, df_ratios], axis=1)

        # 3. Construcción de Pilares basados en Ratios (Escala ~0 a 2.0, se normalizará después)
        # Resilience: 60% no morir tontamente, 40% mitigar eficientemente si aplica
        df["resilience_index"] = (df["ratio_survival"] * 0.6) + (df["ratio_mitigation"] * 0.4)
        
        df["pilar_combat_efficiency"] = (df["ratio_dpm"] * 0.5) + (df["kill_participation"] * 0.5)
        df["pilar_map_pressure"] = df["objective_control"] * 0.1 # Simplificado, ajustar según escala de objetivos
        df["pilar_tactical_utility"] = (df["ratio_vision"] * 0.5) + (df["resilience_index"] * 0.5)
        df["pilar_team_synergy"] = df.get("synergy_score", 0.0)

    else:
        # Fallback de emergencia (Min-Max) si no hay base de datos cargada
        logger.warning("Faltan benchmarks. Usando fallback Min-Max para Impact Score.")
        
        # FIX: Calcular resiliencia relativa en la partida actual
        max_deaths = df.groupby("match_id")["deaths"].transform("max").clip(lower=1)
        max_mitigation = df.groupby("match_id")["damage_mitigated"].transform("max").clip(lower=1)
        
        norm_survival = (max_deaths - df["deaths"]) / max_deaths
        norm_mitigation = df["damage_mitigated"] / max_mitigation
        
        df["resilience_index"] = (norm_survival * 0.6) + (norm_mitigation * 0.4)
        
        # Pilares Min-Max
        df["pilar_combat_efficiency"] = df["damage_per_min"] / df.groupby("match_id")["damage_per_min"].transform("max").clip(lower=1)
        df["pilar_map_pressure"] = df["objective_control"] / df.groupby("match_id")["objective_control"].transform("max").clip(lower=1)
        df["pilar_tactical_utility"] = df["vision_per_min"] / df.groupby("match_id")["vision_per_min"].transform("max").clip(lower=1)
        df["pilar_team_synergy"] = df.get("synergy_score", 0.0)

    # 4. Impact Score Final (Normalizado a escala 0-1)
    raw_impact = (
        0.25 * df["pilar_combat_efficiency"] +
        0.25 * df["pilar_map_pressure"] +
        0.25 * df["pilar_tactical_utility"] +
        0.25 * df["pilar_team_synergy"]
    ).fillna(0.0)
    
    # Capamos a 1.0 para mantener la escala del dashboard consistente
    df["impact_score"] = raw_impact.clip(0, 1.0)

    return df


# ──────────────────────────────────────────────────────────────────
# Sinergia Profesional (Shared Kill Participation - SKP)
# ──────────────────────────────────────────────────────────────────

def compute_synergy_matrix(
    df_events: pd.DataFrame, 
    df_participants: pd.DataFrame
) -> dict[str, float]:
    """
    Calcula la sinergia profesional basada en SKP (Shared Kill Participation).
    Mide la coordinación real en asesinatos y objetivos.
    
    Fórmula: SKP = (Kills conjuntas) / (Total Kills Equipo)
    Aplica multiplicadores por rol:
      - JG + SUP: 2.0x
      - JG + MID: 1.5x
      - JG + TOP: 1.2x
      - ADC + SUP: 0.8x
      - Otros: 1.0x
    """
    if df_events.empty or df_participants.empty:
        return {}

    kills = df_events[df_events["event_type"] == "CHAMPION_KILL"].copy()
    if kills.empty:
        return {}

    # Mapeo de participant_id -> role para esta partida
    # Solo procesamos un equipo a la vez (asumimos que es para el equipo analizado)
    # En benchmarks, lo hacemos por cada equipo de la partida.
    
    # Identificar equipos
    results = {}
    for team_id in [100, 200]:
        team_meta = df_participants[df_participants["team_id"] == team_id]
        if team_meta.empty: continue
        
        role_map = team_meta.set_index("participant_id")["role"].to_dict()
        
        # Denominador: Todas las kills del equipo (para que la métrica sea sobre el impacto total)
        team_kills_all = kills[kills["team_id"] == team_id].copy()
        total_team_kills = len(team_kills_all)
        
        # Mantenemos las kills filtradas solo para el cálculo de pares si fuera necesario, 
        # pero usaremos team_kills_all para el bucle.
        
        if total_team_kills == 0:
            results[team_id] = {p[2]: 0.0 for p in [
                ("JUNGLE", "SUPPORT", "synergy_jg_sup"),
                ("JUNGLE", "MID",     "synergy_jg_mid"),
                ("JUNGLE", "TOP",     "synergy_jg_top"),
                ("BOT",    "SUPPORT", "synergy_adc_sup"),
                ("MID",    "BOT",     "synergy_mid_bot"),
                ("MID",    "TOP",     "synergy_mid_top"),
                ("TOP",    "BOT",     "synergy_top_bot"),
                ("TOP",    "SUPPORT", "synergy_top_sup"),
                ("MID",    "SUPPORT", "synergy_mid_sup"),
                ("JUNGLE", "BOT",     "synergy_jg_adc"),
            ]}
            continue

        # Función para verificar si un par participó en una kill
        def check_pair(kill_row, role_a, role_b):
            killer_id = kill_row["participant_id"]
            assists = kill_row.get("assisting_ids", [])
            
            # Blindaje: Asegurar que assists sea una lista
            if isinstance(assists, str):
                try:
                    assist_ids = [int(x.strip()) for x in assists.split(",") if x.strip() and x != "nan"]
                except:
                    assist_ids = []
            elif isinstance(assists, (list, np.ndarray)):
                assist_ids = list(assists)
            else:
                assist_ids = []
            
            pids_involved = [killer_id] + assist_ids
            # Obtener roles de los participantes involucrados
            roles_involved = [role_map.get(pid) for pid in pids_involved if pid in role_map]
            
            # La sinergia ocurre si AMBOS roles están en la jugada
            return role_a in roles_involved and role_b in roles_involved

        # Todas las combinaciones posibles (10 pares para 5 roles)
        pairs = [
            ("JUNGLE", "SUPPORT", "synergy_jg_sup", 2.0),
            ("JUNGLE", "MID",     "synergy_jg_mid", 1.5),
            ("JUNGLE", "TOP",     "synergy_jg_top", 1.2),
            ("BOT",    "SUPPORT", "synergy_adc_sup", 0.8),
            ("MID",    "BOT",     "synergy_mid_bot", 1.2),
            ("MID",    "TOP",     "synergy_mid_top", 1.0),
            ("TOP",    "BOT",     "synergy_top_bot", 1.0),
            ("TOP",    "SUPPORT", "synergy_top_sup", 1.0),
            ("MID",    "SUPPORT", "synergy_mid_sup", 1.0),
            ("JUNGLE", "BOT",     "synergy_jg_adc",  1.0),
        ]

        team_results = {}
        for role_a, role_b, key, weight in pairs:
            # Vectorización sobre todas las kills del equipo
            shared_kills = team_kills_all.apply(lambda r: check_pair(r, role_a, role_b), axis=1).sum()
            skp = shared_kills / total_team_kills
            # Normalizar a 0-1 con el peso (capado a 1.0)
            team_results[key] = min(1.0, round(float(skp * weight), 2))
            
        results[team_id] = team_results

    return results


def compute_kd_density(df_events: pd.DataFrame, df_participants: pd.DataFrame):
    """
    Calcula matrices de densidad (Roles vs Tiempo) para Kills y Muertes.
    """
    if df_events.empty or df_participants.empty:
        return pd.DataFrame(), pd.DataFrame()

    # 1. Filtrar solo muertes de campeones
    kills = df_events[df_events["event_type"] == "CHAMPION_KILL"].copy()
    if kills.empty:
        return pd.DataFrame(), pd.DataFrame()

    # 2. Mapeo de match_id + participant_id -> role y team_id
    # Usamos df_participants completo para asegurar que tenemos a todos los jugadores mapeados
    mapping = df_participants[["match_id", "participant_id", "role", "team_id", "game_name"]].copy()
    
    # Identificar quiénes son de nuestro equipo
    from src.config import TEAM_PLAYER_ROLE_MAP
    team_names = {n.lower() for n in TEAM_PLAYER_ROLE_MAP.keys()}
    mapping["is_our_team"] = mapping["game_name"].str.lower().isin(team_names)

    # Convertir a dict para acceso rápido: (match_id, p_id) -> (role, is_our_team)
    meta_map = mapping.set_index(["match_id", "participant_id"])[["role", "is_our_team"]].to_dict("index")

    # 3. Clasificar Kills y Muertes
    def get_meta(row, pid_col):
        res = meta_map.get((row["match_id"], row[pid_col]))
        return res if res else {"role": "UNKNOWN", "is_our_team": False}

    kills["killer_meta"] = kills.apply(lambda r: get_meta(r, "participant_id"), axis=1) # participant_id es el killer
    kills["victim_meta"] = kills.apply(lambda r: get_meta(r, "victim_id"), axis=1)

    # Kills: el killer es de nuestro equipo
    our_kills = kills[kills["killer_meta"].apply(lambda x: x["is_our_team"])].copy()
    our_kills["role"] = our_kills["killer_meta"].apply(lambda x: x["role"])

    # Muertes: la victima es de nuestro equipo
    our_deaths = kills[kills["victim_meta"].apply(lambda x: x["is_our_team"])].copy()
    our_deaths["role"] = our_deaths["victim_meta"].apply(lambda x: x["role"])

    # 4. Tiempo entero minuto a minuto basado en la duración real
    max_game_min = int(df_participants["duration_minutes"].max()) if "duration_minutes" in df_participants.columns else 45
    if max_game_min < 5: max_game_min = 45 # Fallback por si la data es inconsistente
    
    def get_min(df, limit):
        if "timestamp_min" in df.columns:
            return df["timestamp_min"].fillna(0).astype(int).clip(0, limit)
        elif "timestamp" in df.columns:
            return (df["timestamp"] / 60000).fillna(0).astype(int).clip(0, limit)
        return pd.Series(0, index=df.index)

    our_kills["minute"] = get_min(our_kills, max_game_min)
    our_deaths["minute"] = get_min(our_deaths, max_game_min)

    # 5. Generar Matrices Finales
    from src.config import ROLE_ORDER
    roles = ROLE_ORDER
    all_minutes = [str(m) for m in range(max_game_min + 1)] 

    def build_matrix(df, role_col):
        if df.empty:
            return pd.DataFrame(0, index=roles, columns=all_minutes)
        df["minute_str"] = df["minute"].astype(str)
        matrix = df.groupby([role_col, "minute_str"], observed=False).size().unstack(fill_value=0)
        return matrix.reindex(index=roles, columns=all_minutes, fill_value=0)

    kill_matrix = build_matrix(our_kills, "role")
    death_matrix = build_matrix(our_deaths, "role")

    return kill_matrix, death_matrix

# ──────────────────────────────────────────────────────────────────
# Position Nodes v2 — Dense event mesh with Anchored Gold Interpolation
# ──────────────────────────────────────────────────────────────────

def compute_position_nodes(
    df_timeline: pd.DataFrame,
    df_events: pd.DataFrame,
) -> pd.DataFrame:
    """
    Builds a dense position-node mesh by merging 1-minute timeline frames
    with exact-position events, then computing unspent gold at every node
    via Anchored Gold Interpolation.

    Anchored Gold Interpolation — math:
    ─────────────────────────────────────
    Let A be the most recent 1-minute anchor frame for a participant, with
    timestamp T_a (ms) and total_gold G_a.

    Let E_1, E_2, ..., E_k be ITEM_PURCHASED events between T_a and the
    current node at timestamp T_n, each with item cost C_i.

    The unspent gold at node N is:

        unspent_gold(N) = G_a
                        + passive_gold_rate * (T_n - T_a) / 1000   [ms to s]
                        - sum(C_i)   for all purchases in (T_a, T_n]

    This gives a piecewise-linear gold curve with discrete drops at
    purchase events, anchored to ground-truth 1-minute frames from
    Riot's API.

    Args:
        df_timeline: From normalize_timeline(). Columns: match_id,
                     participant_id, timestamp_ms, timestamp_min,
                     total_gold, pos_x, pos_y.
        df_events:   From normalize_timeline(). Columns: match_id,
                     timestamp_ms, event_type, participant_id,
                     item_id, position_x, position_y.

    Returns:
        DataFrame with columns:
          match_id, participant_id, timestamp_ms, x_pos, y_pos,
          node_type ('frame' or 'event'), calculated_unspent_gold
        Sorted chronologically per (match_id, participant_id).
    """
    if df_timeline.empty:
        logger.warning("compute_position_nodes: timeline vacio")
        return pd.DataFrame(
            columns=["match_id", "participant_id", "timestamp_ms",
                     "x_pos", "y_pos", "node_type", "calculated_unspent_gold"]
        )

    # ── 1. Extract 1-minute anchor frames ─────────────────────────
    #    Riot's timeline has frames every 60s by default. We keep all
    #    of them as ground-truth gold anchors.
    tl = df_timeline.copy()
    tl["node_type"] = "frame"
    tl = tl.rename(columns={"pos_x": "x_pos", "pos_y": "y_pos"})

    frame_nodes = tl[[
        "match_id", "participant_id", "timestamp_ms",
        "x_pos", "y_pos", "node_type", "total_gold"
    ]].copy()

    # ── 2. Extract position-carrying events ───────────────────────
    ev = df_events[df_events["event_type"].isin(_POSITION_EVENT_TYPES)].copy()

    if ev.empty:
        frame_nodes["calculated_unspent_gold"] = frame_nodes["total_gold"]
        return frame_nodes.sort_values(
            ["match_id", "participant_id", "timestamp_ms"]
        ).reset_index(drop=True)

    ev["node_type"] = "event"
    ev = ev.rename(columns={"position_x": "x_pos", "position_y": "y_pos"})

    event_nodes = ev[[
        "match_id", "participant_id", "timestamp_ms",
        "x_pos", "y_pos", "node_type", "event_type", "item_id"
    ]].copy()

    # ── 3. Merge frames + events into a single chronological stream
    combined = pd.concat([frame_nodes, event_nodes], ignore_index=True)
    
    # ── 3.5 Ensure deterministic sort order
    # ITEM_PURCHASED comes before other events (e.g. CHAMPION_KILL) at the same ms
    combined["_sort_weight"] = combined["event_type"].apply(lambda x: 0 if x == "ITEM_PURCHASED" else 1)
    combined = combined.sort_values(
        ["match_id", "participant_id", "timestamp_ms", "_sort_weight"]
    ).reset_index(drop=True)

    # ── 4. Anchored Gold Interpolation ────────────────────────────
    #    Walk each (match, participant) group chronologically:
    #      - Frame: reset anchor G_a = total_gold, T_a = timestamp_ms
    #      - Event: unspent = G_a + passive*(T_n - T_a)/1000 - sum(purchases)
    #      - ITEM_PURCHASED: accumulate cost for subsequent nodes
    unspent_list: list[float] = [0.0] * len(combined)

    for (_mid, _pid), group_df in combined.groupby(["match_id", "participant_id"]):
        idxs = group_df.index.tolist()

        anchor_gold: float = 0.0
        anchor_ts: int = 0
        accumulated_cost: int = 0

        for i in idxs:
            row = combined.loc[i]
            ts = int(row["timestamp_ms"])

            if row["node_type"] == "frame":
                # Ground-truth anchor: reset the interpolation curve.
                anchor_gold = float(row["total_gold"])
                anchor_ts = ts
                accumulated_cost = 0
                unspent_list[i] = anchor_gold

            elif row["node_type"] == "event":
                # Passive gold accrued since last anchor (ms -> seconds).
                elapsed_s = (ts - anchor_ts) / 1000.0
                passive_gold = _PASSIVE_GOLD_PER_SECOND * max(elapsed_s, 0.0)

                # If this event IS an item purchase, compute its cost.
                # The purchase happens at this exact timestamp, so we
                # subtract it AFTER computing unspent for this node
                # (the gold was spent at this moment).
                purchase_cost = 0
                if row.get("event_type") == "ITEM_PURCHASED":
                    item_id = int(row.get("item_id", 0))
                    purchase_cost = _ITEM_COST_MAP.get(item_id, 0)

                unspent = anchor_gold + passive_gold - accumulated_cost
                unspent_list[i] = max(unspent, 0.0)

                # Accumulate cost for nodes that come AFTER this one.
                accumulated_cost += purchase_cost

    combined["calculated_unspent_gold"] = unspent_list

    # ── 5. Drop helper columns, keep only output schema ───────────
    result = combined[[
        "match_id", "participant_id", "timestamp_ms",
        "x_pos", "y_pos", "node_type", "calculated_unspent_gold"
    ]].copy()

    # Downcast for memory efficiency
    for col in ["x_pos", "y_pos", "participant_id"]:
        result[col] = pd.to_numeric(result[col], downcast="integer")
    result["calculated_unspent_gold"] = pd.to_numeric(
        result["calculated_unspent_gold"], downcast="integer"
    )

    logger.info(
        "compute_position_nodes: %d nodes (%d frames + %d events) across %d matches",
        len(result),
        len(frame_nodes),
        len(event_nodes),
        result["match_id"].nunique(),
    )
    return result


# ──────────────────────────────────────────────────────────────────
# Combat Clusters — DBSCAN-based fight detection
# ──────────────────────────────────────────────────────────────────


def compute_combat_clusters(df_events: pd.DataFrame) -> pd.DataFrame:
    """
    Clusters CHAMPION_KILL events into fights using DBSCAN on
    spatio-temporal features, then classifies each cluster by size.

    Feature engineering:
    ────────────────────
    Three features are extracted per kill:
      - position_x:  X coordinate on the Rift (0–15000)
      - position_y:  Y coordinate on the Rift (0–15000)
      - time_scaled: timestamp_ms / 1000 * TEMPORAL_SCALE
                     This maps seconds to pseudo-spatial units so that
                     temporal proximity has comparable weight to spatial
                     proximity in the Euclidean distance metric.

    All three features are then standardized via StandardScaler before
    DBSCAN, ensuring zero mean and unit variance across the match.

    Cluster classification:
    ───────────────────────
      - Noise (-1)  → 'PICK'       (isolated solo kill)
      - 2–3 kills   → 'SKIRMISH'   (small skirmish)
      - 4+ kills    → 'TEAMFIGHT'  (full team engagement)

    Args:
        df_events: From normalize_timeline(). Must contain:
                   event_type, position_x, position_y, timestamp_ms,
                   match_id.

    Returns:
        DataFrame with columns:
          match_id, timestamp_ms, position_x, position_y,
          cluster_id, cluster_type
        One row per CHAMPION_KILL event, enriched with cluster metadata.
    """
    if df_events.empty:
        logger.warning("compute_combat_clusters: events vacio")
        return pd.DataFrame(
            columns=["match_id", "timestamp_ms", "position_x", "position_y",
                     "cluster_id", "cluster_type"]
        )

    # ── 1. Filter champion kills ──────────────────────────────────
    kills = df_events[df_events["event_type"] == "CHAMPION_KILL"].copy()
    if kills.empty:
        logger.info("compute_combat_clusters: no CHAMPION_KILL events found")
        return pd.DataFrame(
            columns=["match_id", "timestamp_ms", "position_x", "position_y",
                     "cluster_id", "cluster_type"]
        )

    # ── 2. Feature engineering per match ──────────────────────────
    all_clusters: list[pd.DataFrame] = []

    for match_id, match_kills in kills.groupby("match_id"):
        n_kills = len(match_kills)
        if n_kills == 0:
            continue

        # Build feature matrix: [pos_x, pos_y, time_scaled]
        # time_scaled = seconds * TEMPORAL_SCALE so that 1s ≈ 100 spatial units
        from src.config import TEMPORAL_SCALE, DBSCAN_MIN_SAMPLES, DBSCAN_EPS
        
        X = np.column_stack([
            match_kills["position_x"].values.astype(float),
            match_kills["position_y"].values.astype(float),
            (match_kills["timestamp_ms"].values.astype(float) / 1000.0) * TEMPORAL_SCALE,
        ])

        # ── 3. DBSCAN clustering ──────────────────────────────────
        #    min_samples=2: a point needs at least 1 neighbor to form
        #    a cluster. Isolated kills become noise (label = -1).
        #    We use raw scaled values with a fixed spatial eps to preserve absolute sizes.
        db = DBSCAN(eps=DBSCAN_EPS, min_samples=DBSCAN_MIN_SAMPLES)
        labels = db.fit_predict(X)

        # ── 4. Classify each cluster by kill count ────────────────
        match_kills = match_kills.copy()
        match_kills["cluster_id"] = labels

        # Count kills per cluster (excluding noise)
        cluster_sizes = (
            match_kills[match_kills["cluster_id"] != -1]
            .groupby("cluster_id")
            .size()
        )

        def classify_cluster(cid: int) -> str:
            if cid == -1:
                return "PICK"
            size = cluster_sizes.get(cid, 1)
            if size >= 4:
                return "TEAMFIGHT"
            return "SKIRMISH"

        match_kills["cluster_type"] = match_kills["cluster_id"].apply(classify_cluster)

        all_clusters.append(match_kills)

    # ── 5. Combine all matches ────────────────────────────────────
    result = pd.concat(all_clusters, ignore_index=True)

    # Select and order output columns
    result = result[[
        "match_id", "timestamp_ms", "position_x", "position_y",
        "cluster_id", "cluster_type"
    ]]

    # Downcast for memory
    for col in ["position_x", "position_y", "cluster_id"]:
        result[col] = pd.to_numeric(result[col], downcast="integer")

    n_picks = (result["cluster_type"] == "PICK").sum()
    n_skirmishes = result[result["cluster_type"] == "SKIRMISH"]["cluster_id"].nunique()
    n_teamfights = result[result["cluster_type"] == "TEAMFIGHT"]["cluster_id"].nunique()

    logger.info(
        "compute_combat_clusters: %d kills → %d PICKs, %d SKIRMISHes, %d TEAMFIGHTs across %d matches",
        len(result), n_picks, n_skirmishes, n_teamfights,
        result["match_id"].nunique(),
    )
    return result


# Agregar a src/features.py

def compute_early_tactical_metrics(df_participants: pd.DataFrame, df_events: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula muertes solo/gank y kills de gank antes del minuto 15.
    """
    df = df_participants.copy()
    # Inicializar columnas
    for col in ["early_solo_deaths", "early_gank_deaths", "early_gank_kills"]:
        df[col] = 0

    if df_events.empty:
        return df

    # Filtramos solo kills de campeones antes del minuto 15
    early_kills = df_events[
        (df_events["event_type"] == "CHAMPION_KILL") & 
        (df_events["timestamp_min"] < 15)
    ].copy()

    for match_id, match_kills in early_kills.groupby("match_id"):
        p_meta = df[df["match_id"] == match_id]
        if p_meta.empty: continue
        
        # Identificar junglas de cada equipo
        junglers = p_meta[p_meta["role"] == "JUNGLE"].set_index("team_id")["participant_id"].to_dict()

        for _, kill in match_kills.iterrows():
            killer_id = int(kill["participant_id"])
            victim_id = int(kill["victim_id"])
            victim_team = int(kill.get("victim_team_id", 0))
            killer_team = int(kill.get("team_id", 0))
            
            # Parsear asistentes
            assists_str = str(kill.get("assisting_ids", ""))
            assist_ids = [int(x) for x in assists_str.split(",") if x.strip() and x != "nan"]
            
            # 1. LÓGICA PARA EL JUNGLA (Proactividad)
            # Si el jungla mató o asistió en una kill antes del min 15
            enemy_jungler_id = junglers.get(killer_team)
            if enemy_jungler_id and (killer_id == enemy_jungler_id or enemy_jungler_id in assist_ids):
                # Sumar early_gank_kills al jungla de ese equipo
                df.loc[(df["match_id"] == match_id) & (df["participant_id"] == enemy_jungler_id), "early_gank_kills"] += 1

            # 2. LÓGICA PARA LOS LANERS (Vulnerabilidad)
            # Buscamos quién es el jungla del equipo que mató
            opp_jungler_id = junglers.get(killer_team)
            
            is_gank = False
            if opp_jungler_id and (killer_id == opp_jungler_id or opp_jungler_id in assist_ids):
                is_gank = True
            
            # Asignar a la víctima
            if is_gank:
                df.loc[(df["match_id"] == match_id) & (df["participant_id"] == victim_id), "early_gank_deaths"] += 1
            else:
                df.loc[(df["match_id"] == match_id) & (df["participant_id"] == victim_id), "early_solo_deaths"] += 1

    return df
