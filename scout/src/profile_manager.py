"""
profile_manager.py — Gestión de perfiles multi-usuario.

Maneja:
  - Validación de Riot ID con Riot API
  - Creación de perfiles en Supabase
  - Sincronización automática de datos
  - Listado de perfiles disponibles
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests
from supabase import Client

logger = logging.getLogger(__name__)

# Riot API endpoints
RIOT_API_BASE = {
    "americas": "https://americas.api.riotgames.com",
    "europe": "https://europe.api.riotgames.com",
    "asia": "https://asia.api.riotgames.com",
}

RIOT_PLATFORM_BASE = {
    "la2": "https://la2.api.riotgames.com",
    "na1": "https://na1.api.riotgames.com",
    "euw1": "https://euw1.api.riotgames.com",
}


@dataclass
class RiotAccount:
    """Cuenta de Riot validada."""
    puuid: str
    game_name: str
    tag_line: str
    riot_id: str  # "GameName#TAG"


def validate_riot_id(riot_id: str, region: str = "americas") -> Optional[RiotAccount]:
    """
    Valida un Riot ID con la API de Riot.
    
    Args:
        riot_id: Riot ID en formato "GameName#TAG"
        region: Región (americas, europe, asia)
    
    Returns:
        RiotAccount si es válido, None si no existe o hay error
    """
    api_key = os.environ.get("RIOT_API_KEY")
    if not api_key:
        logger.error("RIOT_API_KEY no configurada en variables de entorno")
        return None
    
    # Parse riot_id
    if "#" not in riot_id:
        logger.error(f"Riot ID inválido: {riot_id}. Debe ser 'GameName#TAG'")
        return None
    
    game_name, tag_line = riot_id.split("#", 1)
    
    # Call Riot API
    url = f"{RIOT_API_BASE[region]}/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    headers = {"X-Riot-Token": api_key}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            return RiotAccount(
                puuid=data["puuid"],
                game_name=data["gameName"],
                tag_line=data["tagLine"],
                riot_id=f"{data['gameName']}#{data['tagLine']}"
            )
        elif response.status_code == 404:
            logger.warning(f"Riot ID no encontrado: {riot_id}")
            return None
        elif response.status_code == 403:
            logger.error("API Key inválida o expirada")
            return None
        else:
            logger.error(f"Error de Riot API: {response.status_code} - {response.text}")
            return None
    
    except requests.RequestException as e:
        logger.error(f"Error al conectar con Riot API: {e}")
        return None


def create_profile_in_db(
    supabase: Client,
    riot_account: RiotAccount,
    primary_role: str = "JUNGLE",
    platform: str = "la2",
) -> Optional[dict]:
    """
    Crea un perfil en Supabase.
    
    Args:
        supabase: Cliente de Supabase
        riot_account: Cuenta validada de Riot
        primary_role: Rol principal (JUNGLE, TOP, etc.)
        platform: Plataforma (la2, na1, etc.)
    
    Returns:
        Dict con datos del perfil creado, None si falla
    """
    try:
        # Check if profile already exists
        existing = supabase.table("scout_profiles").select("*").eq("riot_id", riot_account.riot_id).execute()
        
        if existing.data:
            logger.info(f"Perfil ya existe: {riot_account.riot_id}")
            return existing.data[0]
        
        # Create new profile
        profile_data = {
            "riot_id": riot_account.riot_id,
            "display_name": riot_account.game_name,
            "primary_role": primary_role,
            "platform": platform,
            "match_count": 30,
            "queue_filter": [420],  # Solo ranked solo/duo
            "created_at": datetime.now().isoformat(),
        }
        
        result = supabase.table("scout_profiles").insert(profile_data).execute()
        
        if result.data:
            logger.info(f"Perfil creado exitosamente: {riot_account.riot_id}")
            return result.data[0]
        else:
            logger.error(f"Error al crear perfil: {result}")
            return None
    
    except Exception as e:
        logger.error(f"Error en create_profile_in_db: {e}")
        return None


def sync_profile_data(riot_id: str, platform: str = "la2", region: str = "americas") -> bool:
    """
    Sincroniza datos de un perfil ejecutando sync_player.py.
    
    Args:
        riot_id: Riot ID en formato "GameName#TAG"
        platform: Plataforma (la2, na1, etc.)
        region: Región (americas, europe, asia)
    
    Returns:
        True si la sincronización fue exitosa, False si falló
    """
    try:
        # Build command
        cmd = [
            "python",
            "sync_player.py",
            "--riot-id", riot_id,
            "--platform", platform,
            "--region", region,
        ]
        
        logger.info(f"Ejecutando sync: {' '.join(cmd)}")
        
        # Run sync_player.py
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout
        )
        
        if result.returncode == 0:
            logger.info(f"Sincronización exitosa para {riot_id}")
            return True
        else:
            logger.error(f"Error en sync: {result.stderr}")
            return False
    
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout en sincronización de {riot_id}")
        return False
    except Exception as e:
        logger.error(f"Error en sync_profile_data: {e}")
        return False


def get_all_profiles(supabase: Client) -> list[dict]:
    """
    Obtiene todos los perfiles de Supabase.
    
    Args:
        supabase: Cliente de Supabase
    
    Returns:
        Lista de perfiles ordenados por last_synced (más recientes primero)
    """
    try:
        result = supabase.table("scout_profiles").select("*").order("last_synced", desc=True).execute()
        
        if result.data:
            return result.data
        else:
            return []
    
    except Exception as e:
        logger.error(f"Error en get_all_profiles: {e}")
        return []


def update_last_synced(supabase: Client, riot_id: str) -> bool:
    """
    Actualiza el timestamp de last_synced para un perfil.
    
    Args:
        supabase: Cliente de Supabase
        riot_id: Riot ID del perfil
    
    Returns:
        True si se actualizó correctamente
    """
    try:
        result = supabase.table("scout_profiles").update({
            "last_synced": datetime.now().isoformat()
        }).eq("riot_id", riot_id).execute()
        
        return bool(result.data)
    
    except Exception as e:
        logger.error(f"Error en update_last_synced: {e}")
        return False
