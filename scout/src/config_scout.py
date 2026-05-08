"""
config_scout.py — Configuración dinámica por perfil de jugador.

Reemplaza el config.py del proyecto de equipo.
No contiene Riot IDs hardcodeados: carga todo desde un YAML de perfil.
Las constantes de dominio (umbrales, pesos) se mantienen aquí.
"""

from __future__ import annotations

import pathlib
from typing import Optional


import yaml

# ──────────────────────────────────────────────
# Perfil del Jugador
# ──────────────────────────────────────────────

class PlayerProfile:
    """
    Representación en memoria de un perfil YAML de jugador.
    """

    def __init__(self, yaml_path: str | pathlib.Path) -> None:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self._load_from_dict(data)

    def _load_from_dict(self, data: dict) -> None:
        self.riot_id: str = data["riot_id"]
        self.display_name: str = data["display_name"]
        self.primary_role: str = data["primary_role"].upper()
        self.valid_roles: list[str] = [self.primary_role]
        self.platform: str = data.get("platform", "la2")
        self.region: str = data.get("region", "americas")
        self.queue_filter: list[int] = data.get("queue_filter", [420])
        self.supabase_tag: str = data.get("supabase_tag", self.riot_id.split("#")[0].lower())
        
        # Nombres históricos/alias - para soportar cambios de nombre
        # Incluye automáticamente el game_name actual
        historical_names = data.get("historical_names", [])
        self.all_game_names: list[str] = [self.game_name]
        if historical_names:
            self.all_game_names.extend(historical_names)
        
        # Season start timestamp - si no se especifica, usar Season 2026 S1 por defecto
        from datetime import datetime, timezone
        default_season = datetime(2026, 1, 8, tzinfo=timezone.utc)
        
        if "season_start_ts" in data and data["season_start_ts"]:
            self.season_start_ts: Optional[int] = data["season_start_ts"]
        else:
            # Convertir default_season a epoch timestamp en segundos
            self.season_start_ts: Optional[int] = int(default_season.timestamp())

    @classmethod
    def from_dict(cls, data: dict) -> "PlayerProfile":
        """Crea un PlayerProfile desde un dict (sin necesitar YAML)."""
        obj = cls.__new__(cls)
        obj._load_from_dict(data)
        return obj

    @property
    def game_name(self) -> str:
        """GameName sin el #TAG."""
        return self.riot_id.split("#")[0]

    @property
    def tag_line(self) -> str:
        """Solo el TAG (sin el #)."""
        parts = self.riot_id.split("#")
        return parts[1] if len(parts) > 1 else ""

    def __repr__(self) -> str:
        return f"PlayerProfile({self.riot_id}, role={self.primary_role})"


def load_profile(yaml_path: str | pathlib.Path) -> PlayerProfile:
    """Carga y valida un perfil YAML. Lanza ValueError si el formato es incorrecto."""
    p = pathlib.Path(yaml_path)
    if not p.exists():
        raise FileNotFoundError(f"Perfil no encontrado: {p}")
    return PlayerProfile(p)


# ──────────────────────────────────────────────
# SEASONS - Fechas de inicio oficiales de Riot
# ──────────────────────────────────────────────

from datetime import datetime, timezone

SEASONS: dict[str, datetime] = {
    "Season 2026 S1": datetime(2026, 1, 8, tzinfo=timezone.utc),
    "Season 2026 S2": datetime(2026, 4, 23, tzinfo=timezone.utc),
    "Últimos 90 días": None,
    "Últimos 30 días": None,
}

CURRENT_SEASON = "Season 2026 S2"
CURRENT_SEASON_START = SEASONS[CURRENT_SEASON]

# ──────────────────────────────────────────────
# API / Riot
# ──────────────────────────────────────────────

RIOT_PLATFORM: str = "la2"
RIOT_REGION: str = "americas"

RATE_LIMIT_PER_SECOND: int = 20
RATE_LIMIT_PER_2MIN: int = 100

MAX_RETRIES: int = 5
BACKOFF_BASE_SECONDS: float = 1.5

DEFAULT_QUEUE: int = 420

# ──────────────────────────────────────────────
# Mapeo de roles (API de Riot → labels internos)
# ──────────────────────────────────────────────

ROLE_MAP: dict[str, str] = {
    "TOP":     "TOP",
    "JUNGLE":  "JUNGLE",
    "MIDDLE":  "MID",
    "BOTTOM":  "BOT",
    "UTILITY": "SUPPORT",
    "":        "UNKNOWN",
}

ROLE_ORDER: list[str] = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]

# ──────────────────────────────────────────────
# Fases del juego (en minutos)
# ──────────────────────────────────────────────

EARLY_GAME_END_MIN: float = 15.0
MID_GAME_END_MIN: float = 25.0

GAME_PHASES: dict[str, tuple[float, float]] = {
    "early": (0.0,  EARLY_GAME_END_MIN),
    "mid":   (EARLY_GAME_END_MIN, MID_GAME_END_MIN),
    "late":  (MID_GAME_END_MIN, float("inf")),
}

GOLD_DIFF_SNAPSHOTS_MIN: list[int] = [5, 10, 15]

# ──────────────────────────────────────────────
# Objetivos (eventos de la API)
# ──────────────────────────────────────────────

OBJECTIVE_MONSTER_TYPES: dict[str, str] = {
    "DRAGON":       "dragon",
    "BARON_NASHOR": "baron",
    "RIFTHERALD":   "herald",
    "HORDE":        "voidgrub",
}

BUILDING_KILL_SUBTYPES: dict[str, str] = {
    "TOWER_BUILDING":     "tower",
    "INHIBITOR_BUILDING": "inhibitor",
}

# ──────────────────────────────────────────────
# Feature engineering — pesos para Impact Score individual
# Nota: sin team_synergy. El 4to pilar es CONSISTENCY.
# ──────────────────────────────────────────────

IMPACT_WEIGHTS: dict[str, float] = {
    "kill_participation":   0.18,
    "survivability":        0.12,
    "damage_per_min":       0.15,
    "damage_taken_per_min": 0.07,
    "objective_control":    0.15,
    "vision_per_min":       0.10,
    "cc_per_min":           0.10,
    "cs_per_min":           0.05,
    "utility_score":        0.05,
    "first_blood":          0.03,
}

# Pesos de los 4 pilares del Impact Score individual
PILAR_WEIGHTS: dict[str, float] = {
    "pilar_combat_efficiency": 0.30,
    "pilar_map_pressure":      0.25,
    "pilar_tactical_utility":  0.25,
    "pilar_consistency":       0.20,  # reemplaza team_synergy
}

# ──────────────────────────────────────────────
# Patrones — umbrales de detección
# ──────────────────────────────────────────────

PATTERN_LOSS_THRESHOLD: float = 0.60
GOLD_DIFF_CRITICAL_THRESHOLD: int = -1500
LOW_VISION_PER_MIN: float = 0.8
LOW_OBJECTIVE_RATE: float = 0.4

# Umbral de consistencia: CV (coeff. of variation) mayor a este valor = inconsistente
HIGH_CV_THRESHOLD: float = 0.35

# Ventana de partidas para análisis de tendencia reciente
TREND_WINDOW: int = 10

# ──────────────────────────────────────────────
# Rutas de datos
# ──────────────────────────────────────────────

ROOT_DIR = pathlib.Path(__file__).parent.parent
DATA_RAW_DIR = ROOT_DIR / "data" / "raw"
DATA_PROCESSED_DIR = ROOT_DIR / "data" / "processed"

DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
DATA_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────
# Map Parameters (Summoner's Rift)
# ──────────────────────────────────────────────

MAP_X_MIN: float = 0.0
MAP_X_MAX: float = 14820.0
MAP_Y_MIN: float = 0.0
MAP_Y_MAX: float = 14881.0
MAP_CENTER_X: float = 14820.0 / 2.0
MAP_CENTER_Y: float = 14881.0 / 2.0
PREP_WINDOW_MS: int = 60000

# ──────────────────────────────────────────────
# DBSCAN Clustering
# ──────────────────────────────────────────────

DBSCAN_EPS: float = 1500.0
DBSCAN_MIN_SAMPLES: int = 2
TEMPORAL_SCALE: float = 100.0

# ──────────────────────────────────────────────
# Data Dragon
# ──────────────────────────────────────────────

import requests

def get_latest_ddragon_version() -> str:
    """Obtiene la última versión de Data Dragon."""
    try:
        resp = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=5)
        if resp.status_code == 200:
            return resp.json()[0]
    except Exception:
        pass
    return "16.9.1"
