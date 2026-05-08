"""
migrate_timeline_roles.py — Actualiza role y team_id en scout_timeline existente.

Este script actualiza los registros existentes en scout_timeline con los valores
de role y team_id desde scout_participants, evitando tener que re-sincronizar
 todos los datos.

Uso:
    python migrate_timeline_roles.py
    python migrate_timeline_roles.py --dry-run  # Ver qué se actualizaría
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def migrate_timeline_roles(dry_run: bool = False):
    """
    Actualiza role y team_id en scout_timeline desde scout_participants.
    """
    # Init Supabase
    supabase_url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    
    if not supabase_url or not supabase_key:
        logger.error("❌ SUPABASE_URL o SUPABASE_KEY no configurados en .env")
        return
    
    supabase = create_client(supabase_url, supabase_key)
    
    # Verificar si hay registros sin role/team_id
    logger.info("📊 Verificando registros que necesitan actualización...")
    
    # Contar registros afectados
    count_query = """
        SELECT COUNT(*) as count
        FROM public.scout_timeline t
        WHERE t.role IS NULL OR t.team_id IS NULL
    """
    
    try:
        # Usar RPC para ejecutar SQL personalizado
        result = supabase.table("scout_timeline").select("*", count="exact").is_("role", "null").execute()
        # Nota: La consulta exacta requiere SQL, esta es una aproximación
        logger.info(f"🔎 Encontrados registros sin role en scout_timeline")
    except Exception as e:
        logger.warning(f"No se pudo contar registros exactos: {e}")
    
    if dry_run:
        logger.info("🔍 DRY RUN - No se modificará la base de datos")
        logger.info("Ejecutar sin --dry-run para aplicar cambios")
        return
    
    # Actualizar registros
    logger.info("🔄 Actualizando scout_timeline con role y team_id desde scout_participants...")
    
    # Usar SQL directo via RPC o múltiples queries
    # Opción 1: Actualizar en batches por match_id
    try:
        # Obtener todos los match_id únicos en timeline
        match_ids_res = supabase.table("scout_timeline").select("match_id").execute()
        if not match_ids_res.data:
            logger.info("✅ No hay registros para actualizar")
            return
        
        match_ids = list(set(r["match_id"] for r in match_ids_res.data))
        logger.info(f"🔄 Procesando {len(match_ids)} partidas...")
        
        updated_total = 0
        
        for idx, match_id in enumerate(match_ids, 1):
            try:
                # Obtener participant info para este match
                part_res = supabase.table("scout_participants").select(
                    "match_id, participant_id, role, team_id"
                ).eq("match_id", match_id).execute()
                
                if not part_res.data:
                    continue
                
                # Crear mapping
                for p in part_res.data:
                    pid = p["participant_id"]
                    role = p.get("role")
                    team_id = p.get("team_id")
                    
                    # Actualizar timeline
                    if role is not None or team_id is not None:
                        update_data = {}
                        if role is not None:
                            update_data["role"] = role
                        if team_id is not None:
                            update_data["team_id"] = team_id
                        
                        supabase.table("scout_timeline").update(update_data).eq(
                            "match_id", match_id
                        ).eq("participant_id", pid).execute()
                
                updated_total += 1
                
                if idx % 50 == 0:
                    logger.info(f"[{idx}/{len(match_ids)}] Partidas procesadas...")
                
            except Exception as e:
                logger.warning(f"Error procesando {match_id}: {e}")
        
        logger.info(f"✅ Actualización completada. {updated_total} partidas procesadas.")
        
    except Exception as e:
        logger.error(f"❌ Error en migración: {e}")
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Migrar role y team_id a scout_timeline existente"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simular la migración sin modificar la BD"
    )
    
    args = parser.parse_args()
    migrate_timeline_roles(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
