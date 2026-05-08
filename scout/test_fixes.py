"""
test_fixes.py — Script de testing rápido para validar fixes.

Ejecutar:
    python test_fixes.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

def test_imports():
    """Test 1: Verificar que todos los imports funcionan."""
    print("🧪 Test 1: Imports...")
    try:
        from src.profile_manager import (
            validate_riot_id,
            create_profile_in_db,
            sync_profile_data,
            get_all_profiles,
            update_last_synced,
        )
        from src.jungle_metrics import compute_jungle_metrics, compute_pathing_efficiency
        from src.error_patterns import detect_error_patterns
        from src.visualization_scout import (
            plot_death_heatmap,
            plot_gold_diff_timeline_individual,
            plot_impact_score_evolution,
            plot_pillar_radar_vs_challenger,
            plot_jungle_pathing,
        )
        print("✅ Todos los imports exitosos")
        return True
    except ImportError as e:
        print(f"❌ Error de import: {e}")
        return False


def test_supabase_connection():
    """Test 2: Verificar conexión a Supabase."""
    print("\n🧪 Test 2: Supabase Connection...")
    try:
        from supabase import create_client
        
        url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        
        if not url or not key:
            print("❌ Variables de entorno no configuradas")
            print("   Configura: SUPABASE_URL y SUPABASE_KEY")
            return False
        
        supabase = create_client(url, key)
        
        # Test query
        result = supabase.table("scout_profiles").select("count").execute()
        print(f"✅ Conexión exitosa. Perfiles en DB: {len(result.data) if result.data else 0}")
        return True
    
    except Exception as e:
        print(f"❌ Error de conexión: {e}")
        return False


def test_riot_api_key():
    """Test 3: Verificar Riot API key."""
    print("\n🧪 Test 3: Riot API Key...")
    
    api_key = os.environ.get("RIOT_API_KEY")
    
    if not api_key:
        print("⚠️  RIOT_API_KEY no configurada")
        print("   La feature de agregar usuarios requiere esta key")
        return False
    
    print(f"✅ RIOT_API_KEY configurada (length: {len(api_key)})")
    return True


def test_schema_validation():
    """Test 4: Verificar schema de scout_profiles."""
    print("\n🧪 Test 4: Schema Validation...")
    try:
        from supabase import create_client
        
        url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        
        if not url or not key:
            print("⚠️  Supabase no configurado, saltando test")
            return False
        
        supabase = create_client(url, key)
        
        # Try to select all columns
        result = supabase.table("scout_profiles").select("*").limit(1).execute()
        
        if result.data:
            profile = result.data[0]
            required_cols = ["id", "riot_id", "display_name", "primary_role", "platform"]
            missing = [col for col in required_cols if col not in profile]
            
            if missing:
                print(f"❌ Columnas faltantes en scout_profiles: {missing}")
                return False
            else:
                print(f"✅ Schema válido. Columnas: {list(profile.keys())}")
                return True
        else:
            print("⚠️  No hay perfiles en la DB, pero schema parece correcto")
            return True
    
    except Exception as e:
        print(f"❌ Error de schema: {e}")
        return False


def test_jungle_metrics_validation():
    """Test 5: Verificar validación de columnas en jungle_metrics."""
    print("\n🧪 Test 5: Jungle Metrics Validation...")
    try:
        import pandas as pd
        from src.jungle_metrics import compute_jungle_metrics
        
        # Create empty DataFrames
        df_player = pd.DataFrame()
        df_events = pd.DataFrame()
        df_timeline = pd.DataFrame()
        
        class MockProfile:
            game_name = "TestPlayer"
        
        # Should return empty DataFrame without crashing
        result = compute_jungle_metrics(df_player, df_events, df_timeline, MockProfile())
        
        print("✅ Validación de columnas funciona correctamente")
        return True
    
    except Exception as e:
        print(f"❌ Error en jungle_metrics: {e}")
        return False


def test_error_patterns_validation():
    """Test 6: Verificar validación de schema en error_patterns."""
    print("\n🧪 Test 6: Error Patterns Validation...")
    try:
        import pandas as pd
        from src.error_patterns import detect_error_patterns
        
        # Create empty DataFrames
        df_player = pd.DataFrame()
        df_events = pd.DataFrame()
        df_timeline = pd.DataFrame()
        df_bench = pd.DataFrame()
        
        class MockProfile:
            game_name = "TestPlayer"
        
        # Should return empty list without crashing
        result = detect_error_patterns(df_player, df_events, df_timeline, df_bench, MockProfile())
        
        print("✅ Validación de schema funciona correctamente")
        return True
    
    except Exception as e:
        print(f"❌ Error en error_patterns: {e}")
        return False


def main():
    """Ejecutar todos los tests."""
    print("=" * 60)
    print("🚀 TESTING FIXES & FEATURES")
    print("=" * 60)
    
    results = []
    
    results.append(("Imports", test_imports()))
    results.append(("Supabase Connection", test_supabase_connection()))
    results.append(("Riot API Key", test_riot_api_key()))
    results.append(("Schema Validation", test_schema_validation()))
    results.append(("Jungle Metrics Validation", test_jungle_metrics_validation()))
    results.append(("Error Patterns Validation", test_error_patterns_validation()))
    
    print("\n" + "=" * 60)
    print("📊 RESUMEN")
    print("=" * 60)
    
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    total = len(results)
    passed = sum(1 for _, p in results if p)
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 TODOS LOS TESTS PASARON!")
        print("✅ Ready for deployment")
        return 0
    else:
        print(f"\n⚠️  {total - passed} tests fallaron")
        print("❌ Fix issues before deployment")
        return 1


if __name__ == "__main__":
    sys.exit(main())
