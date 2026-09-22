-- WPOM landing page (QR / shirt / links): run once in the Supabase SQL editor. Safe to re-run.

-- 1. Fast name search for the landing page (12 results max, 3+ letters). Reads the board only.
create or replace function public.wpom_search(q text)
returns table(name text, state text, weight int, score numeric, class_year text, board text)
language sql stable
as $$
  select a->>'name', a->>'state', h.weight::int, (a->>'score')::numeric, a->>'class_year', h.board
  from hs_board h, jsonb_array_elements(h.athletes) a
  where length(trim(q)) >= 3
    and lower(a->>'name') like '%' || lower(trim(q)) || '%'
  order by (lower(a->>'name') like lower(trim(q)) || '%') desc, (a->>'score')::numeric desc
  limit 12
$$;
grant execute on function public.wpom_search(text) to anon, authenticated;

-- 2. Tracking table. The page can INSERT rows; nobody can read them through the public key.
create table if not exists public.landing_events (
  id bigserial primary key,
  ts timestamptz not null default now(),
  sid text,        -- anonymous per-device id
  src text,        -- 'shirt' (QR), 'direct', or whatever ?s= value you put on a link
  ev text,         -- view | search | search_miss | search_error | result_open | claim_click | claim_click_miss | profile_click | coach_click | home_click
  detail text,
  ua text
);
alter table public.landing_events enable row level security;
drop policy if exists landing_events_insert on public.landing_events;
create policy landing_events_insert on public.landing_events for insert to anon, authenticated with check (true);
create index if not exists landing_events_ts on public.landing_events (ts);

-- 3. The funnel, by source. Run this whenever you want the numbers.
create or replace view public.landing_funnel as
select src,
       count(distinct sid) filter (where ev='view')                                   as people_who_opened,
       count(distinct sid) filter (where ev in ('search','search_miss'))              as people_who_searched,
       count(distinct sid) filter (where ev='search')                                 as found_someone,
       count(distinct sid) filter (where ev='search_miss')                            as searched_and_missed,
       count(distinct sid) filter (where ev='result_open')                            as opened_a_result,
       count(distinct sid) filter (where ev in ('claim_click','claim_click_miss'))    as clicked_claim,
       count(distinct sid) filter (where ev='coach_click')                            as coach_clicks
from public.landing_events group by src order by 2 desc;

-- Names people searched for and did not find (your add-to-board list during the event):
-- select split_part(detail,'|',1) searched, count(*) from landing_events where ev='search_miss' group by 1 order by 2 desc limit 100;
