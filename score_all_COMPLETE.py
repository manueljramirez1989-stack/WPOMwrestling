#!/usr/bin/env python3
"""
WPOM — Score All Wrestlers
===========================
Pulls every unique wrestler from Supabase flo_results, applies the LOCKED
WPOM-HS formula (identical to score_check.py), and produces:

  ~/hs_data_final.pkl   — pickled HS_DATA dict for archival
  ~/hs_data_inject.js   — the const HS_DATA = {...} block ready to paste
                          into index.html (replaces the existing constant)

Formula is unchanged from score_check.py. We just iterate the full dataset
instead of a hardcoded 12-name target list.

USAGE:
    python3 ~/score_all.py

NOTES:
- state_titles defaulted to 0 — you can layer manual title data later
- NWI bonus defaulted to 0 — same
- Wrestlers with no national events return score=1.0 (formula floor)
- Output is grouped by weight class for direct HS_DATA injection
- PIAA Girls data is excluded from main HS_DATA and written to a separate
  hs_girls_data_inject.js for the June 2026 rollout
"""

import urllib.request, urllib.parse, json, gzip, pickle, sys, time
from collections import defaultdict

# ── CONFIG ────────────────────────────────────────────────────────────
SUPABASE_URL    = "https://yewigtsyvxiwprcpicup.supabase.co"
SUPABASE_SECRET = "sb_secret_dnVc-VAMmqAqnKog7KU1bA_8H2K0JVq"

T1 = ['CA','OH','NJ','PA','IA']  # 2026-06 restructure: only genuinely deep states
T2 = ['NY','MI','MO','CO','VA','MD','IN','TX','NC','KS','UT','GA','IL','MN','OK','NE','WI']  # IL/MN/OK/NE/WI demoted from T1
EVENT_MULT = {
    'Super 32': 1.00, 'Ironman': 0.95, 'Fargo': 0.90,
    'Walsh Ironman': 0.85, 'NHSCA': 0.80,
    'Beast of the East': 0.75, 'Powerade': 0.75,
    'PA States': 0.85,  # locked — preserves PA Tier 1 elite status without exceeding S32
    'CA States': 0.85, 'OH States': 0.85, 'NJ States': 0.85, 'IA States': 0.85,  # Tier-1 state titles > NHSCA (2026-06)
    'IL States': 0.85,  # IL demoted to Tier-2, but state title weighs = other state titles (2026-06)
}

# Girls event ladder (2026-06): girls wrestling lacks credible cross-state data to
# tier states, so we lean on national events as the high-confidence signal and weight
# state titles meaningfully lower. Not a judgment on state champs — a conservative
# stance that declines to over-credit a signal we can't yet verify. Revisit as more
# national girls data arrives. States are FLAT (no tiers) for girls.
EVENT_MULT_GIRLS = {
    'Super 32': 1.00, 'Fargo': 0.95, 'NHSCA': 0.85,
    'NHSCA Duals': 0.70,                # ladder-ready; backfill data separately (none yet)
    'Ironman': 0.75, 'Walsh Ironman': 0.75,
    'Beast of the East': 0.70, 'Powerade': 0.70,
    # all state championships flat — national events are the trusted signal:
    'PA States': 0.55, 'CA States': 0.55, 'OH States': 0.55,
    'NJ States': 0.55, 'IA States': 0.55, 'IL States': 0.55,
}
EVENT_DEFAULT_GIRLS = 0.55             # unknown event for a girl -> treat as state-level (conservative)

# NFHS HS weight classes used by the index.html HS_DATA structure
NFHS_WEIGHTS = [106, 113, 120, 126, 132, 138, 144, 150, 157, 165, 175, 190, 215, 285]

# Map NHSCA's slightly different weights to NFHS canonical
NHSCA_MAP = {145: 144, 152: 150, 160: 157, 170: 165, 182: 175, 195: 190, 220: 215}


# ── LOCKED FORMULA — verbatim from score_check.py ────────────────────
def place_score(p):
    p = int(p) if p else 99
    if p <= 1: return 4.05
    if p <= 2: return 3.55
    if p <= 4: return 3.15
    if p <= 8: return 2.85
    if p <= 16: return 2.55
    if p <= 32: return 2.25
    if p <= 98: return 2.05
    return 1.45

def state_tier(st):
    if not st: return 3
    if st in T1: return 1
    if st in T2: return 2
    return 3

def state_mult(tier):
    return 1.00 if tier == 1 else 0.85 if tier == 2 else 0.70

def calc_wpom(state, natl_results, state_titles=0, nwi_opponents=None, is_girl=False):
    nwi_opponents = nwi_opponents or []
    if not natl_results and state_titles == 0 and not nwi_opponents:
        return {"score": 1.0, "best": 0, "multi": 0, "title": 0, "nwi": 0,
                "tier": state_tier(state), "events": []}
    tier = state_tier(state)
    sm = 1.0 if is_girl else state_mult(tier)   # girls: no state tiers — national events carry the signal
    events = []
    for r in natl_results:
        if not r.get('event') or not r.get('place'):
            continue
        ps = place_score(r['place'])
        em = (EVENT_MULT_GIRLS.get(r['event'], EVENT_DEFAULT_GIRLS) if is_girl
              else EVENT_MULT.get(r['event'], 0.80))
        events.append({
            'event': r['event'], 'place': r['place'],
            'place_score': ps, 'event_mult': em,
            'event_score': ps * em
        })
    events.sort(key=lambda x: -x['event_score'])
    best = events[0]['event_score'] * sm if events else 0
    multi = min(max(0, len(events) - 1) * 0.15, 0.45)
    title_per = 0.08 if is_girl else (0.15 if tier == 1 else 0.12 if tier == 2 else 0.08)
    tbonus = min(state_titles * title_per, 0.35)
    nwi = 0
    for o in nwi_opponents:
        s = float(o.get('score', 0))
        if s >= 4.5: nwi += 0.30
        elif s >= 4.0: nwi += 0.22
        elif s >= 3.5: nwi += 0.15
        elif s >= 3.0: nwi += 0.10
        elif s >= 2.5: nwi += 0.06
        else: nwi += 0.03
    nwi = min(nwi, 0.40)
    final = round(max(1.0, min(5.0, best + multi + tbonus + nwi)), 2)
    return {
        "score": final, "best": round(best, 2), "multi": round(multi, 2),
        "title": round(tbonus, 2), "nwi": round(nwi, 2),
        "tier": tier, "events": events
    }


# ── SUPABASE HELPERS ──────────────────────────────────────────────────
def supabase_get(path):
    url = SUPABASE_URL + path
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_SECRET,
        "Authorization": f"Bearer {SUPABASE_SECRET}",
        "Accept-Encoding": "gzip"
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw)


def supabase_get_paginated(path, page_size=1000):
    """flo_results has 30k+ rows — must paginate via Range header."""
    all_rows = []
    offset = 0
    while True:
        url = SUPABASE_URL + path
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_SECRET,
            "Authorization": f"Bearer {SUPABASE_SECRET}",
            "Accept-Encoding": "gzip",
            "Range-Unit": "items",
            "Range": f"{offset}-{offset + page_size - 1}"
        })
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            batch = json.loads(raw)
        if not batch:
            break
        all_rows.extend(batch)
        print(f"    fetched {len(all_rows):,} rows...", end="\r")
        if len(batch) < page_size:
            break
        offset += page_size
    print()
    return all_rows


# ── BUILD HS_DATA ─────────────────────────────────────────────────────
def normalize_weight(weight_str, division):
    """Return an NFHS HS weight (int) or None to exclude from main board.
    Fargo freestyle/greco weights are kilos — exclude (not HS board).
    PIAA AAA/AA HS weights are NFHS already. NHSCA needs mapping."""
    if not weight_str:
        return None
    # Exclude freestyle/greco (kg-based, not HS competition)
    div_lower = (division or '').lower()
    if 'freestyle' in div_lower or 'greco' in div_lower:
        return None
    try:
        w = int(weight_str)
    except (ValueError, TypeError):
        return None
    # Map NHSCA weights to NFHS
    w = NHSCA_MAP.get(w, w)
    return w if w in NFHS_WEIGHTS else None


def verdict_for_score(score):
    if score >= 4.5: return 'blue-chip'
    if score >= 4.0: return 'elite'
    if score >= 3.5: return 'high-upside'
    if score >= 3.0: return 'monitor'
    if score >= 2.5: return 'develop'
    return 'develop'


def main():
    t0 = time.time()
    print("=" * 78)
    print("WPOM — SCORE ALL WRESTLERS  (locked formula, full flo_results)")
    print("=" * 78)

    # ── Fetch event registry ──
    print("\n[1/4] Fetching event registry...")
    events = supabase_get("/rest/v1/flo_events?select=event_uuid,short_name,year,event_mult")
    event_map = {e['event_uuid']: e for e in events}
    print(f"      {len(event_map)} events in registry")

    # ── Fetch ALL flo_results (paginated) ──
    print("\n[2/4] Fetching every wrestler row from flo_results...")
    rows = supabase_get_paginated(
        "/rest/v1/flo_results?select=event_uuid,first_name,last_name,state,weight,placement,division"
    )
    print(f"      {len(rows):,} total rows pulled")

    # ── Group by (first, last, state) ──
    print("\n[3/4] Grouping results per wrestler & scoring...")
    grouped = defaultdict(list)
    for r in rows:
        key = (r['first_name'], r['last_name'], r['state'])
        grouped[key].append(r)
    print(f"      {len(grouped):,} unique wrestlers")

    # ── Score each wrestler, build HS_DATA[weight] entries ──
    hs_data = defaultdict(list)      # NFHS boys HS board
    girls_data = defaultdict(list)   # PIAA Girls (June rollout)

    scored_count = 0
    sam_howard_score = None
    landyn_shaffer_score = None

    for (first, last, state), wrestler_rows in grouped.items():
        natl_results = []
        wt_votes = defaultdict(int)
        is_girl = False
        for r in wrestler_rows:
            evt = event_map.get(r['event_uuid'], {})
            event_short = evt.get('short_name', 'Unknown')
            place = r.get('placement')
            if not place:
                continue
            natl_results.append({'event': event_short, 'place': place})
            div = (r.get('division') or '').lower()
            if any(t in div for t in ('girl','women','woman','female','wmn',"girls'",'gl ')):
                is_girl = True
            # Tally weights — heaviest event tells us which weight class to slot
            w = normalize_weight(r.get('weight'), r.get('division'))
            if w:
                wt_votes[w] += 1

        if not natl_results:
            continue

        result = calc_wpom(state, natl_results, is_girl=is_girl)
        score = result['score']

        # Score floor — wrestlers under 1.5 don't appear on the board
        if score < 1.5:
            continue

        # Determine slot weight: most common HS weight in their history.
        # If no HS weights (only freestyle/greco), skip.
        if not wt_votes:
            continue
        slot_weight = max(wt_votes.items(), key=lambda x: x[1])[0]

        # Build events display string
        events_str = '+'.join(sorted({e['event'].upper().replace(' ', '') for e in natl_results}))
        conf = 'green' if len(natl_results) >= 2 else 'yellow'

        athlete = {
            'name': f"{first} {last}",
            'state': state,
            'score': score,
            'events': events_str,
            'conf': conf,
            'verdict': verdict_for_score(score),
            'nil': '',
            'commit': '',
            'gpa': '',
        }

        if is_girl:
            girls_data[slot_weight].append(athlete)
        else:
            hs_data[slot_weight].append(athlete)

        scored_count += 1

        # Capture our two target athletes for the summary
        if first == 'Sam' and last == 'Howard':
            sam_howard_score = (score, natl_results, result)
        if first == 'Landyn' and last == 'Shaffer':
            landyn_shaffer_score = (score, natl_results, result)

    # ── Sort each weight by score descending ──
    for wt in hs_data:
        hs_data[wt].sort(key=lambda a: -a['score'])
    for wt in girls_data:
        girls_data[wt].sort(key=lambda a: -a['score'])

    print(f"      scored {scored_count:,} wrestlers above 1.5 floor")
    print(f"      {sum(len(v) for v in hs_data.values()):,} on boys HS board across "
          f"{len(hs_data)} weights")
    print(f"      {sum(len(v) for v in girls_data.values()):,} on girls HS board across "
          f"{len(girls_data)} weights")

    # ── Write outputs ──
    print("\n[4/4] Writing pkl + inject files...")
    pickle.dump(dict(hs_data), open('/home/manueljramirez1989/hs_data_final.pkl', 'wb'))
    pickle.dump(dict(girls_data), open('/home/manueljramirez1989/hs_girls_data_final.pkl', 'wb'))

    write_inject_js(hs_data, '/home/manueljramirez1989/hs_data_inject.js', var_name='HS_DATA')
    write_inject_js(girls_data, '/home/manueljramirez1989/hs_girls_data_inject.js', var_name='HS_GIRLS_DATA')

    print(f"      ~/hs_data_final.pkl ({len(hs_data)} weights)")
    print(f"      ~/hs_data_inject.js")
    print(f"      ~/hs_girls_data_final.pkl ({len(girls_data)} weights)")
    print(f"      ~/hs_girls_data_inject.js")

    # ── Highlight the two target wrestlers ──
    print("\n" + "=" * 78)
    print("TARGET WRESTLERS")
    print("=" * 78)
    for name, info in [('Sam Howard', sam_howard_score), ('Landyn Shaffer', landyn_shaffer_score)]:
        if info is None:
            print(f"  {name}: NOT IN flo_results")
            continue
        score, results, breakdown = info
        print(f"\n  {name}  (state tier {breakdown['tier']}):")
        for r in results:
            print(f"    {r['event']:<20} place {r['place']}")
        print(f"  best x state_mult: {breakdown['best']}")
        print(f"  multi-event bonus: {breakdown['multi']}")
        print(f"  title bonus:       {breakdown['title']}")
        print(f"  NWI bonus:         {breakdown['nwi']}")
        print(f"  WPOM SCORE: {score}")

    print(f"\n[done in {time.time() - t0:.1f}s]")


def write_inject_js(data, path, var_name='HS_DATA'):
    """Write the JS const block ready to paste into index.html."""
    def esc(s):
        return str(s).replace("\\", "\\\\").replace("'", "\\'")
    lines = [f'const {var_name} = {{']
    weights = sorted(data.keys())
    for i, wt in enumerate(weights):
        lines.append(f'  {wt}: [')
        athletes = data[wt]
        for j, a in enumerate(athletes):
            comma = ',' if j < len(athletes) - 1 else ''
            lines.append(
                f"    {{name:'{esc(a['name'])}',state:'{a['state']}',"
                f"score:{a['score']},events:'{esc(a['events'])}',"
                f"conf:'{a['conf']}',verdict:'{a['verdict']}',"
                f"nil:'{esc(a['nil'])}',commit:'{esc(a['commit'])}',"
                f"gpa:'{esc(a['gpa'])}'}}{comma}"
            )
        wt_comma = ',' if i < len(weights) - 1 else ''
        lines.append(f'  ]{wt_comma}')
    lines.append('};')
    open(path, 'w').write('\n'.join(lines))


if __name__ == "__main__":
    main()
