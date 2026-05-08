import os
import pandas as pd
from dotenv import load_dotenv
from src.data_loader import MatchV5Client
from src.analysis import filter_team_players, get_team_match_ids

def main():
    load_dotenv()
    key = os.environ.get("RIOT_API_KEY")
    if not key:
        with open("scratch/filter_output.txt", "w", encoding="utf-8") as f:
            f.write("Error: RIOT_API_KEY not found in .env\n")
        return

    loader = MatchV5Client(key)
    df_p, df_t, df_e = loader.load_team_matches(count_per_player=20)

    with open("scratch/filter_output.txt", "w", encoding="utf-8") as f:
        f.write(f"Initial matches downloaded: {df_p['match_id'].nunique()}\n")
        f.write(f"Unique match IDs downloaded: {df_p['match_id'].unique().tolist()}\n\n")

        from src.config import TEAM_PLAYER_DISPLAY_MAP
        df_mapped = df_p.copy()
        df_mapped["game_name"] = df_mapped["game_name"].str.lower().map(TEAM_PLAYER_DISPLAY_MAP).fillna(df_mapped["game_name"])
        f.write(f"Mapped game_names unique: {df_mapped['game_name'].unique().tolist()}\n\n")

        df_filtered_orig = filter_team_players(df_p)
        f.write(f"Unique matches remaining after original filter_team_players: {df_filtered_orig['match_id'].nunique()}\n")
        f.write(f"{df_filtered_orig.groupby('match_id').size().to_string()}\n\n")

        df_filtered_mapped = filter_team_players(df_mapped)
        f.write(f"Unique matches remaining after mapped filter_team_players: {df_filtered_mapped['match_id'].nunique()}\n")
        f.write(f"{df_filtered_mapped.groupby('match_id').size().to_string()}\n\n")

        valid_ids = get_team_match_ids(df_p)
        f.write(f"Matches valid in get_team_match_ids (original): {len(valid_ids)}\n")
        f.write(f"{list(valid_ids)}\n\n")

        valid_ids_mapped = get_team_match_ids(df_mapped)
        f.write(f"Matches valid in get_team_match_ids (mapped): {len(valid_ids_mapped)}\n")
        f.write(f"{list(valid_ids_mapped)}\n\n")

if __name__ == "__main__":
    main()
