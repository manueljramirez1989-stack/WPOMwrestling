#!/usr/bin/env python3
"""
WPOM BOARD ENGINE  — sustainable score+NWI+merge writer for hs_board
====================================================================
One engine. Locked formula imported from score_all.py (single source of truth).
NWI is no longer a separate patch — it is a step in the scoring pass.

WHAT IT DOES
  1. score affected wrestlers from flo_results via calc_wpom (new tiers/weights)
  2. NWI pass: credit ladder from flo_matches wins vs opponents' board scores
  3. merge onto current hs_board: anchors frozen, NIL reassigned, fields preserved
  4. write hs_board (zero redeploy)

SCOPE (this run): only states whose tier/weight changed move; everyone else
is frozen at their current board score. --full does a complete canonical rebuild
(use only once flo_results is clean and IL is backfilled).

IL is not in flo_results, so IL alone is re-tiered by exact single-event
decomposition (x0.85). CA/MN/OK/NE/WI recompute fully from raw data.

USAGE (Crostini):
  KEY=$(grep -o 'sb_secret_[A-Za-z0-9_-]*' ~/score_all.py | head -1)
  SUPABASE_SECRET="$KEY" python3 board_engine.py            # preview + diff
  SUPABASE_SECRET="$KEY" python3 board_engine.py --apply     # write hs_board
  SUPABASE_SECRET="$KEY" python3 board_engine.py --full      # full rebuild (preview)
"""
import os, sys, json, urllib.request
from collections import defaultdict
import score_all as SA   # locked formula: calc_wpom, place_score, state_tier, state_mult, EVENT_MULT, normalize_weight, NHSCA_MAP

SUPA = "https://yewigtsyvxiwprcpicup.supabase.co"
KEY  = os.environ["SUPABASE_SECRET"]
APPLY = "--apply" in sys.argv
FULL  = "--full" in sys.argv

# states whose scoring changed this round (drive the scoped recompute)
DEMOTED   = {'IL','MN','OK','NE','WI'}            # tier 1 -> 2
EVT_BUMP  = {'CA','OH','NJ','IA'}                 # tier-1 state titles 0.80 -> 0.85 (PA already 0.85)
AFFECTED  = DEMOTED | EVT_BUMP
IL_DECOMP = {'IL'}                                # not in flo_results -> decompose in place

ANCHORS = {  # (name lower, state) — never recomputed
    ("bo bassett","PA"),("melvin miller","PA"),("brayden harer","PA"),("sean kenny","NJ"),
    ("antonio mills","GA"),("fred bachmann","PA"),("caleb noble","IL"),("cael shepherd","PA"),
    ("bentley sly","NC"),("ariah mills","GA"),("meyer shapiro","PA"),("meyer shapiro","MD"),
}
STD_VERDICTS = {"develop","monitor","high-upside","elite","blue-chip"}

def nil_for(s):  # locked ladder (from merge_hs_data)
    s=float(s)
    if s>=5.0: return '$500K (outlier)'
    if s>=4.8: return '$150K – $200K'
    if s>=4.5: return '$100K – $150K'
    if s>=4.0: return '$50K – $100K'
    if s>=3.5: return '$25K – $50K'
    if s>=3.0: return '$10K – $25K'
    if s>=2.5: return '$3K – $10K'
    if s>=2.0: return '$1K – $3K'
    if s>=1.5: return '$500 – $1K'
    return 'Unverified'

def get(path):
    rows, off, page = [], 0, 1000
    while True:
        rq=urllib.request.Request(SUPA+path, headers={"apikey":KEY,"Authorization":"Bearer "+KEY,
            "Range-Unit":"items","Range":f"{off}-{off+page-1}","Accept-Encoding":"gzip"})
        import gzip
        with urllib.request.urlopen(rq, timeout=120) as r:
            raw=r.read()
            if r.headers.get("Content-Encoding")=="gzip": raw=gzip.decompress(raw)
            batch=json.loads(raw)
        if not batch: break
        rows+=batch
        print(f"    {path.split('?')[0].split('/')[-1]}: {len(rows):,}", end="\r")
        if len(batch)<page: break
        off+=page
    print(); return rows

def nwi_bonus(s):
    if s>=4.5: return 0.30
    if s>=4.0: return 0.22
    if s>=3.5: return 0.15
    if s>=3.0: return 0.10
    if s>=2.5: return 0.06
    return 0.03

# ── load everything ───────────────────────────────────────────────
print("[1/6] flo_events..."); 
ev=get("/rest/v1/flo_events?select=event_uuid,short_name,year")
emap={e['event_uuid']:e.get('short_name','Unknown') for e in ev}
print("[2/6] flo_results..."); 
res=get("/rest/v1/flo_results?select=event_uuid,first_name,last_name,state,weight,placement,division")
print("[3/6] flo_matches..."); 
bouts=get("/rest/v1/flo_matches?select=top_wrestler_guid,bottom_wrestler_guid,winner_guid,is_bye")
guidmap=get("/rest/v1/flo_results?select=wrestler_guid,first_name,last_name,state")
print("[4/6] hs_board (current/curation)..."); 
board=get("/rest/v1/hs_board?select=board,weight,athletes")

# current board score lookup (opponent values for NWI + freeze source)
cur_score={}; cur_entry={}
for row in board:
    for a in row['athletes']:
        k=((a.get('name') or '').strip().lower(),(a.get('state') or '').strip().upper())
        sc=float(a.get('score') or 0)
        if sc>cur_score.get(k,0): cur_score[k]=sc; cur_entry[k]=(row['board'],row['weight'],a)

# ── score affected wrestlers from flo_results (Pass 1: base, no NWI) ──
print("[5/6] scoring affected wrestlers from flo_results...")
grouped=defaultdict(list)
for r in res: grouped[(r['first_name'],r['last_name'],r['state'])].append(r)

scored={}   # (name_l,state_U) -> dict(base entry from calc_wpom)
for (first,last,state),rws in grouped.items():
    st=(state or '').upper()
    if not FULL and st not in AFFECTED: continue       # SCOPED: only changed states
    if st in IL_DECOMP: continue                        # IL handled by decomposition, not here
    natl=[]; wt_votes=defaultdict(int); is_girl=False
    for r in rws:
        short=emap.get(r['event_uuid'],'Unknown'); place=r.get('placement')
        if not place: continue
        natl.append({'event':short,'place':place})
        d=(r.get('division') or '').lower()
        if any(t in d for t in ('girl','women','woman','female','wmn')): is_girl=True
        w=SA.normalize_weight(r.get('weight'), r.get('division'))
        if w: wt_votes[w]+=1
    if not natl or not wt_votes: continue
    rec=SA.calc_wpom(state, natl)        # uses NEW tiers/weights
    if rec['score']<1.5: continue
    slot=max(wt_votes.items(), key=lambda x:x[1])[0]
    evstr='+'.join(sorted({e['event'].upper().replace(' ','') for e in natl}))
    scored[((first+' '+last).strip().lower(), st)]={
        'name':f"{first} {last}",'state':state,'base':rec['score'],
        'events':evstr,'conf':'green' if len(natl)>=2 else 'yellow',
        'is_girl':is_girl,'slot':slot}

# ── NWI pass (folded into engine) ─────────────────────────────────
gn={g['wrestler_guid']:((f"{(g.get('first_name') or '').strip()} {(g.get('last_name') or '').strip()}").strip().lower(),
    (g.get('state') or '').strip().upper()) for g in guidmap if g.get('wrestler_guid')}
def bscore(nm,stU):
    s=cur_score.get((nm,stU))
    return s if s else 0
nwi_raw=defaultdict(float)
for b in bouts:
    if b.get('is_bye'): continue
    w=b.get('winner_guid'); t,bo=b.get('top_wrestler_guid'),b.get('bottom_wrestler_guid')
    if not w or w not in (t,bo) or w not in gn: continue
    loser=bo if w==t else t
    if loser not in gn: continue
    wnm,wst=gn[w]; lnm,lst=gn[loser]
    if wnm==lnm: continue
    osc=bscore(lnm,lst)
    if osc>0: nwi_raw[(wnm,wst)]+=nwi_bonus(osc)

# ── merge: compute new score per affected athlete, freeze the rest ──
print("[6/6] merging (anchors frozen, IL decomposed, NIL reassigned)...")
changes=[]; anchor_frozen=0; il_decomp=0; recomputed=0; unexpected=[]
new_board={(row['board'],row['weight']):[] for row in board}

for row in board:
    for a in row['athletes']:
        nm=(a.get('name') or '').strip().lower(); st=(a.get('state') or '').strip().upper()
        key=(nm,st); old=float(a.get('score') or 0); nwi=float(a.get('nwi') or 0)
        out=dict(a)
        if key in ANCHORS:
            anchor_frozen+=1
        elif st in IL_DECOMP and not FULL:
            base=old-nwi; new=round(min(5.0, base*0.85 + nwi),2)   # tier1->2 exact for single-event
            if abs(new-old)>1e-9:
                out['score']=new; out['nwi']=round(nwi,2)
                if out.get('verdict') in STD_VERDICTS: out['verdict']=SA.verdict_for_score(new)
                out['nil']=nil_for(new); il_decomp+=1
                changes.append((a.get('name'),st,old,new,'IL decomp'))
        elif key in scored and (FULL or st in AFFECTED):
            s=scored[key]; addnwi=min(round(nwi_raw.get(key,0),2),0.40)
            new=round(min(5.0, s['base']+addnwi),2)
            if abs(new-old)>1e-9:
                out['score']=new; out['nwi']=addnwi; out['events']=s['events']
                if out.get('verdict') in STD_VERDICTS: out['verdict']=SA.verdict_for_score(new)
                out['nil']=nil_for(new); recomputed+=1
                tag='recompute' if st in AFFECTED else 'rebuild'
                changes.append((a.get('name'),st,old,new,tag))
                if st not in AFFECTED: unexpected.append((a.get('name'),st,old,new))
        new_board[(row['board'],row['weight'])].append(out)

for k in new_board: new_board[k].sort(key=lambda x:-(float(x.get('score') or 0)))

# ── report ────────────────────────────────────────────────────────
changes.sort(key=lambda x:abs(x[3]-x[2]), reverse=True)
print("\n"+"="*72)
print(f"BOARD ENGINE — {'FULL REBUILD' if FULL else 'SCOPED RECOMPUTE'} — {'APPLYING' if APPLY else 'PREVIEW (no writes)'}")
print("="*72)
print(f"  anchors frozen:            {anchor_frozen}")
print(f"  recomputed from flo_results: {recomputed}")
print(f"  IL decomposed (x0.85):     {il_decomp}")
print(f"  total score changes:       {len(changes)}")
ups=sum(1 for c in changes if c[3]>c[2]); downs=sum(1 for c in changes if c[3]<c[2])
print(f"    moved up: {ups}   moved down: {downs}")
print(f"\n  anchor verification (must equal locked values):")
for row in new_board.values():
    for a in row:
        if ((a.get('name') or '').strip().lower(),(a.get('state') or '').strip().upper()) in ANCHORS:
            print(f"     {a['name']:<20} {a['state']} = {a['score']}")
print(f"\n  biggest movers:")
for n,st,o,nw,tag in changes[:15]:
    print(f"     {n:<24} {st} {o} -> {nw}  [{tag}]")
if unexpected:
    print(f"\n  ⚠ UNEXPECTED movers (state NOT in changed set — investigate before apply): {len(unexpected)}")
    for n,st,o,nw in unexpected[:10]: print(f"     {n:<24} {st} {o} -> {nw}")
else:
    print(f"\n  ✓ no unexpected movers — every change is in a state we intended to touch")

if APPLY:
    print("\n  writing hs_board...")
    ok=0
    for row in board:
        bd,wt=row['board'],row['weight']
        body=json.dumps({"athletes":new_board[(bd,wt)]}).encode()
        rq=urllib.request.Request(SUPA+f"/rest/v1/hs_board?board=eq.{bd}&weight=eq.{wt}",
            data=body, method="PATCH",
            headers={"apikey":KEY,"Authorization":"Bearer "+KEY,"Content-Type":"application/json","Prefer":"return=minimal"})
        with urllib.request.urlopen(rq,timeout=120) as r: ok+=1
    print(f"  PATCHed {ok}/{len(board)} rows. hs_board updated.")
else:
    print("\n  re-run with --apply to write.  (review unexpected movers first!)")
