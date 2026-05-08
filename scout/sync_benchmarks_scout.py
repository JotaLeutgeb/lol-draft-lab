"""
sync_benchmarks_scout.py — Recopilación de referencias (Challenger) para Scout Hub.

Extrae partidas Challenger/GM y pobla las tablas:
  - benchmarks_summary (Segmentado por champion, role, patch, region, result)
"""

import os
import sys
import time
import logging
import random
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

from supabase import create_client
from src.data_loader_scout import ScoutMatchClient, normalize_match, normalize_timeline
from src.features_scout import compute_player_metrics, compute_objective_control, compute_kill_conversion, compute_impact_score_individual

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

def sync_benchmarks(regions=["KR", "EUW", "BR"], players_per_region=20):
    riot_key = os.environ.get("RIOT_API_KEY")
    sb_url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    
    if not riot_key or not sb_url or not sb_key:
        logger.error("Faltan credenciales en .env")
        return

    supabase = create_client(sb_url, sb_key)

    region_config = {
        "KR":  {"platform": "kr",   "routing": "asia"},
        "EUW": {"platform": "euw1", "routing": "europe"},
        "LA2": {"platform": "br1",  "routing": "americas"},
    }

    for region_name in regions:
        conf = region_config.get(region_name)
        if not conf: continue

        logger.info(f"====== Procesando Benchmarks para {region_name} ======")
        client = ScoutMatchClient(riot_key, conf["platform"], conf["routing"])

        # 1. Obtener players
        summoners = []
        for tier in ["challenger"]:
            resp = client.session.get(f"https://{conf['platform']}.api.riotgames.com/lol/league/v4/{tier}leagues/by-queue/RANKED_SOLO_5x5")
            if resp.status_code == 200:
                data = resp.json()
                entries = data.get("entries", [])
                if entries:
                    new_puuids = [e["puuid"] for e in entries if "puuid" in e]
                    summoners.extend(new_puuids)
                    logger.info(f"Encontrados {len(entries)} jugadores en la liga {tier}")
                else:
                    logger.warning(f"La liga {tier} en {region_name} no devolvió entradas.")
            else:
                logger.error(f"Error {resp.status_code} consultando liga {tier}: {resp.text}")

        if not summoners:
            logger.warning(f"No se encontraron jugadores para {region_name}")
            continue

        sampled = random.sample(summoners, min(len(summoners), players_per_region))
        logger.info(f"Muestreando {len(sampled)} jugadores...")
        
        match_ids = set()
        for puuid in sampled:
            mids = client.get_match_ids_by_puuid(puuid, count=3, queue=420)
            if mids:
                match_ids.update(mids)
            time.sleep(0.1) # Mucho más rápido sin el lookup de summoner

        logger.info(f"Encontradas {len(match_ids)} partidas en {region_name} (Riot API)")

        # FILTRO DE SUPABASE: No procesar partidas existentes
        if match_ids:
            existing_ids = set()
            ids_list = list(match_ids)
            for i in range(0, len(ids_list), 50):
                chunk = ids_list[i:i+50]
                res = supabase.table("matches").select("match_id").in_("match_id", chunk).execute()
                if res.data:
                    existing_ids.update(r["match_id"] for r in res.data)
            if existing_ids:
                match_ids = match_ids - existing_ids
                logger.info(f"⏭️ Saltando {len(existing_ids)} partidas ya procesadas en Supabase. {len(match_ids)} nuevas.")

        if not match_ids:
            logger.info(f"No hay partidas nuevas para {region_name}")
            continue

        summary_rows = []
        processed_matches_info = []

        for mid in list(match_ids)[:100]: # max 100 per region for demo
            m_data = client.get_match(mid)
            if not m_data: continue

            info = m_data.get("info", {})
            dur = info.get("gameDuration", 0)
            if dur > 7200:
                dur //= 1000
                
            patch = ".".join(info.get("gameVersion", "0.0.0").split(".")[:2])
            
            # Filter matches < 25 min or > 35 min
            if dur < 1500 or dur > 2100:
                # Saltamos la partida sin registrarla en la base de datos (eliminamos el caché de inválidas)
                continue
            
            processed_matches_info.append({"match_id": mid, "platform": conf["platform"], "patch_version": patch, "duration_min": dur/60.0, "is_processed": True})
            
            df_p = normalize_match(m_data)
            if df_p.empty: continue

            df_p = compute_player_metrics(df_p)
            
            t_data = client.get_timeline(mid)
            if not t_data: continue

            p_to_team = df_p.set_index("participant_id")["team_id"].to_dict()
            df_t, df_e = normalize_timeline(t_data, mid, p_to_team)
            
            df_obj = compute_objective_control(df_e, df_p)
            df_kc = compute_kill_conversion(df_e, df_p)
            
            df_p = compute_impact_score_individual(df_p, df_obj, df_kc, consistency_score=0.5)

            # Compute synergy per team
            from src.features_scout import compute_player_synergy_from_events
            syn_per_team = {}
            for team_id in [100, 200]:
                team_p = df_p[df_p["team_id"] == team_id]
                if team_p.empty:
                    continue
                # Use first player's game_name as reference for that team
                ref_name = team_p.iloc[0]["game_name"]
                df_syn_match = compute_player_synergy_from_events(df_e, df_p, ref_name)
                if not df_syn_match.empty:
                    for _, sr in df_syn_match.iterrows():
                        for c in df_syn_match.columns:
                            if c.startswith("synergy_"):
                                syn_per_team.setdefault(team_id, {})[c] = sr[c]

            # Append to Summary list (will be grouped later)
            for _, p in df_p.iterrows():
                team_syn = syn_per_team.get(p["team_id"], {})
                summary_rows.append({
                    "match_id": str(p.get("match_id", mid)),
                    "patch": patch,
                    "champion": p["champion"],
                    "role": p["role"],
                    "result": bool(p["result"]),
                    "gold_per_min": p["gold_per_min"],
                    "cs_per_min": p["cs_per_min"],
                    "vision_per_min": p["vision_per_min"],
                    "damage_per_min": p["damage_per_min"],
                    "kda": p["kda"],
                    "kill_participation": p["kill_participation"],
                    "damage_per_gold": p["damage_per_gold"],
                    "cc_per_min": p["cc_per_min"],
                    "impact_score": p["impact_score"],
                    "kill_conversion": p.get("kill_conversion", 0),
                    "pilar_combat_efficiency": p["pilar_combat_efficiency"],
                    "pilar_map_pressure": p["pilar_map_pressure"],
                    "pilar_tactical_utility": p["pilar_tactical_utility"],
                    "deaths": p["deaths"],
                    "control_wards": p.get("control_wards", 0),
                    "damage_mitigated": p["damage_mitigated"],
                    **{k: team_syn.get(k, 0) for k in [
                        "synergy_jg_sup", "synergy_jg_mid", "synergy_jg_top", "synergy_jg_adc",
                        "synergy_adc_sup", "synergy_mid_bot", "synergy_mid_top", "synergy_mid_sup",
                        "synergy_top_bot", "synergy_top_sup",
                    ]},
                })

        # 1. Guardar Crudos (Individual Stats)
        if summary_rows:
            df_raw = pd.DataFrame(summary_rows)
            recs_raw = df_raw.where(pd.notnull(df_raw), None).to_dict(orient="records")
            for i in range(0, len(recs_raw), 100):
                supabase.table("benchmarks_stats_raw").upsert(
                    recs_raw[i:i+100],
                    ignore_duplicates=True
                ).execute()
            logger.info(f"Guardadas {len(recs_raw)} filas en benchmarks_stats_raw para {region_name}")

        # 2. Registrar Match IDs procesados
        if processed_matches_info:
            for i in range(0, len(processed_matches_info), 50):
                supabase.table("matches").upsert(processed_matches_info[i:i+50], on_conflict="match_id").execute()

    # 4. RECALCULAR SUMMARY (PROMEDIO HISTÓRICO TOTAL)
    logger.info("📊 Recalculando promedios históricos en benchmarks_summary...")
    try:
        # Traemos todos los crudos de la DB (o al menos los relevantes) para agrupar
        all_raw_res = supabase.table("benchmarks_stats_raw").select("*").execute()
        if all_raw_res.data:
            df_all = pd.DataFrame(all_raw_res.data)
            # Agrupamos solo por Campeón, Rol y Resultado (ignorando el parche para un summary global real)
            grp = df_all.groupby(["champion", "role", "result"]).agg(
                gold_per_min=("gold_per_min", "median"),
                cs_per_min=("cs_per_min", "median"),
                vision_per_min=("vision_per_min", "median"),
                damage_per_min=("damage_per_min", "median"),
                kda=("kda", "median"),
                kill_participation=("kill_participation", "median"),
                damage_per_gold=("damage_per_gold", "median"),
                cc_per_min=("cc_per_min", "median"),
                impact_score=("impact_score", "median"),
                kill_conversion=("kill_conversion", "median"),
                pilar_combat_efficiency=("pilar_combat_efficiency", "median"),
                pilar_map_pressure=("pilar_map_pressure", "median"),
                pilar_tactical_utility=("pilar_tactical_utility", "median"),
                control_wards=("control_wards", "mean"),
                deaths=("deaths", "mean"),
                damage_mitigated=("damage_mitigated", "median"),
                synergy_jg_sup=("synergy_jg_sup", "median"),
                synergy_jg_mid=("synergy_jg_mid", "median"),
                synergy_jg_top=("synergy_jg_top", "median"),
                synergy_jg_adc=("synergy_jg_adc", "median"),
                synergy_adc_sup=("synergy_adc_sup", "median"),
                synergy_mid_bot=("synergy_mid_bot", "median"),
                synergy_mid_top=("synergy_mid_top", "median"),
                synergy_mid_sup=("synergy_mid_sup", "median"),
                synergy_top_bot=("synergy_top_bot", "median"),
                synergy_top_sup=("synergy_top_sup", "median"),
                sample_size=("champion", "count")
            ).reset_index()
            
            # Asignamos un parche genérico 'GLOBAL' o el más reciente, pero para la tabla lo fijaremos
            grp["patch"] = "GLOBAL"

            import math
            recs_summary = []
            for _, row in grp.iterrows():
                r = row.to_dict()
                for k, v in r.items():
                    if isinstance(v, float) and math.isnan(v):
                        r[k] = None
                recs_summary.append(r)
                
            for i in range(0, len(recs_summary), 100):
                supabase.table("benchmarks_summary").upsert(
                    recs_summary[i:i+100], 
                    on_conflict="champion,role,patch,result"
                ).execute()
            logger.info(f"✅ benchmarks_summary actualizado con {len(recs_summary)} perfiles de Campeón/Rol.")
    except Exception as e:
        logger.error(f"Error recalculando summary: {e}")

if __name__ == "__main__":
    sync_benchmarks()
