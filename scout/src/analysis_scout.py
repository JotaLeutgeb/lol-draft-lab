"""
analysis_scout.py — Funciones analíticas de alto nivel para análisis individual.

Bifurcación de analysis.py sin dependencias de TEAM_PLAYERS ni filter_team_players.
Agrega:
  - compute_champion_pool_summary(): stats por campeón del jugador
  - analyze_performance_trend(): regresión temporal de impact_score
  - analyze_role_distribution(): distribución de roles jugados
  - compute_peer_benchmarks(): comparación vs los otros 9 de sus partidas
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

from src.config_scout import (
    GAME_PHASES,
    GOLD_DIFF_CRITICAL_THRESHOLD,
    LOW_VISION_PER_MIN,
    ROLE_ORDER,
    HIGH_CV_THRESHOLD,
    TREND_WINDOW,
)
from src.features_scout import filter_player_rows

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# RESUMEN DE PARTIDAS DEL JUGADOR (agregado)
# ──────────────────────────────────────────────────────────────────

def compute_player_summary(df_player: pd.DataFrame) -> dict:
    """
    Agrega métricas clave del jugador sobre todas sus partidas.

    Args:
        df_player: DataFrame filtrado con solo las filas del jugador analizado.

    Returns:
        dict con promedios de KPIs, winrate, n_games, etc.
    """
    if df_player.empty:
        return {}

    kpi_cols = [
        "impact_score", "kda", "cs_per_min", "gold_per_min",
        "damage_per_min", "vision_per_min", "kill_participation",
        "kill_conversion",
        "pilar_combat_efficiency", "pilar_map_pressure",
        "pilar_tactical_utility", "pilar_consistency",
        "consistency_score", "peer_rank",
    ]

    summary = {}
    for col in kpi_cols:
        if col in df_player.columns:
            summary[f"avg_{col}"] = round(float(df_player[col].mean()), 3)

    if "result" in df_player.columns:
        summary["win_rate"] = round(float(df_player["result"].mean()), 3)
        summary["n_games"] = int(len(df_player))

    return summary


# ──────────────────────────────────────────────────────────────────
# CHAMPION POOL
# ──────────────────────────────────────────────────────────────────

def compute_champion_pool_summary(df_player: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega estadísticas por campeón para el jugador analizado.

    Returns:
        DataFrame con columnas:
          champion, role, n_games, win_rate, avg_impact, avg_kda,
          avg_cs_min, avg_damage_min, avg_vision_min, avg_gold_min, consistency
    """
    if df_player.empty or "champion" not in df_player.columns:
        return pd.DataFrame()

    agg_cols = {
        "result": "mean",   # win_rate
        "impact_score": "mean",
        "kda": "mean",
        "cs_per_min": "mean",
        "damage_per_min": "mean",
        "vision_per_min": "mean",
        "gold_per_min": "mean",
        "match_id": "count",
    }
    # Solo agregar columnas que existen
    agg_cols = {k: v for k, v in agg_cols.items() if k in df_player.columns or k == "match_id"}

    group_cols = ["champion"]
    if "role" in df_player.columns:
        group_cols.append("role")

    summary = df_player.groupby(group_cols).agg(agg_cols).reset_index()

    rename = {
        "result": "win_rate",
        "impact_score": "avg_impact",
        "kda": "avg_kda",
        "cs_per_min": "avg_cs_min",
        "damage_per_min": "avg_damage_min",
        "vision_per_min": "avg_vision_min",
        "gold_per_min": "avg_gold_min",
        "kill_conversion": "avg_kill_conversion",
        "match_id": "n_games",
    }
    summary = summary.rename(columns={k: v for k, v in rename.items() if k in summary.columns})

    # Calcular consistencia por campeón (CV de impact_score)
    if "impact_score" in df_player.columns:
        cv_by_champ = df_player.groupby(group_cols)["impact_score"].apply(
            lambda s: float(s.std() / s.mean()) if s.mean() > 0 and len(s) >= 3 else 1.0
        ).reset_index(name="cv_impact")
        summary = summary.merge(cv_by_champ, on=group_cols, how="left")
        summary["consistency"] = (1.0 - summary["cv_impact"].clip(0, 1)).round(3)
        summary = summary.drop(columns=["cv_impact"], errors="ignore")

    summary = summary.round(3)
    summary = summary.sort_values("n_games", ascending=False)
    return summary


# ──────────────────────────────────────────────────────────────────
# TENDENCIA DE RENDIMIENTO
# ──────────────────────────────────────────────────────────────────

def analyze_performance_trend(df_player: pd.DataFrame) -> dict:
    """
    Analiza si el jugador está mejorando o empeorando con el tiempo.
    Usa regresión lineal sobre impact_score ordenado cronológicamente.

    Returns:
        dict con:
          trend: "improving" | "declining" | "stable"
          slope: coeficiente de la regresión (+ = mejora, - = declive)
          r_squared: bondad de ajuste (0-1)
          recent_avg: promedio de las últimas TREND_WINDOW partidas
          overall_avg: promedio histórico
          insight: str legible
    """
    if df_player.empty or "impact_score" not in df_player.columns:
        return {"trend": "stable", "slope": 0.0, "insight": "Datos insuficientes."}

    scores = df_player["impact_score"].dropna().values
    n = len(scores)

    if n < 5:
        return {"trend": "stable", "slope": 0.0, "r_squared": 0.0,
                "recent_avg": float(np.mean(scores)), "overall_avg": float(np.mean(scores)),
                "insight": f"Solo {n} partidas — mínimo 5 para análisis de tendencia."}

    x = np.arange(n)
    slope, intercept, r_value, p_value, _ = stats.linregress(x, scores)
    r_squared = r_value ** 2

    recent_avg = float(np.mean(scores[-TREND_WINDOW:]))
    overall_avg = float(np.mean(scores))

    # Clasificar tendencia
    if abs(slope) < 0.002 or p_value > 0.1:
        trend = "stable"
    elif slope > 0:
        trend = "improving"
    else:
        trend = "declining"

    trend_icons = {"improving": "📈", "declining": "📉", "stable": "➡️"}
    icon = trend_icons[trend]

    insight = (
        f"{icon} Tendencia: {trend.upper()} "
        f"(slope={slope:+.4f}, R²={r_squared:.2f}, p={p_value:.3f}). "
        f"Últimas {TREND_WINDOW} partidas: {recent_avg:.3f} vs histórico: {overall_avg:.3f}."
    )

    return {
        "trend":       trend,
        "slope":       round(float(slope), 4),
        "r_squared":   round(float(r_squared), 3),
        "p_value":     round(float(p_value), 3),
        "recent_avg":  round(recent_avg, 3),
        "overall_avg": round(overall_avg, 3),
        "insight":     insight,
    }


# ──────────────────────────────────────────────────────────────────
# DISTRIBUCIÓN DE ROLES
# ──────────────────────────────────────────────────────────────────

def analyze_role_distribution(df_player: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula winrate y performance por rol jugado.
    Útil para jugadores multi-role.
    """
    if df_player.empty or "role" not in df_player.columns:
        return pd.DataFrame()

    agg: dict = {}
    if "result" in df_player.columns:
        agg["win_rate"] = ("result", "mean")
        agg["n_games"] = ("result", "count")
    if "impact_score" in df_player.columns:
        agg["avg_impact"] = ("impact_score", "mean")

    if not agg:
        return pd.DataFrame()

    summary = df_player.groupby("role").agg(**agg).reset_index()
    summary = summary.round(3)

    role_order = {r: i for i, r in enumerate(ROLE_ORDER)}
    summary["_ord"] = summary["role"].map(role_order).fillna(99)
    return summary.sort_values("_ord").drop(columns=["_ord"])


# ──────────────────────────────────────────────────────────────────
# PEER BENCHMARKS (comparación vs los otros 9 de sus partidas)
# ──────────────────────────────────────────────────────────────────

def compute_peer_benchmarks(df_all: pd.DataFrame, profile) -> dict:
    """
    Compara al jugador analizado vs la mediana de los otros 9 jugadores
    de sus mismas partidas, segmentado por rol.

    Este benchmark es orgánico: no requiere datos externos de Challenger.
    Responde: "¿Cómo estás vs tus oponentes directos?"

    Returns:
        dict { metric_name: {"player": float, "peer_median": float, "gap_pct": float} }
    """
    if df_all.empty:
        return {}

    player_mask = df_all["game_name"].str.lower() == profile.game_name.lower()
    df_player = df_all[player_mask]
    df_peers = df_all[~player_mask]

    if df_player.empty or df_peers.empty:
        return {}

    primary_role = profile.primary_role
    peer_role = df_peers[df_peers["role"] == primary_role]

    metrics = ["kda", "cs_per_min", "damage_per_min", "vision_per_min",
               "gold_per_min", "kill_participation", "kill_conversion", "impact_score"]

    result = {}
    for m in metrics:
        if m not in df_player.columns or m not in df_peers.columns:
            continue
        p_val = float(df_player[m].mean())
        peer_val = float(peer_role[m].median()) if not peer_role.empty else float(df_peers[m].median())
        if peer_val > 0:
            gap = ((p_val / peer_val) - 1) * 100
        else:
            gap = 0.0
        result[m] = {
            "player":      round(p_val, 3),
            "peer_median": round(peer_val, 3),
            "gap_pct":     round(gap, 1),
        }

    return result


# ──────────────────────────────────────────────────────────────────
# ALERTAS INDIVIDUALES (reemplaza get_war_room_alerts)
# ──────────────────────────────────────────────────────────────────

def get_scout_alerts(df_player: pd.DataFrame, df_bench: pd.DataFrame, peer_bench: dict) -> list[dict]:
    """
    Genera alertas de alto impacto para el Scout Hub.
    Combina comparación vs Challenger y vs peers directos.
    """
    alerts = []
    if df_player.empty:
        return alerts

    # 1. Vision gap
    if "vision_per_min" in df_player.columns:
        avg_vs = float(df_player["vision_per_min"].mean())
        if not df_bench.empty and "vision_per_min" in df_bench.columns:
            role = df_player["role"].mode().iloc[0] if "role" in df_player.columns else None
            role_bench = df_bench[df_bench["role"] == role] if role else df_bench
            chall_vs = float(role_bench["vision_per_min"].median()) if not role_bench.empty else avg_vs
        else:
            chall_vs = avg_vs
        vs_gap = ((avg_vs / chall_vs) - 1) * 100 if chall_vs > 0 else 0
        if vs_gap < -20:
            alerts.append({
                "title": f"VISIÓN {vs_gap:.0f}% vs CHALLENGER",
                "desc":  f"{avg_vs:.2f} VS/min vs {chall_vs:.2f} estándar Challenger.",
                "icon":  "👁️", "severity": "warning",
            })

    # 2. CS gap
    if "cs_per_min" in df_player.columns:
        avg_cs = float(df_player["cs_per_min"].mean())
        if not df_bench.empty and "cs_per_min" in df_bench.columns:
            role = df_player["role"].mode().iloc[0] if "role" in df_player.columns else None
            role_bench = df_bench[df_bench["role"] == role] if role else df_bench
            chall_cs = float(role_bench["cs_per_min"].median()) if not role_bench.empty else avg_cs
        else:
            chall_cs = avg_cs
        cs_gap = ((avg_cs / chall_cs) - 1) * 100 if chall_cs > 0 else 0
        if cs_gap < -25:
            alerts.append({
                "title": f"CS {cs_gap:.0f}% vs CHALLENGER",
                "desc":  f"{avg_cs:.2f} CS/min vs {chall_cs:.2f} estándar Challenger.",
                "icon":  "🌾", "severity": "warning",
            })

    # 3. Peer ranking bajo
    if "peer_rank" in df_player.columns:
        avg_rank = float(df_player["peer_rank"].mean())
        if avg_rank > 6:
            alerts.append({
                "title": f"PEER RANK BAJO: #{avg_rank:.1f}/10",
                "desc":  "El jugador promedia estar en la mitad inferior de sus propias partidas.",
                "icon":  "⚠️", "severity": "critical",
            })

    # 4. Consistencia baja
    if "consistency_score" in df_player.columns:
        avg_cons = float(df_player["consistency_score"].mean())
        if avg_cons < 0.5:
            alerts.append({
                "title": f"RENDIMIENTO INCONSISTENTE",
                "desc":  f"Consistency Score: {avg_cons:.2f} — alto CV en KPIs clave. Las mejores partidas son muy superiores a las peores.",
                "icon":  "📊", "severity": "warning",
            })

    return alerts


# ──────────────────────────────────────────────────────────────────
# LOSS PATTERNS (adaptado para individual)
# ──────────────────────────────────────────────────────────────────

def identify_loss_phase(df_player: pd.DataFrame, df_gold_diff: Optional[pd.DataFrame] = None) -> dict:
    """
    Identifica la fase del juego donde el jugador tiene peores resultados.
    Adaptado para 1 jugador (sin filter_team_players).
    """
    result = {"worst_phase": None, "phase_stats": {}, "insight": ""}

    if df_player.empty or "result" not in df_player.columns:
        return result

    # Análisis por gold diff
    if df_gold_diff is not None and not df_gold_diff.empty:
        match_res = df_player.drop_duplicates("match_id")[["match_id", "result"]]
        merged = match_res.merge(df_gold_diff, on="match_id", how="inner")

        gold_diff_cols = [c for c in merged.columns if c.startswith("gold_diff_min")]
        for col in gold_diff_cols:
            valid = merged[[col, "result"]].dropna()
            if len(valid) >= 5:
                r, _ = stats.pearsonr(valid[col], valid["result"].astype(float))
                result.setdefault("correlation", {})[col] = round(r, 3)

    # Win rate por duración de partida (proxy de fase)
    match_res = df_player.drop_duplicates("match_id")[["match_id", "result", "duration_minutes"]]
    phases = {
        "Early (0-15)": (0, 15),
        "Mid (15-25)":  (15, 25),
        "Late (25+)":   (25, 100),
    }
    for label, (t_start, t_end) in phases.items():
        phase_games = match_res[
            (match_res["duration_minutes"] >= t_start) & (match_res["duration_minutes"] < t_end)
        ]
        result["phase_stats"][label] = {
            "win_rate": round(float(phase_games["result"].mean()), 3) if not phase_games.empty else 0.0,
            "n_games": int(len(phase_games)),
        }

    valid_phases = {p: s for p, s in result["phase_stats"].items() if s["n_games"] > 0}
    if valid_phases:
        result["worst_phase"] = min(valid_phases, key=lambda p: valid_phases[p]["win_rate"])
        wf = result["worst_phase"]
        wr = result["phase_stats"][wf]["win_rate"]
        n = result["phase_stats"][wf]["n_games"]
        result["insight"] = f"Fase más problemática: {wf} — WR {wr*100:.0f}% en {n} partidas."

    return result
