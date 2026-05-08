"""
data_loader_scout.py — Cliente Riot API adaptado para análisis individual.

Hereda toda la lógica de bajo nivel de MatchV5Client:
  - Rate limiting, backoff, caché JSON/MongoDB
  - normalize_match, normalize_timeline

Agrega: load_player_matches(profile) — descarga partidas de 1 jugador.
        load_player_from_supabase(profile) — carga historial desde Supabase.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Importar constantes de dominio (sin depender del config.py de equipo)
# ──────────────────────────────────────────────────────────────────

from src.config_scout import (
    OBJECTIVE_MONSTER_TYPES,
    BUILDING_KILL_SUBTYPES,
    ROLE_MAP,
    MAX_RETRIES,
    BACKOFF_BASE_SECONDS,
    DATA_RAW_DIR,
)


# ──────────────────────────────────────────────────────────────────
# Cliente Match-V5 (idéntico al base, sin dependencia de TEAM_PLAYERS)
# ──────────────────────────────────────────────────────────────────

class ScoutMatchClient:
    """
    Cliente Riot API para análisis individual.
    Funcionalidad idéntica al MatchV5Client del proyecto de equipo,
    pero sin lógica de intersección de 5 jugadores.
    """

    def __init__(self, api_key: str, platform: str = "la2", region: str = "americas") -> None:
        self.api_key = api_key
        self.platform = platform
        self.region = region
        self.base_url = f"https://{region}.api.riotgames.com/lol/match/v5/matches"
        self.session = requests.Session()
        self.session.headers.update({"X-Riot-Token": self.api_key})
        self.raw_dir: Path = DATA_RAW_DIR
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_request_time: float = 0.0

        # Supabase
        self.supabase_url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        self.supabase_key = (
            os.environ.get("SUPABASE_KEY")
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        )
        self._supabase_client: Optional[Client] = None

    @property
    def supabase(self) -> Optional[Client]:
        if self._supabase_client is None and self.supabase_url and self.supabase_key:
            self._supabase_client = create_client(self.supabase_url, self.supabase_key)
        return self._supabase_client
    
    @supabase.setter
    def supabase(self, client: Optional[Client]) -> None:
        self._supabase_client = client

    # ── Rate-limited HTTP ───────────────────────────────────────────

    def _request(self, endpoint: str) -> Optional[dict]:
        url = endpoint if endpoint.startswith("http") else f"{self.base_url}/{endpoint}"
        for attempt in range(MAX_RETRIES):
            with self._lock:
                elapsed = time.time() - self._last_request_time
                wait = 1.3 - elapsed
                if wait > 0:
                    time.sleep(wait)
                self._last_request_time = time.time()
            try:
                resp = self.session.get(url, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    retry = int(resp.headers.get("Retry-After", 2)) + 1
                    logger.warning(f"Rate limit. Esperando {retry}s (intento {attempt+1})")
                    time.sleep(retry)
                elif resp.status_code == 404:
                    logger.warning(f"404: {url}")
                    return None
                elif resp.status_code == 401:
                    logger.error("❌ Riot API key inválida (401). Actualizá .env")
                    raise RuntimeError("RIOT_API_401")
                else:
                    logger.error(f"Error {resp.status_code} en {url}")
                    time.sleep(2)
            except RuntimeError:
                raise
            except Exception as e:
                logger.error(f"Excepción HTTP: {e}")
                time.sleep(2)
        return None

    # ── Cache + fetch ───────────────────────────────────────────────

    def get_match(self, match_id: str) -> Optional[dict]:
        return self._request(match_id)

    def get_timeline(self, match_id: str) -> Optional[dict]:
        return self._request(f"{match_id}/timeline")

    def get_puuid(self, game_name: str, tag_line: str) -> Optional[str]:
        from urllib.parse import quote
        url = f"https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{quote(game_name)}/{quote(tag_line)}"
        data = self._request(url)
        return data["puuid"] if data and data.get("puuid") else None

    def get_match_ids_by_puuid(
        self,
        puuid: str,
        count: int = 20,
        queue: Optional[int] = None,
        start_time: Optional[int] = None,
    ) -> list[str]:
        """
        Obtiene match IDs con paginación automática.
        La API de Riot tiene un límite de 100 por request, pero podemos paginar.
        
        Args:
            count: Total de partidas a obtener (se pagina de a 100)
            start_time: Epoch timestamp en segundos para filtrar por fecha
        """
        PAGE_SIZE = 100  # Max permitido por Riot API
        all_ids: list[str] = []
        start = 0
        
        while len(all_ids) < count:
            batch = min(PAGE_SIZE, count - len(all_ids))
            endpoint = f"by-puuid/{puuid}/ids?start={start}&count={batch}"
            if queue and queue > 0:
                endpoint += f"&queue={queue}"
            if start_time:
                endpoint += f"&startTime={start_time}"
            
            resp = self._request(endpoint)
            if not isinstance(resp, list) or not resp:
                break  # No hay más partidas
            
            all_ids.extend(resp)
            if len(resp) < batch:
                break  # Última página
            start += batch
        
        return all_ids

    def load_single_match_by_id(self, match_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        clean_id = match_id.strip()
        match_data = self.get_match(clean_id)
        if not match_data:
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        dur = match_data.get("info", {}).get("gameDuration", 0)
        if dur > 7200:
            dur //= 1000

        df_p = normalize_match(match_data)
        p_to_team = df_p.set_index("participant_id")["team_id"].to_dict()
        p_to_role = df_p.set_index("participant_id")["role"].to_dict()

        df_t, df_e = pd.DataFrame(), pd.DataFrame()
        tl = self.get_timeline(clean_id)
        if tl:
            df_t, df_e = normalize_timeline(tl, clean_id, p_to_team, p_to_role)

        return df_p, df_t, df_e

    # ── MÉTODO PRINCIPAL: individual ───────────────────────────────

    def load_player_matches(self, profile) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Descarga las últimas N partidas de un jugador individual.
        No hace intersección de 5 jugadores — solo necesita el PUUID del perfil.

        Args:
            profile: PlayerProfile cargado desde YAML.

        Returns:
            (df_participants, df_timeline, df_events) — todos los 10 jugadores
            de cada partida (para cálculos de peer ranking y gold diff vs oponente).
        """
        puuid = self.get_puuid(profile.game_name, profile.tag_line)
        if not puuid:
            logger.error(f"No se encontró PUUID para {profile.riot_id}")
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        logger.info(f"📥 Descargando partidas de {profile.riot_id}...")

        # Calcular start_time de season si está disponible
        season_start_ts = getattr(profile, "season_start_ts", None)
        
        # Obtener TODAS las partidas de la season (paginando hasta el límite de Riot)
        # Riot API permite máximo 1000 partidas por request total
        match_ids: set[str] = set()
        for q in profile.queue_filter:
            q_param = q if q != 0 else None
            ids = self.get_match_ids_by_puuid(
                puuid,
                count=1000,  # Máximo permitido - paginará automáticamente
                queue=q_param,
                start_time=season_start_ts,
            )
            match_ids.update(ids)

        logger.info(f"🔎 {len(match_ids)} partidas encontradas para {profile.riot_id} en Riot API (season filter: {season_start_ts})")

        # FILTRAR PARTIDAS YA EXISTENTES EN SUPABASE
        if self.supabase and match_ids:
            try:
                # Partir en chunks porque Supabase in_ tiene límite
                existing_ids = set()
                ids_list = list(match_ids)
                for i in range(0, len(ids_list), 50):
                    chunk = ids_list[i:i+50]
                    res = self.supabase.table("matches").select("match_id").in_("match_id", chunk).execute()
                    if res.data:
                        existing_ids.update(r["match_id"] for r in res.data)
                
                if existing_ids:
                    match_ids = match_ids - existing_ids
                    logger.info(f"⏭️ Saltando {len(existing_ids)} partidas ya procesadas. {len(match_ids)} nuevas por procesar.")
            except Exception as e:
                logger.error(f"Error verificando caché en Supabase: {e}")

        if not match_ids:
            logger.info("✅ Todo al día. No hay partidas nuevas para procesar.")
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        import concurrent.futures
        all_p, all_t, all_e = [], [], []

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(self.load_single_match_by_id, mid): mid for mid in match_ids}
            for idx, future in enumerate(concurrent.futures.as_completed(futures)):
                mid = futures[future]
                try:
                    p, t, e = future.result(timeout=30)
                    if not p.empty:
                        all_p.append(p)
                    if not t.empty:
                        all_t.append(t)
                    if not e.empty:
                        all_e.append(e)
                    logger.info(f"[{idx+1}/{len(match_ids)}] {mid}")
                except Exception as exc:
                    logger.error(f"Error en {mid}: {exc}")

        df_p = pd.concat(all_p, ignore_index=True) if all_p else pd.DataFrame()
        df_t = pd.concat(all_t, ignore_index=True) if all_t else pd.DataFrame()
        df_e = pd.concat(all_e, ignore_index=True) if all_e else pd.DataFrame()
        
        # FILTRAR POR QUEUE_ID - Solo quedarnos con las partidas de las colas especificadas
        if not df_p.empty and "queue_id" in df_p.columns:
            valid_queues = profile.queue_filter
            before_filter = len(df_p["match_id"].unique())
            
            # Filtrar participantes
            df_p = df_p[df_p["queue_id"].isin(valid_queues)].copy()
            
            # Obtener match_ids válidos
            valid_match_ids = df_p["match_id"].unique()
            after_filter = len(valid_match_ids)
            
            # Filtrar timeline y events
            if not df_t.empty:
                df_t = df_t[df_t["match_id"].isin(valid_match_ids)].copy()
            if not df_e.empty:
                df_e = df_e[df_e["match_id"].isin(valid_match_ids)].copy()
            
            filtered_out = before_filter - after_filter
            if filtered_out > 0:
                logger.info(f"🗑️ Filtradas {filtered_out} partidas que no son de las colas especificadas {valid_queues}")
                logger.info(f"✅ {after_filter} partidas válidas después del filtro")
        
        return df_p, df_t, df_e

    def load_player_summary_from_db(self, profile, days_back: int = 30) -> pd.DataFrame:
        """
        Carga resumen agregado directo de Supabase con filtro temporal.
        Evita cargar partidas antiguas innecesarias.
        
        Args:
            profile: PlayerProfile con game_name
            days_back: Días hacia atrás para filtrar (default 30)
            
        Returns:
            DataFrame con métricas agregadas por rol
        """
        if not self.supabase:
            logger.error("Supabase no configurado.")
            return pd.DataFrame()
        
        try:
            # Query con filtro temporal y agregación
            from datetime import datetime, timedelta
            cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()
            
            resp = (
                self.supabase.table("scout_participants")
                .select("*")
                .eq("game_name", profile.game_name)
                .gte("created_at", cutoff)
                .execute()
            )
            
            if not resp.data:
                logger.warning(f"No hay partidas recientes de {profile.game_name} en últimos {days_back} días.")
                return pd.DataFrame()
            
            df = pd.DataFrame(resp.data)
            logger.info(f"Cargadas {len(df)} partidas de últimos {days_back} días (optimizado).")
            return df
            
        except Exception as e:
            logger.error(f"Error en load_player_summary_from_db: {e}")
            return pd.DataFrame()
    
    def load_death_events_optimized(self, profile, limit: int = 200, season_start: Optional[str] = None) -> pd.DataFrame:
        """
        Carga solo eventos de muerte del jugador (CHAMPION_KILL donde es victim).
        Incluye posición, timestamp, asistentes para análisis espacial.
        
        Args:
            profile: PlayerProfile
            limit: Máximo de eventos a cargar
            season_start: Fecha ISO de inicio de temporada para filtrar
            
        Returns:
            DataFrame con eventos de muerte filtrados
        """
        if not self.supabase:
            return pd.DataFrame()
        
        try:
            # Primero obtener match_ids del jugador en el rango de fechas
            query = (
                self.supabase.table("scout_participants")
                .select("match_id, participant_id, game_creation")
            )
            
            # Filtrar por game_name (soportar nombres históricos)
            all_names = getattr(profile, 'all_game_names', [profile.game_name])
            if len(all_names) == 1:
                query = query.eq("game_name", all_names[0])
            else:
                query = query.in_("game_name", all_names)
            
            # Filtrar por fecha si se especifica
            if season_start:
                query = query.gte("game_creation", season_start)
            
            resp_p = query.limit(200).execute()
            
            if not resp_p.data:
                return pd.DataFrame()
            
            df_p = pd.DataFrame(resp_p.data)
            match_ids = df_p["match_id"].unique().tolist()
            
            # Validate match_ids limit for Supabase .in_() operator
            if len(match_ids) > 1000:
                logger.warning(f"Too many matches ({len(match_ids)}). Limiting to 1000.")
                match_ids = match_ids[:1000]
            
            # Cargar eventos de muerte en chunks
            all_deaths = []
            chunk_size = 50
            
            for i in range(0, len(match_ids), chunk_size):
                chunk = match_ids[i:i+chunk_size]
                resp_e = (
                    self.supabase.table("scout_events")
                    .select("*")
                    .eq("event_type", "CHAMPION_KILL")
                    .in_("match_id", chunk)
                    .execute()
                )
                if resp_e.data:
                    all_deaths.append(pd.DataFrame(resp_e.data))
            
            if not all_deaths:
                return pd.DataFrame()
            
            df_events = pd.concat(all_deaths, ignore_index=True)
            
            # Filtrar solo donde el jugador es la víctima
            player_pids = df_p.set_index("match_id")["participant_id"].to_dict()
            df_events["is_player_death"] = df_events.apply(
                lambda row: row["victim_id"] == player_pids.get(row["match_id"]),
                axis=1
            )
            
            df_deaths = df_events[df_events["is_player_death"]].copy()
            logger.info(f"Cargados {len(df_deaths)} eventos de muerte del jugador.")
            return df_deaths
            
        except Exception as e:
            logger.error(f"Error en load_death_events_optimized: {e}")
            return pd.DataFrame()

    def load_player_from_supabase(
        self,
        profile,
        season_start: Optional[str] = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Carga el historial de partidas del jugador desde Supabase.
        Filtra por game_name del perfil en scout_participants.

        Args:
            profile: PlayerProfile o TempProfile con game_name
            season_start: Fecha ISO de inicio de season (ej: "2025-01-08T00:00:00+00:00")
                          Si es None, carga todas las partidas disponibles.
        """
        if not self.supabase:
            logger.error("Supabase no configurado.")
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        try:
            # Si hay filtro de season, primero obtener match_ids del rango desde `matches`
            season_match_ids: Optional[list[str]] = None
            if season_start:
                logger.info(f"Filtrando partidas desde {season_start}")
                matches_q = (
                    self.supabase.table("matches")
                    .select("match_id")
                    .gte("game_timestamp", season_start)
                    .not_.is_("game_timestamp", "null")  # Excluir partidas sin timestamp
                )
                matches_resp = matches_q.execute()
                if matches_resp.data:
                    season_match_ids = [r["match_id"] for r in matches_resp.data]
                    logger.info(f"Partidas en DB dentro del rango: {len(season_match_ids)}")
                else:
                    logger.warning(f"No hay partidas en DB desde {season_start}.")
                    return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

            # Cargar participantes del jugador - buscar en todos los nombres (actual + históricos)
            all_names = getattr(profile, "all_game_names", [profile.game_name])
            logger.info(f"🔍 Buscando partidas para nombres: {all_names}")
            
            if len(all_names) == 1:
                # Solo un nombre - usar eq
                query = (
                    self.supabase.table("scout_participants")
                    .select("*")
                    .eq("game_name", all_names[0])
                )
                resp_p = query.execute()
            else:
                # Múltiples nombres - usar in
                query = (
                    self.supabase.table("scout_participants")
                    .select("*")
                    .in_("game_name", all_names)
                )
                resp_p = query.execute()
            
            df_player = pd.DataFrame(resp_p.data) if resp_p.data else pd.DataFrame()
            logger.info(f"📊 Encontradas {len(df_player)} filas de participantes antes del filtro de fecha")

            # Aplicar filtro de season si corresponde
            if season_match_ids is not None and not df_player.empty:
                before_season_filter = len(df_player["match_id"].unique())
                df_player = df_player[df_player["match_id"].isin(season_match_ids)]
                after_season_filter = len(df_player["match_id"].unique())
                logger.info(f"📅 Filtro de fecha: {before_season_filter} → {after_season_filter} partidas")

            if df_player.empty:
                logger.warning(f"No hay partidas de {all_names} en Supabase (filtro: {season_start or 'todos'}).")
                return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

            match_ids = df_player["match_id"].unique().tolist()
            logger.info(f"Cargando {len(match_ids)} partidas desde Supabase...")

            # Cargar todos los 10 participantes de cada partida (para peer ranking)
            all_p, all_t, all_e = [], [], []
            chunk_size = 50

            for i in range(0, len(match_ids), chunk_size):
                chunk = match_ids[i: i + chunk_size]
                rp = self.supabase.table("scout_participants").select("*").in_("match_id", chunk).execute()
                if rp.data:
                    all_p.append(pd.DataFrame(rp.data))
                rt = self.supabase.table("scout_timeline").select("*").in_("match_id", chunk).execute()
                if rt.data:
                    all_t.append(pd.DataFrame(rt.data))
                re = self.supabase.table("scout_events").select("*").in_("match_id", chunk).execute()
                if re.data:
                    all_e.append(pd.DataFrame(re.data))

            df_p = pd.concat(all_p, ignore_index=True) if all_p else pd.DataFrame()
            df_t = pd.concat(all_t, ignore_index=True) if all_t else pd.DataFrame()
            df_e = pd.concat(all_e, ignore_index=True) if all_e else pd.DataFrame()

            # Normalizar columnas de timestamp para compatibilidad
            for df in [df_t, df_e]:
                if not df.empty and "timestamp_ms" in df.columns and "timestamp" not in df.columns:
                    df.rename(columns={"timestamp_ms": "timestamp"}, inplace=True)
                if not df.empty and "timestamp_min" not in df.columns and "timestamp" in df.columns:
                    df["timestamp_min"] = df["timestamp"] / 60_000.0

            return df_p, df_t, df_e

        except Exception as e:
            logger.error(f"Error cargando desde Supabase: {e}")
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    def get_benchmarks_from_supabase(self, patch: str) -> pd.DataFrame:
        """Lee benchmarks Challenger desde la tabla benchmarks_summary."""
        if not self.supabase:
            return pd.DataFrame()
        try:
            def _fetch_all(q):
                rows, off = [], 0
                while True:
                    r = q.range(off, off + 999).execute()
                    if not r or not r.data:
                        break
                    rows.extend(r.data)
                    if len(r.data) < 1000:
                        break
                    off += 1000
                return rows

            rows = _fetch_all(self.supabase.table("benchmarks_summary").select("*").eq("patch", patch))
            if not rows:
                latest = self.supabase.table("benchmarks_summary").select("patch").order("patch", desc=True).limit(1).execute()
                if latest and latest.data:
                    rows = _fetch_all(self.supabase.table("benchmarks_summary").select("*").eq("patch", latest.data[0]["patch"]))

            return pd.DataFrame(rows) if rows else pd.DataFrame()
        except Exception as e:
            logger.error(f"Error obteniendo benchmarks: {e}")
            return pd.DataFrame()


# ──────────────────────────────────────────────────────────────────
# Normalización JSON → DataFrames (idéntica al proyecto base)
# ──────────────────────────────────────────────────────────────────

def normalize_match(raw: dict) -> pd.DataFrame:
    """Aplana info.participants de un JSON match-v5."""
    info = raw.get("info") or {}
    match_id: str = raw.get("metadata", {}).get("matchId", "UNKNOWN")
    duration_s: int = info.get("gameDuration", 0)
    if duration_s > 7200:
        duration_s //= 1000
    duration_min: float = duration_s / 60.0
    queue_id: int = info.get("queueId", 0)
    is_custom = queue_id == 0 or info.get("gameType") == "CUSTOM_GAME"
    game_creation_ms: int = info.get("gameCreation", 0)  # Timestamp en ms

    rows = []
    for p in info.get("participants") or []:
        first_blood = bool(p.get("firstBloodKill", False) or p.get("firstBloodAssist", False))
        rows.append({
            "match_id":          match_id,
            "participant_id":    p.get("participantId", 0),
            "puuid":             p.get("puuid", ""),
            "game_name":         p.get("riotIdGameName", p.get("summonerName", "")),
            "tag_line":          p.get("riotIdTagline", ""),
            "team_id":           p.get("teamId", 0),
            "role":              ROLE_MAP.get(p.get("teamPosition", ""), p.get("teamPosition", "UNKNOWN")),
            "champion":          p.get("championName", ""),
            "kills":             p.get("kills", 0),
            "deaths":            p.get("deaths", 0),
            "assists":           p.get("assists", 0),
            "gold_earned":       p.get("goldEarned", 0),
            "gold_spent":        p.get("goldSpent", 0),
            "total_damage":      p.get("totalDamageDealtToChampions", 0),
            "physical_damage":   p.get("physicalDamageDealtToChampions", 0),
            "magic_damage":      p.get("magicDamageDealtToChampions", 0),
            "true_damage":       p.get("trueDamageDealtToChampions", 0),
            "damage_taken":      p.get("totalDamageTaken", 0),
            "damage_mitigated":  p.get("damageSelfMitigated", 0),
            "damage_buildings":  p.get("damageDealtToBuildings", p.get("damageDealtToTurrets", 0)),
            "vision_score":      p.get("visionScore", 0),
            "wards_placed":      p.get("wardsPlaced", 0),
            "wards_killed":      p.get("wardsKilled", 0),
            "control_wards":     p.get("visionWardsBoughtInGame", 0),
            "cs":                p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0),
            "total_heal":        p.get("totalHeal", 0),
            "time_cc":           p.get("timeCCingOthers", 0),
            "duration_minutes":  duration_min,
            "result":            p.get("win", False),
            "is_custom":         is_custom,
            "first_blood":       first_blood,
            "game_creation_ms":  game_creation_ms,
            "queue_id":          queue_id,
        })
    return pd.DataFrame(rows)


def normalize_timeline(
    timeline_data: dict,
    match_id: str,
    p_to_team: dict[int, int] | None = None,
    p_to_role: dict[int, str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Normaliza timeline JSON → (df_timeline, df_events)."""
    p_to_team = p_to_team or {}
    p_to_role = p_to_role or {}
    frames = timeline_data.get("info", {}).get("frames", [])
    tl_rows, ev_rows = [], []

    for frame in frames:
        ts_ms: int = frame.get("timestamp", 0)
        ts_min: float = round(ts_ms / 60_000, 2)

        for pid_str, pf in frame.get("participantFrames", {}).items():
            pid = int(pid_str)
            pos = pf.get("position", {})
            tl_rows.append({
                "match_id":       match_id,
                "participant_id": pid,
                "timestamp_ms":   ts_ms,
                "timestamp_min":  ts_min,
                "total_gold":     pf.get("totalGold", 0),
                "cs":             pf.get("minionsKilled", 0) + pf.get("jungleMinionsKilled", 0),
                "xp":             pf.get("xp", 0),
                "level":          pf.get("level", 1),
                "pos_x":          pos.get("x", 0),
                "pos_y":          pos.get("y", 0),
                "role":           p_to_role.get(pid, "UNKNOWN"),
                "team_id":        p_to_team.get(pid, 0),
            })

        for ev in frame.get("events", []):
            ev_type = ev.get("type", "")
            if ev_type not in {
                "CHAMPION_KILL", "ELITE_MONSTER_KILL",
                "BUILDING_KILL", "WARD_PLACED", "WARD_KILL",
                "ITEM_PURCHASED",
            }:
                continue

            pos = ev.get("position", {})
            monster_raw = ev.get("monsterType", "")
            building_raw = ev.get("buildingType", "")
            killer_id = ev.get("killerId") or ev.get("creatorId") or 0
            ev_team = ev.get("teamId") or ev.get("killerTeamId")

            if not ev_team and killer_id in p_to_team:
                ev_team = p_to_team[killer_id]
            if not ev_team:
                ev_team = 100 if killer_id in range(1, 6) else (200 if killer_id in range(6, 11) else 0)

            victim_id = ev.get("victimId", 0)
            victim_team = p_to_team.get(victim_id, 0)
            if not victim_team and victim_id > 0:
                victim_team = 100 if victim_id in range(1, 6) else 200

            ev_rows.append({
                "match_id":       match_id,
                "timestamp_ms":   ts_ms,
                "timestamp_min":  ts_min,
                "event_type":     ev_type,
                "participant_id": killer_id,
                "victim_id":      victim_id,
                "team_id":        ev_team,
                "victim_team_id": victim_team,
                "assisting_ids":  ",".join(str(x) for x in ev.get("assistingParticipantIds", [])),
                "monster_type":   OBJECTIVE_MONSTER_TYPES.get(monster_raw, monster_raw.lower()) if monster_raw else "",
                "building_type":  BUILDING_KILL_SUBTYPES.get(building_raw, building_raw.lower()) if building_raw else "",
                "ward_type":      ev.get("wardType") or ev.get("ward_type", ""),
                "item_id":        ev.get("itemId", 0),
                "position_x":     int(pos.get("x", 0)) if pos and "x" in pos else 0,
                "position_y":     int(pos.get("y", 0)) if pos and "y" in pos else 0,
            })

    df_tl = pd.DataFrame(tl_rows)
    df_ev = pd.DataFrame(ev_rows)

    # Downcast agresivo de memoria
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
