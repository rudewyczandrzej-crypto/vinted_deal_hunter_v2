-- Vinted AI Deal Hunter v2
-- Safe to run multiple times in Supabase SQL Editor.
-- It creates/updates tables used by main.py. It is not a file that the bot reads.

create table if not exists users (
  id bigint primary key generated always as identity,
  telegram_id text unique not null,
  created_at timestamp with time zone default now()
);

create table if not exists searches (
  id bigint primary key generated always as identity,
  telegram_id text not null,
  keyword text not null,
  vinted_query text,
  max_price numeric,
  country text default 'pl',
  active boolean default true,
  filter_json jsonb,
  filter_summary text,
  min_ai_score integer default 4,
  created_at timestamp with time zone default now()
);

alter table searches add column if not exists vinted_query text;
alter table searches add column if not exists filter_json jsonb;
alter table searches add column if not exists filter_summary text;
alter table searches add column if not exists min_ai_score integer default 4;

create table if not exists sent_items (
  id bigint primary key generated always as identity,
  telegram_id text not null,
  search_id bigint,
  item_id text not null,
  url text,
  item_json jsonb default '{}'::jsonb,
  ai_json jsonb default '{}'::jsonb,
  ai_score integer,
  deal_score integer,
  sent_at timestamp with time zone default now(),
  unique(telegram_id, item_id)
);

alter table sent_items add column if not exists item_json jsonb default '{}'::jsonb;
alter table sent_items add column if not exists ai_json jsonb default '{}'::jsonb;
alter table sent_items add column if not exists ai_score integer;
alter table sent_items add column if not exists deal_score integer;

create table if not exists offer_feedback (
  id bigint primary key generated always as identity,
  telegram_id text not null,
  search_id bigint,
  sent_item_id bigint,
  feedback_type text not null,
  note text default '',
  created_at timestamp with time zone default now()
);

alter table offer_feedback add column if not exists note text default '';

create unique index if not exists idx_offer_feedback_unique
  on offer_feedback(telegram_id, sent_item_id);

create table if not exists filter_learning_logs (
  id bigint primary key generated always as identity,
  telegram_id text not null,
  search_id bigint,
  sent_item_id bigint,
  feedback_type text not null,
  old_filter_json jsonb,
  new_filter_json jsonb,
  summary text,
  created_at timestamp with time zone default now()
);

create index if not exists idx_searches_telegram_active on searches(telegram_id, active);
create index if not exists idx_sent_items_telegram_item on sent_items(telegram_id, item_id);
create index if not exists idx_searches_filter_json on searches using gin(filter_json);
create index if not exists idx_offer_feedback_search on offer_feedback(search_id);
create index if not exists idx_filter_learning_logs_search on filter_learning_logs(search_id);
