#!/usr/bin/env python
"""
upload_manual_scrim.py — Ingestion of Manual Scrims from structured JSON.

This script reads a simple JSON representing the final scoreboard of a scrim,
calculates the required advanced metrics (KDA, CS/min, gold/min, first_blood,
objective_score, synergy, and Impact Score), and uploads the results safely
to Supabase without needing the Riot API.

Usage:
    python upload_manual_scrim.py --file scratch/my_scrim.json
"""

import os
import sys
import argparse
import json
import logging
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

from src.features import compute_player_metrics, compute_impact_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

def upload_manual_scrim(file_path: str):
    load_dotenv()
    
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    
    if not url or not key:
        logging.error("❌ Error: SUPABASE_URL o SUPABASE_KEY no configurados en .env")
        sys.exit(1)
        
    if not os.path.exists(file_path):
        logging.error(f"❌ Error: El archivo {file_path} no existe.")
        sys.exit(1)
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"❌ Error al leer el archivo JSON: {e}")
        sys.exit(1)
        
    match_id = data.get("match_id")
    duration_min = float(data.get("duration_minutes", 30.0))
    result = bool(data.get("result", True))
    players = data.get("players", [])
    
    if not match_id or not players:
        logging.error("ERROR: JSON invalido. Debe contener 'match_id' y una lista de 'players'.")
        sys.exit(1)
        
    logging.info(f"Procesando partida manual {match_id} ({len(players)} jugadores)...")
    
    # Importar mapeo para display_name y tags desde config
    from src.config import TEAM_PLAYERS
    roster_map = {p["role"]: p for p in TEAM_PLAYERS}
    
    rows = []
    for idx, p in enumerate(players):
        role = p.get("role")
        champ = p.get("champion")
        kills = int(p.get("kills", 0))
        deaths = int(p.get("deaths", 0))
        assists = int(p.get("assists", 0))
        gold = int(p.get("gold_earned", 10000))
        cs = int(p.get("cs", 200))
        vision = int(p.get("vision_score", 20))
        damage = int(p.get("total_damage", 15000))
        
        # Completar información del jugador titular del roster de config.py
        player_info = roster_map.get(role, {})
        riot_id = player_info.get("riot_id", f"Player_{role}#LAS")
        game_name = riot_id.split("#")[0]
        tag_line = riot_id.split("#")[1] if "#" in riot_id else "LAS"
        
        row = {
            "match_id": match_id,
            "participant_id": idx + 1,
            "puuid": f"manual_puuid_{role.lower()}",
            "game_name": game_name,
            "tag_line": tag_line,
            "team_id": 100,  # Asumimos equipo azul/aliado
            "role": role,
            "champion": champ,
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "gold_earned": gold,
            "total_damage": damage,
            "damage_taken": int(p.get("damage_taken", damage * 0.8)),
            "damage_mitigated": int(p.get("damage_mitigated", damage * 0.2)),
            "damage_buildings": int(p.get("damage_buildings", 0)),
            "total_heal": int(p.get("total_heal", 0)),
            "vision_score": vision,
            "cs": cs,
            "duration_minutes": duration_min,
            "result": result,
            "is_custom": True,
            "first_blood": bool(p.get("first_blood", False)),
            "control_wards": int(p.get("control_wards", 2))
        }
        rows.append(row)
        
    df_p = pd.DataFrame(rows)
    
    # Calcular métricas básicas del jugador (KDA, CS/Min, Gold/Min, etc.)
    df_p = compute_player_metrics(df_p)
    
    # Sinergias (SKP) - Seteamos un valor neutro temporal de 0.5 para que
    # compute_impact_score calcule un puntaje balanceado, pero luego lo limpiaremos a NULL
    df_p["synergy_score"] = 0.5
    
    # Crear un DataFrame dummy para objetivos para que compile compute_impact_score
    df_obj = pd.DataFrame(columns=["match_id", "participant_id", "objective_score_raw"])
    
    # Calcular Impact Score final en modo Fallback Min-Max
    df_p = compute_impact_score(df_p, df_obj)
    
    # Limpiar todas las columnas exclusivas de API / Sinergia a None (NULL)
    # para que no distorsionen los promedios reales calculados en el frontend
    for col in [
        "synergy_jg_sup", "synergy_jg_mid", "synergy_jg_top", "synergy_jg_adc",
        "synergy_adc_sup", "synergy_mid_bot", "synergy_mid_top", "synergy_mid_sup",
        "synergy_top_bot", "synergy_top_sup", "synergy_score",
        "early_solo_deaths", "early_gank_deaths", "early_gank_kills", "kill_conversion"
    ]:
        df_p[col] = None
        
    # Asegurar que se guarde como custom game
    df_p["is_custom"] = True
    
    # 1. Matches Metadata
    supabase = create_client(url, key)
    
    match_record = {
        "match_id": match_id,
        "duration_minutes": duration_min,
        "result": result,
        "is_custom": True,
        "queue_id": 0,
        "patch": "14.24",
        "notes": f"Scrim manual subido vía JSON desde {os.path.basename(file_path)}"
    }
    
    supabase.table("team_match_metadata").upsert(match_record, on_conflict="match_id").execute()
    logging.info("Metadatos guardados en team_match_metadata.")
    
    # 2. Participants Table
    tp_cols = {
        "match_id", "participant_id", "puuid", "game_name", "tag_line", "team_id", "role", "champion",
        "kills", "deaths", "assists", "gold_earned", "total_damage", "damage_taken", "damage_mitigated",
        "damage_buildings_raw", "vision_score", "cs", "duration_minutes", "result", "is_custom", "first_blood",
        "impact_score", "synergy_jg_sup", "synergy_jg_mid", "synergy_jg_top", "synergy_jg_adc", "synergy_adc_sup",
        "synergy_mid_bot", "synergy_mid_top", "synergy_mid_sup", "synergy_top_bot", "synergy_top_sup", "synergy_score",
        "kda", "pilar_combat_efficiency", "pilar_map_pressure", "pilar_tactical_utility", "pilar_team_synergy",
        "resilience_index", "damage_per_gold", "cc_per_min", "kill_participation", "objective_control",
        "kill_conversion", "damage_efficiency", "damage_buildings", "early_solo_deaths", "early_gank_deaths", "early_gank_kills"
    }
    
    participants_records = df_p.where(pd.notnull(df_p), None).to_dict(orient="records")
    clean_tp_records = []
    for r in participants_records:
        clean_r = {k: v for k, v in r.items() if k in tp_cols}
        clean_tp_records.append(clean_r)
        
    if clean_tp_records:
        supabase.table("team_participants").delete().eq("match_id", match_id).execute()
        supabase.table("team_participants").insert(clean_tp_records).execute()
        logging.info(f"Guardados {len(clean_tp_records)} participantes en team_participants.")
        
    logging.info(f"SUCCESS: Scrim manual {match_id} cargada con exito!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Subidor de scrims manuales desde JSON")
    parser.add_argument("--file", type=str, required=True, help="Ruta al archivo JSON de la scrim")
    args = parser.parse_args()
    
    upload_manual_scrim(args.file)
