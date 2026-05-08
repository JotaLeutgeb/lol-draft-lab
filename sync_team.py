import os
import logging
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client
from src.data_loader import RiotAPILoader
from src.features import compute_player_metrics, compute_synergy_matrix, compute_impact_score, compute_objective_control
from src.analysis import compute_player_impact_summary

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def sync_our_team(matches=20):
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    supabase = create_client(url, key)
    
    loader = RiotAPILoader(os.environ.get("RIOT_API_KEY"))
    logging.info(f"📥 Descargando últimas {matches} partidas del equipo...")
    
    df_p, df_t, df_e = loader.load_team_matches(count_per_player=matches, queues=[440])
    if df_p.empty:
        logging.error("❌ No se encontraron partidas que cumplan los criterios (5 jugadores juntos).")
        logging.info("💡 Tip: Verifica que los Riot IDs en src/config.py coincidan con los actuales.")
        return
    
    n_matches = df_p["match_id"].nunique()
    logging.info(f"✅ Se encontraron {n_matches} partidas del equipo. Procesando...")
    df_p = compute_player_metrics(df_p)
    
    # Fix de nombres crudos si es necesario
    if "damageDealtToBuildings" in df_p.columns:
        df_p["damage_buildings"] = df_p["damageDealtToBuildings"]
        
    df_obj = compute_objective_control(df_e, df_p)
    
    # Evitar KeyErrors en stomps
    if "objective_score_raw" not in df_obj.columns: df_obj["objective_score_raw"] = 0.0
    if "objective_score_raw" not in df_p.columns: df_p["objective_score_raw"] = 0.0

    # Sinergia
    synergy_matrix = compute_synergy_matrix(df_e, df_p)
    
    # Inyectar sinergia temporal para el promediado
    for idx, row in df_p.iterrows():
        tid = row["team_id"]
        syn_data = synergy_matrix.get(tid, {})
        for k, v in syn_data.items():
            df_p.at[idx, k] = v
        df_p.at[idx, "synergy_score"] = sum(syn_data.values()) / max(1, len(syn_data))

    # Impact Score Final
    df_p = compute_impact_score(df_p, df_obj)
    
    logging.info("📤 Subiendo partidas completas individuales, participantes, timelines y eventos a Supabase...")
    
    # 1. Matches Table (team_match_metadata)
    matches_records = []
    unique_match_ids = df_p["match_id"].unique().tolist()
    for mid in unique_match_ids:
        match_p = df_p[df_p["match_id"] == mid]
        if not match_p.empty:
            duration_min = float(match_p.iloc[0]["duration_minutes"]) if "duration_minutes" in match_p.columns else 30.0
            result = bool(match_p.iloc[0]["result"]) if "result" in match_p.columns else True
            matches_records.append({
                "match_id": mid,
                "duration_minutes": duration_min,
                "result": result,
                "is_custom": False,
                "queue_id": 420,
                "patch": "14.24",
                "notes": "Sincronizado vía sync_team.py"
            })
    if matches_records:
        supabase.table("team_match_metadata").upsert(matches_records, on_conflict="match_id").execute()
        logging.info(f"✅ Upserted {len(matches_records)} matches en tabla team_match_metadata")

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
        supabase.table("team_participants").delete().in_("match_id", unique_match_ids).execute()
        for i in range(0, len(clean_tp_records), 50):
            chunk = clean_tp_records[i : i + 50]
            supabase.table("team_participants").insert(chunk).execute()
        logging.info(f"✅ Inserted {len(clean_tp_records)} participantes en team_participants")

    # 3. Timeline Table
    if not df_t.empty:
        tl_cols = {"match_id", "participant_id", "timestamp_ms", "total_gold", "cs", "xp", "level", "pos_x", "pos_y"}
        timeline_records = df_t.where(pd.notnull(df_t), None).to_dict(orient="records")
        clean_tl_records = []
        for r in timeline_records:
            clean_r = {k: v for k, v in r.items() if k in tl_cols}
            clean_tl_records.append(clean_r)
        if clean_tl_records:
            supabase.table("team_timeline").delete().in_("match_id", unique_match_ids).execute()
            for i in range(0, len(clean_tl_records), 100):
                chunk = clean_tl_records[i : i + 100]
                supabase.table("team_timeline").insert(chunk).execute()
            logging.info(f"✅ Inserted {len(clean_tl_records)} frames de timeline en team_timeline")

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
            supabase.table("team_events").delete().in_("match_id", unique_match_ids).execute()
            for i in range(0, len(clean_ev_records), 100):
                chunk = clean_ev_records[i : i + 100]
                supabase.table("team_events").insert(chunk).execute()
            logging.info(f"✅ Inserted {len(clean_ev_records)} eventos en team_events")

    logging.info("📊 Consolidando los 5 roles...")
    df_summary = compute_player_impact_summary(df_p)
    
    # Subida limpia de 5 filas
    records = df_summary.fillna(0).to_dict(orient="records")
    supabase.table("team_benchmark").upsert(records, on_conflict="game_name,role").execute()
    logging.info("✅ Team Benchmark sincronizado. El Streamlit ahora vuela.")

if __name__ == "__main__":
    sync_our_team(20)