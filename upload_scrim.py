#!/usr/bin/env python
"""
upload_scrim.py — Ingestor manual y automatizado de Scrims para Challenger Protocol.

Permite subir partidas personalizadas (scrims) a Supabase usando su Riot Match ID.
Calcula todas las métricas avanzadas (Impact Score, Sinergias) y las guarda con `is_custom = True`.
Dado que sync_team.py solo sincroniza partidas de Flex Queue (440), estas partidas de scrim
permanecerán seguras y persistentes en la base de datos sin riesgo de borrado accidental.

Uso:
    python upload_scrim.py --match-id LA2_XXXXXXXXX
"""

import os
import sys
import argparse
import logging
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

from src.data_loader import RiotAPILoader
from src.features import (
    compute_player_metrics,
    compute_synergy_matrix,
    compute_impact_score,
    compute_objective_control
)

# Configuración de Logging profesional
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

def upload_scrim(match_id: str):
    load_dotenv()
    
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    api_key = os.environ.get("RIOT_API_KEY")
    
    if not url or not key:
        logging.error("❌ Error: SUPABASE_URL o SUPABASE_KEY no configurados en .env")
        sys.exit(1)
        
    if not api_key:
        logging.error("❌ Error: RIOT_API_KEY no configurada en .env")
        sys.exit(1)
        
    supabase = create_client(url, key)
    loader = RiotAPILoader(api_key)
    
    logging.info(f"📥 Descargando datos de la partida de scrim: {match_id} desde Riot API...")
    df_p, df_t, df_e = loader.load_single_match_by_id(match_id)
    
    if df_p.empty:
        logging.error(f"❌ No se encontró o no se pudo procesar la partida con ID: {match_id}")
        sys.exit(1)
        
    logging.info("⚙️ Procesando métricas avanzadas y de rendimiento...")
    
    # Asegurar que se marque como custom game
    df_p["is_custom"] = True
    
    # Calcular métricas básicas del jugador
    df_p = compute_player_metrics(df_p)
    
    # Fix de nombres de columnas si aplica
    if "damageDealtToBuildings" in df_p.columns:
        df_p["damage_buildings"] = df_p["damageDealtToBuildings"]
        
    df_obj = compute_objective_control(df_e, df_p)
    
    # Prevenir KeyErrors en stomps
    if "objective_score_raw" not in df_obj.columns: 
        df_obj["objective_score_raw"] = 0.0
    if "objective_score_raw" not in df_p.columns: 
        df_p["objective_score_raw"] = 0.0

    # Calcular Sinergia SKP
    synergy_matrix = compute_synergy_matrix(df_e, df_p)
    
    # Inyectar sinergia para el promedio de radares
    for idx, row in df_p.iterrows():
        tid = row["team_id"]
        syn_data = synergy_matrix.get(tid, {})
        for k, v in syn_data.items():
            df_p.at[idx, k] = v
        df_p.at[idx, "synergy_score"] = sum(syn_data.values()) / max(1, len(syn_data))

    # Calcular Impact Score final (Min-Max)
    df_p = compute_impact_score(df_p, df_obj)
    
    # Forzar is_custom = True en todos los participantes
    df_p["is_custom"] = True
    
    logging.info(f"📤 Subiendo scrim {match_id} a Supabase...")
    
    # 1. Matches Metadata
    duration_min = float(df_p.iloc[0]["duration_minutes"]) if "duration_minutes" in df_p.columns else 30.0
    result = bool(df_p.iloc[0]["result"]) if "result" in df_p.columns else True
    
    match_record = {
        "match_id": match_id,
        "duration_minutes": duration_min,
        "result": result,
        "is_custom": True,
        "queue_id": 0,  # Custom / Scrim
        "patch": "14.24",
        "notes": f"Scrim subida manualmente vía upload_scrim.py el {pd.Timestamp.now().strftime('%Y-%m-%d')}"
    }
    
    supabase.table("team_match_metadata").upsert(match_record, on_conflict="match_id").execute()
    logging.info("✅ Metadatos guardados en team_match_metadata.")

    # Columnas válidas para participantes
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
    
    # 2. Participants Table
    participants_records = df_p.where(pd.notnull(df_p), None).to_dict(orient="records")
    clean_tp_records = []
    for r in participants_records:
        clean_r = {k: v for k, v in r.items() if k in tp_cols}
        clean_tp_records.append(clean_r)
        
    if clean_tp_records:
        supabase.table("team_participants").delete().eq("match_id", match_id).execute()
        supabase.table("team_participants").insert(clean_tp_records).execute()
        logging.info(f"✅ Guardados {len(clean_tp_records)} participantes en team_participants.")

    # 3. Timeline Table
    if not df_t.empty:
        tl_cols = {"match_id", "participant_id", "timestamp_ms", "total_gold", "cs", "xp", "level", "pos_x", "pos_y"}
        timeline_records = df_t.where(pd.notnull(df_t), None).to_dict(orient="records")
        clean_tl_records = []
        for r in timeline_records:
            clean_r = {k: v for k, v in r.items() if k in tl_cols}
            clean_tl_records.append(clean_r)
        if clean_tl_records:
            supabase.table("team_timeline").delete().eq("match_id", match_id).execute()
            for i in range(0, len(clean_tl_records), 100):
                chunk = clean_tl_records[i : i + 100]
                supabase.table("team_timeline").insert(chunk).execute()
            logging.info(f"✅ Guardados {len(clean_tl_records)} frames de timeline.")

    # 4. Events Table
    if not df_e.empty:
        ev_cols = {
            "match_id", "timestamp_ms", "timestamp_min", "event_type", "participant_id",
            "victim_id", "team_id", "victim_team_id", "assisting_ids", "monster_type",
            "building_type", "item_id", "position_x", "position_y"
        }
        event_records = df_e.where(pd.notnull(df_e), None).to_dict(orient="records")
        clean_ev_records = []
        for r in event_records:
            clean_r = {k: v for k, v in r.items() if k in ev_cols}
            clean_ev_records.append(clean_r)
        if clean_ev_records:
            supabase.table("team_events").delete().eq("match_id", match_id).execute()
            for i in range(0, len(clean_ev_records), 100):
                chunk = clean_ev_records[i : i + 100]
                supabase.table("team_events").insert(chunk).execute()
            logging.info(f"✅ Guardados {len(clean_ev_records)} eventos de partida.")

    logging.info(f"🎉 ¡Partida de Scrim {match_id} subida con éxito!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingestor de Scrims para Challenger Protocol")
    parser.add_argument("--match-id", type=str, required=True, help="ID de la partida de Riot (ej: LA2_123456789)")
    args = parser.parse_args()
    
    upload_scrim(args.match_id)
