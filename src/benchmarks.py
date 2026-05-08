"""
benchmarks.py — Gestión de benchmarks de alto rendimiento (Challenger/Grandmaster).
"""

import logging
import random
import time
from pathlib import Path
from typing import Optional, List, Dict

import numpy as np
import pandas as pd
from src import config
from src.data_loader import MatchV5Client, normalize_match, normalize_timeline
from src.features import compute_player_metrics

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Mapeo de Arquetipos (Fallback)
# ──────────────────────────────────────────────────────────────────

ARCHETYPE_MAP = {
    # Top Lane
    "Aatrox": "Juggernaut", "Mordekaiser": "Juggernaut", "Darius": "Juggernaut", "Illaoi": "Juggernaut", "Yorick": "Juggernaut",
    "Fiora": "Duelist", "Jax": "Duelist", "Camille": "Duelist", "Irelia": "Duelist", "Gwen": "Duelist", "Riven": "Duelist",
    "Malphite": "Tank", "Ornn": "Tank", "Sion": "Tank", "K'Sante": "Tank", "Cho'Gath": "Tank", "Shen": "Tank",
    "Jayce": "Artillery", "Kennen": "Teamfight_AP", "Kayle": "Hypercarry",
    
    # Jungle
    "Lee Sin": "Diver", "Jarvan IV": "Diver", "Vi": "Diver", "Diana": "Diver", "Xin Zhao": "Diver",
    "Sejuani": "Tank_JG", "Zac": "Tank_JG", "Maokai": "Tank_JG", "Rammus": "Tank_JG", "Nunu & Willump": "Tank_JG",
    "Kha'Zix": "Assassin", "Rengar": "Assassin", "Evelynn": "Assassin", "Nidalee": "Assassin", "Shaco": "Assassin",
    "Graves": "Carry_JG", "Kindred": "Carry_JG", "Bel'Veth": "Carry_JG",
    
    # Mid Lane
    "Orianna": "Control_Mage", "Azir": "Control_Mage", "Viktor": "Control_Mage", "Syndra": "Control_Mage", "Anivia": "Control_Mage",
    "Zed": "AD_Assassin", "Talon": "AD_Assassin", "Naafiri": "AD_Assassin", "Qiyana": "AD_Assassin",
    "LeBlanc": "AP_Assassin", "Ahri": "AP_Assassin", "Katarina": "AP_Assassin", "Akali": "AP_Assassin", "Fizz": "AP_Assassin",
    "Xerath": "Artillery", "Ziggs": "Artillery", "Lux": "Artillery", "Hwei": "Artillery",
    "Yasuo": "Melee_Carry", "Yone": "Melee_Carry", "Sylas": "Melee_Carry",
    
    # Bot Lane
    "Jinx": "Hypercarry_ADC", "Vayne": "Hypercarry_ADC", "Kog'Maw": "Hypercarry_ADC", "Twitch": "Hypercarry_ADC",
    "Lucian": "Lane_Bully_ADC", "Draven": "Lane_Bully_ADC", "Miss Fortune": "Lane_Bully_ADC", "Caitlyn": "Lane_Bully_ADC", "Kalista": "Lane_Bully_ADC",
    "Ezreal": "Utility_ADC", "Ashe": "Utility_ADC", "Varus": "Utility_ADC", "Jhin": "Utility_ADC", "Sivir": "Utility_ADC",
    "Kai'Sa": "Dive_ADC", "Samira": "Dive_ADC", "Nilah": "Dive_ADC",
    
    # Support
    "Leona": "Engage_Support", "Nautilus": "Engage_Support", "Alistar": "Engage_Support", "Thresh": "Engage_Support", "Blitzcrank": "Engage_Support", "Rell": "Engage_Support",
    "Lulu": "Enchanter", "Janna": "Enchanter", "Milio": "Enchanter", "Soraka": "Enchanter", "Yuumi": "Enchanter", "Nami": "Enchanter", "Sona": "Enchanter",
    "Braum": "Warden", "Taric": "Warden", "Tahm Kench": "Warden",
    "Pyke": "Assassin_Support", "Senna": "Carry_Support", "Zyra": "Mage_Support", "Brand": "Mage_Support",
}

# ──────────────────────────────────────────────────────────────────
# Benchmark Manager
# ──────────────────────────────────────────────────────────────────

class BenchmarkManager:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.output_dir = config.DATA_PROCESSED_DIR / "benchmarks"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Mapeo de región a plataforma y región de routing Match-V5
        self.region_config = {
            "KR":  {"platform": "kr",   "routing": "asia"},
            "EUW": {"platform": "euw1", "routing": "europe"},
            "BR":  {"platform": "br1",  "routing": "americas"},
        }

    def update_benchmarks(self, regions: List[str] = ["KR", "EUW", "BR"], players_per_league: int = 100):
        """
        Descarga y procesa partidas de Challenger/Grandmaster para las regiones indicadas.
        """
        all_benchmarks = []

        for region_name in regions:
            conf = self.region_config.get(region_name)
            if not conf:
                logger.error(f"Región {region_name} no configurada.")
                continue

            logger.info(f"Actualizando benchmarks para {region_name}...")
            
            client = MatchV5Client(self.api_key)
            client.platform = conf["platform"]
            client.region = conf["routing"]
            client.base_url = f"https://{conf['routing']}.api.riotgames.com/lol/match/v5/matches"

            # 1. Obtener Jugadores (Pool)
            players = []
            for tier in ["challenger", "grandmaster"]:
                league_data = client.get_league_by_queue(tier)
                if league_data:
                    entries = league_data.get("entries", [])
                    players.extend([(tier, e["summonerId"]) for e in entries])

            # Muestreo aleatorio
            sampled_players = random.sample(players, min(len(players), players_per_league))
            
            # 2. Recolectar Match IDs (Deduplicación Global)
            unique_match_ids = set()
            for tier, summoner_id in sampled_players:
                # Nota: necesitamos el puuid, pero league-v4 da summonerId. 
                # En un sistema real usaríamos el puuid cacheado o lo buscaríamos.
                
                # Fetch PUUID from summonerId (v4)
                summoner_url = f"https://{conf['platform']}.api.riotgames.com/lol/summoner/v4/summoners/{summoner_id}"
                resp = client.session.get(summoner_url)
                if resp.status_code == 200:
                    puuid = resp.json().get("puuid")
                    if puuid:
                        mids = client.get_match_ids_by_puuid(puuid, count=5, queue=420)
                        unique_match_ids.update(mids)
                
                if len(unique_match_ids) >= 500: # Cap para no quemar la API en una sola corrida
                    break

            logger.info(f"Procesando {len(unique_match_ids)} partidas únicas para {region_name}")

            # 3. Procesar Partidas
            match_stats = []
            for mid in list(unique_match_ids)[:200]: # Limitamos para el demo
                stats = self._process_single_match(client, mid)
                if stats:
                    for s in stats:
                        s["region"] = region_name
                    match_stats.extend(stats)

            if match_stats:
                df = pd.DataFrame(match_stats)
                # Guardar en Parquet particionado
                patch = df["patch"].iloc[0] if "patch" in df.columns else "unknown"
                filename = self.output_dir / f"benchmark_{region_name}_{patch}.parquet"
                df.to_parquet(filename, index=False)
                logger.info(f"Benchmark guardado en {filename}")
                all_benchmarks.append(df)

        return pd.concat(all_benchmarks) if all_benchmarks else pd.DataFrame()

    def _process_single_match(self, client, match_id) -> List[Dict]:
        match_data = client.get_match(match_id)
        if not match_data: return []
        
        info = match_data.get("info", {})
        
        # --- FILTRO: Ventana Competitiva (20-40 min) ---
        duration_sec = info.get("gameDuration", 0)
        if duration_sec < 1200 or duration_sec > 2400:
            logger.warning(f"Partida {match_id} descartada: fuera de ventana competitiva ({duration_sec//60} min)")
            return []

        game_version = info.get("gameVersion", "0.0.0")
        patch = ".".join(game_version.split(".")[:2])
        
        df_p = normalize_match(match_data)
        if df_p.empty: return []

        # 1. Feature Engineering Base
        df_p = compute_player_metrics(df_p)

        # Timeline para Synergy, Objetivos y Kill Conversion
        timeline_data = client.get_timeline(match_id)
        if not timeline_data: return df_p.to_dict("records")

        p_to_team = df_p.set_index("participant_id")["team_id"].to_dict()
        df_t, df_e = normalize_timeline(timeline_data, match_id, p_to_team)

        # 2. NUEVO: Métricas Tácticas Early Game (0-15 min)
        # Importamos la función que creamos en features.py
        from src.features import compute_early_tactical_metrics
        df_p = compute_early_tactical_metrics(df_p, df_e)

        # 2. Professional Synergy (SKP Matrix) - SE CALCULA ANTES
        from src.features import compute_synergy_matrix, compute_objective_control, compute_impact_score
        synergy_matrix = compute_synergy_matrix(df_e, df_p)
        
        role_syn_map = {
            "TOP":     ["synergy_jg_top", "synergy_mid_top", "synergy_top_bot", "synergy_top_sup"],
            "JUNGLE":  ["synergy_jg_sup", "synergy_jg_mid", "synergy_jg_top", "synergy_jg_adc"],
            "MID":     ["synergy_jg_mid", "synergy_mid_bot", "synergy_mid_top", "synergy_mid_sup"],
            "BOT":     ["synergy_adc_sup", "synergy_mid_bot", "synergy_top_bot", "synergy_jg_adc"],
            "SUPPORT": ["synergy_jg_sup", "synergy_adc_sup", "synergy_top_sup", "synergy_mid_sup"]
        }
        
        all_syn_cols = [
            "synergy_jg_sup", "synergy_jg_mid", "synergy_jg_top", "synergy_jg_adc",
            "synergy_adc_sup", "synergy_mid_bot", "synergy_mid_top", "synergy_mid_sup",
            "synergy_top_bot", "synergy_top_sup"
        ]

        # Inyectar datos de sinergia en el DataFrame (Vectorizado)
        def get_syn_data(row):
            tid = row["team_id"]
            role = row["role"]
            team_syn = synergy_matrix.get(tid, {})
            relevant_cols = role_syn_map.get(role, [])
            
            res = {}
            for col in all_syn_cols:
                res[col] = float(team_syn.get(col, 0.0)) if col in relevant_cols else None
                
            vals = [res[c] for c in relevant_cols if res[c] is not None]
            res["synergy_score"] = sum(vals) / len(vals) if vals else 0.0
            return pd.Series(res)

        # Unir las nuevas columnas de sinergia al DF principal
        syn_df = df_p.apply(get_syn_data, axis=1)
        df_p = pd.concat([df_p, syn_df], axis=1)

        # 3. Kill Conversion y Eficiencia
        conversion_scores = self._calculate_kill_conversion(df_e, match_id)
        df_p["kill_conversion"] = df_p["team_id"].map(conversion_scores).fillna(0)
        df_p["damage_efficiency"] = df_p["total_damage"] / (df_p["gold_earned"].clip(lower=1) / 1000)

        # 4. Impact Score (Motor Unificado) - AHORA TIENE LA DATA DE SINERGIA DISPONIBLE
        df_o = compute_objective_control(df_e)
        df_p = compute_impact_score(df_p, df_o)

        # 5. Formateo Final para exportar
        rows = []
        for _, p in df_p.iterrows():
            row = p.to_dict()
            row["patch"] = patch
            # Reemplazar NaN por None para evitar errores JSON/Supabase
            row = {k: (None if pd.isna(v) else v) for k, v in row.items()}
            rows.append(row)

        return rows

    # Synergy score removido por inestabilidad de datos en Timeline

    def _calculate_kill_conversion(self, df_e: pd.DataFrame, match_id: str) -> Dict[int, float]:
        """
        Calcula el % de objetivos tomados en la ventana de 60s tras una kill.
        """
        if df_e.empty: return {100: 0, 200: 0}
        
        kills = df_e[df_e["event_type"] == "CHAMPION_KILL"]
        objectives = df_e[df_e["event_type"].isin(["ELITE_MONSTER_KILL", "BUILDING_KILL"])]
        
        conversions = {100: 0, 200: 0}
        total_kills = {100: kills[kills["team_id"] == 100].shape[0], 
                       200: kills[kills["team_id"] == 200].shape[0]}

        for _, kill in kills.iterrows():
            tk = kill["team_id"]
            t_min = kill["timestamp_min"]
            # Buscar objetivos del mismo equipo en [t, t+1]
            win_objs = objectives[(objectives["team_id"] == tk) & 
                                  (objectives["timestamp_min"] >= t_min) & 
                                  (objectives["timestamp_min"] <= t_min + 1.0)]
            if not win_objs.empty:
                conversions[tk] += 1
                
        return {tk: (conversions[tk] / max(total_kills[tk], 1)) for tk in [100, 200]}

    def get_percentiles(self, df: pd.DataFrame, champion: str, role: str) -> Dict[str, float]:
        """
        Calcula los percentiles 10, 50, 90 para un campeón y rol.
        Si N < 30, aplica Fallback por Arquetipo.
        """
        subset = df[(df["champion"] == champion) & (df["role"] == role)]
        
        if len(subset) < 30:
            archetype = ARCHETYPE_MAP.get(champion, "Standard")
            logger.info(f"Fallback: {champion} ({role}) -> Arquetipo {archetype} (N={len(subset)})")
            # Filtrar por arquetipo en el mismo rol
            archetype_champs = [c for c, a in ARCHETYPE_MAP.items() if a == archetype]
            subset = df[(df["champion"].isin(archetype_champs)) & (df["role"] == role)]
            
        metrics = [
            # Métricas Core y de Eficiencia
            "gold_per_min", "cs_per_min", "vision_per_min", "damage_per_min", 
            "kda", "cc_per_min", "damage_per_gold", "damage_buildings", "resilience_index",
            
            # Impacto y Macro
            "impact_score", "kill_conversion", "damage_efficiency",
            "kill_participation", "objective_control",
            
            # Los 4 Pilares del Impacto
            "pilar_combat_efficiency", "pilar_map_pressure", 
            "pilar_tactical_utility", "pilar_team_synergy",
            
            # Sinergias de Equipo
            "synergy_score", "synergy_jg_sup", "synergy_jg_mid", "synergy_jg_top", 
            "synergy_jg_adc", "synergy_adc_sup", "synergy_mid_bot", 
            "synergy_mid_top", "synergy_mid_sup", "synergy_top_bot", "synergy_top_sup"
        ]
        
        results = {}
        for m in metrics:
            if m in subset.columns:
                results[m] = {
                    "p10": subset[m].quantile(0.1),
                    "p50": subset[m].quantile(0.5),
                    "p90": subset[m].quantile(0.9),
                    "n": len(subset)
                }
        return results
