-- ============================================================
-- Challenger Protocol — v2 Schema
-- Ejecutar en Supabase Dashboard → SQL Editor
-- ============================================================
-- Conserva las tablas matches y team_participants del schema v1.
-- Agrega position_nodes, combat_clusters y objective_prep_stats.
-- ============================================================

-- ──────────────────────────────────────────────
-- Tablas existentes (v1) — conservadas
-- ──────────────────────────────────────────────

create table IF NOT EXISTS public.matches (
  match_id text not null,
  region text not null,
  platform text not null,
  patch_version text not null,
  duration_min double precision not null,
  is_processed boolean null default false,
  created_at timestamp with time zone null default now(),
  constraint matches_pkey primary key (match_id)
) TABLESPACE pg_default;

create table IF NOT EXISTS public.team_participants (
  id bigserial not null,
  match_id text not null,
  participant_id integer not null,
  puuid text null,
  game_name text null,
  tag_line text null,
  team_id integer null,
  role text null,
  champion text null,
  kills integer null default 0,
  deaths integer null default 0,
  assists integer null default 0,
  gold_earned integer null default 0,
  total_damage integer null default 0,
  damage_taken integer null default 0,
  damage_mitigated integer null default 0,
  damage_buildings_raw integer null default 0,
  vision_score integer null default 0,
  cs integer null default 0,
  duration_minutes real null,
  result boolean null,
  is_custom boolean null default false,
  first_blood boolean null default false,
  impact_score double precision null,
  synergy_jg_sup double precision null default 0,
  synergy_jg_mid double precision null default 0,
  synergy_jg_top double precision null default 0,
  synergy_jg_adc double precision null default 0,
  synergy_adc_sup double precision null default 0,
  synergy_mid_bot double precision null default 0,
  synergy_mid_top double precision null default 0,
  synergy_mid_sup double precision null default 0,
  synergy_top_bot double precision null default 0,
  synergy_top_sup double precision null default 0,
  synergy_score double precision null default 0,
  kda double precision null,
  pilar_combat_efficiency double precision null default 0,
  pilar_map_pressure double precision null default 0,
  pilar_tactical_utility double precision null default 0,
  pilar_team_synergy double precision null default 0,
  resilience_index double precision null default 0,
  damage_per_gold real null default 0,
  cc_per_min real null default 0,
  kill_participation double precision null default 0,
  objective_control double precision null default 0,
  kill_conversion double precision null default 0,
  damage_efficiency double precision null default 0,
  damage_buildings integer null default 0,
  early_solo_deaths double precision null default 0,
  early_gank_deaths double precision null default 0,
  early_gank_kills double precision null default 0,
  constraint team_participants_pkey primary key (id),
  constraint team_participants_match_id_participant_id_key unique (match_id, participant_id),
  constraint team_participants_match_id_fkey foreign KEY (match_id) references matches (match_id) on delete CASCADE
) TABLESPACE pg_default;

create index IF not exists idx_tp_match_id on public.team_participants using btree (match_id) TABLESPACE pg_default;
create index IF not exists idx_tp_game_name on public.team_participants using btree (game_name) TABLESPACE pg_default;
create index IF not exists idx_tp_role on public.team_participants using btree (role) TABLESPACE pg_default;

-- ──────────────────────────────────────────────
-- Tablas nuevas (v2)
-- ──────────────────────────────────────────────

-- 1. position_nodes: posiciones unificadas de frames y eventos
create table IF NOT EXISTS public.position_nodes (
  id bigserial not null,
  match_id text not null,
  participant_id integer not null,
  timestamp_ms bigint not null,
  x_pos integer not null default 0,
  y_pos integer not null default 0,
  node_type text not null check (node_type in ('frame', 'event')),
  calculated_unspent_gold integer null default 0,
  constraint position_nodes_pkey primary key (id),
  constraint position_nodes_match_id_fkey foreign KEY (match_id) references matches (match_id) on delete CASCADE
) TABLESPACE pg_default;

-- 2. combat_clusters: salida de DBSCAN sobre eventos de combate
create table IF NOT EXISTS public.combat_clusters (
  id bigserial not null,
  match_id text not null,
  cluster_id integer not null,
  cluster_type text not null check (cluster_type in ('PICK', 'SKIRMISH', 'TEAMFIGHT')),
  center_x double precision not null default 0,
  center_y double precision not null default 0,
  start_time bigint not null,
  end_time bigint not null,
  constraint combat_clusters_pkey primary key (id),
  constraint combat_clusters_match_id_fkey foreign KEY (match_id) references matches (match_id) on delete CASCADE
) TABLESPACE pg_default;

-- 3. objective_prep_stats: métricas T-60s previas a cada objetivo
create table IF NOT EXISTS public.objective_prep_stats (
  id bigserial not null,
  match_id text not null,
  objective_type text not null,
  team_id integer not null,
  wards_placed integer not null default 0,
  avg_numerical_advantage double precision not null default 0,
  constraint objective_prep_stats_pkey primary key (id),
  constraint objective_prep_stats_match_id_fkey foreign KEY (match_id) references matches (match_id) on delete CASCADE
) TABLESPACE pg_default;

-- ──────────────────────────────────────────────
-- Índices para consultas time-series
-- ──────────────────────────────────────────────

-- position_nodes: búsqueda por partida + timestamp (time-series)
create index IF not exists idx_pn_match_ts
  on public.position_nodes using btree (match_id, timestamp_ms)
  TABLESPACE pg_default;

-- position_nodes: búsqueda por participante dentro de una partida
create index IF not exists idx_pn_match_participant
  on public.position_nodes using btree (match_id, participant_id)
  TABLESPACE pg_default;

-- position_nodes: filtro por tipo de nodo
create index IF not exists idx_pn_node_type
  on public.position_nodes using btree (node_type)
  TABLESPACE pg_default;

-- combat_clusters: búsqueda por partida + ventana temporal
create index IF not exists idx_cc_match_time
  on public.combat_clusters using btree (match_id, start_time, end_time)
  TABLESPACE pg_default;

-- combat_clusters: filtro por tipo de cluster
create index IF not exists idx_cc_cluster_type
  on public.combat_clusters using btree (cluster_type)
  TABLESPACE pg_default;

-- objective_prep_stats: búsqueda por partida + tipo de objetivo
create index IF not exists idx_ops_match_objective
  on public.objective_prep_stats using btree (match_id, objective_type)
  TABLESPACE pg_default;

-- objective_prep_stats: búsqueda por equipo dentro de una partida
create index IF not exists idx_ops_match_team
  on public.objective_prep_stats using btree (match_id, team_id)
  TABLESPACE pg_default;

-- ──────────────────────────────────────────────
-- Parches de Integridad para Tablas V1 (Prevención de Duplicados)
-- ──────────────────────────────────────────────
ALTER TABLE public.team_timeline ADD CONSTRAINT uq_timeline_match_time UNIQUE (match_id, participant_id, timestamp_ms);
ALTER TABLE public.team_events ADD CONSTRAINT uq_events_match_time UNIQUE (match_id, participant_id, timestamp_ms, event_type);
