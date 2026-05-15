"""
sync_pro_builds.py — ETL de builds profesionales desde gol.gg

Flujo:
  1. Por cada torneo configurado: scrapea el matchlist (requests + BS4) → extrae game_ids
  2. Por cada game_id: usa Playwright para cargar la página de builds y clic en los 10
     jugadores → extrae ítems con timestamps
  3. Guarda tabla de hechos plana en data/processed/pro_builds/pro_builds.parquet
     (1 fila = 1 jugador en 1 partida)

Uso:
  python sync_pro_builds.py                              # Todos los torneos
  python sync_pro_builds.py --tournament "LPL 2026 Split 2"
  python sync_pro_builds.py --game-id 77949
  python sync_pro_builds.py --resume                    # Saltea game_ids ya scrapeados
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ─────────────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────────────

BASE_URL = "https://gol.gg"

OUTPUT_DIR = Path("data/processed/pro_builds")
OUTPUT_PARQUET = OUTPUT_DIR / "pro_builds.parquet"
SCRAPED_IDS_FILE = OUTPUT_DIR / "scraped_ids.json"

ROLE_ORDER = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]

# Botones 1-5 = Blue side (TOP, JG, MID, BOT, SUP)
# Botones 6-10 = Red side (TOP, JG, MID, BOT, SUP)
BUTTON_TO_SIDE_ROLE = {
    1: ("Blue", "TOP"), 2: ("Blue", "JUNGLE"), 3: ("Blue", "MID"),
    4: ("Blue", "BOT"), 5: ("Blue", "SUPPORT"),
    6: ("Red", "TOP"),  7: ("Red", "JUNGLE"),  8: ("Red", "MID"),
    9: ("Red", "BOT"),  10: ("Red", "SUPPORT"),
}

# Ítems consumibles/de starter que no queremos en el "core"
IGNORED_ITEMS = {
    "Health Potion", "Refillable Potion", "Corrupting Potion",
    "Stealth Ward", "Control Ward", "Oracle Lens",
    "Farsight Alteration", "Elixir of Iron", "Elixir of Sorcery",
    "Elixir of Wrath", "Tear of the Goddess",  # a veces aparece como primer "ítem"
}

# Torneos a scrapear — formato: nombre en URL de gol.gg
# Para agregar torneos: buscar en https://gol.gg/tournament/list/
TOURNAMENTS: list[dict] = [
    {"name": "LCK 2026 Rounds 1-2", "league": "LCK"},
    {"name": "LEC 2026 Spring Season", "league": "LEC"},
    {"name": "LCS 2026 Spring", "league": "LCS"},
    {"name": "LCP 2026 Split 2", "league": "LCP"},
    {"name": "First Stand 2026", "league": "INT"},
    {"name": "Americas Cup 2026", "league": "LTA"},
    {"name": "LES 2026 Spring", "league": "LES"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _load_item_id_map() -> dict[str, str]:
    """Descarga el mapa item_id -> nombre desde DDragon (usado para resolver IDs numéricos)."""
    try:
        resp = requests.get(
            "https://ddragon.leagueoflegends.com/api/versions.json", timeout=5
        )
        if resp.status_code != 200:
            return {}
        latest = resp.json()[0]
        item_resp = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{latest}/data/en_US/item.json",
            timeout=10,
        )
        if item_resp.status_code != 200:
            return {}
        data = item_resp.json().get("data", {})
        # key = ID numérico (str), value = {"name": "Infinity Edge", ...}
        return {k: v["name"] for k, v in data.items()}
    except Exception as e:
        logger.warning(f"No se pudo cargar el mapa de ítems de DDragon: {e}")
        return {}


# Cargado una vez al iniciar el proceso
_ITEM_ID_MAP: dict[str, str] = {}  # Se llena en main() o al primer uso


# ─────────────────────────────────────────────────────────────────────
# STEP 1: MATCHLIST SCRAPING (requests + BS4, sin JS)
# ─────────────────────────────────────────────────────────────────────

def fetch_game_ids_from_tournament(tournament_name: str) -> list[dict]:
    """
    Extrae todos los game_ids y patches de la matchlist de un torneo.
    Implementa un parseo robusto que respeta los colspans de los headers.
    """
    url_name = quote(tournament_name)
    url = f"{BASE_URL}/tournament/tournament-matchlist/{url_name}/"

    logger.info(f"Descargando matchlist: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"  HTTP {resp.status_code} para {tournament_name}")
            return []
    except Exception as e:
        logger.error(f"  Error de red al descargar {tournament_name}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="table_list")
    if not table:
        logger.warning(f"  No se encontró la tabla de partidas para {tournament_name}")
        return []

    # 1. Identificar spans de headers dinámicamente (manejo de colspan)
    header_spans = []
    current_col = 0
    for th in table.find_all("th"):
        name = th.get_text(strip=True).upper()
        colspan = int(th.get("colspan", 1))
        header_spans.append({"name": name, "start": current_col, "width": colspan})
        current_col += colspan

    game_span = next((s for s in header_spans if "GAME" in s["name"]), None)
    patch_span = next((s for s in header_spans if "PATCH" in s["name"]), None)

    if not game_span or not patch_span:
        logger.warning(f"  No se pudieron identificar las columnas GAME/PATCH. Headers: {[s['name'] for s in header_spans]}")
        # Intentar fallback agresivo o continuar

    game_metadatas: list[dict] = []
    rows = table.find_all("tr")[1:]  # Saltear header

    import re
    patch_regex = re.compile(r"(\d{1,2}\.\d{1,2})")

    for row in rows:
        cols = row.find_all("td")
        if not cols:
            continue

        # Extraer Patch: buscar el primer texto que parezca un patch dentro del span de PATCH
        patch = "unknown"
        if patch_span:
            start, width = patch_span["start"], patch_span["width"]
            for i in range(start, start + width):
                if i < len(cols):
                    txt = cols[i].get_text(strip=True)
                    m = patch_regex.search(txt)
                    if m:
                        patch = m.group(1)
                        break
        
        # Extraer Link de Serie: buscar el primer 'a' dentro del span de GAME
        series_url = None
        if game_span:
            start, width = game_span["start"], game_span["width"]
            for i in range(start, start + width):
                if i < len(cols):
                    a_tag = cols[i].find("a")
                    if a_tag:
                        series_url = a_tag["href"]
                        break
        
        if not series_url:
            continue
            
        # Reconstruir URL de serie de forma robusta
        if "/game/stats/" in series_url:
            parts = series_url.split("/game/stats/")
            id_str = parts[1].split("/")[0]
            series_url = f"{BASE_URL}/game/stats/{id_str}/page-summary/"
        elif series_url.startswith("/"):
            series_url = BASE_URL + series_url
        
        # Encontrar todas las partidas de esta serie
        gids = fetch_all_game_ids_from_series(series_url)
        for gid in gids:
            game_metadatas.append({
                "game_id": gid,
                "patch": patch
            })

    # Dedup por game_id
    seen = set()
    unique_metadatas = []
    for m in game_metadatas:
        if m["game_id"] not in seen:
            unique_metadatas.append(m)
            seen.add(m["game_id"])

    logger.info(f"  → {len(unique_metadatas)} partidas encontradas en '{tournament_name}'")
    return unique_metadatas


def fetch_all_game_ids_from_series(series_url: str) -> list[int]:
    """Sigue el link de una serie para encontrar todos los IDs de partidas individuales."""
    # Asegurarnos de que el link vaya a page-summary o similar que liste los juegos
    if "page-summary" not in series_url and "page-game" not in series_url:
        series_url = series_url.replace("page-preview", "page-summary")

    try:
        resp = requests.get(series_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        
        soup = BeautifulSoup(resp.text, "html.parser")
        game_ids = []
        
        # Buscar el menú de navegación de partidas (Game 1, Game 2, etc.)
        # Suele estar en links que contienen "page-game"
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/game/stats/" in href and ("/page-game/" in href or "/page-summary/" in href):
                parts = href.split("/game/stats/")
                if len(parts) > 1:
                    id_str = parts[1].split("/")[0]
                    if id_str.isdigit():
                        gid = int(id_str)
                        if gid not in game_ids:
                            game_ids.append(gid)
        
        # Si no encontró nada en los links, quizás es una serie de un solo juego y el ID está en la URL
        if not game_ids:
            parts = series_url.split("/game/stats/")
            if len(parts) > 1:
                id_str = parts[1].split("/")[0]
                if id_str.isdigit():
                    game_ids = [int(id_str)]

        return game_ids
    except Exception as e:
        logger.debug(f"  Error siguiendo serie {series_url}: {e}")
        return []


def fetch_game_metadata(game_id: int) -> dict:
    """
    Obtiene metadata básica (equipos, ganador, patch) desde la página summary.
    Sin JS, el summary tiene esta info.
    """
    url = f"{BASE_URL}/game/stats/{game_id}/page-game/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {}
        soup = BeautifulSoup(resp.text, "html.parser")

        # Equipos — en el título generalmente "TeamA vs TeamB"
        title = soup.find("title")
        title_text = title.get_text() if title else ""
        # Buscar patch en el HTML
        patch = _extract_patch(soup, title_text)

        return {"patch": patch, "raw_title": title_text}
    except Exception as e:
        logger.debug(f"  Error obteniendo metadata de game {game_id}: {e}")
        return {}


def _extract_patch(soup: BeautifulSoup, title_text: str) -> str:
    """Busca el patch en el HTML de la página usando un regex robusto y genérico."""
    import re
    # Patrón genérico para parches de LoL: v16.9, 16.9, Patch 16.9
    patch_pattern = re.compile(r"(?:Patch\s+|v)?(\d{1,2}\.\d{1,2})", re.IGNORECASE)
    
    # 1. Buscar en el texto de los tags
    for tag in soup.find_all(string=True):
        text = str(tag).strip()
        if 4 <= len(text) <= 15: # Los parches son cortos
            m = patch_pattern.search(text)
            if m:
                return m.group(1)
                
    # 2. Fallback: buscar en el título
    if title_text:
        m = patch_pattern.search(title_text)
        if m:
            return m.group(1)

    return "unknown"


# ─────────────────────────────────────────────────────────────────────
# STEP 2: BUILD SCRAPING (Playwright, con JS)
# ─────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────
# STEP 2: BUILD SCRAPING (AJAX-based, sin Playwright)
# ─────────────────────────────────────────────────────────────────────

def fetch_player_ajax(game_id: int, player_id: int) -> list[dict]:
    """Consulta el endpoint de AJAX para obtener el build de un jugador."""
    url = f"{BASE_URL}/game/ajax.build.php"
    payload = f"game_id={game_id}&player_id={player_id}"
    headers = {
        **HEADERS,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE_URL}/game/stats/{game_id}/page-builds/"
    }
    
    try:
        resp = requests.post(url, headers=headers, data=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.debug(f"  Error AJAX para game {game_id}, player {player_id}: {e}")
    return []

def scrape_game_builds(game_id: int, league: str, patch: str) -> list[dict]:
    """
    Scrapea los builds de los 10 jugadores de una partida usando el endpoint AJAX.
    """
    url = f"{BASE_URL}/game/stats/{game_id}/page-builds/"
    records: list[dict] = []

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"  HTTP {resp.status_code} para game {game_id}")
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"  Error cargando game {game_id}: {e}")
        return []

    # 1. Extraer equipos y winner
    blue_team, red_team, winner = _extract_teams_and_winner(soup)

    # 2. Extraer player_ids y nombres de campeones de los botones
    player_data_map = {} # btn_num -> {player_id, champion}
    for btn_num in range(1, 11):
        btn = soup.find("button", id=f"btn{btn_num}")
        if not btn:
            continue
        
        # Extraer player_id del onclick="toggleBuilds(index, ID)"
        onclick = btn.get("onclick", "")
        m_id = re.search(r'toggleBuilds\(\d+,\s*(\d+)\)', onclick)
        player_id = int(m_id.group(1)) if m_id else None
        
        # Extraer campeón
        champ_img = btn.find("img")
        champion = ""
        if champ_img:
            champion = (champ_img.get("title") or "").strip()
            if not champion:
                src = champ_img.get("src") or ""
                m_champ = re.search(r'[/\\](?:champion|champions_icon)[/\\]([^/\\.]+)\.png', src, re.IGNORECASE)
                if m_champ:
                    champion = m_champ.group(1).strip()
        
        if player_id:
            player_data_map[btn_num] = {"player_id": player_id, "champion": champion or f"Unknown_{btn_num}"}

    # 3. Consultar AJAX en paralelo para los 10 jugadores
    results = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_btn = {
            executor.submit(fetch_player_ajax, game_id, info["player_id"]): btn_num 
            for btn_num, info in player_data_map.items()
        }
        for future in future_to_btn:
            btn_num = future_to_btn[future]
            try:
                results[btn_num] = future.result()
            except Exception as e:
                logger.debug(f"  Error en thread para btn{btn_num}: {e}")
                results[btn_num] = []

    # 4. Procesar resultados y crear récords
    for btn_num, ajax_data in results.items():
        info = player_data_map[btn_num]
        side, role = BUTTON_TO_SIDE_ROLE.get(btn_num, ("Unknown", "UNKNOWN"))
        team = blue_team if side == "Blue" else red_team
        result = (winner == side)
        
        rec = {
            "golgg_game_id": str(game_id),
            "league": league,
            "patch": patch,
            "blue_team": blue_team,
            "red_team": red_team,
            "winner": winner,
            "side": side,
            "role": role,
            "champion": info["champion"],
            "team": team,
            "result": result,
            "build": []
        }

        # El AJAX devuelve [ [{flat_stats}], [{build_events}] ]
        if ajax_data and len(ajax_data) > 1:
            events = ajax_data[1]
            # Procesar eventos cronológicamente
            temp_build = [] # List of (name, seconds)
            for ev in events:
                e_type = ev.get("typeEvent")
                item_id = str(ev.get("itemId", ""))
                seconds = int(ev.get("build_time", 0))
                
                if e_type == "ITEM_PURCHASED":
                    item_name = _ITEM_ID_MAP.get(item_id, f"item_{item_id}")
                    if item_name not in IGNORED_ITEMS:
                        temp_build.append((item_name, seconds))
                elif e_type == "ITEM_UNDO":
                    if temp_build:
                        temp_build.pop() # Deshacer última compra
                elif e_type == "ITEM_SOLD":
                    # Opcional: Podríamos quitarlo del build, pero para Build Lab 
                    # a menudo interesa saber qué se compró y en qué orden.
                    # Por ahora lo dejamos en el build para no perder la traza.
                    pass
            
            # Convertir a formato final (minutos)
            rec["build"] = [{"name": name, "minute": sec // 60} for name, sec in temp_build]

        # Fallback si no hay eventos: usar los items finales del flat_stats
        if not rec["build"] and ajax_data and len(ajax_data[0]) > 0:
            p_data = ajax_data[0][0]
            for j in range(7):
                item_id = str(p_data.get(f"item{j}", "0"))
                if item_id != "0":
                    item_name = _ITEM_ID_MAP.get(item_id, f"item_{item_id}")
                    if item_name not in IGNORED_ITEMS:
                        rec["build"].append({"name": item_name, "minute": 0})

        records.append(rec)

    # 5. Agregar contexto aliados/rivales
    blue_champs = {BUTTON_TO_SIDE_ROLE[i][1]: player_data_map.get(i, {}).get("champion", "") for i in range(1, 6)}
    red_champs  = {BUTTON_TO_SIDE_ROLE[i][1]: player_data_map.get(i, {}).get("champion", "") for i in range(6, 11)}

    for rec in records:
        s = rec["side"]
        r = rec["role"]
        ally_team  = blue_champs if s == "Blue" else red_champs
        enemy_team = red_champs  if s == "Blue" else blue_champs
        for role_name in ROLE_ORDER:
            ally_val = ally_team.get(role_name, "")
            rec[f"ally_{role_name.lower()}"] = ally_val if (ally_val != rec["champion"] or role_name != r) else ""
            rec[f"enemy_{role_name.lower()}"] = enemy_team.get(role_name, "")

    logger.info(f"  Game {game_id}: {len(records)} jugadores scrapeados.")
    return records


def _extract_teams_and_winner(soup: BeautifulSoup) -> tuple[str, str, str]:
    """Extrae nombres de equipos y ganador usando BeautifulSoup."""
    try:
        title_text = soup.find("title").get_text() if soup.find("title") else ""
        if " vs " in title_text:
            vs_part = title_text.split(" - ")[0].strip()
            match = re.match(r'^(.+?) vs (.+?)(?:\s+game|$)', vs_part, re.IGNORECASE)
            if match:
                blue_team = match.group(1).strip()
                red_team  = match.group(2).strip()
            else:
                parts = vs_part.split(" vs ")
                blue_team = parts[0].strip()
                red_team  = parts[1].strip().split()[0] if len(parts) > 1 else "Red"
        else:
            blue_team, red_team = "Blue", "Red"

        winner = "Blue"
        if soup.find(class_=re.compile(r"victory-red|red-win", re.I)):
            winner = "Red"
        elif soup.find(class_=re.compile(r"victory-blue|blue-win", re.I)):
            winner = "Blue"
            
        return blue_team, red_team, winner
    except Exception:
        return "Blue", "Red", "Blue"


# Funciones de utilidad eliminadas: _extract_items_from_page, _resolve_item_title


def save_records_to_supabase(records: list[dict]):
    """Sube los registros scrapeados directamente a la tabla pro_builds de Supabase."""
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.warning("Supabase no configurado en variables de entorno. Se omitirá subida a DB.")
        return

    try:
        supabase: Client = create_client(url, key)
        # Formatear el dict para que coincida exactamente con las columnas, NaN -> None
        clean_records = []
        for rec in records:
            clean_rec = {}
            for k, v in rec.items():
                if isinstance(v, (list, tuple)):
                    clean_rec[k] = v
                elif pd.isna(v):
                    clean_rec[k] = None
                else:
                    clean_rec[k] = v
            clean_records.append(clean_rec)

        response = supabase.table("pro_builds").upsert(
            clean_records, 
            on_conflict="golgg_game_id,role,side"
        ).execute()
        
        logger.info(f"  [{len(clean_records)}] Registros subidos a Supabase (pro_builds)")
    except Exception as e:
        logger.error(f"Error subiendo a Supabase: {e}")


# ─────────────────────────────────────────────────────────────────────
# STEP 3: PERSISTENCIA
# ─────────────────────────────────────────────────────────────────────

def load_scraped_ids() -> set[int]:
    """Carga los game_ids ya scrapeados para el modo --resume."""
    if SCRAPED_IDS_FILE.exists():
        try:
            with open(SCRAPED_IDS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_scraped_id(game_id: int):
    """Registra un game_id como ya scrapeado."""
    scraped = load_scraped_ids()
    scraped.add(game_id)
    SCRAPED_IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SCRAPED_IDS_FILE, "w") as f:
        json.dump(list(scraped), f)


def append_records_to_parquet(records: list[dict]):
    """Agrega registros al Parquet existente (o crea uno nuevo)."""
    if not records:
        return

    df_new = pd.DataFrame(records)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if OUTPUT_PARQUET.exists():
        try:
            df_existing = pd.read_parquet(OUTPUT_PARQUET)
            # Dedup por game_id + side + role
            df_all = pd.concat([df_existing, df_new], ignore_index=True)
            df_all = df_all.drop_duplicates(
                subset=["golgg_game_id", "side", "role"], keep="last"
            ).reset_index(drop=True)
        except Exception as e:
            logger.error(f"Error cargando Parquet existente (posible corrupcion): {e}")
            logger.info("Se creara un nuevo archivo Parquet con los registros actuales.")
            df_all = df_new
    else:
        df_all = df_new

    df_all.to_parquet(OUTPUT_PARQUET, index=False)
    logger.info(f"  Parquet guardado: {len(df_all)} registros totales ({OUTPUT_PARQUET})")


# ─────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────

def run_tournament(tournament: dict, scraped_ids: set[int], resume: bool):
    name, league = tournament["name"], tournament["league"]
    logger.info(f"\n{'='*60}")
    logger.info(f"Torneo: {name} ({league})")
    logger.info(f"{'='*60}")

    game_metas = fetch_game_ids_from_tournament(name)
    if not game_metas:
        logger.warning(f"  Sin partidas para '{name}'. Verificar nombre del torneo en gol.gg.")
        return

    for i, meta in enumerate(game_metas):
        gid = meta["game_id"]
        patch = meta["patch"]

        if resume and gid in scraped_ids:
            logger.info(f"  [{i+1}/{len(game_metas)}] Game {gid} → ya scrapeado, saltando.")
            continue

        logger.info(f"  [{i+1}/{len(game_metas)}] Scrapeando game {gid} (Patch {patch})...")

        # Si el patch es desconocido, intentamos sacarlo de la metadata de la página
        if patch == "unknown":
            game_meta = fetch_game_metadata(gid)
            patch = game_meta.get("patch", "unknown")
            if patch == "unknown":
                logger.warning(f"    No se pudo determinar el patch para game {gid}")

        try:
            records = scrape_game_builds(gid, league, patch)
        except Exception as e:
            logger.error(f"    Error fatal scrapeando game {gid}: {e}")
            records = []

        if records:
            append_records_to_parquet(records)
            save_records_to_supabase(records)
            save_scraped_id(gid)
        else:
            logger.warning(f"    Sin datos para game {gid}. Puede estar incompleto en gol.gg.")

        # Delay mínimo (ya no necesitamos esperar al renderizado)
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="Sync de builds pro desde gol.gg")
    parser.add_argument(
        "--tournament", type=str, default=None,
        help="Nombre exacto del torneo (ej: 'LPL 2026 Split 2'). Por defecto: todos."
    )
    parser.add_argument(
        "--game-id", type=int, default=None,
        help="Scrapear solo un game específico por ID."
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Saltear game_ids que ya fueron scrapeados."
    )
    parser.add_argument(
        "--list-tournaments", action="store_true",
        help="Mostrar los torneos configurados y salir."
    )
    args = parser.parse_args()

    if args.list_tournaments:
        print("\nTorneos configurados:")
        for t in TOURNAMENTS:
            print(f"  [{t['league']}] {t['name']}")
        return

    scraped_ids = load_scraped_ids() if args.resume else set()
    if args.resume:
        logger.info(f"Modo resume: {len(scraped_ids)} partidas ya scrapeadas.")

    # Cargar el mapa de items de DDragon una vez
    global _ITEM_ID_MAP
    logger.info("Cargando mapa de ítems desde DDragon...")
    _ITEM_ID_MAP = _load_item_id_map()
    logger.info(f"  {len(_ITEM_ID_MAP)} ítems cargados.")

    # Modo: solo un juego
    if args.game_id:
        logger.info(f"Modo single game: {args.game_id}")
        meta = fetch_game_metadata(args.game_id)
        patch = meta.get("patch", "unknown")
        records = scrape_game_builds(args.game_id, "MANUAL", patch)
        if records:
            append_records_to_parquet(records)
            save_records_to_supabase(records)
            logger.info(f"✅ Game {args.game_id} guardado ({len(records)} jugadores).")
        else:
            logger.error(f"❌ Sin datos para game {args.game_id}.")
        return

    # Modo: torneo específico o todos
    tournaments_to_run = TOURNAMENTS
    if args.tournament:
        tournaments_to_run = [t for t in TOURNAMENTS if t["name"] == args.tournament]
        if not tournaments_to_run:
            logger.error(f"Torneo '{args.tournament}' no encontrado. Usa --list-tournaments para ver los disponibles.")
            return

    for tournament in tournaments_to_run:
        run_tournament(tournament, scraped_ids, args.resume)

    logger.info("\n✅ Sync de builds pro completado.")

    if OUTPUT_PARQUET.exists():
        df = pd.read_parquet(OUTPUT_PARQUET)
        logger.info(f"   Total registros: {len(df)}")
        logger.info(f"   Partidas únicas: {df['golgg_game_id'].nunique()}")
        logger.info(f"   Ligas: {df['league'].value_counts().to_dict()}")
        logger.info(f"   Patches: {sorted(df['patch'].unique())}")


if __name__ == "__main__":
    main()
