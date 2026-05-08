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

    with open("scratch/aevi_mapping.txt", "w", encoding="utf-8") as f:
        f.write("AEVI Matches Player Roles and Names:\n\n")
        for match_id in aevi_match_ids:
            f.write(f"--- Match ID: {match_id} ---\n")
            df_m = df_aevi[df_aevi["match_id"] == match_id]
            for idx, row in df_m.iterrows():
                f.write(f"Name: {row['game_name']} | Role: {row['role']} | Team Side: {row['team_id']}\n")
            f.write("\n")

if __name__ == "__main__":
    main()
