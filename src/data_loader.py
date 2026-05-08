"""
data_loader.py — Cliente de API Riot y normalización de JSON a DataFrames.
"""

from __future__ import annotations
import logging
import time
from pathlib import Path
from typing import Optional
import concurrent.futures

import pandas as pd
import requests
import os
from supabase import create_client, Client

import json
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from src import config
from src.config import OBJECTIVE_MONSTER_TYPES, BUILDING_KILL_SUBTYPES

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Cliente Riot Match-V5
# ──────────────────────────────────────────────────────────────────

class MatchV5Client:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.platform = config.RIOT_PLATFORM
        self.region = config.RIOT_REGION
        self.base_url = f"https://{self.region}.api.riotgames.com/lol/match/v5/matches"
        self.session = requests.Session()
        self.session.headers.update({"X-Riot-Token": self.api_key})
        self.raw_dir = config.DATA_RAW_DIR
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        import threading
        self._lock = threading.Lock()
        self._last_request_time = 0
        
        # Supabase client (opcional para el loader base, usado en benchmarks)
        self.supabase_url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        self.supabase_key = os.environ.get("SUPABASE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
        self._supabase_client: Optional[Client] = None

        # MongoDB client (Data Lake)
        self.mongodb_uri = os.environ.get("MONGODB_URI")
        self.mongodb_db_name = os.environ.get("MONGODB_DB_NAME", "lol_analytics")
        self._mongodb_client: Optional[MongoClient] = None
        self._mongodb_available = False

    @property
    def supabase(self) -> Optional[Client]:
        if self._supabase_client is None and self.supabase_url and self.supabase_key:
            self._supabase_client = create_client(self.supabase_url, self.supabase_key)
        return self._supabase_client

    @property
    def mongodb(self) -> Optional[MongoClient]:
        """MongoDB client con manejo de errores graceful."""
        if not self.mongodb_uri:
            return None
            
        if self._mongodb_client is None and not self._mongodb_available:
            try:
                self._mongodb_client = MongoClient(self.mongodb_uri, serverSelectionTimeoutMS=5000)
                # Test connection
                self._mongodb_client.admin.command('ping')
                self._mongodb_available = True
                logger.info("MongoDB connection established")
            except PyMongoError as e:
                logger.warning(f"MongoDB connection failed: {e}")
                self._mongodb_client = None
                self._mongodb_available = False
        return self._mongodb_client

    def _request(self, endpoint: str) -> Optional[dict]:
        url = endpoint if endpoint.startswith("http") else f"{self.base_url}/{endpoint}"
        retries = config.MAX_RETRIES
        
        for attempt in range(retries):
            # Throttling global para no superar 100 req / 2 min (aprox 1.2s entre reqs)
            with self._lock:
                elapsed = time.time() - self._last_request_time
                wait_needed = 1.3 - elapsed
                if wait_needed > 0:
                    time.sleep(wait_needed)
                self._last_request_time = time.time()

            try:
                resp = self.session.get(url, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 1))
                    # Si es un rate limit de la API, esperamos lo que diga Riot + buffer
                    wait_time = retry_after + 1.0
                    logger.warning(f"Rate limit alcanzado. Esperando Retry-After: {wait_time}s (Intento {attempt+1}/{retries})")
                    time.sleep(wait_time)
                elif resp.status_code == 503:
                    wait_time = config.BACKOFF_BASE_SECONDS ** (attempt + 1)
                    logger.warning(f"Servidor ocupado (503). Reintentando en {wait_time}s...")
                    time.sleep(wait_time)
                elif resp.status_code == 404:
                    logger.warning(f"Recurso no encontrado (404): {url}")
                    return None
                elif resp.status_code == 401:
                    # API key inválida o expirada — reintentar no sirve de nada
                    logger.error(
                        f"❌ Riot API key inválida o expirada (401). "
                        f"Regenerá tu key en https://developer.riotgames.com y actualizá .env"
                    )
                    raise RuntimeError("RIOT_API_401")  # Abort global
                else:
                    logger.error(f"Error {resp.status_code} de Riot API en {url}")
                    if attempt < retries - 1:
                        time.sleep(2)
                        continue
                    return None
            except Exception as e:
                logger.error(f"Excepción en API Riot: {e}")
                time.sleep(2)
        return None

    def get_match(self, match_id: str) -> Optional[dict]:
        cache_path = self.raw_dir / f"{match_id}_match.json"
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data: return data
            except json.JSONDecodeError:
                pass # Cache corrupto, ignorar y borrar

        mongo_data = self._fetch_from_mongodb("raw_matches", match_id)
        if mongo_data:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(mongo_data, f)
            return mongo_data

        data = self._request(match_id)
        if data:
            self._dump_to_mongodb("raw_matches", match_id, data)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        elif cache_path.exists():
            cache_path.unlink(missing_ok=True) # Elimina caché inútil
        return data

    def get_timeline(self, match_id: str) -> Optional[dict]:
        cache_path = self.raw_dir / f"{match_id}_timeline.json"
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data: return data
            except json.JSONDecodeError:
                pass

        mongo_data = self._fetch_from_mongodb("raw_timelines", match_id)
        if mongo_data:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(mongo_data, f)
            return mongo_data

        data = self._request(f"{match_id}/timeline")
        if data:
            self._dump_to_mongodb("raw_timelines", match_id, data)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        elif cache_path.exists():
            cache_path.unlink(missing_ok=True)
        return data

    def load_single_match_by_id(self, match_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        clean_id = match_id.strip()
        if clean_id.isdigit():
            clean_id = f"{self.platform.upper()}_{clean_id}"

        logger.info(f"Cargando partida individual: {clean_id}")

        match_data = self.get_match(clean_id)
        if not match_data:
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        df_participants = normalize_match(match_data)
        
        # Crear mapeo de participant_id -> team_id para usarlo en el timeline
        p_to_team = df_participants.set_index("participant_id")["team_id"].to_dict()

        df_timeline = pd.DataFrame()
        df_events = pd.DataFrame()
        timeline_data = self.get_timeline(clean_id)
        if timeline_data:
            df_timeline, df_events = normalize_timeline(timeline_data, clean_id, p_to_team)
            logger.info(f"Timeline cargado: {len(df_timeline)} frames")
        else:
            logger.warning(f"Timeline no disponible para {clean_id}")

        return df_participants, df_timeline, df_events

    def get_puuid(self, game_name: str, tag_line: str) -> Optional[str]:
        cache_path = self.raw_dir / f"puuid_{game_name}_{tag_line}.json".replace(" ", "_")
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f).get("puuid")
            except: pass

        from urllib.parse import quote
        encoded_name = quote(game_name)
        encoded_tag = quote(tag_line)
        url = f"https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{encoded_name}/{encoded_tag}"
        
        logger.info(f"Fetching PUUID for {game_name}#{tag_line}...")
        data = self._request(url)
        if data and data.get("puuid"):
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return data.get("puuid")
        return None

    def get_league_by_queue(self, tier: str, queue: str = "RANKED_SOLO_5x5") -> Optional[dict]:
        """
        Obtiene la liga Challenger o Grandmaster para una plataforma específica.
        Tier: 'challenger' o 'grandmaster'
        """
        # Nota: estos endpoints usan la plataforma (la2, br1, kr, etc.), no la región (americas)
        platform = self.platform.lower()
        url = f"https://{platform}.api.riotgames.com/lol/league/v4/{tier}leagues/by-queue/{queue}"
        
        cache_path = self.raw_dir / f"league_{platform}_{tier}_{queue}.json"
        if cache_path.exists():
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)

        # Usar _request() para respetar el rate-limiter compartido (lock + backoff)
        data = self._request(url)
        if data:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return data
        logger.error(f"Error al obtener liga {tier} en {platform}")
        return None

    def get_match_ids_by_puuid(self, puuid: str, count: int = 10, queue: Optional[int] = None) -> list[str]:
        endpoint = f"by-puuid/{puuid}/ids?start=0&count={count}"
        if queue and queue > 0:
            endpoint += f"&queue={queue}"
            
        resp = self._request(endpoint)
        if isinstance(resp, list):
            return resp
        return []

    def load_team_matches(self, count_per_player: int = 10, queues: list[int] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        team_matches_sets = []
        # Si no hay colas, usamos [None] para traer todo
        queue_list = queues if (queues and len(queues) > 0) else [None]
        
        logger.info(f"Averiguando partidas de los titulares. count={count_per_player}, queues={queue_list}")
        for player in config.TEAM_PLAYERS:
            if player.get("role") not in {"JUNGLE", "MID", "BOT", "SUPPORT"}:
                continue
            player_matches = set()
            accounts = player.get("accounts", [player["riot_id"]])
            for acc in accounts:
                parts = acc.split("#")
                if len(parts) == 2:
                    puuid = self.get_puuid(parts[0], parts[1])
                    if puuid:
                        for q in queue_list:
                            # Si q es 0 (Custom), en la API de Riot se pide sin el param queue
                            q_param = q if q != 0 else None
                            # Aumentamos la profundidad de escaneo para que las partidas de Solo Queue no oculten los partidos de equipo completo
                            scan_depth = max(100, count_per_player * 5)
                            ids = self.get_match_ids_by_puuid(puuid, count=scan_depth, queue=q_param)
                            player_matches.update(ids)
            if player_matches:
                team_matches_sets.append(player_matches)
        
        # Intersección: Solo con los 4 jugadores del núcleo
        if len(team_matches_sets) == 4:
            match_ids = set.intersection(*team_matches_sets)
            # Ordenar por ID descendente (más recientes primero) y conservar las solicitadas
            match_ids = sorted(list(match_ids), reverse=True)[:count_per_player]
        else:
            logger.warning(f"No se pueden buscar partidas en equipo: Solo se encontraron {len(team_matches_sets)} de 4 jugadores del núcleo.")
            match_ids = []
            
        all_p, all_t, all_e = [], [], []
        logger.info(f"Identificadas {len(match_ids)} partidas COMPLETAS jugadas en equipo.")
        
        # Multithreading conservador para no saturar el rate limit de 100 req / 2 min
        # Con 3 workers y el throttling de 1.3s, el sistema es estable y cancelable.
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_mid = {executor.submit(self.load_single_match_by_id, mid): mid for mid in match_ids}
            try:
                for idx, future in enumerate(concurrent.futures.as_completed(future_to_mid)):
                    mid = future_to_mid[future]
                    try:
                        p, t, e = future.result(timeout=30) # Timeout por partida para evitar cuelgues
                        if not p.empty:
                            # Ignorar partidas casuales/customs donde todos nuestros jugadores tengan rol UNKNOWN
                            team_p = p[p["game_name"].str.lower().map(lambda x: x.split("#")[0] if isinstance(x, str) else "").isin({name.split("#")[0] for name in config.TEAM_GAME_NAMES})]
                            if not team_p.empty and (team_p.get("teamPosition", pd.Series(dtype=str)).isin(["", None])).all():
                                logger.warning(f"Ignorando partida casual/custom {mid} por roles UNKNOWN")
                                continue
                            all_p.append(p)
                        if not t.empty: all_t.append(t)
                        if not e.empty: all_e.append(e)
                        logger.info(f"[{idx+1}/{len(match_ids)}] Descargada partida {mid}")
                    except concurrent.futures.TimeoutError:
                        logger.error(f"Timeout descargando partida {mid}")
                    except Exception as exc:
                        logger.error(f"Error cargando match {mid}: {exc}")
            except KeyboardInterrupt:
                logger.warning("Carga interrumpida por el usuario. Cancelando tareas pendientes...")
                executor.shutdown(wait=False, cancel_futures=True)
                raise
                    
        # Eliminar las caches de partidas basura que no eran 5v5 para ahorrar RAM
        # Si un archivo local en data/raw es match.json o timeline.json pero su ID no está
        # en match_ids que iteramos, está ocupando espacio estático. (Opt-in)
        import os
        valid_prefixes = [f"{mid}_" for mid in match_ids] + ["puuid_", "match_ids_"]
        for p in self.raw_dir.glob("*.json"):
            if not any(p.name.startswith(vp) for vp in valid_prefixes):
                try: p.unlink()
                except Exception: pass

        import pandas as pd
        df_p = pd.concat(all_p, ignore_index=True) if all_p else pd.DataFrame()
        df_t = pd.concat(all_t, ignore_index=True) if all_t else pd.DataFrame()
        df_e = pd.concat(all_e, ignore_index=True) if all_e else pd.DataFrame()
        return df_p, df_t, df_e

    def load_team_matches_from_supabase(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Carga las partidas del equipo directamente desde Supabase.
        Retorna el mismo formato (df_p, df_t, df_e) que load_team_matches().

        Ventajas vs carga por API:
          - No requiere Riot API key activa
          - Datos persistentes entre sesiones (sin re-descarga)
          - Funciona offline (datos ya cargados en la nube)
        """
        if not self.supabase:
            logger.error("Supabase no configurado. Revisa SUPABASE_URL y SUPABASE_KEY en .env")
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        try:
            def _fetch_all(query):
                all_rows = []
                off = 0
                lim = 1000
                while True:
                    r = query.range(off, off + lim - 1).execute()
                    if not r or not r.data: break
                    all_rows.extend(r.data)
                    if len(r.data) < lim: break
                    off += lim
                return all_rows

            # 1. Participants (stats por jugador)
            data_p = _fetch_all(self.supabase.table("team_participants").select("*"))
            df_p = pd.DataFrame(data_p) if data_p else pd.DataFrame()

            if df_p.empty:
                logger.warning("No hay partidas del equipo en Supabase. Usa upload_team_match.py para subir datos.")
                return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

            match_ids = df_p["match_id"].unique().tolist()
            logger.info(f"Cargando {len(match_ids)} partidas desde Supabase...")

            # 2. Timeline — en lotes para evitar URLs demasiado largas (Supabase limit)
            all_tl = []
            chunk_size = 50
            for i in range(0, len(match_ids), chunk_size):
                chunk = match_ids[i : i + chunk_size]
                data_t = _fetch_all(
                    self.supabase.table("team_timeline")
                    .select("*")
                    .in_("match_id", chunk)
                )
                if data_t:
                    all_tl.append(pd.DataFrame(data_t))
            df_t = pd.concat(all_tl, ignore_index=True) if all_tl else pd.DataFrame()

            # 3. Eventos
            all_ev = []
            for i in range(0, len(match_ids), chunk_size):
                chunk = match_ids[i : i + chunk_size]
                data_e = _fetch_all(
                    self.supabase.table("team_events")
                    .select("*")
                    .in_("match_id", chunk)
                )
                if data_e:
                    all_ev.append(pd.DataFrame(data_e))
            df_e = pd.concat(all_ev, ignore_index=True) if all_ev else pd.DataFrame()

            # 4. Normalizar columnas para compatibilidad con el pipeline existente
            if not df_t.empty:
                # La tabla guarda timestamp_ms; el pipeline espera 'timestamp'
                if "timestamp_ms" in df_t.columns and "timestamp" not in df_t.columns:
                    df_t = df_t.rename(columns={"timestamp_ms": "timestamp"})
                # Asegurar timestamp_min
                if "timestamp_min" not in df_t.columns and "timestamp" in df_t.columns:
                    df_t["timestamp_min"] = df_t["timestamp"] / 60_000.0

            if not df_e.empty:
                if "timestamp_ms" in df_e.columns and "timestamp" not in df_e.columns:
                    df_e = df_e.rename(columns={"timestamp_ms": "timestamp"})
                if "timestamp_min" not in df_e.columns and "timestamp" in df_e.columns:
                    df_e["timestamp_min"] = df_e["timestamp"] / 60_000.0

            logger.info(
                f"Supabase: {len(df_p)} participantes | "
                f"{len(df_t)} frames timeline | {len(df_e)} eventos"
            )
            return df_p, df_t, df_e

        except Exception as e:
            logger.error(f"Error cargando desde Supabase: {e}")
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    def get_benchmarks_from_supabase(self, region: str, patch: str) -> pd.DataFrame:
        """Lee los benchmarks en formato ancho desde Supabase."""
        if not self.supabase:
            logger.error("Supabase no configurado.")
            return pd.DataFrame()
            
        try:
            def _fetch_all(query):
                all_rows = []
                off = 0
                lim = 1000
                while True:
                    r = query.range(off, off + lim - 1).execute()
                    if not r or not r.data: break
                    all_rows.extend(r.data)
                    if len(r.data) < lim: break
                    off += lim
                return all_rows

            # 1. Intentar cargar parche solicitado
            rows = _fetch_all(self.supabase.table("benchmarks_summary").select("*").eq("patch", patch))
            
            # 2. FALLBACK: Si no hay datos, buscar parche más reciente
            if not rows:
                logger.warning(f"No hay benchmarks para parche {patch}. Buscando el más reciente...")
                latest_resp = self.supabase.table("benchmarks_summary").select("patch").order("patch", desc=True).limit(1).execute()
                if latest_resp and latest_resp.data:
                    latest_patch = latest_resp.data[0]["patch"]
                    rows = _fetch_all(self.supabase.table("benchmarks_summary").select("*").eq("patch", latest_patch))
                    logger.info(f"Usando benchmarks del parche {latest_patch} como fallback.")
            
            if not rows:
                return pd.DataFrame(columns=["champion", "role", "patch", "gold_per_min", "cs_per_min", "vision_per_min", "damage_per_min", "impact_score", "kda", "sample_size"])
            
            df = pd.DataFrame(rows)
            
            # Garantizar que existan las columnas críticas para evitar KeyErrors
            required_cols = ["kda", "vision_per_min", "impact_score", "sample_size", "role"]
            for col in required_cols:
                if col not in df.columns:
                    df[col] = None
            
            # Si el dashboard pide una región específica (aunque ahora unificamos en GLOBAL)
            # Devolvemos la tabla completa (una fila por campeón/rol)
            # El filtrado o agrupación se hace en el componente que lo necesite
            return df

        except Exception as e:
            logger.error(f"Error al obtener benchmarks de Supabase: {e}")
            return pd.DataFrame()

    def _fetch_from_mongodb(self, collection_name: str, match_id: str) -> Optional[dict]:
        """Lee un documento crudo desde MongoDB. Retorna None si no existe o si hay error."""
        if not self.mongodb:
            return None
        try:
            doc = self.mongodb[self.mongodb_db_name][collection_name].find_one({"_id": match_id})
            if doc:
                logger.debug(f"MongoDB cache hit: {match_id} in {collection_name}")
                return doc.get("data")
            return None
        except PyMongoError as e:
            logger.warning(f"MongoDB read failed for {match_id} in {collection_name}: {e}")
            return None

    def _dump_to_mongodb(self, collection_name: str, match_id: str, data: dict) -> None:
        """Upserta datos crudos en MongoDB Data Lake usando match_id como _id."""
        if not self.mongodb:
            return
        try:
            self.mongodb[self.mongodb_db_name][collection_name].update_one(
                {"_id": match_id},
                {"$set": {"data": data, "fetched_at": pd.Timestamp.now().isoformat()}},
                upsert=True
            )
            logger.debug(f"Upserted {match_id} to MongoDB {collection_name}")
        except PyMongoError as e:
            logger.warning(f"MongoDB upsert failed for {match_id} in {collection_name}: {e}")


# ──────────────────────────────────────────────────────────────────
# Normalizacion de Match JSON -> DataFrame
# ──────────────────────────────────────────────────────────────────

def normalize_match(raw: dict) -> pd.DataFrame:
    """
    Aplana la seccion info.participants de un JSON de match-v5.
    """
    info = raw.get("info") or {}
    match_id: str = raw.get("metadata", {}).get("matchId", "UNKNOWN")
    duration_s: int = info.get("gameDuration", 0)

    if duration_s > 7200:
        duration_s //= 1000
    duration_min: float = duration_s / 60.0

    is_custom = info.get("queueId") == 0 or info.get("gameType") == "CUSTOM_GAME"

    rows: list[dict] = []
    
    participants = info.get("participants") or []
    for p in participants:
        # Extraer first_blood desde las banderas de partida
        first_blood = bool(p.get("firstBloodKill", False) or p.get("firstBloodAssist", False))
        
        row = {
            "match_id": match_id,
            "participant_id": p.get("participantId", 0),
            "puuid": p.get("puuid", ""),
            "game_name": p.get("riotIdGameName", p.get("summonerName", "")),
            "tag_line": p.get("riotIdTagline", ""),
            "team_id": p.get("teamId", 0),
            "role": config.ROLE_MAP.get(p.get("teamPosition", ""), p.get("teamPosition", "UNKNOWN")),
            "champion": p.get("championName", ""),
            "kills": p.get("kills", 0),
            "deaths": p.get("deaths", 0),
            "assists": p.get("assists", 0),
            "gold_earned": p.get("goldEarned", 0),
            "gold_spent": p.get("goldSpent", 0),
            "total_damage": p.get("totalDamageDealtToChampions", 0),
            "physical_damage": p.get("physicalDamageDealtToChampions", 0),
            "magic_damage": p.get("magicDamageDealtToChampions", 0),
            "true_damage": p.get("trueDamageDealtToChampions", 0),
            "damage_taken": p.get("totalDamageTaken", 0),
            "damage_mitigated": p.get("damageSelfMitigated", 0),
            "damage_buildings": p.get("damageDealtToBuildings", p.get("damageDealtToTurrets", 0)),
            "vision_score": p.get("visionScore", 0),
            "wards_placed": p.get("wardsPlaced", 0),
            "wards_killed": p.get("wardsKilled", 0),
            "control_wards": p.get("visionWardsBoughtInGame", 0),
            "cs": p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0),
            "total_heal": p.get("totalHeal", 0),
            "time_cc": p.get("timeCCingOthers", 0),
            "duration_minutes": duration_min,
            "result": p.get("win", False),
            "is_custom": is_custom,
            "first_blood": first_blood,
        }
        rows.append(row)

    return pd.DataFrame(rows)


# ──────────────────────────────────────────────────────────────────
# Normalizacion Timeline -> DataFrames
# ──────────────────────────────────────────────────────────────────

def normalize_timeline(timeline_data: dict, match_id: str, p_to_team: dict[int, int] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    p_to_team = p_to_team or {}
    frames = timeline_data.get("info", {}).get("frames", [])
    timeline_rows: list[dict] = []
    event_rows: list[dict] = []

    for frame in frames:
        ts_ms: int = frame.get("timestamp", 0)
        ts_min: float = round(ts_ms / 60_000, 2)

        for pid_str, pf in frame.get("participantFrames", {}).items():
            pos = pf.get("position", {})
            timeline_rows.append({
                "match_id":       match_id,
                "participant_id": int(pid_str),
                "timestamp_ms":   ts_ms,
                "timestamp_min":  ts_min,
                "total_gold":     pf.get("totalGold", 0),
                "cs":             pf.get("minionsKilled", 0) + pf.get("jungleMinionsKilled", 0),
                "xp":             pf.get("xp", 0),
                "level":          pf.get("level", 1),
                "pos_x":          pos.get("x", 0),
                "pos_y":          pos.get("y", 0),
            })

        for ev in frame.get("events", []):
            ev_type: str = ev.get("type", "")
            if ev_type not in {
                "CHAMPION_KILL", "ELITE_MONSTER_KILL",
                "BUILDING_KILL", "WARD_PLACED", "WARD_KILL",
                "ITEM_PURCHASED",
            }:
                continue

            pos = ev.get("position", {})
            monster_raw = ev.get("monsterType", "")
            building_raw = ev.get("buildingType", "")

            # Determinar team_id con fallback a mapeo de participantes y luego a heurística
            killer_id = ev.get("killerId") or ev.get("creatorId") or 0
            ev_team = ev.get("teamId") or ev.get("killerTeamId")
            
            if not ev_team and killer_id in p_to_team:
                ev_team = p_to_team[killer_id]
            
            if not ev_team:
                ev_team = 100 if killer_id in range(1, 6) else (200 if killer_id in range(6, 11) else 0)

            # Capturar asistentes para Kills, Monstruos Épicos y Estructuras
            assisting_raw = ev.get("assistingParticipantIds", [])

            # Identificar equipo de la víctima para el mapa de muertes
            victim_id = ev.get("victimId", 0)
            victim_team = p_to_team.get(victim_id, 0)
            if not victim_team and victim_id > 0:
                victim_team = 100 if victim_id in range(1, 6) else 200

            event_rows.append({
                "match_id":      match_id,
                "timestamp_ms":  ts_ms,
                "timestamp_min": ts_min,
                "event_type":    ev_type,
                "participant_id": killer_id,
                "victim_id":     victim_id,
                "team_id":       ev_team,
                "victim_team_id": victim_team,
                "assisting_ids": ",".join(str(x) for x in assisting_raw),
                "monster_type":  OBJECTIVE_MONSTER_TYPES.get(monster_raw, monster_raw.lower()) if monster_raw else "",
                "building_type": BUILDING_KILL_SUBTYPES.get(building_raw, building_raw.lower()) if building_raw else "",
                "item_id":       ev.get("itemId", 0),
                "position_x":    pos.get("x", 0),
                "position_y":    pos.get("y", 0),
            })

    df_tl = pd.DataFrame(timeline_rows)
    df_ev = pd.DataFrame(event_rows)

    # Optimizacion de memoria: Downcast agresivo
    if not df_tl.empty:
        for col in ["pos_x", "pos_y", "total_gold", "cs", "xp", "level", "participant_id"]:
            if col in df_tl.columns:
                df_tl[col] = pd.to_numeric(df_tl[col], downcast="integer")
        if "timestamp_min" in df_tl.columns:
            df_tl["timestamp_min"] = pd.to_numeric(df_tl["timestamp_min"], downcast="float")

    if not df_ev.empty:
        for col in ["position_x", "position_y", "item_id", "participant_id", "victim_id", "team_id"]:
            if col in df_ev.columns:
                df_ev[col] = pd.to_numeric(df_ev[col], downcast="integer")
        if "timestamp_min" in df_ev.columns:
            df_ev["timestamp_min"] = pd.to_numeric(df_ev["timestamp_min"], downcast="float")

    return df_tl, df_ev


# ──────────────────────────────────────────────────────────────────
# CSV Loader
# ──────────────────────────────────────────────────────────────────

CSV_EXPECTED_COLUMNS: list[str] = [
    "match_id", "participant_id", "puuid", "game_name", "tag_line",
    "team_id", "role", "champion", "kills", "deaths", "assists",
    "gold_earned", "gold_spent", "total_damage", "vision_score",
    "wards_placed", "wards_killed", "control_wards", "cs",
    "duration_minutes", "result",
]

class CSVLoader:
    """Cargador de datos desde archivos CSV."""

    def __init__(self, filepath: str | Path) -> None:
        self.filepath = Path(filepath)
        if not self.filepath.exists():
            raise FileNotFoundError(f"Archivo no encontrado: {self.filepath}")

    def load(self) -> pd.DataFrame:
        df = pd.read_csv(self.filepath)
        missing = [c for c in CSV_EXPECTED_COLUMNS if c not in df.columns]
        if missing:
            logger.warning(f"Columnas faltantes en CSV (se rellenan con NaN): {missing}")
            for col in missing:
                df[col] = None

        int_cols = ["kills", "deaths", "assists", "gold_earned", "cs", "vision_score"]
        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

        if "result" in df.columns:
            df["result"] = df["result"].astype(bool)

        logger.info(f"CSV cargado: {len(df)} registros desde {self.filepath.name}")
        return df

# Alias para compatibilidad con scripts existentes
RiotAPILoader = MatchV5Client
