import requests
import json
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache")
CORE_ITEMS_CACHE_FILE = CACHE_DIR / "core_items.json"

def fetch_core_items(force_refresh: bool = False) -> tuple[set[str], set[str]]:
    """
    Obtiene la lista de ítems "Core" y "Boots" desde la API de DDragon de Riot Games.
    Retorna (core_items, boot_items).
    Utiliza una caché local para evitar consultas innecesarias.
    """
    if not force_refresh and CORE_ITEMS_CACHE_FILE.exists():
        try:
            with open(CORE_ITEMS_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Manejar formato viejo vs formato nuevo dict
                if isinstance(data, dict):
                    return set(data.get("core", [])), set(data.get("boots", []))
                else:
                    # Formato viejo: forzar refresh
                    pass
        except Exception as e:
            logger.warning(f"No se pudo leer la caché de core items: {e}")

    logger.info("Descargando data de ítems desde DDragon...")
    try:
        resp = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=5)
        resp.raise_for_status()
        latest = resp.json()[0]

        item_resp = requests.get(f"https://ddragon.leagueoflegends.com/cdn/{latest}/data/en_US/item.json", timeout=10)
        item_resp.raise_for_status()
        data = item_resp.json().get("data", {})

        core_items = set()
        boot_items = set()
        
        for item_id, item_data in data.items():
            name = item_data.get("name")
            if not name:
                continue
                
            gold_info = item_data.get("gold", {})
            gold_total = gold_info.get("total", 0)
            purchasable = gold_info.get("purchasable", True)
            tags = item_data.get("tags", [])

            # Ignorar ítems que no están en la Grieta del Invocador (mapa 11)
            if not item_data.get("maps", {}).get("11", False):
                continue
                
            # Sólo ítems comprables normalmente (evitar items de eventos raros)
            if not purchasable:
                continue

            # Excepciones
            if "Trinket" in tags or "Elixir" in name or "Potion" in name:
                continue

            # Clasificación
            if "Boots" in tags and gold_total > 500:
                boot_items.add(name)
            elif gold_total >= 1500:
                core_items.add(name)

        # Guardar en caché
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CORE_ITEMS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"core": list(core_items), "boots": list(boot_items)}, f)

        logger.info(f"Caché de DDragon actualizada: {len(core_items)} core, {len(boot_items)} boots detectados.")
        return core_items, boot_items

    except Exception as e:
        logger.error(f"Error consultando DDragon: {e}")
        # Si falla, retornar un set de emergencia básico
        return {"Trinity Force", "Sundered Sky", "Black Cleaver", "Guardian Angel", "Sterak's Gage"}, {"Mercury's Treads", "Plated Steelcaps"}
