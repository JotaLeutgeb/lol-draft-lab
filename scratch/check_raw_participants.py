import os
from dotenv import load_dotenv
from supabase import create_client
import pandas as pd

def main():
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    supabase = create_client(url, key)

    res = supabase.table("team_participants").select("*").execute()
    df_p = pd.DataFrame(res.data)
    print("Total rows:", len(df_p))
    
    counts = df_p.groupby("match_id").size()
    print("\nRaw participants per match_id:")
    print(counts)

    for mid, count in counts.items():
        if count != 10:
            print(f"\nMatch {mid} has {count} rows in Supabase!")
            # Print unique participant_ids or names
            m_rows = df_p[df_p["match_id"] == mid]
            print(m_rows[["participant_id", "game_name", "role", "champion"]])

if __name__ == "__main__":
    main()
