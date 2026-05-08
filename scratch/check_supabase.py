import os
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

def main():
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("Error: SUPABASE_URL or SUPABASE_KEY not found in .env")
        return

    supabase = create_client(url, key)
    print(f"Connecting to Supabase at: {url}")

    # Fetch all team_participants
    all_rows = []
    off = 0
    lim = 1000
    while True:
        r = supabase.table("team_participants").select("*").range(off, off + lim - 1).execute()
        if not r or not r.data:
            break
        all_rows.extend(r.data)
        if len(r.data) < lim:
            break
        off += lim

    df = pd.DataFrame(all_rows)
    print(f"\nTotal rows in team_participants: {len(df)}")
    if df.empty:
        print("No participants found in database!")
        return

    unique_matches = df["match_id"].unique()
    print(f"Total unique match_ids: {len(unique_matches)}")
    print("List of unique match_ids:")
    print(unique_matches)

    print("\nUnique game_names in database:")
    print(df["game_name"].unique())

    print("\nRows per game_name:")
    print(df["game_name"].value_counts())

    print("\nChecking role distributions:")
    if "role" in df.columns:
        print(df.groupby(["game_name", "role"]).size())
    else:
        print("No 'role' column found in database!")

if __name__ == "__main__":
    main()
