"""
config.py — Constantes globales del dominio LoL Esports Analyzer.

Centraliza toda la configuración para evitar magic numbers dispersos
en el código. Modificar aquí impacta todo el sistema.
"""

# ──────────────────────────────────────────────
# API / Riot
# ──────────────────────────────────────────────

# Plataforma de la región (para endpoints de summoner/league)
RIOT_PLATFORM: str = "la2"   # LAS

# Routing regional para match-v5 (americas cubre NA, LAN, LAS, BR)
RIOT_REGION: str = "americas"

# Rate limits de desarrollo (requests/s y requests/2min)
RATE_LIMIT_PER_SECOND: int = 20
RATE_LIMIT_PER_2MIN: int = 100

# Reintentos ante 429 / 503
MAX_RETRIES: int = 5
BACKOFF_BASE_SECONDS: float = 1.5   # backoff exponencial: 1.5^intento

# Tipo de cola por defecto (420 = Ranked Solo/Duo, 400 = Draft Normal, 0 = todos)
DEFAULT_QUEUE: int = 420

# ──────────────────────────────────────────────
# Equipo (Riot IDs configurados)
# ──────────────────────────────────────────────

# Lista de jugadores del equipo. Estructura: {"role": "...", "riot_id": "GameName#TAG", "display_name": "..."}
TEAM_PLAYERS: list[dict] = [
    {
        "role": "TOP",
        "riot_id": "Franfon#vayne",
        "display_name": "Winis",
        "accounts": ["Franfon#vayne", "AEVI Winis#Winis"],
        "main": "Winis#SB5"
    },
    {
        "role": "JUNGLE",
        "riot_id": "Jöta#ZAC",
        "display_name": "Jöta",
        "accounts": ["Jöta#ZAC", "AEVI Jöta#jjjjj"],
        "main": None
    },
    {
        "role": "MID",
        "riot_id": "xPíxel#MID",
        "display_name": "xPíxel",
        "accounts": ["xPíxel#MID", "AEVI Digu#MID"],
        "main": None
    },
    {
        "role": "BOT",
        "riot_id": "FranFontana#Chan",
        "display_name": "FranFontana",
        "accounts": ["FranFontana#Chan", "AEVI Fonti#Fran"],
        "main": None
    },
    {
        "role": "SUPPORT",
        "riot_id": "Elrayray#JGDIF",
        "display_name": "Elrayray",
        "accounts": ["Elrayray#JGDIF", "AEVI Ray#Ray"],
        "main": None
    },
]

# Mapa nombre_en_minusculas -> rol asignado en el equipo.
# Permite filtrar partidas donde un jugador aparece en un rol diferente
# al que tiene asignado (ej: Franfon jugando MID en solo queue).
TEAM_PLAYER_ROLE_MAP: dict[str, str] = {}
for p in TEAM_PLAYERS:
    disp = p.get("display_name", p["riot_id"].split("#")[0])
    TEAM_PLAYER_ROLE_MAP[disp.lower()] = p["role"]
    for acc in p.get("accounts", [p["riot_id"]]):
        TEAM_PLAYER_ROLE_MAP[acc.split("#")[0].lower()] = p["role"]

# Mapa nombre_en_minusculas -> display name del jugador titular para fusionar cuentas.
TEAM_PLAYER_DISPLAY_MAP: dict[str, str] = {}
for p in TEAM_PLAYERS:
    disp = p.get("display_name", p["riot_id"].split("#")[0])
    TEAM_PLAYER_DISPLAY_MAP[disp.lower()] = disp
    for acc in p.get("accounts", [p["riot_id"]]):
        TEAM_PLAYER_DISPLAY_MAP[acc.split("#")[0].lower()] = disp

TEAM_GAME_NAMES = set(TEAM_PLAYER_ROLE_MAP.keys())

# ──────────────────────────────────────────────
# Mapeo de roles (API de Riot → labels internos)
# ──────────────────────────────────────────────
# La API entrega "teamPosition" con los siguientes valores:
ROLE_MAP: dict[str, str] = {
    "TOP":     "TOP",
    "JUNGLE":  "JUNGLE",
    "MIDDLE":  "MID",
    "BOTTOM":  "BOT",
    "UTILITY": "SUPPORT",
    # Fallback para partidas sin datos de posición:
    "":        "UNKNOWN",
}

# Orden canónico de roles para visualizaciones
ROLE_ORDER: list[str] = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]

# ──────────────────────────────────────────────
# Fases del juego (en minutos)
# ──────────────────────────────────────────────
EARLY_GAME_END_MIN: float   = 15.0
MID_GAME_END_MIN: float     = 25.0
# late game = 25+ min

GAME_PHASES: dict[str, tuple[float, float]] = {
    "early": (0.0,  EARLY_GAME_END_MIN),
    "mid":   (EARLY_GAME_END_MIN, MID_GAME_END_MIN),
    "late":  (MID_GAME_END_MIN, float("inf")),
}

# Minutos clave para snapshots de gold difference
GOLD_DIFF_SNAPSHOTS_MIN: list[int] = [5, 10, 15]

# ──────────────────────────────────────────────
# Objetivos (eventos de la API)
# ──────────────────────────────────────────────
OBJECTIVE_MONSTER_TYPES: dict[str, str] = {
    "DRAGON":       "dragon",
    "BARON_NASHOR": "baron",
    "RIFTHERALD":   "herald",
    "HORDE":        "voidgrub",   # void grubs (parche 14+)
}

BUILDING_KILL_SUBTYPES: dict[str, str] = {
    "TOWER_BUILDING":    "tower",
    "INHIBITOR_BUILDING": "inhibitor",
}

# ──────────────────────────────────────────────
# Feature engineering — pesos para impacto real
# ──────────────────────────────────────────────
# Ajustar según prioridad táctica del equipo.
# Deben sumar 1.0.
# gold_per_min excluido: queda capturado por gold_efficiency (métrica económica separada).
IMPACT_WEIGHTS: dict[str, float] = {
    "kill_participation":   0.18,  # Involucramiento en muertes (universal)
    "survivability":        0.12,  # No morir = seguir siendo relevante
    "damage_per_min":       0.15,  # Output ofensivo directo
    "damage_taken_per_min": 0.07,  # Absorber dmg = proteger a carries
    "objective_control":    0.15,  # Dragones, barones, herald, torres
    "vision_per_min":       0.10,  # Control de mapa
    "cc_per_min":           0.10,  # Disrupción y teamfight
    "cs_per_min":           0.05,  # Disciplina de farm (critca en laning)
    "utility_score":        0.05,  # Heal, shields, control de wards
    "first_blood":          0.03,  # Presión de early game
}

# ──────────────────────────────────────────────
# Patrones de derrota — umbrales
# ──────────────────────────────────────────────
# Porcentaje mínimo de partidas perdidas donde el patrón debe aparecer
PATTERN_LOSS_THRESHOLD: float = 0.60

# Diferencia de gold negativa (vs oponente) que se considera crítica (en gold)
GOLD_DIFF_CRITICAL_THRESHOLD: int = -1500

# Vision score por minuto considerado bajo
LOW_VISION_PER_MIN: float = 0.8

# Participación en objetivos considerada baja
LOW_OBJECTIVE_RATE: float = 0.4

# ──────────────────────────────────────────────
# Rutas de datos
# ──────────────────────────────────────────────
import pathlib

ROOT_DIR = pathlib.Path(__file__).parent.parent
DATA_RAW_DIR      = ROOT_DIR / "data" / "raw"
DATA_PROCESSED_DIR = ROOT_DIR / "data" / "processed"

DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
DATA_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────
# V2 Refactor Parameters
# ──────────────────────────────────────────────

# DBSCAN Clustering Parameters (Combat Clusters)
DBSCAN_EPS: float = 1500.0         # Spatial distance in map units (approx 10% of map)
DBSCAN_MIN_SAMPLES: int = 2        # Minimum points to form a cluster (1 neighbor)
TEMPORAL_SCALE: float = 100.0      # Maps seconds to spatial units (1s ≈ 100 spatial units)

# ──────────────────────────────────────────────
# Map Parameters (Summoner's Rift)
# ──────────────────────────────────────────────
MAP_X_MIN: float = 0.0
MAP_X_MAX: float = 14820.0
MAP_Y_MIN: float = 0.0
MAP_Y_MAX: float = 14881.0

MAP_CENTER_X: float = 14820.0 / 2.0
MAP_CENTER_Y: float = 14881.0 / 2.0
PREP_WINDOW_MS: int = 60000       # 60 seconds prep window before objective kill

# ──────────────────────────────────────────────
# Data Dragon (DDragon)
# ──────────────────────────────────────────────
import requests

def get_latest_ddragon_version():
    """Obtiene la última versión de Data Dragon."""
    try:
        response = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=5)
        if response.status_code == 200:
            return response.json()[0]
    except Exception:
        pass
    return "16.9.1"  # Fallback actualizado
