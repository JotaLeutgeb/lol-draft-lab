"""
patterns.py — Detección automática de patrones de derrota.

Usa reglas estadísticas sobre el dataset de partidas para identificar
comportamientos presentes de forma recurrente en las derrotas del equipo.

Decisión de diseño: reglas > ML.
Con 20-100 partidas de un solo equipo, un modelo supervisado estaría
sobreentrenando en ruido. Las reglas son interpretables, auditables
y accionables en el contexto de coaching.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from src.config import (
    GOLD_DIFF_CRITICAL_THRESHOLD,
    LOW_OBJECTIVE_RATE,
    LOW_VISION_PER_MIN,
    PATTERN_LOSS_THRESHOLD,
    TEAM_PLAYERS,
)

logger = logging.getLogger(__name__)

from src.analysis import TEAM_GAME_NAMES


# ──────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────

@dataclass
class Insight:
    """Representa un patrón detectado con su severidad y descripción."""

    title:       str
    description: str
    severity:    str              # "critical" | "warning" | "info"
    metric:      str              # nombre de la métrica involucrada
    value:       Optional[float]  # valor observado
    threshold:   Optional[float]  # umbral de referencia
    loss_rate:   float            # % de derrotas donde aparece este patrón

    def to_dict(self) -> dict:
        return {
            "title":       self.title,
            "description": self.description,
            "severity":    self.severity,
            "metric":      self.metric,
            "value":       round(self.value, 3) if self.value is not None else None,
            "threshold":   self.threshold,
            "loss_rate":   round(self.loss_rate, 3),
        }


# ──────────────────────────────────────────────────────────────────
# Pattern Detector
# ──────────────────────────────────────────────────────────────────

from src.analysis import filter_team_players

class PatternDetector:
    """
    Analiza el dataset de partidas en busca de patrones que aparezcan
    consistentemente en las pérdidas del equipo.
    """

    def __init__(
        self,
        df_participants: pd.DataFrame,
        df_objectives:   Optional[pd.DataFrame] = None,
        df_gold_diff:    Optional[pd.DataFrame] = None,
        threshold:       float = PATTERN_LOSS_THRESHOLD,
    ) -> None:
        """
        Args:
            df_participants: DataFrame con compute_player_metrics() aplicado.
            df_objectives:   Output de compute_objective_control().
            df_gold_diff:    Output de compute_gold_diff().
            threshold:       Fracción mínima de derrotas para reportar patrón.
        """
        self.threshold = threshold

        # Filtrar solo partidas del equipo usando la lógica robusta de analysis.py
        self.df = filter_team_players(df_participants)
        
        if self.df.empty:
            logger.error("PatternDetector: No se encontraron datos del equipo. Los patrones serán inválidos.")
            self.total_losses = 0
            self.total_wins = 0
            self.losses_ids = set()
            self.wins_ids = set()
            return

        self.df_obj = df_objectives
        self.df_gd  = df_gold_diff

        # Aislar partidas ganadas y perdidas (a nivel de match)
        match_results = self.df.drop_duplicates("match_id")[["match_id", "result", "duration_minutes"]]
        self.losses_ids = set(match_results[match_results["result"] == False]["match_id"])
        self.wins_ids   = set(match_results[match_results["result"] == True]["match_id"])
        self.total_losses = len(self.losses_ids)
        self.total_wins   = len(self.wins_ids)

        logger.info(
            f"PatternDetector inicializado: {self.total_losses} derrotas, "
            f"{self.total_wins} victorias de equipo detectadas."
        )

    def _loss_rate(self, match_ids_with_pattern: set[str]) -> float:
        """Fracción de derrotas que cumplen el patrón."""
        if self.total_losses == 0:
            return 0.0
        return len(match_ids_with_pattern & self.losses_ids) / self.total_losses

    # ── Reglas individuales ───────────────────────────────────────

    def _check_low_vision(self) -> Optional[Insight]:
        """Patrón: visión baja en partidas perdidas."""
        if "vision_per_min" not in self.df.columns:
            return None

        # Calcular visión promedio del equipo por partida
        match_vision = self.df.groupby("match_id")["vision_per_min"].mean()
        
        # Consideramos visión baja si el promedio del equipo es inferior al umbral global
        low_vision_matches = set(match_vision[match_vision < LOW_VISION_PER_MIN].index)

        rate = self._loss_rate(low_vision_matches)
        
        # Valor promedio en las partidas con visión baja
        avg_val = match_vision[match_vision.index.isin(low_vision_matches)].mean()

        if rate >= self.threshold:
            return Insight(
                title="Déficit de visión colectiva",
                description=(
                    f"En el {rate*100:.0f}% de las derrotas, el equipo promedia "
                    f"menos de {LOW_VISION_PER_MIN:.2f} VS/min. "
                    "Falta de inversión en Control Wards y rotación de Trinkets en mid-game."
                ),
                severity="critical" if rate > 0.80 else "warning",
                metric="vision_per_min",
                value=float(avg_val) if pd.notna(avg_val) else None,
                threshold=LOW_VISION_PER_MIN,
                loss_rate=rate,
            )
        return None

    def _check_early_gold_deficit(self) -> Optional[Insight]:
        """Patrón: déficit de gold en minuto 15 en derrotas."""
        if self.df_gd is None or self.df_gd.empty:
            return None
        if "gold_diff_min15" not in self.df_gd.columns:
            return None

        # Sumar la diferencia de oro de los 5 jugadores para obtener el total del equipo
        match_gd = self.df_gd.groupby("match_id")["gold_diff_min15"].sum()
        
        # Partidas donde el equipo (total) está por debajo del umbral crítico
        deficit_matches = set(
            match_gd[match_gd < GOLD_DIFF_CRITICAL_THRESHOLD].index
        )

        rate = self._loss_rate(deficit_matches)
        
        # Valor promedio del déficit en partidas perdidas
        avg_val = match_gd[match_gd.index.isin(self.losses_ids)].mean()

        if rate >= self.threshold:
            return Insight(
                title="Déficit de gold en early/mid game",
                description=(
                    f"En el {rate*100:.0f}% de las derrotas, el equipo tiene un déficit total de más de "
                    f"{abs(GOLD_DIFF_CRITICAL_THRESHOLD):,}g al minuto 15. "
                    "Revisar tradeos en lane y eficiencia de jungle clears."
                ),
                severity="critical" if rate > 0.75 else "warning",
                metric="gold_diff_min15",
                value=float(avg_val) if pd.notna(avg_val) else None,
                threshold=float(GOLD_DIFF_CRITICAL_THRESHOLD),
                loss_rate=rate,
            )
        return None

    def _check_objective_rate(self) -> Optional[Insight]:
        """Patrón: bajo control de objetivos principales en derrotas."""
        if self.df_obj is None or self.df_obj.empty:
            return None

        # Usar objective_score_raw que es una métrica compuesta (Barón, Dragón, Torres, etc.)
        if "objective_score_raw" not in self.df_obj.columns:
            return None

        # Calcular el score promedio del equipo en victorias vs derrotas
        match_obj_scores = self.df_obj.groupby("match_id")["objective_score_raw"].sum()
        
        loss_scores = match_obj_scores[match_obj_scores.index.isin(self.losses_ids)]
        win_scores  = match_obj_scores[match_obj_scores.index.isin(self.wins_ids)]
        
        if loss_scores.empty or win_scores.empty:
            return None

        avg_loss = loss_scores.mean()
        avg_win  = win_scores.mean()
        
        # Si en derrotas el score de objetivos es < 50% del de victorias
        if avg_loss < (avg_win * 0.5):
            low_obj_matches = set(match_obj_scores[match_obj_scores < (avg_win * 0.6)].index)
            rate = self._loss_rate(low_obj_matches)
            
            if rate >= self.threshold:
                return Insight(
                    title="Colapso en control de objetivos",
                    description=(
                        f"En el {rate*100:.0f}% de las derrotas, el equipo obtiene un score de objetivos "
                        f"críticamente bajo ({avg_loss:.1f} avg vs {avg_win:.1f} en victorias). "
                        "Mejorar la preparación de visión 1 min antes de objetivos y el setup de lanes."
                    ),
                    severity="critical" if rate > 0.70 else "warning",
                    metric="objective_score_raw",
                    value=float(avg_loss),
                    threshold=float(avg_win * 0.8),
                    loss_rate=rate,
                )
        return None

    def _check_high_death_rate(self) -> Optional[Insight]:
        """Patrón: alta tasa de muertes en early game (antes de minuto 15)."""
        if "duration_minutes" not in self.df.columns:
            return None

        # Muertes totales del equipo por partida
        match_deaths = self.df.groupby("match_id")["deaths"].sum()
        loss_deaths = match_deaths[match_deaths.index.isin(self.losses_ids)]
        win_deaths  = match_deaths[match_deaths.index.isin(self.wins_ids)]

        if loss_deaths.empty or win_deaths.empty:
            return None

        avg_loss = loss_deaths.mean()
        avg_win  = win_deaths.mean()
        diff_pct = (avg_loss - avg_win) / max(avg_win, 0.1)

        # Reportar si hay >30% más muertes en derrotas
        if diff_pct > 0.30:
            high_death_matches = set(match_deaths[match_deaths > match_deaths.median()].index)
            rate = self._loss_rate(high_death_matches)
            return Insight(
                title="Alta tasa de muertes en derrotas",
                description=(
                    f"El equipo recibe un {diff_pct*100:.0f}% más de muertes en partidas perdidas "
                    f"({avg_loss:.1f} vs {avg_win:.1f} en victorias). "
                    "Priorizar disciplina de posicionamiento y comunicación de trades."
                ),
                severity="warning",
                metric="team_deaths",
                value=float(avg_loss),
                threshold=float(avg_win),
                loss_rate=rate,
            )
        return None

    def _check_long_game_performance(self) -> Optional[Insight]:
        """Patrón: rendimiento en partidas largas (>30 min)."""
        if "duration_minutes" not in self.df.columns:
            return None

        match_results = self.df.drop_duplicates("match_id")[["match_id", "result", "duration_minutes"]]
        long_games = match_results[match_results["duration_minutes"] > 30]

        if len(long_games) < 3:
            return None

        wr_long = long_games["result"].mean()
        wr_short = match_results[match_results["duration_minutes"] <= 30]["result"].mean()

        if wr_long < 0.40 and (wr_short - wr_long) > 0.20:
            long_losses = set(long_games[~long_games["result"]]["match_id"])
            rate = self._loss_rate(long_losses)
            return Insight(
                title="Caída de rendimiento en late game",
                description=(
                    f"Winrate en partidas >30min: {wr_long*100:.0f}% "
                    f"vs {wr_short*100:.0f}% en partidas cortas. "
                    "El equipo pierde ventaja en late game. "
                    "Evaluar composiciones con más power late o cerrar partidas antes."
                ),
                severity="warning",
                metric="late_game_winrate",
                value=float(wr_long),
                threshold=0.50,
                loss_rate=rate,
            )
        return None

    def _check_support_vision(self) -> Optional[Insight]:
        """Patrón: support con visión baja comparado con el umbral de rol."""
        if "role" not in self.df.columns or "vision_per_min" not in self.df.columns:
            return None

        support_df = self.df[self.df["role"] == "SUPPORT"]
        if support_df.empty:
            return None

        support_vision = support_df.groupby("match_id")["vision_per_min"].mean()
        # Support debería tener al menos 1.5 VS/min
        low_support = set(support_vision[support_vision < 1.5].index)
        rate = self._loss_rate(low_support)

        if rate >= self.threshold:
            avg_val = support_vision[support_vision.index.isin(self.losses_ids)].mean()
            return Insight(
                title="Support con visión insuficiente",
                description=(
                    f"En el {rate*100:.0f}% de las derrotas, el support promedia "
                    f"menos de 1.5 VS/min. "
                    "El support debe priorizar trinkets, control wards en lane y deep ward en mid game."
                ),
                severity="warning",
                metric="support_vision_per_min",
                value=float(avg_val) if pd.notna(avg_val) else None,
                threshold=1.5,
                loss_rate=rate,
            )
        return None

    # ── Punto de entrada ──────────────────────────────────────────

    def detect_all(self) -> list[Insight]:
        """
        Ejecuta todas las reglas de detección y devuelve los insights
        ordenados por severidad.

        Returns:
            Lista de Insight, de más crítico a informativo.
        """
        if self.total_losses == 0:
            logger.warning("No hay derrotas en el dataset, no se pueden detectar patrones")
            return []

        checks = [
            self._check_low_vision,
            self._check_early_gold_deficit,
            self._check_objective_rate,
            self._check_high_death_rate,
            self._check_long_game_performance,
            self._check_support_vision,
        ]

        insights: list[Insight] = []
        for check in checks:
            try:
                result = check()
                if result is not None:
                    insights.append(result)
            except Exception as e:
                logger.error(f"Error en patrón {check.__name__}: {e}")

        # Ordenar: critical > warning > info
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        insights.sort(key=lambda x: (severity_order.get(x.severity, 3), -x.loss_rate))

        logger.info(f"PatternDetector: {len(insights)} patrones detectados")
        return insights

    def to_dataframe(self) -> pd.DataFrame:
        """Convierte los insights en un DataFrame para visualización."""
        insights = self.detect_all()
        if not insights:
            return pd.DataFrame()
        return pd.DataFrame([i.to_dict() for i in insights])
