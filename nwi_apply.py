#!/usr/bin/env python3
"""WPOM NWI APPLY — writes NWI-adjusted scores into public.hs_board (boys).

Same computation as nwi_audit.py, with protections:
  - LOCKED ANCHORS are never modified (scores are calibration points)
  - score capped at 5.0; NIL band + verdict refreshed from the locked ladders
  - verdicts outside the standard ladder vocabulary (custom/curated) are preserved
  - each athlete gains an `nwi` field for transparency; weights re-sorted by score

Safety: runs in REPORT mode by default. Add --apply to write.

Usage:
  KEY=$(grep -o 'sb_secret_[A-Za-z0-9_-]*' ~/score_all.py | head -1)
  SUPABASE_SECRET="$KEY" python3 nwi_apply.py            # preview
  SUPABASE_SECRET="$KEY" python3 nwi_apply.py --apply    # write
"""
import os, sys, json, urllib.request
from collections import defaultdict

SUPA = "https://yewigtsyvxiwprcpicup.supabase.co"
KEY  = os.environ["SUPABASE_SECRET"]
APPLY = "--apply" in sys.argv

ANCHORS = {  # (name lower, state) — never recalculated
    ("bo bassett","PA"),("melvin miller","PA"),("brayden harer","PA"),
    ("sean kenny","NJ"),("antonio mills","GA"),("fred bachmann","PA"),
    ("caleb noble","IL"),("cael shepherd","PA"),("bentley sly","NC"),
    ("ariah mills","GA"),("meyer shapiro","PA"),("meyer shapiro","MD"),
}
STD_VERDICTS={"develop","monitor","high-upside","elite","blue-chip"}

def nil_for(s):
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
def verdict_for(s):
    s=float(s)
    if s>=4.5: return 'blue-chip'
    if s>=4.0: return 'elite'
    if s>=3.5: return 'high-upside'
    if s>=3.0: return 'monitor'
    return 'develop'
def nwi_bonus(s):
    if s>=4.5: return 0.30
    if s>=4.0: return 0.22
    if s>=3.5: return 0.15
    if s>=3.0: return 0.10
    if s>=2.5: return 0.06
    return 0.03

def get(path):
    rows, off, page = [], 0, 1000
    while True:
        rq=urllib.request.Request(SUPA+path, headers={"apikey":KEY,"Authorization":"Bearer "+KEY,
            "Range-Unit":"items","Range":f"{off}-{off+page-1}"})
        with urllib.request.urlopen(rq, timeout=120) as r:
            batch=json.loads(r.read())
        if not batch: break
        rows+=batch
        print(f"    {path.split('?')[0].split('/')[-1]}: {len(rows):,}", end="\r")
        if len(batch)<page: break
        off+=page
    print(); return rows

print("[1/5] hs_board (boys)...")
board=get("/rest/v1/hs_board?select=board,weight,athletes&board=eq.boys")
score_ns={}; score_nm=defaultdict(list)
for row in board:
    for a in row["athletes"]:
        nm=(a.get("name") or "").strip().lower(); st=(a.get("state") or "").strip().upper()
        sc=float(a.get("score") or 0)
        if sc>score_ns.get((nm,st),0): score_ns[(nm,st)]=sc
        score_nm[nm].append(sc)
print(f"    {sum(len(r['athletes']) for r in board):,} athletes")

print("[2/5] flo_results (guid map)...")
res=get("/rest/v1/flo_results?select=wrestler_guid,first_name,last_name,state")
guid={r["wrestler_guid"]:(f"{(r.get('first_name') or '').strip()} {(r.get('last_name') or '').strip()}".strip().lower(),
      (r.get("state") or "").strip()) for r in res if r.get("wrestler_guid")}

print("[3/5] flo_matches (bouts)...")
bouts=get("/rest/v1/flo_matches?select=top_wrestler_guid,bottom_wrestler_guid,winner_guid,is_bye")

def bscore(nm,st):
    s=score_ns.get((nm,(st or '').upper()))
    if s: return s
    lst=score_nm.get(nm) or []
    return max(lst) if lst and len(set(round(x,2) for x in lst))==1 else 0

print("[4/5] computing NWI...")
nwi_by=defaultdict(float)
for b in bouts:
    if b.get("is_bye"): continue
    w=b.get("winner_guid"); t,bo=b.get("top_wrestler_guid"),b.get("bottom_wrestler_guid")
    if not w or w not in (t,bo) or w not in guid: continue
    loser=bo if w==t else t
    if loser not in guid: continue
    wnm,wst=guid[w]; lnm,lst=guid[loser]
    if wnm==lnm: continue
    osc=bscore(lnm,lst)
    if osc<=0: continue
    nwi_by[wnm]+=nwi_bonus(osc)   # aggregate by name; state resolved per board entry below

changed=0; anchors_skipped=0; per_wt={}
for row in board:
    arr=row["athletes"]; n=0
    for a in arr:
        nm=(a.get("name") or "").strip().lower(); st=(a.get("state") or "").strip().upper()
        raw=nwi_by.get(nm,0)
        if raw<=0: continue
        if (nm,st) in ANCHORS:
            anchors_skipped+=1; continue
        nwi=min(round(raw,2),0.40)
        cur=float(a.get("score") or 0)
        new=round(min(5.0,cur+nwi),2)
        if new<=cur: continue
        a["score"]=new; a["nwi"]=nwi
        a["nil"]=nil_for(new)
        if (a.get("verdict") or "") in STD_VERDICTS: a["verdict"]=verdict_for(new)
        n+=1; changed+=1
    arr.sort(key=lambda x:-(float(x.get("score") or 0)))
    per_wt[row["weight"]]=n

print(f"\n[5/5] {'APPLYING' if APPLY else 'PREVIEW (no writes — add --apply)'}")
print(f"    athletes adjusted: {changed:,}   anchor entries protected: {anchors_skipped}")
for wt in sorted(per_wt): print(f"      {wt}: {per_wt[wt]} adjusted")
if APPLY:
    ok=0
    for row in board:
        body=json.dumps({"athletes":row["athletes"]}).encode()
        rq=urllib.request.Request(SUPA+f"/rest/v1/hs_board?board=eq.boys&weight=eq.{row['weight']}",
            data=body, method="PATCH",
            headers={"apikey":KEY,"Authorization":"Bearer "+KEY,"Content-Type":"application/json",
                     "Prefer":"return=minimal"})
        with urllib.request.urlopen(rq, timeout=120) as r: ok+=1
        print(f"      wrote boys {row['weight']}")
    print(f"    PATCHed {ok}/{len(board)} weight rows. hs_board is now NWI-adjusted.")
else:
    print("    re-run with --apply to write.")
