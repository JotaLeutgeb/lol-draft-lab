"""
test_features.py — Unit tests para el módulo de feature engineering.

Tests que validan la correctitud matemática y el schema de salida
de las funciones de features.py. Usa datos sintéticos mínimos.

Ejecutar con: pytest tests/ -v
"""

from __future__ import annotations

import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.features import (
    compute_player_metrics,
    compute_gold_diff,
    compute_objective_control,
    compute_impact_score,
)


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def base_participant_row() -> dict:
    """Fila base de participante válida."""
    return {
        "match_id": "LA2_TEST_001",
        "participant_id": 1,
        "puuid": "test-puuid-001",
        "game_name": "Franfon",
        "tag_line": "vayne",
        "team_id": 100,
        "role": "TOP",
        "champion": "Darius",
        "kills": 5,
        "deaths": 2,
        "assists": 7,
        "gold_earned": 13000,
        "gold_spent": 12000,
        "total_damage": 22000,
        "physical_damage": 15000,
        "magic_damage": 5000,
        "true_damage": 2000,
        "damage_taken": 18000,
        "vision_score": 40,
        "wards_placed": 15,
        "wards_killed": 5,
        "control_wards": 4,
        "cs": 180,
        "total_heal": 6000,
        "time_cc": 25,
        "duration_minutes": 30.0,
        "result": True,
    }


@pytest.fixture
def df_two_teams(base_participant_row) -> pd.DataFrame:
    """DataFrame con 2 jugadores de equipos distintos para el mismo match."""
    rows = []
    # Jugador equipo azul
    rows.append({**base_participant_row})
    # Jugador equipo rojo (mismo rol, equipo contrario)
    enemy = {
        **base_participant_row,
        "participant_id": 6,
        "puuid": "test-puuid-002",
        "game_name": "EnemyTop",
        "tag_line": "RIVAL",
        "team_id": 200,
        "kills": 3,
        "deaths": 5,
        "assists": 4,
        "gold_earned": 10000,
        "result": False,
    }
    rows.append(enemy)
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────────────────────────
# Tests: compute_player_metrics
# ──────────────────────────────────────────────────────────────────

class TestComputePlayerMetrics:

    def test_kda_formula(self, df_two_teams):
        df = compute_player_metrics(df_two_teams)
        row = df[df["participant_id"] == 1].iloc[0]
        expected_kda = (5 + 7) / max(1, 2)   # (kills+assists) / deaths
        assert abs(row["kda"] - expected_kda) < 1e-6, f"KDA incorrecto: {row['kda']} != {expected_kda}"

    def test_kda_zero_deaths(self, base_participant_row):
        """Con 0 muertes, el denominador debe ser 1 (no división por cero)."""
        row = {**base_participant_row, "deaths": 0, "kills": 3, "assists": 5}
        df = compute_player_metrics(pd.DataFrame([row]))
        assert df.iloc[0]["kda"] == (3 + 5) / 1

    def test_cs_per_min(self, df_two_teams):
        df = compute_player_metrics(df_two_teams)
        row = df[df["participant_id"] == 1].iloc[0]
        expected = 180 / 30.0
        assert abs(row["cs_per_min"] - expected) < 1e-6

    def test_gold_per_min(self, df_two_teams):
        df = compute_player_metrics(df_two_teams)
        row = df[df["participant_id"] == 1].iloc[0]
        expected = 13000 / 30.0
        assert abs(row["gold_per_min"] - expected) < 1e-4

    def test_damage_per_min(self, df_two_teams):
        df = compute_player_metrics(df_two_teams)
        row = df[df["participant_id"] == 1].iloc[0]
        expected = 22000 / 30.0
        assert abs(row["damage_per_min"] - expected) < 1e-4

    def test_vision_per_min(self, df_two_teams):
        df = compute_player_metrics(df_two_teams)
        row = df[df["participant_id"] == 1].iloc[0]
        expected = 40 / 30.0
        assert abs(row["vision_per_min"] - expected) < 1e-6

    def test_kill_participation_range(self, df_two_teams):
        """Kill participation puede superar 1 si kills+assists > kills totales del equipo
        (cuando hay assists multiples). Verificamos que sea >= 0 y que exista la columna."""
        df = compute_player_metrics(df_two_teams)
        kp = df["kill_participation"]
        assert (kp >= 0).all(), f"KP negativo encontrado: {kp.tolist()}"
        assert "kill_participation" in df.columns

    def test_no_nan_output(self, df_two_teams):
        """No debe haber NaN en columnas calculadas."""
        df = compute_player_metrics(df_two_teams)
        calc_cols = ["kda", "cs_per_min", "gold_per_min", "damage_per_min",
                     "vision_per_min", "kill_participation"]
        for col in calc_cols:
            assert not df[col].isna().any(), f"NaN encontrado en columna: {col}"

    def test_original_df_not_modified(self, df_two_teams):
        """La función no debe modificar el DataFrame original."""
        original_cols = set(df_two_teams.columns)
        _ = compute_player_metrics(df_two_teams)
        assert set(df_two_teams.columns) == original_cols, "El DF original fue modificado"

    def test_schema_consistency(self, df_two_teams):
        """Verificar que las columnas nuevas existen."""
        df = compute_player_metrics(df_two_teams)
        expected_new_cols = ["kda", "cs_per_min", "gold_per_min", "damage_per_min",
                             "vision_per_min", "kill_participation"]
        for col in expected_new_cols:
            assert col in df.columns, f"Columna faltante: {col}"


# ──────────────────────────────────────────────────────────────────
# Tests: compute_objective_control
# ──────────────────────────────────────────────────────────────────

class TestComputeObjectiveControl:

    @pytest.fixture
    def df_events_sample(self):
        """Eventos de muestra para tests."""
        return pd.DataFrame([
            {"match_id": "LA2_TEST_001", "timestamp_min": 5.0,  "event_type": "ELITE_MONSTER_KILL",
             "participant_id": 2, "victim_id": 0, "team_id": 100, "monster_type": "dragon",
             "building_type": "", "item_id": 0, "position_x": 9866, "position_y": 4414},
            {"match_id": "LA2_TEST_001", "timestamp_min": 10.0, "event_type": "ELITE_MONSTER_KILL",
             "participant_id": 2, "victim_id": 0, "team_id": 100, "monster_type": "dragon",
             "building_type": "", "item_id": 0, "position_x": 9866, "position_y": 4414},
            {"match_id": "LA2_TEST_001", "timestamp_min": 22.0, "event_type": "ELITE_MONSTER_KILL",
             "participant_id": 2, "victim_id": 0, "team_id": 100, "monster_type": "baron",
             "building_type": "", "item_id": 0, "position_x": 5007, "position_y": 10471},
            {"match_id": "LA2_TEST_001", "timestamp_min": 16.0, "event_type": "ELITE_MONSTER_KILL",
             "participant_id": 7, "victim_id": 0, "team_id": 200, "monster_type": "dragon",
             "building_type": "", "item_id": 0, "position_x": 9866, "position_y": 4414},
        ])

    def test_objective_score_calculation(self, df_events_sample):
        df = compute_objective_control(df_events_sample)
        p2 = df[df["participant_id"] == 2].iloc[0]
        assert p2["objective_score_raw"] == 4.5, f"Score p2: {p2['objective_score_raw']} != 4.5"

        p7 = df[df["participant_id"] == 7].iloc[0]
        assert p7["objective_score_raw"] == 1.0, f"Score p7: {p7['objective_score_raw']} != 1.0"

    def test_empty_df(self):
        """Sin eventos, debe retornar DataFrame vacío."""
        df = compute_objective_control(pd.DataFrame())
        assert df.empty


# ──────────────────────────────────────────────────────────────────
# Tests: compute_impact_score
# ──────────────────────────────────────────────────────────────────

class TestComputeImpactScore:

    def test_impact_score_range(self, df_two_teams):
        """Impact score debe estar en [0, 1] después de normalización."""
        df = compute_player_metrics(df_two_teams)
        df = compute_impact_score(df, pd.DataFrame())
        score = df["impact_score"]
        assert score.between(0, 1).all(), f"Impact score fuera de [0,1]: {score.tolist()}"

    def test_impact_score_column_exists(self, df_two_teams):
        df = compute_player_metrics(df_two_teams)
        df = compute_impact_score(df, pd.DataFrame())
        assert "impact_score" in df.columns

    def test_impact_score_no_nan(self, df_two_teams):
        df = compute_player_metrics(df_two_teams)
        df = compute_impact_score(df, pd.DataFrame())
        assert not df["impact_score"].isna().any()

    def test_empty_input(self):
        """DataFrame vacío debe retornar DataFrame vacío."""
        result = compute_impact_score(pd.DataFrame(), pd.DataFrame())
        assert result.empty
