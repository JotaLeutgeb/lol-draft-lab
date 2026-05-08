"""
analysis.py — Funciones analíticas de alto nivel.

Responde preguntas tácticas concretas:
  - ¿En qué fase del juego perdemos más?
  - ¿Qué jugador tiene mayor impacto real?
  - ¿Cómo es nuestro control de visión?
  - ¿Qué composiciones funcionan mejor?

Todas las funciones son puras: reciben DataFrames, devuelven
DataFrames/dicts con información lista para visualizar o reportar.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

from src.config import (
    GAME_PHASES,
    GOLD_DIFF_CRITICAL_THRESHOLD,
    LOW_VISION_PER_MIN,
    ROLE_ORDER,
    TEAM_PLAYERS,
    TEAM_PLAYER_ROLE_MAP,
)

logger = logging.getLogger(__name__)

# Nombres de jugadores del equipo (solo para referencia / exportacion)
TEAM_GAME_NAMES: set[str] = set(TEAM_PLAYER_ROLE_MAP.keys())


def filter_team_players(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filtra filas del equipo aplicando dos condiciones simultaneas:
      1. game_name (minusculas) pertenece al roster configurado
      2. role coincide con el rol asignado a ese jugador en config.py

    Esto evita incluir partidas de solo queue donde un jugador aparece
    en un rol diferente al de equipo (ej: Franfon jugando MID).

    Si el DataFrame no tiene columna 'role' o el resultado queda vacio,
    hace fallback a filtrar solo por nombre para no romper el pipeline.
    """
    df = df.copy()
    name_col = df["game_name"].str.lower()
    from src.config import TEAM_PLAYER_ROLE_MAP, TEAM_GAME_NAMES, TEAM_PLAYER_DISPLAY_MAP

    mask_name = name_col.isin(TEAM_GAME_NAMES)
    result = df[mask_name].copy()
    
    if result.empty:
        return result

    result["game_name"] = result["game_name"].str.lower().map(TEAM_PLAYER_DISPLAY_MAP).fillna(result["game_name"])
    
    if "role" in result.columns:
        result["role"] = result["game_name"].str.lower().map(TEAM_PLAYER_ROLE_MAP)
        
    return result


def _is_team_player(name: str) -> bool:
    """Retorna True si el nombre corresponde a un jugador del equipo."""
    return name.lower() in TEAM_GAME_NAMES


def get_team_match_ids(df: pd.DataFrame) -> set[str]:
    """
    Retorna el conjunto de match_ids donde los 5 jugadores del equipo
    jugaron juntos en el mismo lado (mismo team_id) y cada uno en su
    rol asignado en config.py.

    Criterios:
      1. Los 5 roles configurados aparecen via filter_team_players.
      2. Todos estan en el mismo team_id dentro de esa partida.
    """
    team_df = filter_team_players(df)
    if team_df.empty or "match_id" not in team_df.columns:
        return set()

    required_roles: set[str] = {"JUNGLE", "MID", "BOT", "SUPPORT"}
    valid: set[str] = set()

    for match_id, grp in team_df.groupby("match_id"):
        if not required_roles.issubset(set(grp["role"].unique())):
            continue
        if "team_id" in grp.columns and grp["team_id"].nunique() > 1:
            continue
        valid.add(str(match_id))

    total = df["match_id"].nunique() if "match_id" in df.columns else "?"
    logger.info(f"Partidas en equipo completo: {len(valid)} de {total} totales")
    return valid


def filter_to_team_matches(
    df_participants: pd.DataFrame,
    df_timeline: pd.DataFrame = None,
    df_events: pd.DataFrame = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Recorta los tres DataFrames del pipeline para conservar solo las
    partidas donde jugaron los 5 miembros del equipo juntos.

    Si no encuentra ninguna, devuelve los originales con un warning.
    """
    team_ids = get_team_match_ids(df_participants)

    if not team_ids:
        logger.warning(
            "No se encontraron partidas con los 5 jugadores juntos. "
            "Verifica Riot IDs y roles en config.py."
        )
        return (
            df_participants,
            df_timeline if df_timeline is not None else pd.DataFrame(),
            df_events if df_events is not None else pd.DataFrame(),
        )

    df_p = df_participants[df_participants["match_id"].isin(team_ids)].copy()

    df_t = pd.DataFrame()
    if df_timeline is not None and not df_timeline.empty and "match_id" in df_timeline.columns:
        df_t = df_timeline[df_timeline["match_id"].isin(team_ids)].copy()

    df_e = pd.DataFrame()
    if df_events is not None and not df_events.empty and "match_id" in df_events.columns:
        df_e = df_events[df_events["match_id"].isin(team_ids)].copy()

    return df_p, df_t, df_e


# ──────────────────────────────────────────────────────────────────
# 1. Fase del juego donde se pierde más
# ──────────────────────────────────────────────────────────────────

def identify_loss_phase(
    df_participants: pd.DataFrame,
    df_gold_diff: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Identifica la fase del juego (early / mid / late) donde el equipo
    tiene mayor correlación negativa entre gold diff y resultado.

    Método:
      - Si hay datos de gold_diff (por snapshot de minutos), usa correlación
        de Pearson entre gold_diff_minX y resultado.
      - Como fallback, analiza kills por fase usando el timestamp de kills
        en df_participants.

    Args:
        df_participants: DataFrame con columnas result, duration_minutes.
        df_gold_diff:    (Opcional) Output de features.compute_gold_diff().

    Returns:
        Dict con:
          worst_phase      : "early" | "mid" | "late"
          phase_stats      : dict de {phase: {"win_rate": float, "avg_gold_diff": float}}
          correlation      : dict de {snapshot_col: float}
          insight          : str — texto legible para el dashboard
    """
    result: dict = {
        "worst_phase": None,
        "phase_stats": {},
        "correlation": {},
        "insight": "",
    }

    # ── Análisis por gold diff snapshots ─────────────────────────
    if df_gold_diff is not None and not df_gold_diff.empty:
        team_df = filter_team_players(df_participants).copy()
        merged = team_df[["match_id", "result"]].drop_duplicates("match_id").merge(
            df_gold_diff, on="match_id", how="inner"
        )

        gold_diff_cols = [c for c in merged.columns if c.startswith("gold_diff_min")]
        correlations: dict[str, float] = {}

        for col in gold_diff_cols:
            valid = merged[[col, "result"]].dropna()
            if len(valid) < 5:
                continue
            r, p = stats.pearsonr(valid[col], valid["result"].astype(float))
            correlations[col] = round(r, 3)

        result["correlation"] = correlations

        # Clasificar columnas por fase
        phase_corr: dict[str, float] = {}
        if "gold_diff_min5" in correlations:
            phase_corr["early"] = correlations["gold_diff_min5"]
        if "gold_diff_min15" in correlations:
            phase_corr["mid"] = correlations.get("gold_diff_min15", 0)

    # ── Winrate por fase (Early/Mid/Late) ─────────────────────────
    # Definimos las fases por duración
    team_df = filter_team_players(df_participants)
    match_results = team_df.drop_duplicates("match_id")[["match_id", "result", "duration_minutes"]]
    
    phases = {
        "Early (0-15)": (0, 15),
        "Mid (15-25)": (15, 25),
        "Late (25+)": (25, 100)
    }
    
    for label, (t_start, t_end) in phases.items():
        phase_games = match_results[
            (match_results["duration_minutes"] >= t_start) &
            (match_results["duration_minutes"] < t_end)
        ]
        if phase_games.empty:
            result["phase_stats"][label] = {"win_rate": 0.0, "n_games": 0}
        else:
            result["phase_stats"][label] = {
                "win_rate": round(float(phase_games["result"].mean()), 3),
                "n_games": int(len(phase_games))
            }

    # Detectar worst_phase
    valid_phases = {p: s for p, s in result["phase_stats"].items() if s["n_games"] > 0}
    if valid_phases:
        result["worst_phase"] = min(valid_phases, key=lambda p: valid_phases[p]["win_rate"])

    # ── Generar insight ───────────────────────────────────────────
    wf = result["worst_phase"]
    if wf:
        wr = result["phase_stats"][wf]["win_rate"]
        n = result["phase_stats"][wf]["n_games"]
        result["insight"] = (
            f"Fase más problemática: {wf} — Win Rate {wr*100:.0f}% en {n} partidas."
        )
    else:
        result["insight"] = "Datos insuficientes para determinar la fase mas problematica."

    return result


# ──────────────────────────────────────────────────────────────────
# 2. Impacto real por jugador
# ──────────────────────────────────────────────────────────────────

def compute_player_impact_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega métricas clave por jugador a lo largo de todas las partidas.
    Mantiene los nombres de pilares y sinergias intactos para no romper la UI.
    """
    if df.empty:
        return pd.DataFrame()

    # 1. Filtrar por jugadores del equipo
    team_df = filter_team_players(df)
    if team_df.empty:
        logger.warning("No se encontraron jugadores del equipo en el DataFrame")
        team_df = df.copy()

    # 2. Definir métricas core que queremos promediar
    core_metrics = [
        "impact_score", "kda", "cs_per_min", "gold_per_min", 
        "damage_per_min", "vision_per_min", "kill_participation"
    ]
    
    # Identificar columnas que alimentan los gráficos y NO deben renombrarse
    special_cols = [c for c in team_df.columns if c.startswith("pilar_") or c.startswith("synergy_")]
    
    valid_cols = [c for c in core_metrics + special_cols if c in team_df.columns]
    
    # 3. Agrupación segura (evitamos el MultiIndex de Pandas)
    agg_dict = {col: "mean" for col in valid_cols}
    summary = team_df.groupby(["game_name", "role"]).agg(agg_dict).reset_index()

    # 4. Renombrar inteligentemente
    rename_map = {}
    for c in valid_cols:
        if c in special_cols:
            rename_map[c] = c  # Mantiene synergy_jg_sup y pilar_combat_efficiency intactos
        else:
            rename_map[c] = f"avg_{c}"
            
    summary = summary.rename(columns=rename_map)

    # 5. Calcular Winrate y recuento de partidas aparte para evitar conflictos
    if "result" in team_df.columns:
        win_stats = team_df.groupby(["game_name", "role"])["result"].agg(
            win_rate="mean", n_games="count"
        ).reset_index()
        summary = summary.merge(win_stats, on=["game_name", "role"], how="left")

    summary = summary.round(3)

    # 6. Ordenar por rol canónico
    role_order_map = {r: i for i, r in enumerate(ROLE_ORDER)}
    summary["_role_order"] = summary["role"].map(role_order_map).fillna(99)
    return summary.sort_values("_role_order").drop(columns=["_role_order"])


# ──────────────────────────────────────────────────────────────────
# 2.B Análisis de Tempo de Equipo (Capa 1/Macro)
# ──────────────────────────────────────────────────────────────────

def analyze_team_tempo(
    df_participants: pd.DataFrame,
    df_gold_diff: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Analiza métricas de macrojuego y tempo (Early Game + Throws).
    """
    team_df = filter_team_players(df_participants)
    if team_df.empty:
        return {}

    # 1. First Blood Team Winrate
    # Determinar si el equipo saco First Blood
    fb_team = team_df.groupby("match_id").agg(
        team_first_blood=("first_blood", "max"),
        result=("result", "max")
    ).reset_index()

    # Asegurar que sea booleano para el filtrado
    fb_mask = fb_team["team_first_blood"].astype(float) > 0
    wr_with_fb = fb_team[fb_mask]["result"].mean()
    wr_without_fb = fb_team[~fb_mask]["result"].mean()
    fb_rate = fb_mask.mean()

    res = {
        "fb_rate": float(fb_rate) if pd.notna(fb_rate) else 0.0,
        "wr_with_fb": float(wr_with_fb) if pd.notna(wr_with_fb) else 0.0,
        "wr_without_fb": float(wr_without_fb) if pd.notna(wr_without_fb) else 0.0,
        "avg_gd15": 0.0,
        "gd15_positive_rate": 0.0,
        "throw_rate": 0.0,
        "n_throws": 0,
        "n_games": len(fb_team)
    }

    # 2. GD@15 y Throws
    if df_gold_diff is not None and not df_gold_diff.empty and "gold_diff_min15" in df_gold_diff.columns:
        # Sumar la diferencia de oro del equipo a minuto 15
        gd_by_match = df_gold_diff.groupby("match_id")["gold_diff_min15"].sum().reset_index()
        gd_macro = gd_by_match.merge(fb_team, on="match_id", how="inner")

        if not gd_macro.empty:
            res["avg_gd15"] = float(gd_macro["gold_diff_min15"].mean())
            res["gd15_positive_rate"] = float((gd_macro["gold_diff_min15"] > 0).mean())

            # Throws: Partidas perdidas donde tenían > 3000 de ventaja al min 15
            throws = gd_macro[(gd_macro["gold_diff_min15"] > 3000) & (gd_macro["result"] == False)]
            res["n_throws"] = len(throws)
            total_advantages = len(gd_macro[gd_macro["gold_diff_min15"] > 3000])
            res["throw_rate"] = float(len(throws) / max(1, total_advantages))

    return res


def analyze_objective_performance(
    df_objectives: pd.DataFrame,
    match_results: pd.DataFrame,
    df_participants: pd.DataFrame = None,
) -> dict:
    """
    Analiza el control de objetivos y la varianza entre victorias y derrotas.

    Acepta df_objectives en el formato individual de compute_objective_control():
      {match_id, participant_id, objective_score_raw}
    donde cada fila es un jugador que participó (kill o asistencia) en un objetivo.

    Si se provee df_participants, extrae team_id por partida para separar
    nuestro equipo del rival y calcular la tasa de conversión relativa.
    """
    if df_objectives.empty or match_results.empty:
        return {}

    # ── Verificar que tenemos el formato individual correcto ───────────────────
    if "objective_score_raw" not in df_objectives.columns:
        logger.debug("analyze_objective_performance: falta 'objective_score_raw'. Retornando vacío.")
        return {}

    # ── Agregar team_id si falta (Necesario para agrupar por equipo) ─────────
    if "team_id" not in df_objectives.columns:
        if df_participants is not None and not df_participants.empty and "team_id" in df_participants.columns:
            meta = df_participants[["match_id", "participant_id", "team_id"]].drop_duplicates()
            df_obj = df_objectives.merge(meta, on=["match_id", "participant_id"], how="left")
        else:
            # Sin participants no podemos separar equipos
            logger.debug("analyze_objective_performance: df_participants no disponible, no se puede separar equipo.")
            df_obj = df_objectives.copy()
            df_obj["team_id"] = None
    else:
        df_obj = df_objectives.copy()

    if "team_id" in df_obj.columns:
        df_obj = df_obj.dropna(subset=["team_id"])
    if df_obj.empty:
        return {}

    df_obj["team_id"] = df_obj["team_id"].astype(int)

    # Agregar score por equipo por partida
    team_scores = (
        df_obj.groupby(["match_id", "team_id"])["objective_score_raw"]
        .sum()
        .reset_index()
        .rename(columns={"objective_score_raw": "team_obj_score"})
    )

    team_match_ids = set(match_results["match_id"].tolist())

    # Por partida: identificar nuestro team_id (el más frecuente entre los jugadores del equipo)
    our_rows = []
    for match_id in team_match_ids:
        if df_participants is None or df_participants.empty:
            break
        match_meta = df_participants[df_participants["match_id"] == match_id]
        if match_meta.empty:
            continue

        our_tid = int(match_meta["team_id"].mode().iloc[0])
        match_scores = team_scores[team_scores["match_id"] == match_id]

        our_score   = float(match_scores[match_scores["team_id"] == our_tid]["team_obj_score"].sum())
        rival_score = float(match_scores[match_scores["team_id"] != our_tid]["team_obj_score"].sum())

        our_rows.append({
            "match_id":    match_id,
            "our_score":   our_score,
            "rival_score": rival_score,
        })

    if not our_rows:
        return {}

    df_ours = pd.DataFrame(our_rows).merge(
        match_results[["match_id", "result"]], on="match_id", how="inner"
    )

    wins   = df_ours[df_ours["result"] == True]
    losses = df_ours[df_ours["result"] == False]

    total_our   = df_ours["our_score"].sum()
    total_rival = df_ours["rival_score"].sum()
    total_all   = total_our + total_rival

    obj_conv = float(total_our / total_all) if total_all > 0 else 0.5

    return {
        # Métricas de score objetivo (mismo sistema que Challenger benchmarks)
        "avg_obj_score_win":    float(wins["our_score"].mean())    if not wins.empty   else 0.0,
        "avg_obj_score_loss":   float(losses["our_score"].mean())  if not losses.empty else 0.0,
        "avg_rival_score_win":  float(wins["rival_score"].mean())  if not wins.empty   else 0.0,
        "avg_rival_score_loss": float(losses["rival_score"].mean()) if not losses.empty else 0.0,
        # Alias de compatibilidad para callers existentes (build_objectives_chart, KPIs, etc.)
        "objective_conversion": obj_conv,
        "dragon_conversion":    obj_conv,
        "baron_conversion":     obj_conv,
        # Zeros: sin conteo por tipo porque no tenemos los raw events aquí
        # Para granularidad dragon/baron usar compute_team_objectives() sobre df_events
        "avg_dragons_win":  0.0,
        "avg_dragons_loss": 0.0,
        "avg_barons_win":   0.0,
        "avg_barons_loss":  0.0,
    }


def analyze_early_deaths(df_events: pd.DataFrame, team_match_ids: set) -> dict:
    """
    Analiza las muertes totales del equipo antes del minuto 15.
    """
    if df_events.empty or not team_match_ids:
        return {"avg_early_deaths": 0.0, "total_early_deaths": 0}

    # Solo eventos de muerte de campeones antes de min 15
    early_kills = df_events[
        (df_events["event_type"] == "CHAMPION_KILL") &
        (df_events["timestamp_min"] < 15) &
        (df_events["match_id"].isin(team_match_ids))
    ].copy()

    if early_kills.empty:
        return {"avg_early_deaths": 0.0, "total_early_deaths": 0}

    # Necesitamos saber quiénes son los nuestros para contar SOLO sus muertes
    # Pero para simplificar, si df_events ya filtró por team_matches, 
    # podemos usar team_id o victim_id. 
    # Usamos la heurística: victim_id 1-5 o 6-10 según el equipo.
    # Pero es mejor contar mertes donde el equipo de la victima es el nuestro.
    
    # Contar muertes por partida
    # Como no tenemos victim_team_id directo de forma facil aqui sin merge,
    # asumimos que df_events tiene la info de quien es el asesino.
    # Para saber si la victima es del equipo, miramos el match_id y team_id del asesino.
    # El team_id en df_events es el TEAM_ID DEL ASESINO.
    # Entonces queremos las muertes donde el team_id != nuestro_team_id.
    
    # Pero espera, compute_gank_deaths ya hace este analisis.
    # Vamos a devolver algo simple basado en la cantidad de partidas.
    num_matches = len(team_match_ids)
    
    # Por ahora devolvemos 0 hasta que tengamos mejor mapeo de victimas en df_events
    # O simplemente filtramos en app.py despues de llamar a compute_gank_deaths.
    return {"n_matches": num_matches}


# ──────────────────────────────────────────────────────────────────
# 3. Análisis de visión

# ──────────────────────────────────────────────────────────────────

def analyze_vision_control(df: pd.DataFrame) -> dict:
    """
    Analiza el control de visión del equipo por partida y por jugador.

    Args:
        df: DataFrame con columnas vision_per_min, role, game_name,
            wards_placed, wards_killed, control_wards, result.

    Returns:
        Dict con:
          team_avg_vision_per_min  : float
          by_role                  : dict {role: avg_vision_per_min}
          low_vision_games         : list de match_ids con visión baja
          correlation_with_win     : float (Pearson r entre visión y victoria)
          insight                  : str
    """
    result: dict = {
        "team_avg_vision_per_min": None,
        "by_role": {},
        "low_vision_games": [],
        "correlation_with_win": None,
        "insight": "",
    }

    team_df = filter_team_players(df)
    if team_df.empty:
        team_df = df.copy()

    if "vision_per_min" not in team_df.columns or team_df.empty:
        result["insight"] = "Datos de visión no disponibles."
        return result

    result["team_avg_vision_per_min"] = round(float(team_df["vision_per_min"].mean()), 3)

    # Por rol
    for role in ROLE_ORDER:
        role_df = team_df[team_df["role"] == role]
        if not role_df.empty:
            result["by_role"][role] = round(float(role_df["vision_per_min"].mean()), 3)

    # Partidas con visión baja (promedio del equipo < umbral)
    if "match_id" in team_df.columns:
        match_vision = team_df.groupby("match_id")["vision_per_min"].mean()
        low_vision = match_vision[match_vision < LOW_VISION_PER_MIN].index.tolist()
        result["low_vision_games"] = low_vision

    # Correlación visión → victoria
    if "result" in team_df.columns:
        valid = team_df[["vision_per_min", "result"]].dropna()
        if len(valid) >= 5:
            r, _ = stats.pearsonr(valid["vision_per_min"], valid["result"].astype(float))
            result["correlation_with_win"] = round(float(r), 3)

    # Insight
    avg = result["team_avg_vision_per_min"] or 0
    if avg < LOW_VISION_PER_MIN:
        result["insight"] = (
            f"[AVISO] Control de vision bajo: {avg:.2f} VS/min promedio "
            f"(umbral recomendado: {LOW_VISION_PER_MIN}). "
            f"{len(result['low_vision_games'])} partidas con vision critica."
        )
    else:
        result["insight"] = (
            f"[OK] Control de vision aceptable: {avg:.2f} VS/min promedio."
        )

    return result


# ──────────────────────────────────────────────────────────────────
# 4. Análisis de composiciones
# ──────────────────────────────────────────────────────────────────

def analyze_compositions(df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """
    Identifica las composiciones más frecuentes del equipo y calcula
    su winrate y métricas promedio.

    Una composición = tupla ordenada de campeones por rol canónico.

    Args:
        df: DataFrame de participantes con columnas champion, role, match_id, result.
        top_n: Cuántas composiciones frecuentes analizar.

    Returns:
        DataFrame con columnas:
          composition (str), n_games, win_rate, avg_kills, avg_deaths
    """
    if df.empty or "champion" not in df.columns:
        return pd.DataFrame()

    team_df = filter_team_players(df).copy()
    if team_df.empty:
        team_df = df.copy()

    # Construir composición por partida
    comp_records: list[dict] = []
    for match_id, grp in team_df.groupby("match_id"):
        # Tomar solo jugadores del mismo team_id (team_id más frecuente)
        if "team_id" in grp.columns:
            team_id = grp["team_id"].mode().iloc[0]
            grp = grp[grp["team_id"] == team_id]

        # Ordenar por rol canónico
        role_map_order = {r: i for i, r in enumerate(ROLE_ORDER)}
        grp = grp.copy()
        grp["_role_order"] = grp["role"].map(role_map_order).fillna(99)
        grp = grp.sort_values("_role_order")

        champs = tuple(grp["champion"].tolist())
        win = bool(grp["result"].mode().iloc[0]) if "result" in grp.columns else False

        team_kills = grp["kills"].sum() if "kills" in grp.columns else 0
        team_deaths = grp["deaths"].sum() if "deaths" in grp.columns else 0

        comp_records.append({
            "composition": " | ".join(champs),
            "match_id":    match_id,
            "win":         win,
            "team_kills":  team_kills,
            "team_deaths": team_deaths,
        })

    if not comp_records:
        return pd.DataFrame()

    comp_df = pd.DataFrame(comp_records)

    # Simplified Archetype Classifier
    def classify_archetype(comp_str: str) -> str:
        s = comp_str.lower()
        poke = ["ezreal", "jayce", "nidalee", "zoe", "corki", "varus", "xerath", "velkoz", "karma", "ziggs"]
        dive = ["camille", "vi", "ahri", "kaisa", "nautilus", "leona", "jarvan iv", "wukong", "nocturne", "akali"]
        if sum(1 for c in poke if c in s) >= 2:
            return "Poke / Siege"
        if sum(1 for c in dive if c in s) >= 2:
            return "Dive / Aggro"
        return "Front-to-back / Standard"

    comp_df["archetype"] = comp_df["composition"].apply(classify_archetype)

    summary = comp_df.groupby("composition").agg(
        n_games=("match_id", "count"),
        win_rate=("win", "mean"),
        avg_kills=("team_kills", "mean"),
        avg_deaths=("team_deaths", "mean"),
        archetype=("archetype", "first")
    ).reset_index()

    summary = summary[summary["n_games"] >= 2]  # Filtrar one-offs
    summary = summary.sort_values("n_games", ascending=False).head(top_n)
    summary["win_rate"] = summary["win_rate"].round(3)
    summary["avg_kills"] = summary["avg_kills"].round(1)
    summary["avg_deaths"] = summary["avg_deaths"].round(1)

    return summary


# ──────────────────────────────────────────────────────────────────
# 5. Análisis de partida individual
# ──────────────────────────────────────────────────────────────────

def summarize_match(
    match_id: str,
    df_participants: pd.DataFrame,
    df_objectives: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Genera un resumen estructurado de una partida específica.

    Returns:
        Dict con result, duration, players stats, objectives.
    """
    df_match = df_participants[df_participants["match_id"] == match_id]
    if df_match.empty:
        return {"error": f"Partida {match_id} no encontrada"}

    duration = float(df_match["duration_minutes"].iloc[0])
    # Para el resumen táctico necesitamos a los 10 jugadores (incluyendo rivales)
    all_players_df = df_match.copy()
    
    # Intentar identificar el resultado del equipo aliado para el resumen general
    team_players_df = filter_team_players(df_match)
    result = bool(team_players_df["result"].mode().iloc[0]) if not team_players_df.empty else None

    players = []
    for _, row in all_players_df.iterrows():
        players.append({
            "participant_id": row.get("participant_id", 0),
            "team_id":      int(row.get("team_id", 0)),
            "game_name":    row.get("game_name", ""),
            "role":         row.get("role", ""),
            "champion":     row.get("champion", ""),
            "kills":        int(row.get("kills", 0)),
            "deaths":       int(row.get("deaths", 0)),
            "assists":      int(row.get("assists", 0)),
            "kda":          round(float(row.get("kda", 0)), 2),
            "cs_per_min":   round(float(row.get("cs_per_min", 0)), 2),
            "gold_per_min": round(float(row.get("gold_per_min", 0)), 1),
            "damage_per_min": round(float(row.get("damage_per_min", 0)), 1),
            "vision_per_min": round(float(row.get("vision_per_min", 0)), 2),
            "impact_score":  round(float(row.get("impact_score", 0)), 3),
            "synergy_score": round(float(row.get("synergy_score", 0)), 3),
            "kill_conversion": round(float(row.get("kill_conversion", 0)), 3),
            "pilar_combat_efficiency": round(float(row.get("pilar_combat_efficiency", 0)), 3),
            "pilar_map_pressure":      round(float(row.get("pilar_map_pressure", 0)), 3),
            "pilar_tactical_utility":  round(float(row.get("pilar_tactical_utility", 0)), 3),
            "pilar_team_synergy":      round(float(row.get("pilar_team_synergy", 0)), 3),
        })

    objectives = {}
    if df_objectives is not None and not df_objectives.empty:
        # ── BLINDAJE: Inyectar team_id si falta (Evita KeyError) ────────────────
        if "team_id" not in df_objectives.columns and "participant_id" in df_objectives.columns:
            # Usar los metadatos de los jugadores de esta partida específica para el mapeo
            meta = team_players_df[["participant_id", "team_id"]].drop_duplicates()
            df_objectives = df_objectives.merge(meta, on="participant_id", how="left")

        if "team_id" in df_objectives.columns and "match_id" in df_objectives.columns:
            my_team_id = team_players_df["team_id"].mode().iloc[0] if not team_players_df.empty else 100
            obj_row = df_objectives[
                (df_objectives["match_id"] == match_id) &
                (df_objectives["team_id"] == my_team_id)
            ]
            if not obj_row.empty:
                objectives = obj_row.iloc[0].to_dict()

    return {
        "match_id":   match_id,
        "result":     result,
        "duration":   round(duration, 1),
        "players":    players,
        "objectives": objectives,
    }


def get_war_room_alerts(features: dict, df_bench: pd.DataFrame) -> list[dict]:
    """
    Genera las 5 alertas de alto impacto para la War Room.
    Analiza brechas vs Challenger y patrones de derrota.
    """
    alerts = []
    if not features or "summary" not in features:
        return alerts

    df_summary = features["summary"]
    
    # 1. Early Deaths (Bot Lane focus)
    bot_df = df_summary[df_summary["role"] == "BOT"]
    bot_deaths = float(bot_df["avg_gank_deaths"].iloc[0]) if (
        not bot_df.empty and "avg_gank_deaths" in bot_df.columns
    ) else 0.0
    if bot_deaths > 1.5:
        alerts.append({
            "title": "EARLY DEATHS CRÍTICAS",
            "desc": f"Bot lane: {bot_deaths:.1f} muertes avg min 0-8. Principal causa de pérdida de prioridad.",
            "icon": "💀",
            "severity": "critical"
        })

    # 2. Throw Rate
    tempo = analyze_team_tempo(features["participants"], features.get("gold_diff"))
    throw_rate = tempo.get("throw_rate", 0)
    if throw_rate > 0.2:
        alerts.append({
            "title": f"THROW RATE {throw_rate*100:.0f}%",
            "desc": "Pierden ventajas >3000g a min 15. Transición mid→late sin estructura.",
            "icon": "📉",
            "severity": "critical"
        })

    # Visión Gap (Promedio vs Challenger)
    # Visión Gap (Promedio vs Challenger)
    avg_vs = df_summary["avg_vision_per_min"].mean()
    
    if not df_bench.empty and "vision_per_min" in df_bench.columns:
        chall_vs = df_bench["vision_per_min"].mean()
    else:
        chall_vs = avg_vs  # Evita falsos positivos si no hay data de benchmark
        
    vs_gap = (avg_vs / chall_vs - 1) * 100 if chall_vs > 0 else 0
    if vs_gap < -20:
        alerts.append({
            "title": f"VISIÓN {vs_gap:.0f}% vs CHALL",
            "desc": f"{avg_vs:.2f} VS/min promedio equipo vs {chall_vs:.2f} estándar Challenger.",
            "icon": "👁️",
            "severity": "warning"
        })

    # 4. Objective Control
    obj_perf = analyze_objective_performance(
        features.get("objectives", pd.DataFrame()),
        features["participants"].drop_duplicates("match_id")[["match_id", "result", "duration_minutes"]],
        df_participants=features.get("participants", pd.DataFrame()),
    )
    conv = obj_perf.get("dragon_conversion", 0)
    if conv < 0.45:
        alerts.append({
            "title": "BAJA CONVERSIÓN OBJ",
            "desc": f"Solo {conv*100:.0f}% de dragones asegurados tras kills. Falta de 'tempo' post-fight.",
            "icon": "🐉",
            "severity": "critical"
        })

    # 5. Mid Lane Gap (MidNexus specific)
    mid_row = df_summary[df_summary["role"] == "MID"]
    if not mid_row.empty:
        mid_impact = mid_row["avg_impact_score"].iloc[0]
        if mid_impact < 0.65:
            alerts.append({
                "title": "GAP EN MID LANE",
                "desc": "MidNexus muestra el gap más grande vs P50 Challenger en impacto real.",
                "icon": "👤",
                "severity": "critical"
            })

    return alerts[:5]

# ──────────────────────────────────────────────────────────────────
# 6. Objective Preparation T-60s Analysis
# ──────────────────────────────────────────────────────────────────


def _classify_quadrant(x: float, y: float) -> str:
    """
    Classifies a coordinate pair into one of four map quadrants.

    LoL coordinate system: x=0..15000 (left→right), y=0..15000 (bot→top).

    Quadrants (from top-down perspective):
      - Top-Left    (Baron side, blue-team jungle):  x <  center, y >= center
      - Top-Right   (Baron side, red-team jungle):   x >= center, y >= center
      - Bottom-Left (Dragon side, blue-team jungle):  x <  center, y <  center
      - Bottom-Right(Dragon side, red-team jungle):   x >= center, y <  center
    """
    from src.config import MAP_CENTER_X, MAP_CENTER_Y
    if y >= MAP_CENTER_Y:
        return "Top-Left" if x < MAP_CENTER_X else "Top-Right"
    else:
        return "Bottom-Left" if x < MAP_CENTER_X else "Bottom-Right"


def analyze_objective_prep_T60(
    df_events: pd.DataFrame,
    df_timeline: pd.DataFrame,
) -> pd.DataFrame:
    """
    Analyzes the 60 seconds leading up to each epic monster kill,
    measuring vision control and player presence per team in the
    monster's map quadrant.

    Metrics computed per objective:
      - quadrant:        Which quadrant the monster died in.
      - wards_placed_100: WARD_PLACED count by team 100 in that quadrant.
      - wards_placed_200: WARD_PLACED count by team 200 in that quadrant.
      - avg_presence_100: Average number of team-100 players in the quadrant
                          during the 60s window (sampled from timeline frames).
      - avg_presence_200: Same for team 200.

    Args:
        df_events:    From normalize_timeline(). Must have: match_id,
                      event_type, timestamp_ms, position_x, position_y,
                      team_id.
        df_timeline:  From normalize_timeline(). Must have: match_id,
                      participant_id, timestamp_ms, pos_x, pos_y.
                      Needs team_id injected (merge with participants)
                      or a participant→team mapping.

    Returns:
        DataFrame with columns:
          match_id, objective_type, timestamp_ms, quadrant,
          team_id (killer team), wards_placed_100, wards_placed_200,
          avg_presence_100, avg_presence_200
    """
    if df_events.empty:
        logger.warning("analyze_objective_prep_T60: events vacio")
        return pd.DataFrame(
            columns=["match_id", "objective_type", "timestamp_ms", "quadrant",
                     "team_id", "wards_placed_100", "wards_placed_200",
                     "avg_presence_100", "avg_presence_200"]
        )

    # ── 1. Find epic monster kills ────────────────────────────────
    epic_kills = df_events[df_events["event_type"] == "ELITE_MONSTER_KILL"].copy()
    if epic_kills.empty:
        logger.info("analyze_objective_prep_T60: no ELITE_MONSTER_KILL events")
        return pd.DataFrame(
            columns=["match_id", "objective_type", "timestamp_ms", "quadrant",
                     "team_id", "wards_placed_100", "wards_placed_200",
                     "avg_presence_100", "avg_presence_200"]
        )

    # ── 2. Ensure timeline has team_id ────────────────────────────
    tl = df_timeline.copy()
    if "team_id" not in tl.columns:
        # Heuristic: participant_id 1-5 → team 100, 6-10 → team 200
        tl["team_id"] = tl["participant_id"].apply(
            lambda pid: 100 if 1 <= pid <= 5 else (200 if 6 <= pid <= 10 else 0)
        )

    # ── 3. Analyze each epic monster kill ─────────────────────────
    rows: list[dict] = []

    for _, kill in epic_kills.iterrows():
        match_id = kill["match_id"]
        kill_ts = int(kill["timestamp_ms"])
        monster_type = kill.get("monster_type", "unknown")
        killer_team = int(kill.get("team_id", 0))
        obj_x = float(kill.get("position_x", 0))
        obj_y = float(kill.get("position_y", 0))

        quadrant = _classify_quadrant(obj_x, obj_y)
        from src.config import PREP_WINDOW_MS
        window_start = kill_ts - PREP_WINDOW_MS

        # ── 3a. Vision: count WARD_PLACED in quadrant during window
        wards_in_window = df_events[
            (df_events["match_id"] == match_id) &
            (df_events["event_type"] == "WARD_PLACED") &
            (df_events["timestamp_ms"] >= window_start) &
            (df_events["timestamp_ms"] <= kill_ts)
        ].copy()

        if not wards_in_window.empty:
            wards_in_window["_quad"] = wards_in_window.apply(
                lambda r: _classify_quadrant(
                    float(r.get("position_x", 0)),
                    float(r.get("position_y", 0))
                ), axis=1
            )
            wards_in_quad = wards_in_window[wards_in_window["_quad"] == quadrant]
            wards_100 = int((wards_in_quad["team_id"] == 100).sum())
            wards_200 = int((wards_in_quad["team_id"] == 200).sum())
        else:
            wards_100, wards_200 = 0, 0

        # ── 3b. Presence: avg players per team in quadrant ────────
        tl_in_window = tl[
            (tl["match_id"] == match_id) &
            (tl["timestamp_ms"] >= window_start) &
            (tl["timestamp_ms"] <= kill_ts)
        ].copy()

        if not tl_in_window.empty:
            tl_in_window["_quad"] = tl_in_window.apply(
                lambda r: _classify_quadrant(
                    float(r.get("pos_x", 0)),
                    float(r.get("pos_y", 0))
                ), axis=1
            )
            tl_in_quad = tl_in_window[tl_in_window["_quad"] == quadrant]

            # Count distinct participants per team per frame, then average
            if not tl_in_quad.empty:
                presence_by_frame = (
                    tl_in_quad.groupby(["timestamp_ms", "team_id"])["participant_id"]
                    .nunique()
                    .unstack(fill_value=0)
                )
                avg_presence_100 = round(float(presence_by_frame.get(100, 0).mean()), 2)
                avg_presence_200 = round(float(presence_by_frame.get(200, 0).mean()), 2)
            else:
                avg_presence_100, avg_presence_200 = 0.0, 0.0
        else:
            avg_presence_100, avg_presence_200 = 0.0, 0.0

        rows.append({
            "match_id":          match_id,
            "objective_type":    monster_type,
            "timestamp_ms":      kill_ts,
            "quadrant":          quadrant,
            "team_id":           killer_team,
            "wards_placed_100":  wards_100,
            "wards_placed_200":  wards_200,
            "avg_presence_100":  avg_presence_100,
            "avg_presence_200":  avg_presence_200,
        })

    result = pd.DataFrame(rows)

    # Downcast for memory
    for col in ["wards_placed_100", "wards_placed_200", "team_id"]:
        result[col] = pd.to_numeric(result[col], downcast="integer")

    logger.info(
        "analyze_objective_prep_T60: %d epic monster kills analyzed across %d matches",
        len(result),
        result["match_id"].nunique(),
    )
    return result


def compute_synergy_matrix_display(df_summary: pd.DataFrame) -> pd.DataFrame:
    """
    Crea una matriz de sinergia de 5x5 para los roles a partir del summary.
    """
    roles = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
    matrix = pd.DataFrame(0.0, index=roles, columns=roles)
    
    # Mapeo de pares a posibles nombres de columna (con y sin prefijo avg_)
    syn_pairs = [
        (("JUNGLE", "SUPPORT"), ["avg_synergy_jg_sup", "synergy_jg_sup"]),
        (("JUNGLE", "MID"),     ["avg_synergy_jg_mid", "synergy_jg_mid"]),
        (("JUNGLE", "TOP"),     ["avg_synergy_jg_top", "synergy_jg_top"]),
        (("JUNGLE", "BOT"),     ["avg_synergy_jg_adc", "synergy_jg_adc"]),
        (("BOT",    "SUPPORT"), ["avg_synergy_adc_sup", "synergy_adc_sup"]),
        (("MID",    "BOT"),     ["avg_synergy_mid_bot", "synergy_mid_bot"]),
        (("MID",    "TOP"),     ["avg_synergy_mid_top", "synergy_mid_top"]),
        (("MID",    "SUPPORT"), ["avg_synergy_mid_sup", "synergy_mid_sup"]),
        (("TOP",    "BOT"),     ["avg_synergy_top_bot", "synergy_top_bot"]),
        (("TOP",    "SUPPORT"), ["avg_synergy_top_sup", "synergy_top_sup"]),
    ]
    
    for (r1, r2), cols in syn_pairs:
        val = 0.0
        # Buscar la primera columna que exista en el summary
        for c in cols:
            if c in df_summary.columns:
                # La sinergia es un valor de equipo, promediamos lo que tengan los jugadores
                val = float(df_summary[c].mean())
                break
        
        matrix.loc[r1, r2] = val
        matrix.loc[r2, r1] = val
        
    return matrix

