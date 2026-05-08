import os
import pandas as pd
from dotenv import load_dotenv
from src.data_loader import MatchV5Client

def main():
    load_dotenv()
    key = os.environ.get("RIOT_API_KEY")
    if not key:
        return

    loader = MatchV5Client(key)
    df_p, _, _ = loader.load_team_matches(count_per_player=20)

    aevi_match_ids = ['LA2_1593392872', 'LA2_1593373178', 'LA2_1593388019', 'LA2_1593379013']
    df_aevi = df_p[df_p["match_id"].isin(aevi_match_ids)]

    # We want to find game_name and tag_line for player names starting with "AEVI"
    df_aevi_players = df_aevi[df_aevi["game_name"].str.startswith("AEVI", na=False)]
    
    unique_players = df_aevi_players[["game_name", "tag_line"]].drop_duplicates()
    
    with open("scratch/aevi_tags.txt", "w", encoding="utf-8") as f:
        f.write("Precise AEVI Accounts:\n\n")
        for idx, row in unique_players.iterrows():
            f.write(f"Riot ID: {row['game_name']}#{row['tag_line']}\n")

if __name__ == "__main__":
    main()
