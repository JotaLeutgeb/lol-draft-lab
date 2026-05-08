"""
draft_engine.py — Motor de consulta de drafts profesionales.

Carga el Parquet generado por sync_pro_drafts.py y responde
queries de análisis de draft dado un estado parcial.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────────────

ROLE_ORDER = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
ROLE_COLS  = {
    "blue": ["blue_top", "blue_jg", "blue_mid", "blue_bot", "blue_sup"],
    "red":  ["red_top",  "red_jg",  "red_mid",  "red_bot",  "red_sup"],
}
ROLE_TO_COL = {
    "blue": {r: c for r, c in zip(ROLE_ORDER, ROLE_COLS["blue"])},
    "red":  {r: c for r, c in zip(ROLE_ORDER, ROLE_COLS["red"])},
}

BAN_COLS = {
    "blue": ["blue_ban1", "blue_ban2", "blue_ban3", "blue_ban4", "blue_ban5"],
    "red":  ["red_ban1",  "red_ban2",  "red_ban3",  "red_ban4",  "red_ban5"],
}

SEQ_PICK_COLS = {
    "blue": ["blue_pick1", "blue_pick2", "blue_pick3", "blue_pick4", "blue_pick5"],
    "red":  ["red_pick1",  "red_pick2",  "red_pick3",  "red_pick4",  "red_pick5"],
}

DEFAULT_PARQUET = Path("data/processed/pro_drafts/pro_drafts.parquet")

# ─────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────

@dataclass
class DraftState:
    """Estado actual del simulador de draft."""
    # Picks por lado y rol (None = vacío)
    blue_picks: dict[str, Optional[str]] = field(
        default_factory=lambda: {r: None for r in ROLE_ORDER}
    )
    red_picks: dict[str, Optional[str]] = field(
        default_factory=lambda: {r: None for r in ROLE_ORDER}
    )
    # Bans: fase 1 (3 por lado) + fase 2 (2 por lado)
    blue_bans: list[Optional[str]] = field(default_factory=lambda: [None] * 5)
    red_bans:  list[Optional[str]] = field(default_factory=lambda: [None] * 5)

    def all_known_champs(self) -> set[str]:
        """Todos los campeones ya asignados (cualquier lado)."""
        picks = (
            list(self.blue_picks.values()) +
            list(self.red_picks.values()) +
            self.blue_bans + self.red_bans
        )
        return {c for c in picks if c}

    def known_picks(self, side: str) -> dict[str, str]:
        """Picks ya asignados para un lado."""
        source = self.blue_picks if side == "blue" else self.red_picks
        return {role: champ for role, champ in source.items() if champ}

    def empty_roles(self, side: str) -> list[str]:
        """Roles sin pick para un lado."""
        source = self.blue_picks if side == "blue" else self.red_picks
        return [role for role, champ in source.items() if not champ]


@dataclass
class PickSuggestion:
    champion: str
    role: str
    win_rate: float
    n_games: int
    leagues: dict[str, int]   # {"LCK": 12, "LEC": 5, ...}


@dataclass
class CounterSuggestion:
    """Qué pickeo el lado contrario en situaciones similares."""
    champion: str
    role: str
    win_rate: float           # Win rate del lado que pickeó este champ
    n_games: int
    leagues: dict[str, int]


@dataclass
class DraftAnalysisResult:
    # Para el panel "Respuestas Pro" (qué pickeó el rival agrupado por rol)
    counter_picks: dict[str, list[CounterSuggestion]]  # role → [sugerencias]
    # Para el panel "Next Picks" (mis líneas vacías)
    next_picks: dict[str, list[PickSuggestion]]        # role → [sugerencias]
    # Meta
    n_matching_games: int
    wr_compo_completa: Optional[float]           # Si el draft está completo
    leagues_distribution: dict[str, int]
    patches_found: list[str]



# ─────────────────────────────────────────────────────────────────────
# NORMALIZACIÓN DE NOMBRES
# ─────────────────────────────────────────────────────────────────────

# Alias → nombre canónico (OE y Leaguepedia a veces difieren)
CHAMP_ALIASES: dict[str, str] = {
    "Nunu & Willump": "Nunu",
    "Nunu &Willump": "Nunu",
    "Wukong": "MonkeyKing",
    "Renata Glasc": "Renata",
    "Cho'Gath": "Chogath",
    "Kha'Zix": "Khazix",
    "Kog'Maw": "KogMaw",
    "Kai'Sa": "Kaisa",
    "Bel'Veth": "Belveth",
    "K'Sante": "KSante",
    "Vel'Koz": "Velkoz",
    "Rek'Sai": "RekSai",
    "LeBlanc": "Leblanc",
    "Jarvan IV": "JarvanIV",
    "Dr. Mundo": "DrMundo",
    "Miss Fortune": "MissFortune",
    "Twisted Fate": "TwistedFate",
    "Lee Sin": "LeeSin",
    "Master Yi": "MasterYi",
    "Tahm Kench": "TahmKench",
    "Xin Zhao": "XinZhao",
    "Aurelion Sol": "AurelionSol",
}


def normalize_champ(name: str) -> str:
    if not name:
        return name
    name = str(name).strip()
    return CHAMP_ALIASES.get(name, name)


# ─────────────────────────────────────────────────────────────────────
# MOTOR
# ─────────────────────────────────────────────────────────────────────

class DraftEngine:
    def __init__(self, parquet_path: Path | str = DEFAULT_PARQUET):
        self.parquet_path = Path(parquet_path)
        self._df: Optional[pd.DataFrame] = None

    def load(self) -> bool:
        """Carga y normaliza el Parquet. Retorna True si hay datos."""
        if not self.parquet_path.exists():
            logger.warning(f"No se encontró {self.parquet_path}. Ejecuta sync_pro_drafts.py primero.")
            self._df = pd.DataFrame()
            return False

        df = pd.read_parquet(self.parquet_path)

        # Normalizar nombres de campeones en todas las columnas de picks y bans
        champ_cols = (
            ROLE_COLS["blue"] + ROLE_COLS["red"] +
            BAN_COLS["blue"] + BAN_COLS["red"] +
            SEQ_PICK_COLS["blue"] + SEQ_PICK_COLS["red"]
        )
        for col in champ_cols:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: normalize_champ(x) if pd.notna(x) else None)

        self._df = df
        logger.info(
            f"DraftEngine cargado: {len(df)} partidas | "
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
        return sorted(self.df["patch"].unique(), reverse=True)

    def available_leagues(self) -> list[str]:
        if not self.is_ready():
            return []
        return sorted(self.df["league"].unique())

    # ─────────────────────────────────────────────────────────────────
    # FILTRADO
    # ─────────────────────────────────────────────────────────────────

    def _filter_base(
        self,
        leagues:    list[str],
        patches:    list[str] | None,
        patch_tolerance: int = 1,
    ) -> pd.DataFrame:
        """
        Filtra el dataframe base por ligas y patches.
        patch_tolerance: cuántos patches adicionales hacia atrás incluir.
        """
        df = self.df.copy()

        # Filtro de ligas
        if leagues:
            df = df[df["league"].isin(leagues)]

        # Filtro de patches (con tolerancia ±N patches)
        if patches:
            target_patches = set()
            for p in patches:
                target_patches.update(_expand_patch_range(p, patch_tolerance))
            df = df[df["patch"].isin(target_patches)]

        return df

    def predict_timeline_picks(
        self,
        state: DraftState,
        first_pick_side: str,
        leagues: list[str],
        patch: Optional[list[str]] = None,
        patch_tolerance: int = 2,
    ) -> list[dict]:
        """
        Calcula las predicciones probabilísticas de ROL/LÍNEA para los 10 slots
        del timeline de selección pro basados en la base de datos histórica.
        """
        if not self.is_ready():
            return []

        # 1. Filtro base de ligas y parches
        patches = _expand_patch_range(patch, patch_tolerance) if patch else None
        df_base = self._filter_base(leagues, patches, patch_tolerance)
        if df_base.empty:
            return []

        # 2. Obtener picks activos por bando (con su rol asignado)
        blue_active = [(r, c) for r, c in state.blue_picks.items() if c]
        red_active = [(r, c) for r, c in state.red_picks.items() if c]

        # 3. Filtrar el dataframe para que coincida con los campeones asignados hasta ahora (Side-agnóstico dentro de cada lado)
        df_matched = df_base.copy()
        
        # Para el Blue Side
        for r, champ in blue_active:
            blue_cols = ["blue_pick1", "blue_pick2", "blue_pick3", "blue_pick4", "blue_pick5"]
            mask = pd.Series(False, index=df_matched.index)
            for col_p in blue_cols:
                if col_p in df_matched.columns:
                    mask |= (df_matched[col_p].str.lower() == champ.lower())
            df_matched = df_matched[mask]

        # Para el Red Side
        for r, champ in red_active:
            red_cols = ["red_pick1", "red_pick2", "red_pick3", "red_pick4", "red_pick5"]
            mask = pd.Series(False, index=df_matched.index)
            for col_p in red_cols:
                if col_p in df_matched.columns:
                    mask |= (df_matched[col_p].str.lower() == champ.lower())
            df_matched = df_matched[mask]

        if len(df_matched) < 3:
            df_matched = df_base.copy()

        # Definir secuencia cronológica de slots según quién tiene First Pick
        if first_pick_side == "blue":
            sequence = [
                {"slot": "blue_pick1", "side": "blue", "num": 1},
                {"slot": "red_pick1",  "side": "red",  "num": 1},
                {"slot": "red_pick2",  "side": "red",  "num": 2},
                {"slot": "blue_pick2", "side": "blue", "num": 2},
                {"slot": "blue_pick3", "side": "blue", "num": 3},
                {"slot": "red_pick3",  "side": "red",  "num": 3},
                {"slot": "red_pick4",  "side": "red",  "num": 4},
                {"slot": "blue_pick4", "side": "blue", "num": 4},
                {"slot": "blue_pick5", "side": "blue", "num": 5},
                {"slot": "red_pick5",  "side": "red",  "num": 5},
            ]
        else:
            sequence = [
                {"slot": "red_pick1",  "side": "red",  "num": 1},
                {"slot": "blue_pick1", "side": "blue", "num": 1},
                {"slot": "blue_pick2", "side": "blue", "num": 2},
                {"slot": "red_pick2",  "side": "red",  "num": 2},
                {"slot": "red_pick3",  "side": "red",  "num": 3},
                {"slot": "blue_pick3", "side": "blue", "num": 3},
                {"slot": "blue_pick4", "side": "blue", "num": 4},
                {"slot": "red_pick4",  "side": "red",  "num": 4},
                {"slot": "red_pick5",  "side": "red",  "num": 5},
                {"slot": "blue_pick5", "side": "blue", "num": 5},
            ]

        timeline_results = []
        for step in sequence:
            side = step["side"]
            col = step["slot"]
            slot_num = step["num"]
            active_list = blue_active if side == "blue" else red_active
            
            if slot_num <= len(active_list):
                # Slot ya elegido: mostrar el campeón real fijado
                role_name, champ = active_list[slot_num - 1]
                timeline_results.append({
                    "step_name": f"{side.upper()} Pick {slot_num}",
                    "side": side,
                    "slot": col,
                    "status": "chosen",
                    "champion": champ,
                    "role": role_name,
                    "predictions": [],
                })
            else:
                # Slot vacío: predecir probabilísticamente qué rol se elegirá
                # Encontrar los roles que quedan vacíos para este bando
                empty_roles = [r for r, c in (state.blue_picks.items() if side == "blue" else state.red_picks.items()) if not c]
                
                # Calcular distribución histórica del slot
                role_counts = {}
                for r in ["top", "jg", "mid", "bot", "sup"]:
                    r_upper = r.upper() if r != "sup" else "SUPPORT"
                    if r_upper not in empty_roles:
                        continue
                    
                    col_role = f"{side}_{r}"
                    if col_role in df_matched.columns and col in df_matched.columns:
                        matches = (df_matched[col].str.lower() == df_matched[col_role].str.lower()).sum()
                        role_counts[r_upper] = float(matches)
                
                total_matches = sum(role_counts.values())
                predictions = []
                if total_matches > 0:
                    sorted_roles = sorted(role_counts.items(), key=lambda x: x[1], reverse=True)
                    for r_name, count in sorted_roles:
                        pct = count / total_matches
                        if pct > 0.0:
                            predictions.append({
                                "role": r_name,
                                "percentage": pct
                            })
                
                timeline_results.append({
                    "step_name": f"{side.upper()} Pick {slot_num}",
                    "side": side,
                    "slot": col,
                    "status": "empty",
                    "champion": None,
                    "role": None,
                    "predictions": predictions,
                })

        return timeline_results

    def query(
        self,
        state:           DraftState,
        our_side:        str,          # "blue" o "red"
        leagues:         list[str],
        patch:           str | None = None,
        patch_tolerance: int = 1,
        min_games:       int = 3,
        top_n:           int = 10,
    ) -> DraftAnalysisResult:
        """
        Dado un estado de draft (picks y bans conocidos):
        - Si no hay picks aún pero hay bans asignados, hace un match basado en solapamiento de bans.
        - De lo contrario, hace un match side-agnóstico de campeones pickeados.
        - Excluye campeones baneados y ya pickeados de las recomendaciones resultantes.
        """
        if not self.is_ready():
            return _empty_result()

        # 1. Filtro base de ligas y parches
        patches = _expand_patch_range(patch, patch_tolerance) if patch else None
        df_base = self._filter_base(leagues, patches, patch_tolerance)

        if df_base.empty:
            return _empty_result()

        # 2. Match de draft state
        our_picks = state.known_picks(our_side)
        active_bans = [b.lower() for b in state.blue_bans + state.red_bans if b]
        
        mask_as_blue = pd.Series([True] * len(df_base), index=df_base.index)
        mask_as_red  = pd.Series([True] * len(df_base), index=df_base.index)

        has_picks = False
        for role, champ in our_picks.items():
            has_picks = True
            col_b = ROLE_TO_COL["blue"].get(role)
            col_r = ROLE_TO_COL["red"].get(role)
            if col_b and col_b in df_base.columns:
                mask_as_blue &= df_base[col_b].str.lower() == champ.lower()
            if col_r and col_r in df_base.columns:
                mask_as_red &= df_base[col_r].str.lower() == champ.lower()

        if not has_picks:
            if active_bans:
                all_ban_cols = BAN_COLS["blue"] + BAN_COLS["red"]
                ban_overlap_count = pd.Series(0, index=df_base.index)
                for col in all_ban_cols:
                    if col in df_base.columns:
                        ban_overlap_count += df_base[col].str.lower().isin(active_bans).astype(int)
                df_matched = df_base[ban_overlap_count >= 1].copy()
                if df_matched.empty or len(df_matched) < min_games:
                    patches_wide = _expand_patch_range(patch, patch_tolerance + 2) if patch else None
                    df_base_wide = self._filter_base(leagues, patches_wide, patch_tolerance + 2)
                    ban_overlap_count_w = pd.Series(0, index=df_base_wide.index)
                    for col in all_ban_cols:
                        if col in df_base_wide.columns:
                            ban_overlap_count_w += df_base_wide[col].str.lower().isin(active_bans).astype(int)
                    df_matched = df_base_wide[ban_overlap_count_w >= 1].copy()
                    if df_matched.empty:
                        df_matched = df_base_wide.copy()
            else:
                df_matched = df_base.copy()
                
            df_matched["matched_our_side"] = our_side
            df_matched["matched_rival_side"] = "red" if our_side == "blue" else "blue"
        else:
            df_as_blue = df_base[mask_as_blue].copy()
            df_as_blue["matched_our_side"] = "blue"
            df_as_blue["matched_rival_side"] = "red"

            df_as_red = df_base[mask_as_red].copy()
            df_as_red["matched_our_side"] = "red"
            df_as_red["matched_rival_side"] = "blue"

            df_matched = pd.concat([df_as_blue, df_as_red]).drop_duplicates(subset=["gameid", "matched_our_side"])

            if df_matched.empty or len(df_matched) < min_games:
                logger.info(
                    f"Solo {len(df_matched)} partidas. Ampliando tolerancia de patch a {patch_tolerance + 2}."
                )
                patches_wide = _expand_patch_range(patch, patch_tolerance + 2) if patch else None
                df_base_wide = self._filter_base(leagues, patches_wide, patch_tolerance + 2)
                
                mask_as_blue_w = pd.Series([True] * len(df_base_wide), index=df_base_wide.index)
                mask_as_red_w  = pd.Series([True] * len(df_base_wide), index=df_base_wide.index)
                for role, champ in our_picks.items():
                    col_b = ROLE_TO_COL["blue"].get(role)
                    col_r = ROLE_TO_COL["red"].get(role)
                    if col_b and col_b in df_base_wide.columns:
                        mask_as_blue_w &= df_base_wide[col_b].str.lower() == champ.lower()
                    if col_r and col_r in df_base_wide.columns:
                        mask_as_red_w &= df_base_wide[col_r].str.lower() == champ.lower()

                df_as_blue_w = df_base_wide[mask_as_blue_w].copy()
                df_as_blue_w["matched_our_side"] = "blue"
                df_as_blue_w["matched_rival_side"] = "red"

                df_as_red_w = df_base_wide[mask_as_red_w].copy()
                df_as_red_w["matched_our_side"] = "red"
                df_as_red_w["matched_rival_side"] = "blue"

                df_matched = pd.concat([df_as_blue_w, df_as_red_w]).drop_duplicates(subset=["gameid", "matched_our_side"])

        # Generar las columnas dinámicas de picks por side mapeado
        for role in ROLE_ORDER:
            col_b = ROLE_TO_COL["blue"][role]
            col_r = ROLE_TO_COL["red"][role]
            df_matched[f"rival_{role}"] = df_matched[col_b].where(df_matched["matched_rival_side"] == "blue", df_matched[col_r])
            df_matched[f"our_{role}"]   = df_matched[col_b].where(df_matched["matched_our_side"] == "blue", df_matched[col_r])

        n_games = len(df_matched)
        leagues_dist = df_matched["league"].value_counts().to_dict()
        patches_found = df_matched["patch"].unique().tolist()

        # 3. WR de la compo (solo si el draft está completo)
        wr_completa = None
        our_all_filled = all(v for v in state.blue_picks.values() if our_side == "blue") or \
                         all(v for v in state.red_picks.values() if our_side == "red")
        if our_all_filled and n_games > 0:
            wins = (df_matched["winner"].str.lower() == df_matched["matched_our_side"].str.lower()).sum()
            wr_completa = wins / n_games

        # Construir pool de ignorados (bans + ya elegidos)
        banned_champs = [b.lower() for b in state.blue_bans + state.red_bans if b]
        picked_champs = [p.lower() for p in list(state.blue_picks.values()) + list(state.red_picks.values()) if p]
        ignored_champs = set(banned_champs + picked_champs)

        # 4. Counter picks (qué pickeó el rival)
        rival_side = "red" if our_side == "blue" else "blue"
        rival_picks = state.known_picks(rival_side)
        ignore_roles = list(rival_picks.keys())

        counter_picks = self._build_counter_picks(df_matched, top_n=10, ignore_roles=ignore_roles, ignored_champs=ignored_champs)

        # 5. Next picks (para nuestras líneas vacías)
        empty_roles = state.empty_roles(our_side)
        next_picks  = self._build_next_picks(df_matched, empty_roles, top_n=10, ignored_champs=ignored_champs)

        return DraftAnalysisResult(
            counter_picks=counter_picks,
            next_picks=next_picks,
            n_matching_games=n_games,
            wr_compo_completa=wr_completa,
            leagues_distribution=leagues_dist,
            patches_found=sorted(patches_found, reverse=True),
        )

    def _build_counter_picks(
        self,
        df: pd.DataFrame,
        top_n: int,
        ignore_roles: list[str] = [],
        ignored_champs: set[str] = set(),
    ) -> dict[str, list[CounterSuggestion]]:
        """Extrae los picks más frecuentes del rival agrupados por línea con WR (filtrando ignorados)."""
        result: dict[str, list[CounterSuggestion]] = {}
        for role in ROLE_ORDER:
            if role in ignore_roles:
                continue
            col = f"rival_{role}"
            if col not in df.columns:
                result[role] = []
                continue
            sub = df[df[col].notna()]
            suggestions = []
            for champ, grp in sub.groupby(col):
                if champ.lower() in ignored_champs:
                    continue
                wins = (grp["winner"].str.lower() == grp["matched_rival_side"].str.lower()).sum()
                wr   = wins / len(grp) if len(grp) > 0 else 0.0
                suggestions.append(CounterSuggestion(
                    champion=champ,
                    role=role,
                    win_rate=wr,
                    n_games=len(grp),
                    leagues=grp["league"].value_counts().to_dict(),
                ))
            suggestions.sort(key=lambda x: (x.n_games, x.win_rate), reverse=True)
            result[role] = suggestions[:top_n]
        return result

    def _build_next_picks(
        self,
        df: pd.DataFrame,
        empty_roles: list[str],
        top_n: int = 10,
        ignored_champs: set[str] = set(),
    ) -> dict[str, list[PickSuggestion]]:
        """Para cada línea vacía, retorna los picks más frecuentes con WR (filtrando ignorados)."""
        result: dict[str, list[PickSuggestion]] = {}

        for role in empty_roles:
            col = f"our_{role}"
            if col not in df.columns:
                result[role] = []
                continue

            suggestions = []
            sub = df[df[col].notna()]
            for champ, grp in sub.groupby(col):
                if champ.lower() in ignored_champs:
                    continue
                wins = (grp["winner"].str.lower() == grp["matched_our_side"].str.lower()).sum()
                wr   = wins / len(grp) if len(grp) > 0 else 0.0
                suggestions.append(PickSuggestion(
                    champion=champ,
                    role=role,
                    win_rate=wr,
                    n_games=len(grp),
                    leagues=grp["league"].value_counts().to_dict(),
                ))

            suggestions.sort(key=lambda x: (x.n_games, x.win_rate), reverse=True)
            result[role] = suggestions[:top_n]

        return result




    def get_champion_stats(
        self,
        champion: str,
        role: str,
        leagues: list[str],
        patch: str | None = None,
        patch_tolerance: int = 2,
    ) -> dict:
        """Stats globales de un campeón en un rol (pick rate, ban rate, WR)."""
        if not self.is_ready():
            return {}

        patches = _expand_patch_range(patch, patch_tolerance) if patch else None
        df = self._filter_base(leagues, patches, patch_tolerance)
        total = len(df)
        if total == 0:
            return {}

        champ_norm = normalize_champ(champion)

        # Partidas donde fue pickeado
        pick_col_b = ROLE_TO_COL["blue"].get(role)
        pick_col_r = ROLE_TO_COL["red"].get(role)

        picked = pd.Series([False] * total, index=df.index)
        if pick_col_b and pick_col_b in df.columns:
            picked |= df[pick_col_b].str.lower() == champ_norm.lower()
        if pick_col_r and pick_col_r in df.columns:
            picked |= df[pick_col_r].str.lower() == champ_norm.lower()

        # Partidas donde fue baneado
        all_ban_cols = BAN_COLS["blue"] + BAN_COLS["red"]
        banned = pd.Series([False] * total, index=df.index)
        for bc in all_ban_cols:
            if bc in df.columns:
                banned |= df[bc].str.lower() == champ_norm.lower()

        df_picked = df[picked]
        wins_b = (df_picked[df_picked[pick_col_b].str.lower() == champ_norm.lower()]["winner"] == "Blue").sum() if pick_col_b and pick_col_b in df_picked.columns else 0
        wins_r = (df_picked[df_picked[pick_col_r].str.lower() == champ_norm.lower()]["winner"] == "Red").sum() if pick_col_r and pick_col_r in df_picked.columns else 0
        total_wins = wins_b + wins_r

        return {
            "champion":  champ_norm,
            "role":      role,
            "n_picked":  int(picked.sum()),
            "n_banned":  int(banned.sum()),
            "n_total_games": total,
            "pick_rate": picked.sum() / total if total > 0 else 0.0,
            "ban_rate":  banned.sum() / total if total > 0 else 0.0,
            "win_rate":  total_wins / int(picked.sum()) if picked.sum() > 0 else 0.0,
            "presence":  (picked.sum() + banned.sum()) / total if total > 0 else 0.0,
        }

    def get_champion_pick_order_stats(
        self,
        champion: str,
        role: str,
        leagues: list[str],
        patch: str | None = None,
        patch_tolerance: int = 2,
    ) -> dict:
        """
        Calcula estadísticas avanzadas del campeón según el orden de pick (Blind vs Counter) en un rol dado.
        """
        if not self.is_ready():
            return {}

        patches = _expand_patch_range(patch, patch_tolerance) if patch else None
        df = self._filter_base(leagues, patches, patch_tolerance)
        total = len(df)
        if total == 0:
            return {}

        champ_norm = normalize_champ(champion)

        blue_col = ROLE_TO_COL["blue"].get(role)
        red_col = ROLE_TO_COL["red"].get(role)

        records = []

        # Mapeos de turnos globales de selección (1 a 10)
        # Blue picks: p1 (Turno 1), p2 (Turno 4), p3 (Turno 5), p4 (Turno 8), p5 (Turno 9)
        # Red picks:  p1 (Turno 2), p2 (Turno 3), p3 (Turno 6), p4 (Turno 7), p5 (Turno 10)
        blue_turns = {1: 1, 2: 4, 3: 5, 4: 8, 5: 9}
        red_turns  = {1: 2, 2: 3, 3: 6, 4: 7, 5: 10}

        for _, row in df.iterrows():
            side = None
            if blue_col and blue_col in row and str(row[blue_col]).lower() == champ_norm.lower():
                side = "blue"
            elif red_col and red_col in row and str(row[red_col]).lower() == champ_norm.lower():
                side = "red"

            if side is None:
                continue

            rival_side = "red" if side == "blue" else "blue"
            winner = str(row["winner"]).lower()
            is_win = (winner == side)

            # Buscar en qué pick secuencial fue elegido nuestro champion y el rival
            our_champ = champ_norm
            rival_champ = row[f"{rival_side}_{role.lower()}"] if f"{rival_side}_{role.lower()}" in row else None

            # Secuencias de picks de cada equipo
            our_seq = [row[f"{side}_pick{i}"] for i in range(1, 6) if f"{side}_pick{i}" in row]
            rival_seq = [row[f"{rival_side}_pick{i}"] for i in range(1, 6) if f"{rival_side}_pick{i}" in row]

            try:
                our_pick_idx = our_seq.index(our_champ) + 1
                our_turn = blue_turns[our_pick_idx] if side == "blue" else red_turns[our_pick_idx]
            except ValueError:
                our_turn = None

            try:
                rival_pick_idx = rival_seq.index(rival_champ) + 1 if rival_champ and rival_champ in rival_seq else None
                rival_turn = (blue_turns[rival_pick_idx] if rival_side == "blue" else red_turns[rival_pick_idx]) if rival_pick_idx else None
            except ValueError:
                rival_turn = None

            # Determinar Blind vs Counter
            if our_turn is not None and rival_turn is not None:
                is_counter = (our_turn > rival_turn)
            else:
                is_counter = False

            records.append({
                "win": is_win,
                "is_counter": is_counter,
                "turn": our_turn,
            })

        if not records:
            return {}

        total_picked = len(records)
        blind_records = [r for r in records if not r["is_counter"]]
        counter_records = [r for r in records if r["is_counter"]]

        n_blind = len(blind_records)
        n_counter = len(counter_records)

        wr_blind = sum(r["win"] for r in blind_records) / n_blind if n_blind > 0 else 0.0
        wr_counter = sum(r["win"] for r in counter_records) / n_counter if n_counter > 0 else 0.0

        return {
            "champion": champ_norm,
            "role": role,
            "total_games": total_picked,
            "n_blind": n_blind,
            "n_counter": n_counter,
            "blind_rate": n_blind / total_picked,
            "counter_rate": n_counter / total_picked,
            "win_rate_blind": wr_blind,
            "win_rate_counter": wr_counter,
        }

    def get_champion_matchup_stats(
        self,
        champion: str,
        rival_champion: str | None,
        role: str,
        leagues: list[str],
        patch: str | None = None,
        patch_tolerance: int = 3,
    ) -> dict:
        """
        Calcula estadísticas de enfrentamiento directo (Matchup Head-to-Head) en un rol dado.
        """
        if not self.is_ready():
            return {}

        champ_norm = normalize_champ(champion)
        rival_norm = normalize_champ(rival_champion) if rival_champion else None

        if not rival_norm:
            return {"win_rate": None, "n_games": 0}

        patches = _expand_patch_range(patch, patch_tolerance) if patch else None
        df = self._filter_base(leagues, patches, patch_tolerance)
        total = len(df)
        if total == 0:
            return {"win_rate": None, "n_games": 0}

        blue_col = ROLE_TO_COL["blue"].get(role)
        red_col = ROLE_TO_COL["red"].get(role)

        if not blue_col or not red_col:
            return {"win_rate": None, "n_games": 0}

        # Filtrar partidas donde blue == champ y red == rival, o red == champ y blue == rival
        cond1 = (df[blue_col].str.lower() == champ_norm.lower()) & (df[red_col].str.lower() == rival_norm.lower())
        cond2 = (df[red_col].str.lower() == champ_norm.lower()) & (df[blue_col].str.lower() == rival_norm.lower())

        sub_df1 = df[cond1]
        sub_df2 = df[cond2]

        n_games = len(sub_df1) + len(sub_df2)
        if n_games == 0:
            return {"win_rate": None, "n_games": 0}

        wins_blue = (sub_df1["winner"].str.lower() == "blue").sum()
        wins_red = (sub_df2["winner"].str.lower() == "red").sum()
        total_wins = wins_blue + wins_red

        return {
            "win_rate": total_wins / n_games,
            "n_games": n_games,
        }


# ─────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────

def _expand_patch_range(patch: str | list[str] | None, tolerance: int) -> list[str] | None:
    """
    Expande un patch (o lista de patches) a una lista de patches cercanos.
    """
    if patch is None:
        return None
    if isinstance(patch, list):
        expanded = set()
        for p in patch:
            res = _expand_patch_range(p, tolerance)
            if res:
                expanded.update(res)
        return list(expanded) if expanded else None
    try:
        major, minor = map(int, patch.split("."))
        patches = []
        for delta in range(-tolerance, tolerance + 1):
            m = minor + delta
            if m > 0:
                patches.append(f"{major}.{m}")
        return patches
    except Exception:
        return [patch]


def _empty_result() -> DraftAnalysisResult:
    return DraftAnalysisResult(
        counter_picks=[],
        next_picks={},
        n_matching_games=0,
        wr_compo_completa=None,
        leagues_distribution={},
        patches_found=[],
    )
