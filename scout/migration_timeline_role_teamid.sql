-- Migration: Agregar role y team_id a scout_timeline
-- Estas columnas son necesarias para filtrar por rol en visualizaciones

-- 1. Agregar las columnas
ALTER TABLE public.scout_timeline 
ADD COLUMN IF NOT EXISTS role text NULL,
ADD COLUMN IF NOT EXISTS team_id integer NULL DEFAULT 0;

-- 2. Crear índices para mejorar performance
CREATE INDEX IF NOT EXISTS idx_stl_role ON public.scout_timeline USING btree (role);
CREATE INDEX IF NOT EXISTS idx_stl_team_id ON public.scout_timeline USING btree (team_id);

-- 3. Comentarios para documentación
COMMENT ON COLUMN public.scout_timeline.role IS 'Rol del jugador (TOP, JUNGLE, MID, BOT, SUPPORT)';
COMMENT ON COLUMN public.scout_timeline.team_id IS 'ID del equipo (100 = azul, 200 = rojo)';

-- Nota: Los registros existentes tendrán NULL en estas columnas.
-- Se deben re-sincronizar los datos para poblar estas columnas correctamente.
-- O ejecutar un UPDATE con JOIN a scout_participants:

-- Opcional: Actualizar registros existentes desde scout_participants
-- UPDATE public.scout_timeline t
-- SET role = p.role, team_id = p.team_id
-- FROM public.scout_participants p
-- WHERE t.match_id = p.match_id 
--   AND t.participant_id = p.participant_id
--   AND (t.role IS NULL OR t.team_id IS NULL);
