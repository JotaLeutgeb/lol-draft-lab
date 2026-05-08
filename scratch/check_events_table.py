import os
from dotenv import load_dotenv
from supabase import create_client

def main():
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    supabase = create_client(url, key)

    try:
        r = supabase.table("team_events").select("*").limit(1).execute()
        if r and r.data:
            print("Columns in team_events:", r.data[0].keys())
        else:
            print("team_events is empty!")
    except Exception as e:
        print("Error fetching team_events columns:", e)

if __name__ == "__main__":
    main()
