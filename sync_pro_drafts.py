"""
sync_pro_drafts.py — ETL de drafts profesionales.

Fuentes:
  1. Oracle's Elixir (CSV anual)  → contexto histórico + parche actual
  2. Leaguepedia via mwclient    → parche actual / últimas semanas

Uso:
  python sync_pro_drafts.py                    # Descarga OE + Leaguepedia parche actual
  python sync_pro_drafts.py --oe-only          # Solo Oracle's Elixir
  python sync_pro_drafts.py --patch 15.9       # Forzar parche específico en Leaguepedia
  python sync_pro_drafts.py --year 2025        # Año para OE (default: actual)
"""

from __future__ import annotations

import argparse
import io
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ─────────────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────────────

ROLE_ORDER = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]

# Ligas "de calidad" incluidas en el análisis
QUALITY_LEAGUES = {"LCK", "LPL", "LCS", "LEC", "CBLOL", "VCS", "PCS", "LLA"}

# Alias de ligas en OE → etiqueta canónica
LEAGUE_ALIAS = {
    "LCS": "LCS",
    "LEC": "LEC",
    "EMEA": "LEC",
    "LCK": "LCK",
    "LPL": "LPL",
    "CBLOL": "CBLOL",
    "VCS": "VCS",
    "PCS": "PCS",
    "LLA": "LLA",
}

# Roles OE → canónico
OE_ROLE_MAP = {
    "top": "TOP",
    "jng": "JUNGLE",
    "mid": "MID",
    "bot": "BOT",
    "sup": "SUPPORT",
}

OUTPUT_DIR = Path("data/processed/pro_drafts")
OUTPUT_PARQUET = OUTPUT_DIR / "pro_drafts.parquet"

# Google Drive file IDs del folder público de Oracle's Elixir
# https://drive.google.com/drive/folders/1gLSw0RLjBbtaNy0dgnGQDAZOHIgCe-HH
OE_GDRIVE_FILE_IDS: dict[int, str] = {
    2026: "1hnpbrUpBMS1TZI7IovfpKeZfWJH1Aptm",
    2025: "1tI85fX1zW_T0L9i_T9F6v-A0vM-rZp6Y",
    # Agregar IDs de años anteriores si se necesitan
}


def download_oe_csv(year: int) -> pd.DataFrame:
    """
    Descarga el CSV de Oracle's Elixir desde Google Drive.
    Requiere: pip install gdown
    """
    try:
        import gdown  # type: ignore
    except ImportError:
        logger.error("gdown no instalado. Ejecuta: pip install gdown")
        return pd.DataFrame()

    file_id = OE_GDRIVE_FILE_IDS.get(year)
    if not file_id:
        logger.error(f"No hay file ID configurado para el año {year}. Agrega el ID en OE_GDRIVE_FILE_IDS.")
        return pd.DataFrame()

    url = f"https://drive.google.com/uc?id={file_id}&export=download"
    output_path = OUTPUT_DIR / f"oe_{year}_raw.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info(f"Descargando Oracle's Elixir {year} desde Google Drive (ID: {file_id})...")
    try:
        gdown.download(url, str(output_path), quiet=False)
        df = pd.read_csv(output_path, low_memory=False)
        logger.info(f"OE {year} descargado: {len(df)} filas, {df['gameid'].nunique()} partidas.")
        return df
    except Exception as e:
        logger.error(f"Error descargando OE {year}: {e}")
        return pd.DataFrame()


def parse_oe_csv(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Extrae drafts de las filas OE.

    OE tiene 12 filas por partida:
      - 2 filas con position=="team" → picks/bans del equipo en orden de draft
      - 10 filas de jugadores        → champion + position (top/jng/mid/bot/sup)

    Unificamos: los bans vienen de las filas "team", el rol→champ de las filas de jugador.
    """
    if df_raw.empty:
        return pd.DataFrame()

    required_team_cols = {"gameid", "side", "ban1", "ban2", "ban3", "ban4", "ban5"}
    if not required_team_cols.issubset(df_raw.columns):
        logger.warning("OE CSV no tiene las columnas esperadas. Verificar schema.")
        return pd.DataFrame()

    # Normalizar columnas de league y side
    df_raw["league"] = df_raw.get("league", "UNKNOWN").str.upper().str.strip()
    df_raw["side"]   = df_raw.get("side", "").str.strip()

    # Filtrar solo ligas de calidad
    df_raw = df_raw[df_raw["league"].isin(QUALITY_LEAGUES)].copy()
    if df_raw.empty:
        logger.warning("Ninguna partida de ligas de calidad en el CSV de OE.")
        return pd.DataFrame()

    records = []

    for game_id, grp in df_raw.groupby("gameid"):
        team_rows   = grp[grp["position"].str.lower() == "team"]
        player_rows = grp[grp["position"].str.lower().isin(OE_ROLE_MAP)]

        if len(team_rows) < 2:
            continue

        blue_team_row = team_rows[team_rows["side"].str.lower() == "blue"]
        red_team_row  = team_rows[team_rows["side"].str.lower() == "red"]

        if blue_team_row.empty or red_team_row.empty:
            continue

        b = blue_team_row.iloc[0]
        r = red_team_row.iloc[0]

        # Resultado: en OE "result"=1 si ganaron, 0 si no
        winner = "Blue" if b.get("result", 0) == 1 else "Red"

        # Bans (5 por equipo, OE no distingue fase 1 vs fase 2 en columnas separadas)
        def _safe(row, col):
            v = row.get(col, None)
            return str(v).strip() if pd.notna(v) and str(v).strip() not in ("", "nan") else None

        # Picks por rol (de filas de jugador)
        blue_picks: dict[str, Optional[str]] = {r_: None for r_ in ROLE_ORDER}
        red_picks:  dict[str, Optional[str]] = {r_: None for r_ in ROLE_ORDER}

        for _, prow in player_rows.iterrows():
            role   = OE_ROLE_MAP.get(str(prow.get("position", "")).lower())
            champ  = _safe(prow, "champion")
            side   = str(prow.get("side", "")).strip().lower()
            if role and champ:
                if side == "blue":
                    blue_picks[role] = champ
                elif side == "red":
                    red_picks[role] = champ

        patch_raw = str(b.get("patch", "")).strip()
        # OE a veces trae "15.09" → normalizar a "15.9"
        try:
            parts = patch_raw.split(".")
            patch = f"{int(parts[0])}.{int(parts[1])}" if len(parts) >= 2 else patch_raw
        except Exception:
            patch = patch_raw

        rec = {
            "gameid":     str(game_id),
            "league":     str(b.get("league", "UNKNOWN")).upper(),
            "patch":      patch,
            "date":       str(b.get("date", "")),
            "blue_team":  str(b.get("teamname", "")),
            "red_team":   str(r.get("teamname", "")),
            "winner":     winner,
            # Bans fase 1 (1-3) y fase 2 (4-5)
            "blue_ban1": _safe(b, "ban1"), "blue_ban2": _safe(b, "ban2"),
            "blue_ban3": _safe(b, "ban3"), "blue_ban4": _safe(b, "ban4"),
            "blue_ban5": _safe(b, "ban5"),
            "red_ban1":  _safe(r, "ban1"), "red_ban2":  _safe(r, "ban2"),
            "red_ban3":  _safe(r, "ban3"), "red_ban4":  _safe(r, "ban4"),
            "red_ban5":  _safe(r, "ban5"),
            # Picks por rol
            "blue_top":  blue_picks["TOP"],    "blue_jg":  blue_picks["JUNGLE"],
            "blue_mid":  blue_picks["MID"],    "blue_bot": blue_picks["BOT"],
            "blue_sup":  blue_picks["SUPPORT"],
            "red_top":   red_picks["TOP"],     "red_jg":   red_picks["JUNGLE"],
            "red_mid":   red_picks["MID"],     "red_bot":  red_picks["BOT"],
            "red_sup":   red_picks["SUPPORT"],
            # Picks secuenciales (orden de draft)
            "blue_pick1": _safe(b, "pick1"), "blue_pick2": _safe(b, "pick2"),
            "blue_pick3": _safe(b, "pick3"), "blue_pick4": _safe(b, "pick4"),
            "blue_pick5": _safe(b, "pick5"),
            "red_pick1":  _safe(r, "pick1"), "red_pick2":  _safe(r, "pick2"),
            "red_pick3":  _safe(r, "pick3"), "red_pick4":  _safe(r, "pick4"),
            "red_pick5":  _safe(r, "pick5"),
            "source": "oracleselixir",
        }
        records.append(rec)

    df = pd.DataFrame(records)
    logger.info(f"OE parseado: {len(df)} partidas.")
    return df


# ─────────────────────────────────────────────────────────────────────
# LEAGUEPEDIA (mwclient) — INCREMENTAL
# ─────────────────────────────────────────────────────────────────────

# Mapeo leagues en Leaguepedia → etiqueta canónica
LP_TOURNAMENT_LEAGUE_MAP = {
    "LCK": "LCK",
    "LPL": "LPL",
    "LCS": "LCS",
    "LEC": "LEC",
    "EMEA": "LEC",
    "CBLOL": "CBLOL",
    "VCS": "VCS",
    "PCS": "PCS",
    "LLA": "LLA",
}


def _detect_league_from_tournament(tournament: str) -> Optional[str]:
    t_upper = tournament.upper()
    for key, val in LP_TOURNAMENT_LEAGUE_MAP.items():
        if key in t_upper:
            return val
    return None


def fetch_leaguepedia(patches: list[str], leagues: list[str] | None = None, delay: float = 2.0) -> pd.DataFrame:
    """
    Descarga datos del parche actual desde Leaguepedia via mwclient.
    Usa dos queries separadas (ScoreboardGames + ScoreboardPlayers) para evitar
    rate limiting por queries con JOIN.
    """
    try:
        import mwclient  # type: ignore
    except ImportError:
        logger.error("mwclient no instalado. Ejecuta: pip install mwclient")
        return pd.DataFrame()

    leagues_filter = set(leagues) if leagues else QUALITY_LEAGUES
    site = mwclient.Site("lol.fandom.com", path="/")

    all_records = []

    for patch in patches:
        logger.info(f"Leaguepedia: buscando partidas del parche {patch}...")
        offset = 0
        limit  = 500
        games_meta: dict[str, dict] = {}

        # ── Query 1: ScoreboardGames (metadata + bans)
        while True:
            time.sleep(delay)
            try:
                result = site.api(
                    "cargoquery",
                    tables="ScoreboardGames",
                    fields="GameId,Tournament,Patch,Team1,Team2,Winner,DateTime__UTC,BansTeam1,BansTeam2,PicksTeam1,PicksTeam2",
                    where=f'Patch="{patch}"',
                    limit=limit,
                    offset=offset,
                )
            except Exception as e:
                logger.warning(f"Leaguepedia ScoreboardGames error (patch={patch}): {e}")
                time.sleep(5)
                break

            rows = result.get("cargoquery", [])
            if not rows:
                break

            for row in rows:
                d = row.get("title", {})
                gid = d.get("GameId", "").strip()
                if not gid:
                    continue
                league = _detect_league_from_tournament(d.get("Tournament", ""))
                if not league or league not in leagues_filter:
                    continue
                games_meta[gid] = {
                    "tournament": d.get("Tournament", ""),
                    "patch":      d.get("Patch", patch),
                    "team1":      d.get("Team1", ""),
                    "team2":      d.get("Team2", ""),
                    "winner":     d.get("Winner", ""),
                    "date":       d.get("DateTime UTC", ""),
                    "bans_t1":    d.get("BansTeam1", ""),
                    "bans_t2":    d.get("BansTeam2", ""),
                    "picks_t1":   d.get("PicksTeam1", ""),
                    "picks_t2":   d.get("PicksTeam2", ""),
                    "league":     league,
                    "players":    [],
                }

            offset += limit
            if len(rows) < limit:
                break

        if not games_meta:
            logger.info(f"Sin partidas para patch {patch} en ligas objetivo.")
            continue

        logger.info(f"Patch {patch}: {len(games_meta)} partidas encontradas. Descargando jugadores...")

        # ── Query 2: ScoreboardPlayers (role → champion)
        game_ids_list = list(games_meta.keys())
        chunk_size = 50   # Procesar en lotes para evitar queries enormes

        for i in range(0, len(game_ids_list), chunk_size):
            chunk = game_ids_list[i:i + chunk_size]
            ids_sql = ",".join(f'"{g}"' for g in chunk)
            time.sleep(delay)
            try:
                result = site.api(
                    "cargoquery",
                    tables="ScoreboardPlayers",
                    fields="GameId,Name,Role,Champion,Team",
                    where=f"GameId IN ({ids_sql})",
                    limit=500,
                )
            except Exception as e:
                logger.warning(f"Leaguepedia ScoreboardPlayers error (chunk {i}): {e}")
                time.sleep(5)
                continue

            for row in result.get("cargoquery", []):
                d = row.get("title", {})
                gid = d.get("GameId", "").strip()
                if gid in games_meta:
                    games_meta[gid]["players"].append({
                        "name":  d.get("Name", ""),
                        "role":  d.get("Role", ""),
                        "champ": d.get("Champion", ""),
                        "team":  d.get("Team", ""),
                    })

        # ── Convertir a records
        lp_role_map = {
            "Top": "TOP", "Jungle": "JUNGLE", "Mid": "MID",
            "Bot": "BOT", "Support": "SUPPORT",
        }

        def parse_bans(bans_str: str) -> list[Optional[str]]:
            parts = [b.strip() for b in str(bans_str).split(",")]
            while len(parts) < 5:
                parts.append(None)
            return [p if p else None for p in parts[:5]]

        def parse_picks(picks_str: str) -> list[Optional[str]]:
            if not picks_str:
                return [None] * 5
            parts = [p.strip() for p in str(picks_str).split(",")]
            while len(parts) < 5:
                parts.append(None)
            return [p if p else None for p in parts[:5]]

        for gid, data in games_meta.items():
            blue_team = data["team1"]
            red_team  = data["team2"]
            winner    = "Blue" if data["winner"] == blue_team else "Red"

            b_bans = parse_bans(data["bans_t1"])
            r_bans = parse_bans(data["bans_t2"])
            b_picks = parse_picks(data.get("picks_t1", ""))
            r_picks = parse_picks(data.get("picks_t2", ""))

            blue_picks: dict[str, Optional[str]] = {r_: None for r_ in ROLE_ORDER}
            red_picks:  dict[str, Optional[str]] = {r_: None for r_ in ROLE_ORDER}

            for player in data["players"]:
                role  = lp_role_map.get(player["role"])
                champ = player["champ"].strip() if player["champ"] else None
                team  = player["team"].strip() if player["team"] else None
                if not role or not champ:
                    continue
                if team == blue_team:
                    blue_picks[role] = champ
                elif team == red_team:
                    red_picks[role] = champ

            p = data["patch"].strip()
            try:
                parts = p.split(".")
                patch_norm = f"{int(parts[0])}.{int(parts[1])}" if len(parts) >= 2 else p
            except Exception:
                patch_norm = p

            rec = {
                "gameid":    gid,
                "league":    data["league"],
                "patch":     patch_norm,
                "date":      data["date"],
                "blue_team": blue_team,
                "red_team":  red_team,
                "winner":    winner,
                "blue_ban1": b_bans[0], "blue_ban2": b_bans[1], "blue_ban3": b_bans[2],
                "blue_ban4": b_bans[3], "blue_ban5": b_bans[4],
                "red_ban1":  r_bans[0], "red_ban2":  r_bans[1], "red_ban3":  r_bans[2],
                "red_ban4":  r_bans[3], "red_ban5":  r_bans[4],
                "blue_top": blue_picks["TOP"],    "blue_jg": blue_picks["JUNGLE"],
                "blue_mid": blue_picks["MID"],    "blue_bot": blue_picks["BOT"],
                "blue_sup": blue_picks["SUPPORT"],
                "red_top":  red_picks["TOP"],     "red_jg":  red_picks["JUNGLE"],
                "red_mid":  red_picks["MID"],     "red_bot":  red_picks["BOT"],
                "red_sup":  red_picks["SUPPORT"],
                # Picks secuenciales (orden de draft)
                "blue_pick1": b_picks[0], "blue_pick2": b_picks[1], "blue_pick3": b_picks[2],
                "blue_pick4": b_picks[3], "blue_pick5": b_picks[4],
                "red_pick1":  r_picks[0], "red_pick2":  r_picks[1], "red_pick3":  r_picks[2],
                "red_pick4":  r_picks[3], "red_pick5":  r_picks[4],
                "source": "leaguepedia",
            }
            all_records.append(rec)

    df = pd.DataFrame(all_records)
    logger.info(f"Leaguepedia: {len(df)} partidas descargadas.")
    return df


# ─────────────────────────────────────────────────────────────────────
# MERGE Y PERSISTENCIA
# ─────────────────────────────────────────────────────────────────────

def merge_and_save(df_oe: pd.DataFrame, df_lp: pd.DataFrame) -> pd.DataFrame:
    """
    Une OE + Leaguepedia, deduplica por gameid, guarda en Parquet.
    Leaguepedia tiene prioridad para partidas del parche actual.
    """
    dfs = [df for df in [df_oe, df_lp] if not df.empty]
    if not dfs:
        logger.error("No hay datos para guardar.")
        return pd.DataFrame()

    df_all = pd.concat(dfs, ignore_index=True)

    # Dedup: si hay el mismo gameid de dos fuentes, quedarse con Leaguepedia (más fresco)
    df_all = df_all.sort_values("source", ascending=False)  # leaguepedia > oracleselixir alfa
    df_all = df_all.drop_duplicates(subset="gameid", keep="first").reset_index(drop=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df_all.to_parquet(OUTPUT_PARQUET, index=False)
    logger.info(
        f"✅ Guardado en {OUTPUT_PARQUET}: {len(df_all)} partidas | "
        f"Ligas: {df_all['league'].value_counts().to_dict()} | "
        f"Patches: {sorted(df_all['patch'].unique())}"
    )
    return df_all


# ─────────────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────────────

def get_current_patch() -> str:
    """Obtiene el parche actual de Riot DDragon."""
    try:
        r = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=5)
        if r.status_code == 200:
            raw = r.json()[0]
            parts = raw.split(".")
            return f"{int(parts[0])}.{int(parts[1])}"
    except Exception:
        pass
    return "15.9"


def get_adjacent_patches(patch: str, n: int = 2) -> list[str]:
    """Retorna el parche actual y los N anteriores. Ej: 15.9 → [15.9, 15.8, 15.7]"""
    try:
        major, minor = map(int, patch.split("."))
        patches = []
        for i in range(n + 1):
            m = minor - i
            if m > 0:
                patches.append(f"{major}.{m}")
            else:
                patches.append(f"{major - 1}.{24 + m}")  # rough fallback
        return patches
    except Exception:
        return [patch]


# ─────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sincronización de drafts pro")
    parser.add_argument("--oe-only",  action="store_true", help="Solo Oracle's Elixir")
    parser.add_argument("--lp-only",  action="store_true", help="Solo Leaguepedia")
    parser.add_argument("--patch",    type=str, default=None, help="Patch para Leaguepedia (default: actual)")
    parser.add_argument("--year",     type=int, default=2026, help="Año para OE (default: 2026)")
    parser.add_argument("--lp-patches", type=int, default=3, help="Últimos N patches desde Leaguepedia")
    args = parser.parse_args()

    df_oe = pd.DataFrame()
    df_lp = pd.DataFrame()

    # Oracle's Elixir
    if not args.lp_only:
        df_raw = download_oe_csv(args.year)
        if not df_raw.empty:
            df_oe = parse_oe_csv(df_raw)

    # Leaguepedia (incremental, parche actual)
    if not args.oe_only:
        current = args.patch or get_current_patch()
        patches_to_fetch = get_adjacent_patches(current, n=args.lp_patches - 1)
        logger.info(f"Leaguepedia: patches a descargar = {patches_to_fetch}")
        df_lp = fetch_leaguepedia(patches_to_fetch)

    merge_and_save(df_oe, df_lp)


if __name__ == "__main__":
    main()
