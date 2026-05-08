import os
from dotenv import load_dotenv
from supabase import create_client
import pandas as pd
from src.analysis import filter_team_players
from src.config import TEAM_PLAYER_DISPLAY_MAP

def main():
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    supabase = create_client(url, key)

    # Load participants
    res = supabase.table("team_participants").select("*").execute()
    df_p = pd.DataFrame(res.data)
    print("Total rows loaded:", len(df_p))
    
    # Apply display map
    df_p["game_name"] = df_p["game_name"].str.lower().map(TEAM_PLAYER_DISPLAY_MAP).fillna(df_p["game_name"])
    
    # Filter team players
    df_team = filter_team_players(df_p)
    print("Total rows in filtered df_team:", len(df_team))
    
    # Group by match_id to see rows per match
    counts = df_team.groupby("match_id").size()
    print("\nRows per match_id in df_team:")
    print(counts)
    
    # Let's inspect a match with more than 5 rows if any, or see what roles are present
    for mid, count in counts.items():
        if count != 5:
            print(f"\nMatch {mid} has {count} rows:")
            print(df_team[df_team["match_id"] == mid][["game_name", "role", "champion", "kills", "deaths", "assists"]])

if __name__ == "__main__":
    main()
