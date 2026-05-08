"""
run_analysis.py — Entrypoint CLI para analisis sin Streamlit.

Flujo completo:
  1. Carga datos (API — historial del equipo o partida individual)
  2. Feature engineering
  3. Analisis
  4. Exporta resultados a CSV

Uso:
    # Historial del equipo via API (ultimas N partidas de cada jugador)
    python run_analysis.py --source api --count 20 --queue 420

    # Una partida especifica por ID
    python run_analysis.py --source api --match-id LA2_7251234567

    # Solo el numero (prefija LA2_ automaticamente)
    python run_analysis.py --source api --match-id 7251234567

    # Con CSV propio
    python run_analysis.py --source csv --file data/processed/mis_partidas.csv
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.config import DATA_PROCESSED_DIR
from src.features import (
    compute_gold_diff,
    compute_impact_score,
    compute_objective_control,
    compute_phase_stats,
    compute_player_metrics,
)
from src.analysis import (
    analyze_compositions,
    analyze_vision_control,
    compute_player_impact_summary,
    filter_to_team_matches,
    identify_loss_phase,
)
from src.patterns import PatternDetector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LoL Esports Analyzer -- CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--source",
        choices=["api", "csv"],
        default="api",
        help="Fuente de datos:\n  api — Riot Games API\n  csv — CSV local",
    )
    parser.add_argument("--file",     type=str, help="Ruta al CSV (solo con --source csv)")
    parser.add_argument("--count",    type=int, default=20, help="Partidas por jugador (solo API, historial)")
    parser.add_argument("--queue",    type=int, default=420, help="Tipo de cola (solo API). 0=todas.")
    parser.add_argument("--match-id", type=str, default=None, dest="match_id",
                        help="Analizar una partida especifica por ID (ej: LA2_7251234567).\n"
                             "Si solo pasas el numero, se prefija con LA2_ automaticamente.\n"
                             "Cuando se usa --match-id, --count y --queue se ignoran.")
    parser.add_argument("--output",   type=str, default=str(DATA_PROCESSED_DIR), help="Directorio de salida")
    return parser.parse_args()


def load_data(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Carga datos segun la fuente y el modo seleccionado."""
    from dotenv import load_dotenv
    load_dotenv()

    if args.source == "api":
        from src.data_loader import RiotAPILoader
        loader = RiotAPILoader()

        # -- Modo: partida individual por ID --
        if args.match_id:
            logger.info(f"Cargando partida especifica: {args.match_id}")
            return loader.load_single_match_by_id(args.match_id)

        # -- Modo: historial del equipo --
        logger.info("Cargando historial del equipo desde API de Riot...")
        queue = args.queue if args.queue > 0 else None
        return loader.load_team_matches(count_per_player=args.count, queue=queue)

    elif args.source == "csv":
        if not args.file:
            logger.error("--file es requerido con --source csv")
            sys.exit(1)
        from src.data_loader import CSVLoader
        loader = CSVLoader(args.file)
        df_p = loader.load()
        return df_p, pd.DataFrame(), pd.DataFrame()

    return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


def run_pipeline(
    df_p: pd.DataFrame,
    df_t: pd.DataFrame,
    df_e: pd.DataFrame,
    output_dir: Path,
) -> None:
    """Ejecuta el pipeline completo y exporta resultados."""

    if df_p.empty:
        logger.error("Sin datos de participantes. Abortando.")
        return

    # Filtrar a partidas donde jugaron los 5 juntos
    df_p, df_t, df_e = filter_to_team_matches(df_p, df_t if not df_t.empty else None, df_e if not df_e.empty else None)
    if df_p.empty:
        logger.error(
            "No se encontraron partidas con los 5 jugadores juntos. "
            "Verifica Riot IDs y roles en config.py."
        )
        return
    logger.info(f"Analizando {df_p['match_id'].nunique()} partidas en equipo completo.")

    logger.info("=== FEATURE ENGINEERING ===")
    df_participants = compute_player_metrics(df_p)
    logger.info("  ✅ Métricas de jugadores calculadas")

    df_objectives = compute_objective_control(df_e) if not df_e.empty else pd.DataFrame()
    if not df_objectives.empty:
        logger.info("  ✅ Control de objetivos calculado")

    df_gold_diff = compute_gold_diff(df_t, df_participants) if not df_t.empty else pd.DataFrame()
    if not df_gold_diff.empty:
        logger.info("  ✅ Gold difference calculado")

    df_phase_stats = compute_phase_stats(df_t) if not df_t.empty else pd.DataFrame()
    if not df_phase_stats.empty:
        logger.info("  ✅ Stats por fase calculados")

    df_with_impact = compute_impact_score(df_participants, df_objectives)
    logger.info("  ✅ Impact score calculado")

    # ── V2 Spatial & Temporal Metrics ─────────────────────────────
    if not df_t.empty and not df_e.empty:
        from src.features import compute_position_nodes, compute_combat_clusters
        from src.analysis import analyze_objective_prep_T60
        df_nodes = compute_position_nodes(df_t, df_e)
        df_clusters = compute_combat_clusters(df_e)
        df_prep = analyze_objective_prep_T60(df_e, df_t)
        logger.info("  ✅ V2 Spatial & Temporal Metrics (DBSCAN & Interpolation) calculados")

    logger.info("=== ANÁLISIS ===")
    df_summary = compute_player_impact_summary(df_with_impact)
    phase_result = identify_loss_phase(df_participants, df_gold_diff if not df_gold_diff.empty else None)
    vision_result = analyze_vision_control(df_participants)
    df_comps = analyze_compositions(df_participants)

    # ── Patrones ─────────────────────────────────────────────────
    logger.info("=== PATRONES DE DERROTA ===")
    detector = PatternDetector(
        df_participants=df_with_impact,
        df_objectives=df_objectives if not df_objectives.empty else None,
        df_gold_diff=df_gold_diff if not df_gold_diff.empty else None,
    )
    insights = detector.detect_all()

    # ── Imprimir resultados ───────────────────────────────────────
    print("\n" + "=" * 60)
    print("  LoL ESPORTS ANALYZER -- REPORTE")
    print("=" * 60)

    print(f"\nRESUMEN DEL EQUIPO")
    from src.analysis import TEAM_GAME_NAMES
    team_df = df_participants[df_participants["game_name"].str.lower().isin(TEAM_GAME_NAMES)]
    if not team_df.empty:
        match_res = team_df.drop_duplicates("match_id")[["match_id", "result"]]
        wr = match_res["result"].mean()
        print(f"  Partidas analizadas : {len(match_res)}")
        print(f"  Winrate del equipo  : {wr*100:.1f}%")

    if phase_result.get("worst_phase"):
        print(f"\nFASE MAS PROBLEMATICA: {phase_result['worst_phase'].upper()}")
        print(f"  {phase_result.get('insight', '')}")

    print(f"\nCONTROL DE VISION")
    print(f"  {vision_result.get('insight', 'Sin datos')}")

    if not df_summary.empty:
        print(f"\nRANKING DE IMPACTO REAL")
        ranking = df_summary.sort_values("avg_impact_score", ascending=False)
        for rank, (_, row) in enumerate(ranking.iterrows(), 1):
            name = row.get("game_name", "?")
            role = row.get("role", "?")
            score = row.get("avg_impact_score", 0)
            wr_p = row.get("win_rate", 0)
            print(f"  {rank}. {name:20s} ({role:8s}) | Impact: {score:.3f} | WR: {wr_p*100:.0f}%")

    if insights:
        print(f"\nPATRONES DE DERROTA ({len(insights)} detectados)")
        for ins in insights:
            sev = {"critical": "[CRITICO]", "warning": "[AVISO]", "info": "[INFO]"}. \
                get(ins.severity, "[?]")
            print(f"\n{sev} {ins.title} ({ins.loss_rate*100:.0f}% de derrotas)")
            print(f"   {ins.description}")
    else:
        print("\n[OK] No se detectaron patrones de derrota significativos.")

    print("\n" + "=" * 60)

    # ── Exportar CSVs ─────────────────────────────────────────────
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df_with_impact.to_csv(output_dir / "participants_features.csv", index=False)
    logger.info("[OK] participants_features.csv exportado")

    if not df_summary.empty:
        df_summary.to_csv(output_dir / "player_summary.csv", index=False)
        logger.info("[OK] player_summary.csv exportado")

    if not df_objectives.empty:
        df_objectives.to_csv(output_dir / "objectives.csv", index=False)
        logger.info("[OK] objectives.csv exportado")

    if not df_gold_diff.empty:
        df_gold_diff.to_csv(output_dir / "gold_diff.csv", index=False)
        logger.info("[OK] gold_diff.csv exportado")

    if insights:
        df_insights = detector.to_dataframe()
        df_insights.to_csv(output_dir / "patterns.csv", index=False)
        logger.info("[OK] patterns.csv exportado")

    logger.info(f"\n[INFO] Reportes guardados en: {output_dir}")


if __name__ == "__main__":
    args = parse_args()

    # Advertir si se intenta usar --match-id sin --source api
    if getattr(args, "match_id", None) and args.source != "api":
        logger.warning("--match-id solo funciona con --source api. Cambiando source a api.")
        args.source = "api"

    df_p, df_t, df_e = load_data(args)

    # Titulo del reporte segun modo
    if getattr(args, "match_id", None):
        logger.info(f"Analizando partida individual: {args.match_id}")
    else:
        logger.info("Analizando historial del equipo")

    run_pipeline(df_p, df_t, df_e, Path(args.output))
