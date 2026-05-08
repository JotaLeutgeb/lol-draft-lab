import os
from dotenv import load_dotenv
from supabase import create_client

def main():
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    supabase = create_client(url, key)

    match_id = "LA2_1582309764"
    print(f"Deleting match {match_id} from Supabase...")
    
    try:
        # Delete from team_events
        r1 = supabase.table("team_events").delete().eq("match_id", match_id).execute()
        print("Deleted events:", len(r1.data) if r1.data else 0)
        
        # Delete from team_timeline
        r2 = supabase.table("team_timeline").delete().eq("match_id", match_id).execute()
        print("Deleted timeline:", len(r2.data) if r2.data else 0)
        
        # Delete from team_participants
        r3 = supabase.table("team_participants").delete().eq("match_id", match_id).execute()
        print("Deleted participants:", len(r3.data) if r3.data else 0)
        
        # Delete from team_match_metadata
        r4 = supabase.table("team_match_metadata").delete().eq("match_id", match_id).execute()
        print("Deleted metadata:", len(r4.data) if r4.data else 0)
        
        print("Successfully cleaned up ancient match!")
    except Exception as e:
        print("Error during cleanup:", e)

if __name__ == "__main__":
    main()
