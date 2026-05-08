import pandas as pd
from src.features import (
    compute_player_metrics, compute_early_tactical_metrics, 
    compute_synergy_matrix, compute_objective_control, 
    compute_impact_score, compute_position_nodes, compute_combat_clusters
)
from src.analysis import analyze_objective_prep_T60
from src.data_loader import normalize_match, normalize_timeline

class MatchPipeline:
    @staticmethod
    def process_match(match_data: dict, timeline_data: dict, df_bench: pd.DataFrame) -> dict:
        if not match_data or not timeline_data:
            return {}
            
        match_id = match_data["metadata"]["matchId"]
        df_p = normalize_match(match_data)
        if df_p.empty:
            return {"participants": df_p}
            
        p_to_team = df_p.set_index("participant_id")["team_id"].to_dict()
        df_t, df_e = normalize_timeline(timeline_data, match_id, p_to_team)
        
        # 1. Base
        df_p = compute_player_metrics(df_p)
        df_p = compute_early_tactical_metrics(df_p, df_e)
        
        # 2. Sinergia Vectorizada
        synergy_results = compute_synergy_matrix(df_e, df_p)
        if synergy_results:
            df_p['synergy_data'] = df_p['team_id'].map(synergy_results).fillna({})
            synergy_df = pd.json_normalize(df_p['synergy_data'])
            df_p = pd.concat([df_p.drop(columns=['synergy_data']).reset_index(drop=True), synergy_df.reset_index(drop=True)], axis=1)
        
        # 3. Objetivos
        df_obj = compute_objective_control(df_e, df_p)
        
        # 4. Impact Score Dinámico
        df_p = compute_impact_score(df_p, df_obj, df_bench=df_bench)
        
        # 5. V2 Spatial & Temporal Metrics
        df_nodes = compute_position_nodes(df_t, df_e)
        df_clusters = compute_combat_clusters(df_e)
        df_prep = analyze_objective_prep_T60(df_e, df_t)
        
        return {
            "participants": df_p.where(pd.notnull(df_p), None),
            "position_nodes": df_nodes,
            "combat_clusters": df_clusters,
            "objective_prep": df_prep
        }