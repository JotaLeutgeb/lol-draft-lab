"""
migrate_existing_matches.py — Actualiza game_timestamp para partidas existentes.

Este script recorre todas las partidas en la DB que no tienen game_timestamp,
consulta la Riot API para obtener el gameCreation, y actualiza la base de datos.

Uso:
    python migrate_existing_matches.py
    python migrate_existing_matches.py --limit 100  # Solo migrar 100 partidas
    python migrate_existing_matches.py --dry-run    # Ver qué se actualizaría sin modificar BD
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

from src.data_loader_scout import ScoutMatchClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def migrate_matches(dry_run: bool = False, limit: int = None):
    """
    Migra partidas existentes sin game_timestamp.
    
    Args:
        dry_run: Si es True, solo muestra lo que se haría sin modificar la BD
        limit: Número máximo de partidas a migrar (None = todas)
    """
    # Init Supabase
    supabase_url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    
    if not supabase_url or not supabase_key:
        logger.error("❌ SUPABASE_URL o SUPABASE_KEY no configurados en .env")
        return
    
    supabase = create_client(supabase_url, supabase_key)
    
    # Init Riot API client
    riot_key = os.environ.get("RIOT_API_KEY")
    if not riot_key:
        logger.error("❌ RIOT_API_KEY no configurado en .env")
        return
    
    client = ScoutMatchClient(riot_key, platform="la2", region="americas")
    
    # Obtener partidas sin game_timestamp
    logger.info("📥 Obteniendo partidas sin game_timestamp...")
    query = supabase.table("matches").select("match_id").is_("game_timestamp", "null")
    
    if limit:
        query = query.limit(limit)
    
    resp = query.execute()
    
    if not resp.data:
        logger.info("✅ No hay partidas para migrar. Todo al día!")
        return
    
    match_ids = [r["match_id"] for r in resp.data]
    logger.info(f"🔎 Encontradas {len(match_ids)} partidas para migrar")
    
    if dry_run:
        logger.info("🔍 DRY RUN - No se modificará la base de datos")
        logger.info(f"Se actualizarían {len(match_ids)} partidas")
        return
    
    # Procesar en batches
    updated_count = 0
    failed_count = 0
    
    for idx, match_id in enumerate(match_ids, 1):
        try:
            # Obtener datos del match desde Riot API
            match_data = client.get_match(match_id)
            
            if not match_data:
                logger.warning(f"[{idx}/{len(match_ids)}] ⚠️ No se pudo obtener datos de {match_id}")
                failed_count += 1
                continue
            
            # Extraer gameCreation
            game_creation_ms = match_data.get("info", {}).get("gameCreation", 0)
            
            if game_creation_ms == 0:
                logger.warning(f"[{idx}/{len(match_ids)}] ⚠️ {match_id} no tiene gameCreation")
                failed_count += 1
                continue
            
            # Convertir a ISO timestamp
            game_timestamp = datetime.fromtimestamp(
                game_creation_ms / 1000, 
                tz=timezone.utc
            ).isoformat()
            
            # Actualizar en DB
            supabase.table("matches").update({
                "game_timestamp": game_timestamp
            }).eq("match_id", match_id).execute()
            
            updated_count += 1
            logger.info(f"[{idx}/{len(match_ids)}] ✅ {match_id} → {game_timestamp}")
            
        except Exception as e:
            logger.error(f"[{idx}/{len(match_ids)}] ❌ Error en {match_id}: {e}")
            failed_count += 1
    
    logger.info("=" * 60)
    logger.info(f"✅ Migración completada:")
    logger.info(f"   - Actualizadas: {updated_count}")
    logger.info(f"   - Fallidas: {failed_count}")
    logger.info(f"   - Total procesadas: {updated_count + failed_count}")


def main():
    parser = argparse.ArgumentParser(
        description="Migrar game_timestamp para partidas existentes"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simular la migración sin modificar la BD"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Límite de partidas a migrar (default: todas)"
    )
    
    args = parser.parse_args()
    migrate_matches(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
