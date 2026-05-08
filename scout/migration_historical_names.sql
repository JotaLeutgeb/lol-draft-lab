-- Migration: Agregar historical_names a scout_profiles
-- Permite almacenar nombres históricos del jugador para filtrado correcto

-- 1. Agregar columna
ALTER TABLE public.scout_profiles 
ADD COLUMN IF NOT EXISTS historical_names text[] NULL;

-- 2. Comentario
COMMENT ON COLUMN public.scout_profiles.historical_names 
IS 'Nombres históricos/alias del jugador (solo GameName, sin TAG)';

-- 3. Ejemplo de actualización manual (opcional)
-- UPDATE public.scout_profiles 
-- SET historical_names = ARRAY['Jöta'] 
-- WHERE riot_id = 'AEVI Jöta#jjjjj';
