"""
build_engine.py — Motor de consulta de builds profesionales.

Carga el Parquet generado por sync_pro_builds.py y responde queries
del Build Lab: builds más comunes de un campeón, filtradas por
liga, patch, aliados y rivales.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
from supabase import create_client, Client
from dotenv import load_dotenv

from src.utils.ddragon import fetch_core_items

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_PARQUET = Path("data/processed/pro_builds/pro_builds.parquet")

ROLE_ORDER = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
ROLE_LOWER = ["top", "jungle", "mid", "bot", "support"]

# Ítems que no forman parte del "core" de la build y se excluyen del clustering
STARTER_OR_CONSUMABLE = {
    "Doran's Blade", "Doran's Ring", "Doran's Shield",
    "Health Potion", "Refillable Potion", "Corrupting Potion",
    "Stealth Ward", "Control Ward", "Oracle Lens", "Farsight Alteration",
    "Elixir of Iron", "Elixir of Sorcery", "Elixir of Wrath",
    "Long Sword", "Amplifying Tome", "Cloth Armor", "Null-Magic Mantle",
    "Cull", "Dark Seal",
}


# ─────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────

@dataclass
class ItemStep:
    """Un paso de compra en la build."""
    name: str
    avg_min: float    # minuto promedio en que se compra
    freq: int         # en cuántas partidas aparece en este slot


@dataclass
class BuildPattern:
    """Un patrón de build (secuencia de ítems core más frecuente)."""
    rank: int                           # 1 = más frecuente
    core_items: list[ItemStep]          # ítems en orden cronológico (sin botas)
    boot_item: ItemStep | None          # Bota final más común
    n_games: int                        # partidas donde se usó esta build
    win_rate: float                     # WR con esta build
    ally_comps: list[dict]              # Composiciones aliadas enfrentadas
    enemy_comps: list[dict]             # Composiciones enemigas enfrentadas


@dataclass
class ChampionContextEntry:
    champion: str
    n_games: int
    win_rate: float


@dataclass
class BuildQueryResult:
    champion: str
    n_games: int
    win_rate: float
    role_dist: dict[str, int]             # role → n_games
    leagues_dist: dict[str, int]
    patches_dist: dict[str, int]
    builds: list[BuildPattern]            # patrones de build encontrados
    ally_freq: list[ChampionContextEntry] # aliados más frecuentes
    enemy_freq: list[ChampionContextEntry] # rivales más frecuentes
    ally_role_freq: dict[str, list[tuple[str, dict]]] = field(default_factory=dict) # role -> [(champ, stats)]
    enemy_role_freq: dict[str, list[tuple[str, dict]]] = field(default_factory=dict) # role -> [(champ, stats)]


# ─────────────────────────────────────────────────────────────────────
# MOTOR
# ─────────────────────────────────────────────────────────────────────

class BuildEngine:

    def __init__(self, parquet_path: Path | str = DEFAULT_PARQUET):
        self.parquet_path = Path(parquet_path)
        self._df: Optional[pd.DataFrame] = None
        self.core_items, self.boot_items = fetch_core_items()

    def load(self) -> bool:
        # Intentar cargar desde Supabase primero
        url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
        
        df = pd.DataFrame()
        loaded_from_supabase = False
        
        if url and key:
            try:
                supabase: Client = create_client(url, key)
                logger.info("Cargando pro_builds desde Supabase...")
                all_rows = []
                off = 0
                lim = 1000
                while True:
                    r = supabase.table("pro_builds").select("*").range(off, off + lim - 1).execute()
                    if not r or not r.data: break
                    all_rows.extend(r.data)
                    if len(r.data) < lim: break
                    off += lim
                
                if all_rows:
                    df = pd.DataFrame(all_rows)
                    loaded_from_supabase = True
                    logger.info(f"Cargados {len(df)} registros desde Supabase.")
                else:
                    logger.warning("La tabla pro_builds en Supabase está vacía.")
            except Exception as e:
                logger.error(f"Error conectando a Supabase para pro_builds: {e}")

        # Fallback a Parquet local
        if not loaded_from_supabase:
            if not self.parquet_path.exists():
                logger.warning(
                    f"No se encontró Supabase ni {self.parquet_path}. "
                    "Ejecuta sync_pro_builds.py primero."
                )
                self._df = pd.DataFrame()
                return False
            logger.info("Cargando pro_builds desde Parquet local...")
            df = pd.read_parquet(self.parquet_path)

        if df.empty:
            self._df = df
            return False

        # Normalizar champion y nombres de ítems
        df["champion"] = df["champion"].str.strip()
        df["patch"]    = df["patch"].astype(str).str.strip()
        df["league"]   = df["league"].str.strip()
        self._df = df
        logger.info(
            f"BuildEngine listo: {len(df)} registros | "
            f"Ligas: {df['league'].value_counts().to_dict()} | "
            f"Patches: {sorted(df['patch'].unique())}"
        )
        return True

    @property
    def df(self) -> pd.DataFrame:
        if self._df is None:
            self.load()
        return self._df  # type: ignore

    def is_ready(self) -> bool:
        return self._df is not None and not self._df.empty

    def available_patches(self) -> list[str]:
        if not self.is_ready():
            return []
        patches = [p for p in self._df["patch"].unique() if p and p != "unknown"]
        return sorted(patches, reverse=True)

    def available_leagues(self) -> list[str]:
        if not self.is_ready():
            return []
        return sorted(self._df["league"].unique())

    def available_champions(self) -> list[str]:
        if not self.is_ready():
            return []
        return sorted(self._df["champion"].unique())

    # ─────────────────────────────────────────────────────────────────
    # QUERY PRINCIPAL
    # ─────────────────────────────────────────────────────────────────

    def query_champion(
        self,
        champion: str,
        leagues: list[str],
        patches: list[str] | None = None,
        role: str | None = None,
        allies: list[str] | None = None,
        rivals: list[str] | None = None,
        top_builds: int = 5,
        top_context: int = 15,
    ) -> BuildQueryResult:
        """
        Consulta builds de un campeón con filtros opcionales.
        - leagues: ligas a incluir
        - patches: patches a incluir (None = todos)
        - role: filtrar por rol (None = todos los roles)
        - allies: lista de campeones aliados que deben estar presentes
        - rivals: lista de campeones rivales que deben estar presentes
        """
        if not self.is_ready():
            return _empty_result(champion)

        df = self._filter(champion, leagues, patches, role, allies, rivals)

        if df.empty:
            return _empty_result(champion)

        n_games   = len(df)
        win_rate  = df["result"].mean() if "result" in df.columns else 0.0
        role_dist = df["role"].value_counts().to_dict()
        leagues_d = df["league"].value_counts().to_dict()
        patches_d = df["patch"].value_counts().to_dict()

        builds    = self._build_patterns(df, top_n=top_builds)
        ally_freq = self._context_freq(df, side="ally", top_n=top_context)
        enemy_freq = self._context_freq(df, side="enemy", top_n=top_context)

        return BuildQueryResult(
            champion=champion,
            n_games=n_games,
            win_rate=win_rate,
            role_dist=role_dist,
            leagues_dist=leagues_d,
            patches_dist=patches_d,
            builds=builds,
            ally_freq=ally_freq,
            enemy_freq=enemy_freq,
            ally_role_freq=self._role_context_freq(df, side="ally", top_n=top_context),
            enemy_role_freq=self._role_context_freq(df, side="enemy", top_n=top_context),
        )

    # ─────────────────────────────────────────────────────────────────
    # FILTRADO
    # ─────────────────────────────────────────────────────────────────

    def _filter(
        self,
        champion: str,
        leagues: list[str],
        patches: list[str] | None,
        role: str | None,
        allies: list[str] | None,
        rivals: list[str] | None,
    ) -> pd.DataFrame:
        df = self.df.copy()

        # Campeón (case-insensitive)
        df = df[df["champion"].str.lower() == champion.lower()]
        if df.empty:
            return df

        # Ligas
        if leagues:
            df = df[df["league"].isin(leagues)]
        if df.empty:
            return df

        # Patches
        if patches:
            df = df[df["patch"].isin(patches)]
        if df.empty:
            return df

        # Rol
        if role:
            df = df[df["role"].str.upper() == role.upper()]
        if df.empty:
            return df

        # Aliados: todos los aliados especificados deben aparecer en el mismo equipo
        if allies:
            for ally in allies:
                ally_lower = ally.lower().strip()
                ally_mask = pd.Series(False, index=df.index)
                for r in ROLE_ORDER:
                    col = f"ally_{r.lower()}"
                    if col in df.columns:
                        ally_mask |= df[col].str.lower().fillna("") == ally_lower
                df = df[ally_mask]
                if df.empty:
                    return df

        # Rivales: todos los rivales especificados deben aparecer en el equipo contrario
        if rivals:
            for rival in rivals:
                rival_lower = rival.lower().strip()
                rival_mask = pd.Series(False, index=df.index)
                for r in ROLE_ORDER:
                    col = f"enemy_{r.lower()}"
                    if col in df.columns:
                        rival_mask |= df[col].str.lower().fillna("") == rival_lower
                df = df[rival_mask]
                if df.empty:
                    return df

        return df

    # ─────────────────────────────────────────────────────────────────
    # BUILD PATTERNS
    # ─────────────────────────────────────────────────────────────────

    def _build_patterns(self, df: pd.DataFrame, top_n: int = 5) -> list[BuildPattern]:
        """
        Agrupa las builds por secuencia de ítems core y retorna las top_n más frecuentes.
        Clustering: los primeros 4 ítems significativos (sin consumibles/starters).
        """
        # Construir lista de (core_key, full_item_sequence) por partida
        patterns: dict[tuple, list[dict]] = {}

        for _, row in df.iterrows():
            build_seq = row.get("build")
            
            # Soporte dual: si la caché antigua (Parquet) tiene lista vacía o nula,
            # intentamos leer la lista. Si no existe, ignorar.
            if not isinstance(build_seq, (list, tuple)) or not build_seq:
                continue

            full_seq: list[tuple[str, int]] = []
            core_seq: list[tuple[str, int]] = []
            final_boot: tuple[str, int] | None = None
            seen_core = set()
            
            for item in build_seq:
                name = item.get("name")
                minute = item.get("minute", 0)
                if not name:
                    continue
                name_str = str(name).strip()
                minute_val = int(minute) if pd.notna(minute) else 0
                
                full_seq.append((name_str, minute_val))
                
                is_core = name_str in self.core_items
                is_boot = name_str in self.boot_items
                
                # Las botas van a final_boot (si compran otra, se actualiza, guardando la mejora final)
                if is_boot:
                    final_boot = (name_str, minute_val)
                # Los ítems core van a core_seq
                elif is_core and name_str not in seen_core:
                    core_seq.append((name_str, minute_val))
                    seen_core.add(name_str)

            # Clusterizar exclusivamente por los primeros 2 ítems CORE reales (sin botas)
            cluster_tuple = tuple(name for name, _ in core_seq)[:2]

            if not cluster_tuple:
                continue

            win = bool(row.get("result", False))
            if cluster_tuple not in patterns:
                patterns[cluster_tuple] = []
            
            # Recolectar composición completa
            ally_comp = {
                "TOP": str(row.get("ally_top", "")).strip(),
                "JUNGLE": str(row.get("ally_jungle", "")).strip(),
                "MID": str(row.get("ally_mid", "")).strip(),
                "BOT": str(row.get("ally_bot", "")).strip(),
                "SUPPORT": str(row.get("ally_support", "")).strip(),
            }
            enemy_comp = {
                "TOP": str(row.get("enemy_top", "")).strip(),
                "JUNGLE": str(row.get("enemy_jungle", "")).strip(),
                "MID": str(row.get("enemy_mid", "")).strip(),
                "BOT": str(row.get("enemy_bot", "")).strip(),
                "SUPPORT": str(row.get("enemy_support", "")).strip(),
            }
            
            # Pasamos la secuencia core filtrada, deduplicada y la bota final
            patterns[cluster_tuple].append({
                "seq": core_seq, 
                "win": win, 
                "ally_comp": ally_comp,
                "enemy_comp": enemy_comp, 
                "boot": final_boot
            })

        if not patterns:
            return []

        # Ordenar por frecuencia
        sorted_patterns = sorted(patterns.items(), key=lambda x: len(x[1]), reverse=True)

        results = []
        for rank, (core, games) in enumerate(sorted_patterns[:top_n], start=1):
            n = len(games)
            wr = sum(g["win"] for g in games) / n if n > 0 else 0.0
            a_comps = [g["ally_comp"] for g in games]
            e_comps = [g["enemy_comp"] for g in games]
            
            # Asegurar que 'win' esté en los comps para análisis posterior si es necesario
            for i, g in enumerate(games):
                a_comps[i]["win"] = g["win"]
                e_comps[i]["win"] = g["win"]

            # Calcular items con minuto promedio
            merged_seq = {}
            for g in games:
                for idx, (item, min_val) in enumerate(g["seq"]):
                    if idx not in merged_seq:
                        merged_seq[idx] = []
                    merged_seq[idx].append((item, min_val))

            item_steps: list[ItemStep] = []
            seen_in_visual = set()
            # Tomamos hasta el tamaño de la secuencia máxima observada en este cluster
            max_len = max(len(g["seq"]) for g in games)
            for i in range(max_len):
                if i in merged_seq:
                    items_at_i = merged_seq[i]
                    # Nombre más común en esta posición que no hayamos agregado ya
                    from collections import Counter
                    c = Counter(name for name, _ in items_at_i if name not in seen_in_visual)
                    if not c:
                        continue
                        
                    most_common_name = c.most_common(1)[0][0]
                    seen_in_visual.add(most_common_name)
                    
                    # Promedio de minuto
                    mins = [m for name, m in items_at_i if name == most_common_name and m > 0]
                    avg_min = sum(mins) / len(mins) if mins else 0
                    freq = sum(1 for name, _ in items_at_i if name == most_common_name)
                    
                    item_steps.append(ItemStep(name=most_common_name, avg_min=avg_min, freq=freq))

            # Calcular bota final más común
            boot_item = None
            valid_boots = [g["boot"] for g in games if g["boot"] is not None]
            if valid_boots:
                from collections import Counter
                c = Counter(b_name for b_name, _ in valid_boots)
                most_common_boot = c.most_common(1)[0][0]
                mins = [m for b_name, m in valid_boots if b_name == most_common_boot and m > 0]
                avg_min = sum(mins) / len(mins) if mins else 0
                freq = sum(1 for b_name, _ in valid_boots if b_name == most_common_boot)
                boot_item = ItemStep(name=most_common_boot, avg_min=avg_min, freq=freq)

            results.append(BuildPattern(
                rank=rank,
                core_items=item_steps,
                boot_item=boot_item,
                n_games=n,
                win_rate=wr,
                ally_comps=a_comps,
                enemy_comps=e_comps
            ))

        return results

    # ─────────────────────────────────────────────────────────────────
    # CONTEXTO ALIADOS / RIVALES
    # ─────────────────────────────────────────────────────────────────

    def _context_freq(
        self,
        df: pd.DataFrame,
        side: str,  # "ally" o "enemy"
        top_n: int = 15,
    ) -> list[ChampionContextEntry]:
        """
        Retorna los campeones más frecuentes como aliados o rivales,
        con el WR del campeón principal cuando ese aliado/rival está presente.
        """
        counter: dict[str, dict] = {}

        for _, row in df.iterrows():
            win = bool(row.get("result", False))
            for r in ROLE_ORDER:
                col = f"{side}_{r.lower()}"
                champ = row.get(col, "")
                if not champ or pd.isna(champ) or not str(champ).strip():
                    continue
                champ = str(champ).strip()
                if champ not in counter:
                    counter[champ] = {"n": 0, "wins": 0}
                counter[champ]["n"] += 1
                counter[champ]["wins"] += 1 if win else 0

        sorted_entries = sorted(counter.items(), key=lambda x: x[1]["n"], reverse=True)

        result = []
        for champ, stats in sorted_entries[:top_n]:
            n = stats["n"]
            wr = stats["wins"] / n if n > 0 else 0.0
            result.append(ChampionContextEntry(champion=champ, n_games=n, win_rate=wr))

        return result

    def _role_context_freq(
        self,
        df: pd.DataFrame,
        side: str,
        top_n: int = 15,
    ) -> dict[str, list[tuple[str, dict]]]:
        """Agrupa frecuencias de aliados/rivales por rol."""
        result = {}
        for role in ROLE_ORDER:
            counter: dict[str, dict] = {}
            col = f"{side}_{role.lower()}"
            if col not in df.columns:
                result[role] = []
                continue
                
            for _, row in df.iterrows():
                champ = row.get(col, "")
                if not champ or pd.isna(champ) or not str(champ).strip():
                    continue
                champ = str(champ).strip()
                win = bool(row.get("result", False))
                
                if champ not in counter:
                    counter[champ] = {"n": 0, "wins": 0}
                counter[champ]["n"] += 1
                if win:
                    counter[champ]["wins"] += 1
            
            sorted_role = []
            for champ, stats in counter.items():
                stats["wr"] = stats["wins"] / stats["n"]
                sorted_role.append((champ, stats))
            
            result[role] = sorted(sorted_role, key=lambda x: x[1]["n"], reverse=True)[:top_n]
        return result


# ─────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────

def _empty_result(champion: str) -> BuildQueryResult:
    return BuildQueryResult(
        champion=champion,
        n_games=0,
        win_rate=0.0,
        role_dist={},
        leagues_dist={},
        patches_dist={},
        builds=[],
        ally_freq=[],
        enemy_freq=[],
        ally_role_freq={r: [] for r in ROLE_ORDER},
        enemy_role_freq={r: [] for r in ROLE_ORDER},
    )
