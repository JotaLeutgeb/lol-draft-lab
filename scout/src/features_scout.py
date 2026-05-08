"""
features_scout.py — Feature engineering para análisis individual.

Bifurcación de features.py con las siguientes diferencias clave:
  1. compute_impact_score_individual(): pilar_team_synergy → pilar_consistency
  2. compute_consistency_metrics():  NUEVO — mide estabilidad de KPIs a lo largo de N partidas
  3. compute_peer_ranking(): NUEVO — rankea al jugador entre los 10 participantes de cada partida
  4. compute_synergy_matrix(): ELIMINADO (no aplica para análisis unipersonal)
  5. filter_player_rows(): reemplaza filter_team_players() — opera sobre profile.game_name
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.config_scout import (
    GAME_PHASES,
    GOLD_DIFF_SNAPSHOTS_MIN,
    OBJECTIVE_MONSTER_TYPES,
    ROLE_ORDER,
    HIGH_CV_THRESHOLD,
    TREND_WINDOW,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# FILTRO DE JUGADOR INDIVIDUAL
# ──────────────────────────────────────────────────────────────────

def filter_player_rows(df: pd.DataFrame, profile) -> pd.DataFrame:
    """
    Filtra filas del DataFrame que corresponden al jugador analizado.
    Opera sobre game_name del perfil y valida que el rol sea válido.
    Soporta nombres históricos/alias del jugador.

    Args:
        df:      DataFrame con columna game_name (y opcionalmente role).
        profile: PlayerProfile cargado desde YAML.

    Returns:
        Subconjunto de df con solo las filas del jugador analizado.
    """
    if df.empty or "game_name" not in df.columns:
        return df

    # Buscar por todos los nombres (actual + históricos)
    all_names = getattr(profile, "all_game_names", [profile.game_name])
    all_names_lower = [n.lower() for n in all_names]
    
    name_mask = df["game_name"].str.lower().isin(all_names_lower)

    if "role" in df.columns and profile.valid_roles:
        role_mask = df["role"].isin(profile.valid_roles) | (df["role"] == "UNKNOWN")
        mask = name_mask & role_mask
        result = df[mask]
        if not result.empty:
            return result

    return df[name_mask]


# ──────────────────────────────────────────────────────────────────
# MÉTRICAS BASE POR JUGADOR × PARTIDA
# ──────────────────────────────────────────────────────────────────

def compute_player_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula métricas por minuto y ratios base.
    Idéntico al proyecto de equipo — funciona sobre 10 o 1 jugador.
    """
    df = df.copy()
    dur = df["duration_minutes"].clip(lower=0.01)

    df["kda"] = (df["kills"] + df["assists"]) / df["deaths"].clip(lower=1)
    df["cs_per_min"] = df.get("cs", 0) / dur
    df["gold_per_min"] = df["gold_earned"] / dur
    df["damage_per_min"] = df["total_damage"] / dur
    df["vision_per_min"] = df["vision_score"] / dur
    df["cc_per_min"] = df.get("time_cc", 0) / dur
    df["damage_taken_per_min"] = df["damage_taken"] / dur
    df["damage_per_gold"] = df["total_damage"].astype(float) / df["gold_earned"].clip(lower=1).astype(float)

    if "damage_buildings" not in df.columns:
        df["damage_buildings"] = 0

    # Kill participation (vs el equipo completo en esa partida)
    team_kills = df.groupby(["match_id", "team_id"])["kills"].transform("sum").clip(lower=1)
    df["kill_participation"] = (df["kills"] + df["assists"]) / team_kills

    # Gold/damage/vision share (para peer ranking posterior)
    team_gold = df.groupby(["match_id", "team_id"])["gold_earned"].transform("sum").clip(lower=1)
    team_damage = df.groupby(["match_id", "team_id"])["total_damage"].transform("sum").clip(lower=1)
    team_vision = df.groupby(["match_id", "team_id"])["vision_score"].transform("sum").clip(lower=1)

    df["gold_share"] = df["gold_earned"] / team_gold
    df["damage_share"] = df["total_damage"] / team_damage
    df["vision_share"] = df["vision_score"] / team_vision

    df["utility_score"] = (
        df.get("total_heal", 0)
        + (df.get("control_wards", 0) * 500)
        + (df.get("wards_killed", 0) * 200)
    ) / dur

    return df


# ──────────────────────────────────────────────────────────────────
# PEER RANKING (nuevo)
# ──────────────────────────────────────────────────────────────────

def compute_peer_ranking(df: pd.DataFrame, profile) -> pd.DataFrame:
    """
    Rankea al jugador objetivo entre los 10 participantes de cada partida
    según su impact_score calculado.

    Returns DataFrame con columnas: match_id, peer_rank (1=mejor entre los 10).
    """
    if df.empty or "impact_score" not in df.columns:
        return pd.DataFrame()

    rows = []
    for match_id, grp in df.groupby("match_id"):
        grp_sorted = grp.sort_values("impact_score", ascending=False).reset_index(drop=True)
        grp_sorted["peer_rank"] = grp_sorted.index + 1  # 1-indexed

        player_row = grp_sorted[grp_sorted["game_name"].str.lower() == profile.game_name.lower()]
        if not player_row.empty:
            rows.append({
                "match_id":  match_id,
                "peer_rank": int(player_row.iloc[0]["peer_rank"]),
            })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ──────────────────────────────────────────────────────────────────
# CONSISTENCY METRICS (nuevo)
# ──────────────────────────────────────────────────────────────────

def compute_consistency_metrics(df_player: pd.DataFrame) -> dict:
    """
    Mide la estabilidad de rendimiento del jugador a lo largo de sus partidas.
    Usa el Coeficiente de Variación (CV = std / mean) de los KPIs principales.

    CV bajo = jugador predecible y consistente.
    CV alto = rendimiento volátil.

    Args:
        df_player: DataFrame filtrado con solo las partidas del jugador analizado.

    Returns:
        dict con:
          cv_by_metric: {metric_name: cv_value}
          overall_cv: promedio de CVs (menor = más consistente)
          consistency_score: 1 - overall_cv (escala 0-1, mayor = más consistente)
          trend_slope: pendiente de regresión lineal sobre impact_score (últimas TREND_WINDOW partidas)
    """
    if df_player.empty:
        return {"consistency_score": 0.0, "overall_cv": 1.0, "cv_by_metric": {}, "trend_slope": 0.0}

    kpi_cols = ["kda", "cs_per_min", "damage_per_min", "vision_per_min", "gold_per_min", "kill_participation"]
    available = [c for c in kpi_cols if c in df_player.columns]

    cv_by_metric: dict[str, float] = {}
    for col in available:
        series = df_player[col].dropna()
        if len(series) >= 3 and series.mean() > 0:
            cv = float(series.std() / series.mean())
            cv_by_metric[col] = round(min(cv, 2.0), 3)  # cap en 2.0 para evitar outliers

    overall_cv = float(np.mean(list(cv_by_metric.values()))) if cv_by_metric else 1.0
    consistency_score = float(max(0.0, 1.0 - overall_cv))

    # Tendencia de impact_score (regresión lineal simple)
    trend_slope = 0.0
    if "impact_score" in df_player.columns:
        recent = df_player["impact_score"].dropna().tail(TREND_WINDOW)
        if len(recent) >= 4:
            x = np.arange(len(recent))
            slope, _ = np.polyfit(x, recent.values, 1)
            trend_slope = round(float(slope), 4)

    return {
        "consistency_score": round(consistency_score, 3),
        "overall_cv":        round(overall_cv, 3),
        "cv_by_metric":      cv_by_metric,
        "trend_slope":       trend_slope,
    }


# ──────────────────────────────────────────────────────────────────
# CONTROL DE OBJETIVOS (adaptado — sin TEAM_PLAYER_ROLE_MAP)
# ──────────────────────────────────────────────────────────────────

def compute_objective_control(df_events: pd.DataFrame, df_participants: pd.DataFrame = None) -> pd.DataFrame:
    """Calcula el control de objetivos a nivel individual."""
    empty_df = pd.DataFrame(columns=["match_id", "participant_id", "objective_score_raw", "team_id"])
    if df_events.empty:
        return empty_df

    objectives = df_events[df_events["event_type"].isin(["ELITE_MONSTER_KILL", "BUILDING_KILL"])].copy()
    if objectives.empty:
        return empty_df

    weights = {"baron": 2.5, "herald": 1.5, "dragon": 1.0, "voidgrub": 0.5, "tower": 1.0, "inhibitor": 1.5}
    rows = []

    for match_id, match_objs in objectives.groupby("match_id"):
        player_scores: dict[int, float] = {}
        for _, obj in match_objs.iterrows():
            killer_id = obj["participant_id"]
            assists_str = str(obj.get("assisting_ids", ""))
            assist_ids = []
            if assists_str and assists_str not in ("nan", ""):
                try:
                    assist_ids = [int(x.strip()) for x in assists_str.split(",") if x.strip()]
                except ValueError:
                    pass

            obj_type = (
                obj.get("monster_type") if obj["event_type"] == "ELITE_MONSTER_KILL"
                else obj.get("building_type")
            )
            weight = weights.get(obj_type, 0)
            if weight == 0:
                continue

            if killer_id > 0:
                player_scores[killer_id] = player_scores.get(killer_id, 0.0) + weight
            for aid in assist_ids:
                if aid > 0:
                    player_scores[aid] = player_scores.get(aid, 0.0) + weight

        for pid, score in player_scores.items():
            rows.append({"match_id": match_id, "participant_id": pid, "objective_score_raw": score})

    df = pd.DataFrame(rows)
    if df_participants is not None and not df_participants.empty and not df.empty:
        meta = df_participants[["match_id", "participant_id", "team_id"]].drop_duplicates()
        df = df.merge(meta, on=["match_id", "participant_id"], how="left")

    return df


# ──────────────────────────────────────────────────────────────────
# GOLD DIFF VS OPONENTE (adaptado — sin team roster filter)
# ──────────────────────────────────────────────────────────────────

def compute_gold_diff(df_timeline: pd.DataFrame, df_participants: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula diferencia de gold vs el jugador del mismo rol en el equipo contrario.
    No filtra por roster de equipo — opera sobre cualquier participant_id.
    """
    if df_timeline.empty or df_participants.empty:
        return pd.DataFrame()

    tl = df_timeline.copy()
    for col in ["role", "team_id", "game_name"]:
        if col in tl.columns:
            tl = tl.drop(columns=[col])

    meta = df_participants[["match_id", "participant_id", "role", "team_id", "game_name"]].drop_duplicates().copy()
    meta["match_id"] = meta["match_id"].astype(str)
    meta["participant_id"] = pd.to_numeric(meta["participant_id"], downcast="integer", errors="coerce").fillna(0).astype(int)
    tl["match_id"] = tl["match_id"].astype(str)
    tl["participant_id"] = pd.to_numeric(tl["participant_id"], downcast="integer", errors="coerce").fillna(0).astype(int)
    tl = tl.merge(meta, on=["match_id", "participant_id"], how="left")

    result_rows = []
    for snap_min in GOLD_DIFF_SNAPSHOTS_MIN:
        snap = tl[
            (tl["timestamp_min"] >= snap_min - 1) & (tl["timestamp_min"] <= snap_min + 1)
        ].copy()
        if snap.empty:
            continue

        snap["dist"] = abs(snap["timestamp_min"] - snap_min)
        snap = snap.sort_values("dist").drop_duplicates(["match_id", "participant_id"])

        snap_a = snap.rename(columns={"total_gold": "gold_self", "team_id": "team_a"})
        snap_b = snap.rename(columns={"total_gold": "gold_opp", "team_id": "team_b",
                                       "participant_id": "opp_participant_id"})

        cols_b = ["match_id", "role", "team_b", "gold_opp"]
        merged = snap_a.merge(snap_b.reindex(columns=cols_b), on=["match_id", "role"])
        merged = merged[merged["team_a"] != merged["team_b"]].copy()

        if merged.empty:
            continue

        merged[f"gold_diff_min{snap_min}"] = merged["gold_self"] - merged["gold_opp"]
        keep = ["match_id", "participant_id", "role", "game_name", f"gold_diff_min{snap_min}"]
        result_rows.append(merged.reindex(columns=keep))

    if not result_rows:
        return pd.DataFrame()

    from functools import reduce
    return reduce(
        lambda l, r: pd.merge(l, r, on=["match_id", "participant_id", "role", "game_name"], how="outer"),
        result_rows,
    )


# ──────────────────────────────────────────────────────────────────
# PHASE STATS
# ──────────────────────────────────────────────────────────────────

def compute_phase_stats(df_timeline: pd.DataFrame) -> pd.DataFrame:
    """Agrega métricas por jugador y fase del juego (early/mid/late)."""
    if df_timeline.empty:
        return pd.DataFrame()

    df = df_timeline.copy()
    rows = []
    for phase_name, (t_start, t_end) in GAME_PHASES.items():
        mask = (df["timestamp_min"] >= t_start) & (df["timestamp_min"] < t_end)
        phase_df = df[mask]
        if phase_df.empty:
            continue

        grouped = phase_df.groupby(["match_id", "participant_id"]).agg(
            gold_start=("total_gold", "first"),
            gold_end=("total_gold", "last"),
            cs_start=("cs", "first"),
            cs_end=("cs", "last"),
            ts_start=("timestamp_min", "first"),
            ts_end=("timestamp_min", "last"),
        ).reset_index()

        grouped["phase_duration"] = (grouped["ts_end"] - grouped["ts_start"]).clip(lower=0.01)
        grouped["avg_gold_per_min"] = (grouped["gold_end"] - grouped["gold_start"]) / grouped["phase_duration"]
        grouped["avg_cs_per_min"] = (grouped["cs_end"] - grouped["cs_start"]) / grouped["phase_duration"]
        grouped["phase"] = phase_name
        grouped["gold_at_end_of_phase"] = grouped["gold_end"]
        rows.append(grouped[["match_id", "participant_id", "phase", "avg_gold_per_min", "avg_cs_per_min", "gold_at_end_of_phase"]])

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# ──────────────────────────────────────────────────────────────────
# KILL CONVERSION (nuevo — 30s)
# ──────────────────────────────────────────────────────────────────

def compute_kill_conversion(df_events: pd.DataFrame, df_participants: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula el Kill Conversion (ratio de participaciones en kills que terminan en un objetivo
    dentro de los próximos 30 segundos).
    """
    if df_events.empty or df_participants.empty:
        df = df_participants[["match_id", "participant_id"]].drop_duplicates().copy()
        df["kill_conversion"] = 0.0
        return df

    kills = df_events[df_events["event_type"] == "CHAMPION_KILL"].copy()
    objectives = df_events[df_events["event_type"].isin(["ELITE_MONSTER_KILL", "BUILDING_KILL"])].copy()

    # Pre-calculamos ventanas
    obj_by_match_team = {}
    for (match_id, team_id), grp in objectives.groupby(["match_id", "team_id"]):
        obj_by_match_team[(match_id, team_id)] = grp["timestamp_min"].values

    def parse_assists(a_str):
        if not a_str or pd.isna(a_str) or a_str == "nan": return []
        return [int(x) for x in str(a_str).split(",") if x.strip().isdigit()]

    # Contadores
    player_participations = {}
    player_conversions = {}

    for _, kill in kills.iterrows():
        match_id = str(kill["match_id"])
        killer = int(kill["participant_id"])
        assists = parse_assists(kill.get("assisting_ids", ""))
        team_id = int(kill.get("team_id", 0))
        t_min = float(kill["timestamp_min"])

        involved = [killer] + assists
        involved = [p for p in involved if p > 0]

        # Check conversion (objetivo en los próximos 30s = 0.5 mins)
        converted = False
        objs = obj_by_match_team.get((match_id, team_id), np.array([]))
        if len(objs) > 0:
            if np.any((objs >= t_min) & (objs <= t_min + 0.5)):
                converted = True

        for pid in involved:
            key = (match_id, pid)
            player_participations[key] = player_participations.get(key, 0) + 1
            if converted:
                player_conversions[key] = player_conversions.get(key, 0) + 1

    rows = []
    for (match_id, pid), parts in player_participations.items():
        conv = player_conversions.get((match_id, pid), 0)
        rows.append({
            "match_id": match_id,
            "participant_id": pid,
            "kill_conversion": round(conv / parts, 3) if parts > 0 else 0.0
        })
    
    df_conv = pd.DataFrame(rows)
    df_res = df_participants[["match_id", "participant_id"]].drop_duplicates().copy()
    df_res["match_id"] = df_res["match_id"].astype(str)
    
    if not df_conv.empty:
        df_conv["match_id"] = df_conv["match_id"].astype(str)
        df_res = df_res.merge(df_conv, on=["match_id", "participant_id"], how="left")
    
    df_res["kill_conversion"] = df_res["kill_conversion"].fillna(0.0)
    return df_res


# ──────────────────────────────────────────────────────────────────
# IMPACT SCORE INDIVIDUAL (bifurcación — 4to pilar = CONSISTENCIA)
# ──────────────────────────────────────────────────────────────────

def compute_impact_score_individual(
    df_participants: pd.DataFrame,
    df_objectives: pd.DataFrame,
    df_kill_conversion: pd.DataFrame,
    consistency_score: float = 0.5,
) -> pd.DataFrame:
    """
    Calcula Impact Score individual con 4 pilares (25% cada uno):
      1. pilar_combat_efficiency  — DPM + Kill Participation
      2. pilar_map_pressure       — Objective Control (normalizado) + Kill Conversion
      3. pilar_tactical_utility   — Visión + Resiliencia
      4. pilar_consistency        — Inverso de CV de KPIs (estabilidad)

    Args:
        df_participants: DataFrame con compute_player_metrics() aplicado.
        df_objectives:   Output de compute_objective_control().
        consistency_score: Escalar pre-calculado (0-1) de compute_consistency_metrics().
    """
    if df_participants.empty:
        return df_participants

    df = df_participants.copy()

    # Limpiar columnas anteriores
    for col in ["objective_score_raw", "objective_control", "impact_score",
                "pilar_combat_efficiency", "pilar_map_pressure",
                "pilar_tactical_utility", "pilar_consistency"]:
        if col in df.columns:
            df = df.drop(columns=[col])

    # Inyectar objective_control
    if not df_objectives.empty and "objective_score_raw" in df_objectives.columns:
        df = df.merge(
            df_objectives[["match_id", "participant_id", "objective_score_raw"]],
            on=["match_id", "participant_id"],
            how="left",
        )
        df["objective_control"] = df["objective_score_raw"].fillna(0)
    else:
        df["objective_control"] = 0.0

    # Inyectar kill_conversion
    if not df_kill_conversion.empty and "kill_conversion" in df_kill_conversion.columns:
        df = df.merge(
            df_kill_conversion[["match_id", "participant_id", "kill_conversion"]],
            on=["match_id", "participant_id"],
            how="left"
        )
        df["kill_conversion"] = df["kill_conversion"].fillna(0.0)
    else:
        df["kill_conversion"] = 0.0

    # Normalización Min-Max dentro de cada partida (escala 0-1 por partida → comparable)
    def minmax_within_match(series_col: str) -> pd.Series:
        mn = df.groupby("match_id")[series_col].transform("min")
        mx = df.groupby("match_id")[series_col].transform("max")
        rng = (mx - mn).clip(lower=1e-6)
        return (df[series_col].fillna(0) - mn) / rng

    # Pilar 1: Combat Efficiency
    norm_dpm = minmax_within_match("damage_per_min")
    df["pilar_combat_efficiency"] = (norm_dpm * 0.5 + df["kill_participation"].clip(0, 1) * 0.5).clip(0, 1)

    # Pilar 2: Map Pressure (Objective Control + Kill Conversion)
    norm_obj = minmax_within_match("objective_control")
    df["pilar_map_pressure"] = (norm_obj * 0.7 + df["kill_conversion"].clip(0, 1) * 0.3).clip(0, 1)

    # Pilar 3: Tactical Utility (Visión + Resiliencia)
    norm_vision = minmax_within_match("vision_per_min")
    max_deaths = df.groupby("match_id")["deaths"].transform("max").clip(lower=1)
    norm_survival = (max_deaths - df["deaths"]) / max_deaths
    max_mitigation = df.groupby("match_id")["damage_mitigated"].transform("max").clip(lower=1)
    norm_mitigation = df["damage_mitigated"] / max_mitigation
    resilience = (norm_survival * 0.6 + norm_mitigation * 0.4).clip(0, 1)
    df["resilience_index"] = resilience
    df["pilar_tactical_utility"] = (norm_vision * 0.5 + resilience * 0.5).clip(0, 1)

    # Pilar 4: Consistency (escalar externo — mismo valor para todas las filas del jugador en este batch)
    df["pilar_consistency"] = float(consistency_score)

    # Impact Score Final
    df["impact_score"] = (
        0.25 * df["pilar_combat_efficiency"]
        + 0.25 * df["pilar_map_pressure"]
        + 0.25 * df["pilar_tactical_utility"]
        + 0.25 * df["pilar_consistency"]
    ).clip(0, 1.0).fillna(0.0)

    return df


# ──────────────────────────────────────────────────────────────────
# KILL/DEATH DENSITY MATRIX
# ──────────────────────────────────────────────────────────────────

def compute_kd_density(df_events: pd.DataFrame, df_players: pd.DataFrame, profile) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Calcula matrices de densidad Kills/Muertes por minuto para el jugador analizado.
    """
    if df_events.empty or df_players.empty:
        return pd.DataFrame(), pd.DataFrame()

    kills = df_events[df_events["event_type"] == "CHAMPION_KILL"].copy()
    if kills.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Identificar participant_id del jugador en cada partida
    player_meta = df_players[
        df_players["game_name"].str.lower() == profile.game_name.lower()
    ][["match_id", "participant_id"]].drop_duplicates()

    player_pid_map = player_meta.set_index("match_id")["participant_id"].to_dict()

    def get_min(df):
        if "timestamp_min" in df.columns:
            return df["timestamp_min"].fillna(0).astype(int)
        if "timestamp" in df.columns:
            return (df["timestamp"] / 60000).fillna(0).astype(int)
        return pd.Series(0, index=df.index)

    max_min = 45
    our_kills, our_deaths = [], []

    for match_id, grp in kills.groupby("match_id"):
        pid = player_pid_map.get(match_id)
        if pid is None:
            continue
        k = grp[grp["participant_id"] == pid].copy()
        d = grp[grp["victim_id"] == pid].copy()
        k["minute"] = get_min(k).clip(0, max_min)
        d["minute"] = get_min(d).clip(0, max_min)
        our_kills.append(k)
        our_deaths.append(d)

    all_minutes = [str(m) for m in range(max_min + 1)]

    def build_series(frames):
        if not frames:
            return pd.Series(0, index=all_minutes)
        df = pd.concat(frames, ignore_index=True)
        counts = df["minute"].astype(str).value_counts()
        return counts.reindex(all_minutes, fill_value=0)

    kills_series = build_series(our_kills)
    deaths_series = build_series(our_deaths)

    return pd.DataFrame({"kills": kills_series}), pd.DataFrame({"deaths": deaths_series})


# ──────────────────────────────────────────────────────────────────
# SYNERGY MATRIX (adaptado de War Room para jugador individual)
# ──────────────────────────────────────────────────────────────────

def compute_player_synergy_from_events(
    df_events: pd.DataFrame,
    df_participants: pd.DataFrame,
    player_game_name: str,
) -> pd.DataFrame:
    """
    Calcula sinergia SKP para el equipo del jugador en cada partida.
    Retorna DataFrame con columnas synergy_* por match_id.
    """
    if df_events.empty or df_participants.empty:
        return pd.DataFrame()

    kills = df_events[df_events["event_type"] == "CHAMPION_KILL"].copy()
    if kills.empty:
        return pd.DataFrame()

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
        ("JUNGLE", "BOT",     "synergy_jg_adc", 1.0),
    ]

    rows = []
    for match_id, grp_p in df_participants.groupby("match_id"):
        match_kills = kills[kills["match_id"] == match_id]
        if match_kills.empty:
            continue

        # Encontrar el team_id del jugador
        player_row = grp_p[grp_p["game_name"].str.lower() == player_game_name.lower()]
        if player_row.empty:
            continue
        player_team = player_row.iloc[0]["team_id"]

        team_p = grp_p[grp_p["team_id"] == player_team]
        team_kills = match_kills[match_kills["team_id"] == player_team]
        total_team_kills = len(team_kills)
        if total_team_kills == 0:
            continue

        role_map = team_p.set_index("participant_id")["role"].to_dict()

        def check_pair(kill_row, role_a, role_b):
            killer_id = kill_row["participant_id"]
            assists = kill_row.get("assisting_ids", [])
            if isinstance(assists, str):
                try:
                    assist_ids = [int(x.strip()) for x in assists.split(",") if x.strip() and x != "nan"]
                except Exception:
                    assist_ids = []
            elif isinstance(assists, (list, np.ndarray)):
                assist_ids = list(assists)
            else:
                assist_ids = []
            pids = [killer_id] + assist_ids
            roles_involved = [role_map.get(pid) for pid in pids if pid in role_map]
            return role_a in roles_involved and role_b in roles_involved

        row = {"match_id": match_id}
        for role_a, role_b, key, weight in pairs:
            shared = team_kills.apply(lambda r: check_pair(r, role_a, role_b), axis=1).sum()
            skp = shared / total_team_kills
            row[key] = min(1.0, round(float(skp * weight), 2))
        rows.append(row)

    return pd.DataFrame(rows) if rows else pd.DataFrame()
