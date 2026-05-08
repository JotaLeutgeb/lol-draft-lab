import os
from dotenv import load_dotenv
from supabase import create_client

def main():
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    supabase = create_client(url, key)

    try:
        # Fetch one row or try to get column names from a dummy request
        r = supabase.table("team_match_metadata").select("*").limit(1).execute()
        if r and r.data:
            print("Columns in team_match_metadata:", r.data[0].keys())
        else:
            print("team_match_metadata is empty, trying to fetch schema or columns.")
            # Let's insert a test row and catch the error to see the columns, or just try basic ones
            print("Trying basic columns...")
    except Exception as e:
        print("Error fetching team_match_metadata columns:", e)

if __name__ == "__main__":
    main()
