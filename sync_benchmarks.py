"""
sync_benchmarks.py — Script ETL para sincronizar benchmarks con Supabase.
Arquitectura 'Medalist': Deduplicación + Persistencia + Pre-cálculo.
"""

import os
import logging
import random
import time
import argparse
from typing import List, Dict, Optional

import pandas as pd
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

from src import config
from src.data_loader import MatchV5Client, normalize_match, normalize_timeline
from src.features import compute_player_metrics
from src.benchmarks import BenchmarkManager, ARCHETYPE_MAP

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ETL-Medalist")

load_dotenv()

# ──────────────────────────────────────────────────────────────────
# Patch Tracker (Data Dragon)
# ──────────────────────────────────────────────────────────────────

def get_current_patch() -> str:
    """Obtiene la versión actual de LoL desde Data Dragon."""
    try:
        url = "https://ddragon.leagueoflegends.com/api/versions.json"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            version = resp.json()[0]
            # Retornar formato "XX.XX"
            return ".".join(version.split(".")[:2])
    except Exception as e:
        logger.error(f"Error detectando parche: {e}")
    return "unknown"

# ──────────────────────────────────────────────────────────────────
# ETL Medalist
# ──────────────────────────────────────────────────────────────────

class MedalistETL:
    def __init__(self):
        url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
        
        if not url or not key:
            raise ValueError(f"Faltan credenciales de Supabase en el .env (URL: {bool(url)}, KEY: {bool(key)})")
        
        self.supabase: Client = create_client(url, key)
        self.riot_key = os.environ.get("RIOT_API_KEY")
        self.benchmark_manager = BenchmarkManager(self.riot_key)
        
        # Validar conexión y tablas al iniciar
        self._check_supabase_connection()

    def _check_supabase_connection(self):
        """Verifica que las tablas necesarias existan en Supabase."""
        required_tables = ["matches", "challenger_stats", "benchmarks_summary"]
        logger.info("Validando conexión y esquema de Supabase...")
        
        for table in required_tables:
            try:
                # Intento de consulta mínima para verificar existencia
                self.supabase.table(table).select("count", count="exact").limit(1).execute()
                logger.info(f"Tabla '{table}' validada OK.")
            except Exception as e:
                logger.error(f"Fallo de validación en tabla '{table}': {e}")
                logger.warning(f"Asegúrate de haber ejecutado el SQL en el Dashboard de Supabase.")
                raise RuntimeError(f"La base de datos no está lista. Falta la tabla '{table}'.")

    def run(self, region: str, players_per_tier: int = 2):
        """Ejecuta el pipeline completo para una región."""
        patch = get_current_patch()
        logger.info(f"Iniciando ETL para {region} en Parche {patch}...")
        
        conf = self.benchmark_manager.region_config.get(region)
        if not conf:
            logger.error(f"Región {region} no válida.")
            return

        client = MatchV5Client(self.riot_key)
        client.platform = conf["platform"]
        client.region = conf["routing"]
        client.base_url = f"https://{conf['routing']}.api.riotgames.com/lol/match/v5/matches"

        # 1. Scraping de Match IDs
        players = []
        for tier in ["challenger", "grandmaster"]:
            logger.info(f"Pidiendo liga {tier}...")
            league_data = client.get_league_by_queue(tier)
            if league_data:
                entries = league_data.get("entries", [])
                logger.info(f"Liga {tier} obtenida. Entradas encontradas: {len(entries)}")
                
                if not entries:
                    logger.warning(f"No se encontraron entradas para {tier} en {region}")
                    continue
                
                # Validación de estructura: ¡Riot ahora devuelve PUUID directamente en 2026!
                valid_entries = []
                for e in entries:
                    p_id = e.get("puuid") or e.get("summonerId") or e.get("summonerName")
                    if p_id:
                        valid_entries.append((tier, p_id, "puuid" in e))
                
                logger.info(f"Entradas válidas para {tier}: {len(valid_entries)}")
                players.extend(valid_entries)
            else:
                logger.error(f"Fallo crítico: No se pudo obtener la liga {tier} (league_data es None)")

        if not players:
            logger.error(f"No se pudieron obtener jugadores para la región {region}. Abortando.")
            return

        sampled_players = random.sample(players, min(len(players), players_per_tier))
        
        # 2. Recolectar Match IDs (Deduplicación Global + Caché Local)
        all_match_ids = []
        cache_file = config.DATA_RAW_DIR / f"scraped_ids_{region}_{patch}.json"
        
        # Si NO pedimos fresh y el archivo existe, cargamos IDs viejos
        if not getattr(self, 'fresh_ids', False) and cache_file.exists():
            import json
            with open(cache_file, "r") as f:
                all_match_ids = json.load(f)
            logger.info(f"Cargados {len(all_match_ids)} Match IDs desde caché local ({cache_file.name})")
        
        if not all_match_ids:
            if getattr(self, 'fresh_ids', False) and cache_file.exists():
                logger.info("MODO FRESH: Ignorando caché de IDs y buscando partidas nuevas...")
            
            logger.info(f"Obteniendo Match IDs para {len(sampled_players)} jugadores...")
            try:
                for tier, identifier, is_puuid in sampled_players:
                    puuid = identifier
                    if not is_puuid:
                        summoner_url = f"https://{conf['platform']}.api.riotgames.com/lol/summoner/v4/summoners/{identifier}"
                        resp = client.session.get(summoner_url)
                        if resp.status_code == 200:
                            puuid = resp.json().get("puuid")
                            time.sleep(1.2)
                        else: continue

                    if puuid:
                        mids = client.get_match_ids_by_puuid(puuid, count=10, queue=420)
                        all_match_ids.extend(mids)
                        logger.info(f"[{tier}] OK: {puuid[:8]}... | Total: {len(all_match_ids)}")
                        time.sleep(0.5)
                    
                    if len(set(all_match_ids)) >= 500: break
                
                # Guardar en caché local para evitar re-scraping
                with open(cache_file, "w") as f:
                    import json
                    json.dump(list(set(all_match_ids)), f)
            except KeyboardInterrupt:
                logger.warning("Interrupción detectada. Guardando progreso parcial en caché...")
                with open(cache_file, "w") as f:
                    import json
                    json.dump(list(set(all_match_ids)), f)
                raise

        unique_scraped = list(set(all_match_ids))
        logger.info(f"DEBUG: Jugadores muestreados: {len(sampled_players)}")
        logger.info(f"DEBUG: IDs de partidas únicas encontradas: {len(unique_scraped)}")

        if not unique_scraped:
            logger.error("No se encontraron partidas para procesar. Abortando.")
            return

        # 2. Deduplicación contra Supabase
        if not getattr(self, 'force_reprocess', False):
            existing_matches = self.supabase.table("matches").select("match_id").in_("match_id", unique_scraped).execute()
            existing_ids = {row["match_id"] for row in existing_matches.data}
            to_process = [mid for mid in unique_scraped if mid not in existing_ids]
            logger.info(f"Deduplicación completada: {len(to_process)} partidas nuevas a procesar.")
        else:
            to_process = unique_scraped
            logger.info(f"MODO FORCE: Re-procesando {len(to_process)} partidas (pueden existir en DB).")

        # 3. Procesamiento y Carga Paralela
        import concurrent.futures
        
        def process_match(mid):
            # Clientes locales para evitar colisiones de estado en hilos
            from supabase import create_client
            url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
            key = os.environ.get("SUPABASE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
            local_supabase = create_client(url, key)
            
            local_client = MatchV5Client(self.riot_key)
            local_client.platform = conf["platform"]
            local_client.region = conf["routing"]
            local_client.base_url = f"https://{conf['routing']}.api.riotgames.com/lol/match/v5/matches"
            
            try:
                stats = self.benchmark_manager._process_single_match(local_client, mid)
                if not stats: return False
                
                # Metadata de la partida para 'matches'
                match_meta = {
                    "match_id": mid,
                    "region": region,
                    "platform": conf["platform"],
                    "patch_version": patch,
                    "duration_min": stats[0].get("duration_minutes", 0),
                    "is_processed": True
                }
                # En modo FORCE, limpiamos stats previos de esta partida para evitar duplicados
                if getattr(self, 'force_reprocess', False):
                    local_supabase.table("challenger_stats").delete().eq("match_id", mid).execute()

                local_supabase.table("matches").upsert(match_meta).execute()
                
                # Stats individuales
                challenger_rows = []
                for s in stats:  # ⚠️ ¡ESTA LÍNEA ES LA QUE FALTABA O ESTABA MAL INDENTADA!
                    row = {
                        "match_id": mid,
                        "champion": s["champion"],
                        "role": s["role"],
                        "tier": "CHALLENGER",
                        "region": region,
                        
                        # --- MATERIA PRIMA ESTRICTA ---
                        "kills": int(s.get("kills", 0)),
                        "deaths": int(s.get("deaths", 0)),
                        "assists": int(s.get("assists", 0)),
                        "cs": int(s.get("cs", 0)),
                        "gold_earned": int(s.get("gold_earned", 0)),
                        "total_damage": int(s.get("total_damage", 0)),
                        "damage_taken": int(s.get("damage_taken", 0)),
                        "damage_mitigated": int(s.get("damage_mitigated", 0)),
                        "damage_buildings": int(s.get("damage_buildings", s.get("damageDealtToBuildings", 0))),
                        "vision_score": int(s.get("vision_score", 0)),
                        "first_blood": bool(s.get("first_blood", False)),
                        "duration_minutes": float(s.get("duration_minutes", 0)),
                        "result": bool(s.get("result", False)),
                        
                        # --- NUEVAS MÉTRICAS TÁCTICAS ---
                        "early_solo_deaths": int(s.get("early_solo_deaths", 0)),
                        "early_gank_deaths": int(s.get("early_gank_deaths", 0)),
                        "early_gank_kills":  int(s.get("early_gank_kills", 0)),
                        
                        # --- MÉTRICAS CALCULADAS AVANZADAS ---
                        "gold_per_min":      float(s.get("gold_per_min", 0)),
                        "cs_per_min":        float(s.get("cs_per_min", 0)),
                        "vision_per_min":    float(s.get("vision_per_min", 0)),
                        "damage_per_min":    float(s.get("damage_per_min", 0)),
                        "impact_score":      float(s.get("impact_score", 0)),
                        "kill_conversion":   float(s.get("kill_conversion", 0)),
                        "damage_efficiency": float(s.get("damage_efficiency", s.get("damage_per_gold", s.get("total_damage", 0) / max(1, s.get("gold_earned", 1))))),
                        "synergy_score":     float(s.get("synergy_score", 0)),
                        "kda":               float(s.get("kda", 0)),
                        "damage_per_gold":   float(s.get("damage_per_gold", 0)),
                        "cc_per_min":        float(s.get("cc_per_min", s.get("timeCCingOthers", s.get("totalTimeCCDealt", 0)) / max(0.01, float(s.get("duration_minutes", 1))))),
                        "resilience_index":  float(s.get("resilience_index", 0)),
                        "kill_participation":float(s.get("kill_participation", 0)),
                        "objective_control": float(s.get("objective_control", 0)),
                        
                        # --- PILARES DE IMPACTO ---
                        "pilar_combat_efficiency": float(s.get("pilar_combat_efficiency", 0)),
                        "pilar_map_pressure":      float(s.get("pilar_map_pressure", 0)),
                        "pilar_tactical_utility":  float(s.get("pilar_tactical_utility", 0)),
                        "pilar_team_synergy":      float(s.get("pilar_team_synergy", 0)),
                        
                        # --- MATRIZ DE SINERGIA ---
                        "synergy_jg_sup": s.get("synergy_jg_sup"),
                        "synergy_jg_mid": s.get("synergy_jg_mid"),
                        "synergy_jg_top": s.get("synergy_jg_top"),
                        "synergy_jg_adc": s.get("synergy_jg_adc"),
                        "synergy_adc_sup": s.get("synergy_adc_sup"),
                        "synergy_mid_bot": s.get("synergy_mid_bot"),
                        "synergy_mid_top": s.get("synergy_mid_top"),
                        "synergy_mid_sup": s.get("synergy_mid_sup"),
                        "synergy_top_bot": s.get("synergy_top_bot"),
                        "synergy_top_sup": s.get("synergy_top_sup")
                    }
                    challenger_rows.append(row)
                
                if challenger_rows:
                    local_supabase.table("challenger_stats").insert(challenger_rows).execute()
                
                logger.info(f"✅ Sincronizada partida {mid}")
                return True
            except RuntimeError as e:
                if "RIOT_API_401" in str(e):
                    raise  # Propagar para abortar el executor
                logger.error(f"❌ Error procesando partida {mid}: {e}")
                return False
            except Exception as e:
                logger.error(f"❌ Error procesando partida {mid}: {e}")
                return False

        logger.info(f"Iniciando sincronizacion paralela (11 workers)...")
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=11) as executor:
                futures = {executor.submit(process_match, mid): mid for mid in to_process[:150]}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except RuntimeError as e:
                        if "RIOT_API_401" in str(e):
                            logger.error("API key invalida o expirada. Cancelando. Regenera en https://developer.riotgames.com")
                            for f in futures:
                                f.cancel()
                            return  # Abortar run() completo
                        raise
        except RuntimeError:
            return

        # 4. Agregación (Pre-cálculo de Benchmarks)
        self.update_benchmarks_summary(region, patch)

    def update_benchmarks_summary(self, region: str, patch: str):
        """Calcula medianas y las guarda en benchmarks_summary (formato ANCHO).
        Una fila por (champion, role) con cada métrica como columna.
        """
        logger.info(f"Actualizando tabla de resumen para {region} - {patch}...")

        # --- 1. CARGA PAGINADA (Bypass 1000 rows limit) ---
        all_data = []
        offset = 0
        limit = 1000
        
        while True:
            resp = self.supabase.table("challenger_stats")\
                .select("*")\
                .eq("region", region)\
                .range(offset, offset + limit - 1)\
                .execute()
            
            if not resp.data:
                break
            
            all_data.extend(resp.data)
            if len(resp.data) < limit:
                break
            offset += limit
            logger.info(f"  Cargadas {len(all_data)} filas de challenger_stats...")

        if not all_data:
            logger.warning("No hay datos para resumir.")
            return

        df = pd.DataFrame(all_data)

        base_metrics = [
            "gold_per_min", "cs_per_min", "vision_per_min", "damage_per_min",
            "impact_score", "kill_conversion", "damage_efficiency", "synergy_score",
            "kda", "damage_per_gold", "damage_buildings", "cc_per_min", "resilience_index",
            "kill_participation", "objective_control",
            "pilar_combat_efficiency", "pilar_map_pressure", "pilar_tactical_utility", "pilar_team_synergy",
            "early_solo_deaths", "early_gank_deaths", "early_gank_kills"
        ]

        role_synergies = {
            "TOP":     ["synergy_jg_top", "synergy_mid_top", "synergy_top_bot", "synergy_top_sup"],
            "JUNGLE":  ["synergy_jg_sup", "synergy_jg_mid", "synergy_jg_top", "synergy_jg_adc"],
            "MID":     ["synergy_jg_mid", "synergy_mid_bot", "synergy_mid_top", "synergy_mid_sup"],
            "BOT":     ["synergy_adc_sup", "synergy_mid_bot", "synergy_top_bot", "synergy_jg_adc"],
            "SUPPORT": ["synergy_jg_sup", "synergy_adc_sup", "synergy_top_sup", "synergy_mid_sup"],
        }

        summary_rows = []

        for (champ, role), group in df.groupby(["champion", "role"]):
            role_metrics = base_metrics + role_synergies.get(role, [])

            # Formato ANCHO: 1 fila por (champion, role), cada métrica = columna con la mediana (p50)
            row: dict = {
                "champion":    champ,
                "role":        role,
                "patch":       patch,
                "sample_size": len(group),
            }

            for m in role_metrics:
                if m not in group.columns:
                    continue
                clean = group[m].dropna()
                if clean.empty:
                    continue
                row[m] = float(clean.quantile(0.5))  # p50 = mediana Challenger

            summary_rows.append(row)

        if summary_rows:
            logger.info(f"Escribiendo {len(summary_rows)} filas en benchmarks_summary (formato ancho)...")
            # Upsert usando el constraint real de la tabla: UNIQUE(champion, role, patch)
            for i in range(0, len(summary_rows), 100):
                self.supabase.table("benchmarks_summary").upsert(
                    summary_rows[i:i+100],
                    on_conflict="champion,role,patch"
                ).execute()
            logger.info(f"✨ Resumen actualizado: {len(summary_rows)} campeónes × roles.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Medalist ETL — Sync benchmarks with Supabase")
    parser.add_argument("--region", type=str, default="KR", help="Región (KR, EUW, BR)")
    parser.add_argument("--players", type=int, default=20, help="Jugadores por liga a scrapear")
    parser.add_argument("--force", action="store_true", help="Re-procesar partidas existentes para actualizar métricas")
    parser.add_argument("--fresh", action="store_true", help="Ignorar caché de IDs y buscar partidas nuevas")
    args = parser.parse_args()

    etl = MedalistETL()
    etl.force_reprocess = args.force
    etl.fresh_ids = args.fresh
    etl.run(region=args.region, players_per_tier=args.players)
