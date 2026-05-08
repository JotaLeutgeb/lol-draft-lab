-- ============================================================
-- Scout Protocol — Schema Completo (DB Nueva)
-- Ejecutar en Supabase Dashboard → SQL Editor
-- ============================================================

-- ──────────────────────────────────────────────
-- 1. PARTIDAS (base)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.matches (
  match_id        text        NOT NULL,
  platform        text        NOT NULL,
  patch_version   text        NOT NULL,
  duration_min    double precision NOT NULL,
  queue_id        integer     NULL DEFAULT 420,
  is_processed    boolean     NULL DEFAULT false,
  game_timestamp  timestamp with time zone NULL,  -- Timestamp real del juego (gameCreation)
  created_at      timestamp with time zone NULL DEFAULT now(),
  CONSTRAINT matches_pkey PRIMARY KEY (match_id)
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_matches_game_timestamp ON public.matches USING btree (game_timestamp);

-- ──────────────────────────────────────────────
-- 2. PARTICIPANTES POR PARTIDA
-- (todos los 10 jugadores de la partida)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.scout_participants (
  id                    bigserial   NOT NULL,
  match_id              text        NOT NULL,
  participant_id        integer     NOT NULL,
  puuid                 text        NULL,
  game_name             text        NULL,
  tag_line              text        NULL,
  team_id               integer     NULL,
  role                  text        NULL,
  champion              text        NULL,
  kills                 integer     NULL DEFAULT 0,
  deaths                integer     NULL DEFAULT 0,
  assists               integer     NULL DEFAULT 0,
  gold_earned           integer     NULL DEFAULT 0,
  gold_spent            integer     NULL DEFAULT 0,
  total_damage          integer     NULL DEFAULT 0,
  physical_damage       integer     NULL DEFAULT 0,
  magic_damage          integer     NULL DEFAULT 0,
  true_damage           integer     NULL DEFAULT 0,
  damage_taken          integer     NULL DEFAULT 0,
  damage_mitigated      integer     NULL DEFAULT 0,
  damage_buildings      integer     NULL DEFAULT 0,
  vision_score          integer     NULL DEFAULT 0,
  wards_placed          integer     NULL DEFAULT 0,
  wards_killed          integer     NULL DEFAULT 0,
  control_wards         integer     NULL DEFAULT 0,
  cs                    integer     NULL DEFAULT 0,
  total_heal            integer     NULL DEFAULT 0,
  time_cc               integer     NULL DEFAULT 0,
  duration_minutes      real        NULL,
  result                boolean     NULL,
  is_custom             boolean     NULL DEFAULT false,
  first_blood           boolean     NULL DEFAULT false,
  -- Métricas derivadas (calculadas en ETL)
  kda                   double precision NULL,
  cs_per_min            real        NULL DEFAULT 0,
  gold_per_min          real        NULL DEFAULT 0,
  damage_per_min        real        NULL DEFAULT 0,
  vision_per_min        real        NULL DEFAULT 0,
  cc_per_min            real        NULL DEFAULT 0,
  damage_taken_per_min  real        NULL DEFAULT 0,
  kill_participation    double precision NULL DEFAULT 0,
  damage_per_gold       real        NULL DEFAULT 0,
  objective_control     double precision NULL DEFAULT 0,
  kill_conversion       double precision NULL DEFAULT 0,
  impact_score          double precision NULL,
  pilar_combat_efficiency  double precision NULL DEFAULT 0,
  pilar_map_pressure       double precision NULL DEFAULT 0,
  pilar_tactical_utility   double precision NULL DEFAULT 0,
  pilar_consistency        double precision NULL DEFAULT 0,  -- reemplaza team_synergy
  consistency_score        double precision NULL DEFAULT 0,
  peer_rank                integer     NULL,  -- ranking 1-10 en esa partida
  resilience_index         double precision NULL DEFAULT 0,
  early_solo_deaths        double precision NULL DEFAULT 0,
  early_gank_deaths        double precision NULL DEFAULT 0,
  CONSTRAINT scout_participants_pkey PRIMARY KEY (id),
  CONSTRAINT scout_participants_match_participant_key UNIQUE (match_id, participant_id),
  CONSTRAINT scout_participants_match_id_fkey FOREIGN KEY (match_id)
    REFERENCES matches (match_id) ON DELETE CASCADE
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_sp_match_id   ON public.scout_participants USING btree (match_id);
CREATE INDEX IF NOT EXISTS idx_sp_puuid      ON public.scout_participants USING btree (puuid);
CREATE INDEX IF NOT EXISTS idx_sp_game_name  ON public.scout_participants USING btree (game_name);
CREATE INDEX IF NOT EXISTS idx_sp_role       ON public.scout_participants USING btree (role);

-- ──────────────────────────────────────────────
-- 3. TIMELINE (frames de posición y gold)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.scout_timeline (
  id              bigserial   NOT NULL,
  match_id        text        NOT NULL,
  participant_id  integer     NOT NULL,
  timestamp_ms    bigint      NOT NULL,
  timestamp_min   real        NULL,
  total_gold      integer     NULL DEFAULT 0,
  cs              integer     NULL DEFAULT 0,
  xp              integer     NULL DEFAULT 0,
  level           integer     NULL DEFAULT 1,
  pos_x           integer     NULL DEFAULT 0,
  pos_y           integer     NULL DEFAULT 0,
  role            text        NULL,  -- Rol del jugador (TOP, JUNGLE, MID, BOT, SUPPORT)
  team_id         integer     NULL DEFAULT 0,  -- 100 = azul, 200 = rojo
  CONSTRAINT scout_timeline_pkey PRIMARY KEY (id),
  CONSTRAINT scout_timeline_match_id_fkey FOREIGN KEY (match_id)
    REFERENCES matches (match_id) ON DELETE CASCADE,
  CONSTRAINT uq_scout_timeline UNIQUE (match_id, participant_id, timestamp_ms)
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_stl_match_ts          ON public.scout_timeline USING btree (match_id, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_stl_match_participant  ON public.scout_timeline USING btree (match_id, participant_id);
CREATE INDEX IF NOT EXISTS idx_stl_role             ON public.scout_timeline USING btree (role);
CREATE INDEX IF NOT EXISTS idx_stl_team_id          ON public.scout_timeline USING btree (team_id);

-- ──────────────────────────────────────────────
-- 4. EVENTOS (kills, objetivos, wards, items)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.scout_events (
  id              bigserial   NOT NULL,
  match_id        text        NOT NULL,
  participant_id  integer     NOT NULL,
  victim_id       integer     NULL DEFAULT 0,
  team_id         integer     NULL DEFAULT 0,
  victim_team_id  integer     NULL DEFAULT 0,
  timestamp_ms    bigint      NOT NULL,
  timestamp_min   real        NULL,
  event_type      text        NOT NULL,
  monster_type    text        NULL DEFAULT '',
  building_type   text        NULL DEFAULT '',
  ward_type       text        NULL DEFAULT '',
  item_id         integer     NULL DEFAULT 0,
  position_x      integer     NULL DEFAULT 0,
  position_y      integer     NULL DEFAULT 0,
  assisting_ids   text        NULL DEFAULT '',
  CONSTRAINT scout_events_pkey PRIMARY KEY (id),
  CONSTRAINT scout_events_match_id_fkey FOREIGN KEY (match_id)
    REFERENCES matches (match_id) ON DELETE CASCADE,
  CONSTRAINT uq_scout_events UNIQUE (match_id, participant_id, timestamp_ms, event_type)
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_sev_match_type ON public.scout_events USING btree (match_id, event_type);
CREATE INDEX IF NOT EXISTS idx_sev_match_ts   ON public.scout_events USING btree (match_id, timestamp_ms);

-- ──────────────────────────────────────────────
-- 5. PERFILES DE JUGADORES ANALIZADOS
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.scout_profiles (
  id                bigserial   NOT NULL,
  riot_id           text        NOT NULL,  -- "GameName#TAG"
  display_name      text        NOT NULL,
  primary_role      text        NOT NULL,
  valid_roles       text[]      NULL,
  platform          text        NOT NULL DEFAULT 'la2',
  queue_filter      integer[]   NULL DEFAULT '{420}',
  historical_names  text[]      NULL,  -- Nombres históricos/alias (solo GameName)
  last_synced       timestamp with time zone NULL,
  created_at        timestamp with time zone NULL DEFAULT now(),
  CONSTRAINT scout_profiles_pkey PRIMARY KEY (id),
  CONSTRAINT scout_profiles_riot_id_key UNIQUE (riot_id)
) TABLESPACE pg_default;

-- ──────────────────────────────────────────────
-- 6. SNAPSHOTS DE PERFORMANCE LONGITUDINAL
-- (1 fila por partida por perfil — histórico de evolución)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.scout_snapshots (
  id                  bigserial   NOT NULL,
  profile_id          bigint      NOT NULL,
  match_id            text        NOT NULL,
  role_played         text        NOT NULL,
  champion            text        NOT NULL,
  result              boolean     NULL,
  duration_minutes    real        NULL,
  patch               text        NULL,
  -- KPIs
  impact_score        double precision NULL,
  kda                 double precision NULL,
  cs_per_min          double precision NULL,
  damage_per_min      double precision NULL,
  vision_per_min      double precision NULL,
  gold_per_min        double precision NULL,
  kill_participation  double precision NULL,
  kill_conversion     double precision NULL,
  -- Pilares
  pilar_combat        double precision NULL,
  pilar_map           double precision NULL,
  pilar_utility       double precision NULL,
  pilar_consistency   double precision NULL,
  -- Individual analytics
  consistency_score   double precision NULL,
  peer_rank           integer     NULL,  -- ranking del jugador entre los 10 (1=mejor)
  -- Timestamps
  match_date          timestamp with time zone NULL,
  recorded_at         timestamp with time zone NULL DEFAULT now(),
  CONSTRAINT scout_snapshots_pkey PRIMARY KEY (id),
  CONSTRAINT scout_snapshots_profile_match_key UNIQUE (profile_id, match_id),
  CONSTRAINT scout_snapshots_profile_id_fkey FOREIGN KEY (profile_id)
    REFERENCES scout_profiles (id) ON DELETE CASCADE,
  CONSTRAINT scout_snapshots_match_id_fkey FOREIGN KEY (match_id)
    REFERENCES matches (match_id) ON DELETE CASCADE
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_ss_profile_match ON public.scout_snapshots USING btree (profile_id, match_id);
CREATE INDEX IF NOT EXISTS idx_ss_profile_date  ON public.scout_snapshots USING btree (profile_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_ss_champion       ON public.scout_snapshots USING btree (profile_id, champion);

-- ──────────────────────────────────────────────
-- 7. CHAMPION POOL (stats agregados por campeón)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.scout_champion_pool (
  id              bigserial   NOT NULL,
  profile_id      bigint      NOT NULL,
  champion        text        NOT NULL,
  role            text        NOT NULL,
  n_games         integer     NULL DEFAULT 0,
  win_rate        double precision NULL,
  avg_impact      double precision NULL,
  avg_kda         double precision NULL,
  avg_cs_min      double precision NULL,
  avg_damage_min  double precision NULL,
  avg_vision_min  double precision NULL,
  avg_gold_min    double precision NULL,
  consistency     double precision NULL,  -- coeff of variation (inverso)
  last_played     timestamp with time zone NULL,
  CONSTRAINT scout_champion_pool_pkey PRIMARY KEY (id),
  CONSTRAINT scout_champion_pool_unique UNIQUE (profile_id, champion, role),
  CONSTRAINT scout_champion_pool_profile_id_fkey FOREIGN KEY (profile_id)
    REFERENCES scout_profiles (id) ON DELETE CASCADE
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_scp_profile ON public.scout_champion_pool USING btree (profile_id);

-- ──────────────────────────────────────────────
-- 8. BENCHMARKS (Challenger reference — igual que proyecto base)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.benchmarks_summary (
  id              bigserial   NOT NULL,
  champion        text        NOT NULL,
  role            text        NOT NULL,
  patch           text        NOT NULL,
  result          boolean     NOT NULL,
  -- Core KPIs
  gold_per_min    double precision NULL,
  cs_per_min      double precision NULL,
  vision_per_min  double precision NULL,
  damage_per_min  double precision NULL,
  kda             double precision NULL,
  kill_participation double precision NULL,
  damage_per_gold double precision NULL,
  cc_per_min      double precision NULL,
  -- Impact
  impact_score    double precision NULL,
  kill_conversion double precision NULL,
  pilar_combat_efficiency  double precision NULL,
  pilar_map_pressure       double precision NULL,
  pilar_tactical_utility   double precision NULL,
  -- Metadata
  sample_size     integer     NULL DEFAULT 0,
  control_wards   double precision NULL,
  deaths          double precision NULL,
  damage_mitigated double precision NULL,
  -- Synergy
  synergy_jg_sup  double precision NULL,
  synergy_jg_mid  double precision NULL,
  synergy_jg_top  double precision NULL,
  synergy_jg_adc  double precision NULL,
  synergy_adc_sup double precision NULL,
  synergy_mid_bot double precision NULL,
  synergy_mid_top double precision NULL,
  synergy_mid_sup double precision NULL,
  synergy_top_bot double precision NULL,
  synergy_top_sup double precision NULL,
  CONSTRAINT benchmarks_summary_pkey PRIMARY KEY (id),
  CONSTRAINT benchmarks_summary_unique UNIQUE (champion, role, patch, result)
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_bm_champion_role ON public.benchmarks_summary USING btree (champion, role);
CREATE INDEX IF NOT EXISTS idx_bm_patch          ON public.benchmarks_summary USING btree (patch);



-- ──────────────────────────────────────────────
-- 9.5 BENCHMARKS STATS RAW (Histórico Challenger)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.benchmarks_stats_raw (
  id              bigserial   NOT NULL,
  match_id        text        NOT NULL,
  champion        text        NOT NULL,
  role            text        NOT NULL,
  patch           text        NOT NULL,
  result          boolean     NOT NULL,
  gold_per_min    double precision NULL,
  cs_per_min      double precision NULL,
  vision_per_min  double precision NULL,
  damage_per_min  double precision NULL,
  kda             double precision NULL,
  kill_participation double precision NULL,
  damage_per_gold     double precision NULL,
  cc_per_min          double precision NULL,
  impact_score    double precision NULL,
  kill_conversion double precision NULL,
  pilar_combat_efficiency  double precision NULL,
  pilar_map_pressure       double precision NULL,
  pilar_tactical_utility   double precision NULL,
  control_wards            double precision NULL,
  deaths                   double precision NULL,
  damage_mitigated         double precision NULL,
  -- Synergy
  synergy_jg_sup  double precision NULL,
  synergy_jg_mid  double precision NULL,
  synergy_jg_top  double precision NULL,
  synergy_jg_adc  double precision NULL,
  synergy_adc_sup double precision NULL,
  synergy_mid_bot double precision NULL,
  synergy_mid_top double precision NULL,
  synergy_mid_sup double precision NULL,
  synergy_top_bot double precision NULL,
  synergy_top_sup double precision NULL,
  CONSTRAINT benchmarks_stats_raw_pkey PRIMARY KEY (id),
  CONSTRAINT benchmarks_stats_raw_unique UNIQUE (match_id, champion, role)
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_bsr_champ_role ON public.benchmarks_stats_raw USING btree (champion, role);

-- ──────────────────────────────────────────────
-- 10. POLÍTICAS DE SEGURIDAD (Permisivas para uso local/anon)
-- ──────────────────────────────────────────────
-- Activamos RLS pero creamos una política abierta para que el script (usando anon key) pueda insertar y leer sin problemas.

DO $$ 
DECLARE 
    t text;
BEGIN 
    FOR t IN 
        SELECT table_name FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name IN (
            'scout_profiles', 'scout_snapshots', 'scout_champion_pool', 
            'matches', 'scout_participants', 'scout_events', 'scout_timeline',
            'benchmarks_summary', 'benchmarks_stats_raw'
        )
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', t);
        
        -- Borrar políticas previas si existían
        EXECUTE format('DROP POLICY IF EXISTS "Permitir_Todo_Anon_%I" ON public.%I;', t, t);
        
        -- Crear política que permite TODO a TODOS (Ideal para este proyecto donde controlas el script)
        EXECUTE format('CREATE POLICY "Permitir_Todo_Anon_%I" ON public.%I FOR ALL USING (true) WITH CHECK (true);', t, t);
    END LOOP;
END $$;


