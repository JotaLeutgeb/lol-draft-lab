"""
sync_player.py — ETL individual para Scout Protocol.

Uso:
    python sync_player.py --profile profiles/franfon.yaml
    python sync_player.py --profile profiles/franfon.yaml --dry-run
    python sync_player.py --profile profiles/franfon.yaml --matches 50

Flujo:
    1. Cargar perfil YAML
    2. Descargar N partidas del jugador vía Riot API
    3. Calcular métricas base + consistencia + peer ranking
    4. Upsert → scout_participants, scout_timeline, scout_events, matches
    5. Upsert → scout_snapshots (1 fila por partida)
    6. Upsert → scout_champion_pool (stats por campeón)
    7. Actualizar scout_profiles.last_synced
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

from src.config_scout import load_profile, PlayerProfile
from src.data_loader_scout import ScoutMatchClient, normalize_match, normalize_timeline
from src.features_scout import (
    compute_player_metrics,
    compute_objective_control,
    compute_gold_diff,
    compute_consistency_metrics,
    compute_peer_ranking,
    compute_impact_score_individual,
    compute_kill_conversion,
    filter_player_rows,
)
from src.analysis_scout import compute_champion_pool_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# UPSERTERS
# ──────────────────────────────────────────────────────────────────

PARTICIPANT_COLUMNS = [
    "match_id", "participant_id", "puuid", "game_name", "tag_line",
    "team_id", "role", "champion", "kills", "deaths", "assists",
    "gold_earned", "gold_spent", "total_damage", "physical_damage",
    "magic_damage", "true_damage", "damage_taken", "damage_mitigated",
    "damage_buildings", "vision_score", "wards_placed", "wards_killed",
    "control_wards", "cs", "total_heal", "time_cc", "duration_minutes",
    "result", "is_custom", "first_blood",
    # Métricas derivadas
    "kda", "cs_per_min", "gold_per_min", "damage_per_min", "vision_per_min",
    "cc_per_min", "damage_taken_per_min", "kill_participation", "damage_per_gold",
    "objective_control", "kill_conversion", "impact_score",
    "pilar_combat_efficiency", "pilar_map_pressure",
    "pilar_tactical_utility", "pilar_consistency",
    "consistency_score", "peer_rank", "resilience_index",
]

TIMELINE_COLUMNS = [
    "match_id", "participant_id", "timestamp_ms", "timestamp_min",
    "total_gold", "cs", "xp", "level", "pos_x", "pos_y",
    "role", "team_id",  # Agregado para soportar filtrado por rol en visualizaciones
]

EVENTS_COLUMNS = [
    "match_id", "participant_id", "victim_id", "team_id", "victim_team_id",
    "timestamp_ms", "timestamp_min", "event_type", "monster_type",
    "building_type", "ward_type", "item_id", "position_x", "position_y", "assisting_ids",
]


def _safe_records(df: pd.DataFrame, columns: list[str]) -> list[dict]:
    """Selecciona columnas disponibles, convierte NaN→None y retorna records."""
    import math
    available = [c for c in columns if c in df.columns]
    sub = df[available].copy()
    
    # Convertir tipos numpy a Python nativos para Supabase
    for col in sub.select_dtypes(include=["bool"]).columns:
        sub[col] = sub[col].astype(object)
        
    int_cols = [
        "participant_id", "team_id", "kills", "deaths", "assists", "gold_earned",
        "gold_spent", "total_damage", "physical_damage", "magic_damage", "true_damage",
        "damage_taken", "damage_mitigated", "damage_buildings", "vision_score",
        "wards_placed", "wards_killed", "control_wards", "cs", "total_heal", "time_cc", "peer_rank"
    ]
        
    records = []
    for _, row in sub.iterrows():
        r = row.to_dict()
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                r[k] = None
            elif k in int_cols and r[k] is not None:
                try:
                    r[k] = int(float(v))
                except:
                    pass
        records.append(r)
    return records


def upsert_matches(supabase, df_p: pd.DataFrame, patch: str) -> None:
    from datetime import datetime, timezone
    
    match_rows = df_p.drop_duplicates("match_id")[["match_id"]].copy()
    match_rows["patch_version"] = patch
    match_rows["platform"] = "la2"
    match_rows["duration_min"] = df_p.drop_duplicates("match_id")["duration_minutes"].values
    match_rows["is_processed"] = True
    
    # Convertir game_creation_ms a timestamp ISO
    if "game_creation_ms" in df_p.columns:
        game_creation = df_p.drop_duplicates("match_id")["game_creation_ms"].values
        match_rows["game_timestamp"] = [
            datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms > 0 else None
            for ms in game_creation
        ]

    import math
    records = []
    for _, row in match_rows.iterrows():
        r = row.to_dict()
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                r[k] = None
        records.append(r)
    for i in range(0, len(records), 50):
        supabase.table("matches").upsert(records[i:i+50], on_conflict="match_id").execute()
    logger.info(f"✅ {len(records)} partidas upserted en matches")


def upsert_participants(supabase, df_p: pd.DataFrame) -> None:
    records = _safe_records(df_p, PARTICIPANT_COLUMNS)
    for i in range(0, len(records), 100):
        supabase.table("scout_participants").upsert(
            records[i:i+100], on_conflict="match_id,participant_id"
        ).execute()
    logger.info(f"✅ {len(records)} participantes upserted")


def upsert_timeline(supabase, df_t: pd.DataFrame) -> None:
    if df_t.empty:
        return
    # Drop duplicates on the conflict constraint to avoid PostgreSQL 21000 ON CONFLICT error
    df_t = df_t.drop_duplicates(subset=["match_id", "participant_id", "timestamp_ms"])
    records = _safe_records(df_t, TIMELINE_COLUMNS)
    for i in range(0, len(records), 500):
        supabase.table("scout_timeline").upsert(
            records[i:i+500], on_conflict="match_id,participant_id,timestamp_ms"
        ).execute()
    logger.info(f"✅ {len(records)} frames de timeline upserted")


def upsert_events(supabase, df_e: pd.DataFrame) -> None:
    if df_e.empty:
        return
    # Drop duplicates on the conflict constraint to avoid PostgreSQL 21000 ON CONFLICT error
    df_e = df_e.drop_duplicates(subset=["match_id", "participant_id", "timestamp_ms", "event_type"])
    records = _safe_records(df_e, EVENTS_COLUMNS)
    for i in range(0, len(records), 500):
        supabase.table("scout_events").upsert(
            records[i:i+500], on_conflict="match_id,participant_id,timestamp_ms,event_type"
        ).execute()
    logger.info(f"✅ {len(records)} eventos upserted")


def upsert_profile(supabase, profile: PlayerProfile) -> int:
    """Upsert el perfil y retorna su ID en Supabase."""
    from datetime import datetime, timezone
    
    # Obtener historical_names (excluir el nombre actual)
    all_names = getattr(profile, "all_game_names", [profile.game_name])
    historical = [n for n in all_names if n != profile.game_name]
    
    record = {
        "riot_id":         profile.riot_id,
        "display_name":    profile.display_name,
        "primary_role":    profile.primary_role,
        "valid_roles":     profile.valid_roles,
        "platform":        profile.platform,
        "queue_filter":    profile.queue_filter,
        "historical_names": historical if historical else None,
        "last_synced":     datetime.now(timezone.utc).isoformat(),
    }
    resp = supabase.table("scout_profiles").upsert(record, on_conflict="riot_id").execute()
    profile_id = resp.data[0]["id"] if resp.data else None
    if profile_id is None:
        resp2 = supabase.table("scout_profiles").select("id").eq("riot_id", profile.riot_id).execute()
        profile_id = resp2.data[0]["id"] if resp2.data else 1
    logger.info(f"✅ Perfil '{profile.riot_id}' sincronizado (id={profile_id})")
    return int(profile_id)


def upsert_snapshots(supabase, df_player: pd.DataFrame, profile_id: int, patch: str) -> None:
    """Upsert 1 fila por partida en scout_snapshots."""
    if df_player.empty:
        return

    cols = {
        "profile_id":        profile_id,
        "patch":             patch,
    }
    snap_cols = [
        "match_id", "role", "champion", "result", "duration_minutes",
        "impact_score", "kda", "cs_per_min", "damage_per_min",
        "vision_per_min", "gold_per_min", "kill_participation",
        "kill_conversion",
        "pilar_combat_efficiency", "pilar_map_pressure",
        "pilar_tactical_utility", "pilar_consistency",
        "consistency_score", "peer_rank",
    ]
    available = [c for c in snap_cols if c in df_player.columns]
    snap = df_player[available].copy()
    snap["profile_id"] = profile_id
    snap["patch"] = patch
    snap = snap.rename(columns={
        "role":                 "role_played",
        "pilar_combat_efficiency": "pilar_combat",
        "pilar_map_pressure":      "pilar_map",
        "pilar_tactical_utility":  "pilar_utility",
    })

    import math
    records = []
    int_cols = ["profile_id", "peer_rank"]
    for _, row in snap.iterrows():
        r = row.to_dict()
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                r[k] = None
            elif k in int_cols and r[k] is not None:
                try:
                    r[k] = int(float(v))
                except:
                    pass
        records.append(r)
    for i in range(0, len(records), 100):
        supabase.table("scout_snapshots").upsert(
            records[i:i+100], on_conflict="profile_id,match_id"
        ).execute()
    logger.info(f"✅ {len(records)} snapshots upserted")


def upsert_champion_pool(supabase, df_pool: pd.DataFrame, profile_id: int) -> None:
    if df_pool.empty:
        return

    df_pool = df_pool.copy()
    df_pool["profile_id"] = profile_id
    col_map = {
        "avg_impact":      "avg_impact",
        "avg_kda":         "avg_kda",
        "avg_cs_min":      "avg_cs_min",
        "avg_damage_min":  "avg_damage_min",
        "avg_vision_min":  "avg_vision_min",
        "avg_gold_min":    "avg_gold_min",
        "win_rate":        "win_rate",
        "n_games":         "n_games",
        "consistency":     "consistency",
    }
    keep = ["profile_id", "champion"] + (["role"] if "role" in df_pool.columns else [])
    keep += [k for k in col_map if k in df_pool.columns]
    df_pool = df_pool[keep].rename(columns=col_map)

    import math
    records = []
    int_cols = ["profile_id", "n_games"]
    for _, row in df_pool.iterrows():
        r = row.to_dict()
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                r[k] = None
            elif k in int_cols and r[k] is not None:
                try:
                    r[k] = int(float(v))
                except:
                    pass
        records.append(r)
    for i in range(0, len(records), 100):
        supabase.table("scout_champion_pool").upsert(
            records[i:i+100], on_conflict="profile_id,champion,role"
        ).execute()
    logger.info(f"✅ {len(records)} entradas de champion pool upserted")


# ──────────────────────────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────────────────────────

def sync_player(
    profile: PlayerProfile,
    matches: int | None = None,
    dry_run: bool = False,
) -> None:
    logger.info(f"🚀 Iniciando sync para {profile.riot_id} ({profile.primary_role})")

    riot_key = os.environ.get("RIOT_API_KEY", "").strip()
    if not riot_key or riot_key.startswith("RGAPI-xxx"):
        logger.error("❌ RIOT_API_KEY no configurada en .env")
        return

    client = ScoutMatchClient(
        api_key=riot_key,
        platform=profile.platform,
        region=profile.region,
    )

    # Override de match_count si se pasa por CLI
    if matches:
        profile.match_count = matches  # type: ignore

    # 1. Descargar partidas
    df_p, df_t, df_e = client.load_player_matches(profile)
    if df_p.empty:
        logger.error("❌ No se descargaron partidas. Verificar Riot ID y API key.")
        return

    n_matches = df_p["match_id"].nunique()
    logger.info(f"📊 {n_matches} partidas descargadas, {len(df_p)} registros de participantes")

    # Detectar patch actual
    patch = "unknown"
    try:
        import requests as _req
        r = _req.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=5)
        if r.status_code == 200:
            patch = ".".join(r.json()[0].split(".")[:2])
    except Exception:
        pass

    # 2. Compute métricas base (10 jugadores para peer ranking correcto)
    df_p = compute_player_metrics(df_p)

    # 3. Objectives
    df_obj = compute_objective_control(df_e, df_p)

    # 3.5 Kill Conversion
    df_kill_conv = compute_kill_conversion(df_e, df_p)

    # 4. Filtrar al jugador analizado
    all_names = getattr(profile, "all_game_names", [profile.game_name])
    logger.info(f"🔍 Buscando jugador: nombres={all_names}, role={profile.primary_role}")
    
    # Debug: mostrar game_names únicos en los datos
    if not df_p.empty and "game_name" in df_p.columns:
        unique_names = df_p["game_name"].unique()
        logger.info(f"🔍 Game names en datos descargados: {unique_names[:10]}")  # Primeros 10
        
        # Verificar si alguno de los nombres del jugador está en los datos
        all_names_lower = [n.lower() for n in all_names]
        name_matches = df_p[df_p["game_name"].str.lower().isin(all_names_lower)]
        logger.info(f"🔍 Partidas con nombres {all_names}: {len(name_matches)}")
        
        if not name_matches.empty and "role" in df_p.columns:
            roles_played = name_matches["role"].value_counts().to_dict()
            logger.info(f"🔍 Roles jugados: {roles_played}")
    
    df_player = filter_player_rows(df_p, profile)
    if df_player.empty:
        logger.error(f"❌ No se encontraron filas para {profile.riot_id} en los datos descargados.")
        logger.error(f"❌ Buscado: game_name='{profile.game_name}' (lower: '{profile.game_name.lower()}'), valid_roles={profile.valid_roles}")
        return

    # 5. Consistency (sobre las partidas del jugador)
    consistency_data = compute_consistency_metrics(df_player)
    consistency_score = consistency_data["consistency_score"]
    logger.info(f"🎯 Consistency Score: {consistency_score:.3f} (CV={consistency_data['overall_cv']:.3f})")

    # 6. Impact Score (sobre 10 jugadores para normalización correcta)
    df_p = compute_impact_score_individual(df_p, df_obj, df_kill_conv, consistency_score)

    # 7. Peer Ranking
    peer_ranks = compute_peer_ranking(df_p, profile)
    if not peer_ranks.empty:
        df_p = df_p.merge(peer_ranks, on="match_id", how="left")
        df_player = filter_player_rows(df_p, profile)

    # Propagar consistency_score a columna
    df_p["consistency_score"] = consistency_score

    # 8. Champion Pool Summary
    df_player_final = filter_player_rows(df_p, profile)
    df_champ_pool = compute_champion_pool_summary(df_player_final)

    # 9. Gold Diff (opcional, no bloquea)
    df_gd = pd.DataFrame()
    if not df_t.empty:
        try:
            df_gd = compute_gold_diff(df_t, df_p)
        except Exception as e:
            logger.warning(f"Gold diff falló: {e}")

    # ── DRY RUN: mostrar resumen sin subir ──────────────────────────
    if dry_run:
        logger.info("🔍 DRY RUN — No se escribirá en Supabase")
        logger.info(f"  Partidas: {df_p['match_id'].nunique()}")
        logger.info(f"  Jugador rows: {len(df_player_final)}")
        logger.info(f"  Consistency: {consistency_score:.3f}")
        logger.info(f"  Champion pool entries: {len(df_champ_pool)}")
        if not df_player_final.empty:
            cols = ["match_id", "champion", "role", "impact_score", "kda", "peer_rank"]
            avail = [c for c in cols if c in df_player_final.columns]
            logger.info(f"\n{df_player_final[avail].to_string(index=False)}")
        return

    # ── UPLOAD A SUPABASE ───────────────────────────────────────────
    from supabase import create_client
    sb_url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")

    if not sb_url or not sb_key:
        logger.error("❌ Supabase URL/KEY no configuradas en .env")
        return

    supabase = create_client(sb_url, sb_key)

    upsert_matches(supabase, df_p, patch)
    upsert_participants(supabase, df_p)
    upsert_timeline(supabase, df_t)
    upsert_events(supabase, df_e)

    profile_id = upsert_profile(supabase, profile)
    upsert_snapshots(supabase, df_player_final, profile_id, patch)
    upsert_champion_pool(supabase, df_champ_pool, profile_id)

    logger.info(f"🏁 Sync completo para {profile.riot_id}")


# ──────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scout Protocol — ETL individual")
    parser.add_argument("--profile", help="Ruta al YAML del perfil (ej: profiles/franfon.yaml)")
    parser.add_argument("--riot-id", help="Riot ID directo (ej: 'AEVI ray#ray')")
    parser.add_argument("--platform", default="la2", help="Plataforma (default: la2)")
    parser.add_argument("--region", default="americas", help="Región (default: americas)")
    parser.add_argument("--role", default="FILL", choices=["TOP", "JUNGLE", "MID", "ADC", "SUPPORT", "FILL"], help="Rol principal (solo si el perfil es nuevo)")
    parser.add_argument("--matches", type=int, default=None, help="Override de cantidad de partidas a descargar (default: 500 para --riot-id, 30 para --profile)")
    parser.add_argument("--season", default="2026s1", choices=["2026s1", "2026s2", "all"], help="Season a sincronizar (default: 2026s1)")
    parser.add_argument("--dry-run", action="store_true", help="Ejecutar sin escribir en Supabase")
    args = parser.parse_args()

    # Load profile from YAML or create from riot_id
    if args.profile:
        profile = load_profile(args.profile)
    elif args.riot_id:
        # Create profile object from riot_id
        from supabase import create_client
        sb_url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        sb_key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        
        if not sb_url or not sb_key:
            logger.error("Supabase no configurado. Define SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY")
            sys.exit(1)
        
        supabase = create_client(sb_url, sb_key)
        
        # Get profile from DB (case-insensitive)
        prof_res = supabase.table("scout_profiles").select("*").ilike("riot_id", args.riot_id).execute()
        
        if not prof_res.data:
            # Perfil no existe en DB → validar con Riot API y crear
            logger.info(f"Perfil no encontrado en DB. Validando '{args.riot_id}' con Riot API...")
            
            api_key = os.environ.get("RIOT_API_KEY")
            if not api_key:
                logger.error("RIOT_API_KEY no configurada. No se puede crear el perfil automáticamente.")
                sys.exit(1)
            
            from urllib.parse import quote
            game_name, tag_line = args.riot_id.split("#", 1)
            url = f"https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{quote(game_name)}/{quote(tag_line)}"
            
            import requests as _requests
            resp = _requests.get(url, headers={"X-Riot-Token": api_key}, timeout=10)
            
            if resp.status_code == 404:
                logger.error(f"Riot ID no existe: {args.riot_id}")
                sys.exit(1)
            elif resp.status_code != 200:
                logger.error(f"Error Riot API: {resp.status_code} — {resp.text}")
                sys.exit(1)
            
            account = resp.json()
            canonical_riot_id = f"{account['gameName']}#{account['tagLine']}"
            logger.info(f"Riot ID validado: {canonical_riot_id}. Creando perfil en DB...")
            
            new_profile = {
                "riot_id": canonical_riot_id,
                "display_name": account["gameName"],
                "primary_role": args.role,
                "platform": args.platform,
                "queue_filter": [420],
                "match_count": 30,
            }
            insert_res = supabase.table("scout_profiles").insert(new_profile).execute()
            
            if not insert_res.data:
                logger.error(f"Error al crear perfil en DB.")
                sys.exit(1)
            
            profile_data = insert_res.data[0]
            logger.info(f"✅ Perfil creado en DB: {canonical_riot_id}")
        else:
            profile_data = prof_res.data[0]
        
        # Create PlayerProfile object
        # Calcular season_start_ts (epoch segundos) según --season
        from datetime import datetime, timezone
        SEASON_STARTS = {
            "2026s1": int(datetime(2026, 1, 8, tzinfo=timezone.utc).timestamp()),
            "2026s2": int(datetime(2026, 4, 23, tzinfo=timezone.utc).timestamp()),
            "all":    None,
        }
        season_start_ts = SEASON_STARTS.get(args.season)
        
        # Para una season entera, usar match_count alto
        default_count = 500 if args.season != "all" else 1000

        profile = PlayerProfile.from_dict({
            "riot_id": profile_data["riot_id"],
            "display_name": profile_data["display_name"],
            "primary_role": profile_data["primary_role"],
            "platform": profile_data.get("platform", args.platform),
            "region": args.region,
            "queue_filter": profile_data.get("queue_filter", [420]),
            "match_count": args.matches or default_count,
            "season_start_ts": season_start_ts,
        })
        
        if season_start_ts:
            logger.info(f"📅 Sincronizando desde season {args.season} ({datetime.fromtimestamp(season_start_ts).strftime('%Y-%m-%d')})")
    else:
        logger.error("Debes especificar --profile o --riot-id")
        sys.exit(1)
    
    sync_player(profile, matches=args.matches, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
