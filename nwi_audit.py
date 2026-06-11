#!/usr/bin/env python3
"""WPOM NWI AUDIT (report-only; writes nothing to the database).

Computes the Net Wins Index bonus for every athlete on the boys HS board:
  - wins come from flo_matches (S32 2024/2025 + NHSCA 2026 bouts)
  - opponent value = opponent's CURRENT board score in hs_board (Pass-1 base)
  - per-win bonus: >=4.5 +0.30 | >=4.0 +0.22 | >=3.5 +0.15 | >=3.0 +0.10
                   | >=2.5 +0.06 | else +0.03 ; capped at +0.40 (locked formula)

Outputs:
  ~/nwi_report.csv   one row per athlete who earns NWI > 0
  console            summary + top movers + Aiden White detail

Usage on Crostini:
  KEY=$(grep -o 'sb_secret_[A-Za-z0-9_-]*' ~/score_all.py | head -1)
  SUPABASE_SECRET="$KEY" python3 nwi_audit.py
"""
import os, json, csv, urllib.request
from collections import defaultdict

SUPA = "https://yewigtsyvxiwprcpicup.supabase.co"
KEY  = os.environ["SUPABASE_SECRET"]

def get(path):
    rows, off, page = [], 0, 1000
    while True:
        rq = urllib.request.Request(SUPA + path, headers={
            "apikey": KEY, "Authorization": "Bearer " + KEY,
            "Range-Unit": "items", "Range": f"{off}-{off+page-1}"})
        with urllib.request.urlopen(rq, timeout=120) as r:
            batch = json.loads(r.read())
        if not batch: break
        rows += batch
        print(f"    {path.split('?')[0].split('/')[-1]}: {len(rows):,} rows", end="\r")
        if len(batch) < page: break
        off += page
    print()
    return rows

print("[1/4] pulling hs_board (current scores)...")
board = get("/rest/v1/hs_board?select=board,weight,athletes&board=eq.boys")
score_by_namestate, score_by_name = {}, defaultdict(list)
wt_of = {}
for row in board:
    for a in row["athletes"]:
        nm = (a.get("name") or "").strip().lower()
        st = (a.get("state") or "").strip().upper()
        sc = float(a.get("score") or 0)
        key = (nm, st)
        if sc > score_by_namestate.get(key, 0):
            score_by_namestate[key] = sc
            wt_of[key] = row["weight"]
        score_by_name[nm].append(sc)
nboard = sum(len(r["athletes"]) for r in board)
print(f"    {nboard:,} boys on board")

print("[2/4] pulling flo_results (guid -> name/state)...")
res = get("/rest/v1/flo_results?select=wrestler_guid,first_name,last_name,state")
guid_name = {}
for r in res:
    g = r.get("wrestler_guid")
    if not g: continue
    nm = f"{(r.get('first_name') or '').strip()} {(r.get('last_name') or '').strip()}".strip().lower()
    guid_name[g] = (nm, (r.get("state") or "").strip())
print(f"    {len(guid_name):,} wrestler guids")

print("[3/4] pulling flo_matches (bouts)...")
bouts = get("/rest/v1/flo_matches?select=top_wrestler_guid,bottom_wrestler_guid,winner_guid,is_bye,weight,division")
print(f"    {len(bouts):,} bouts")

def board_score(nm, st):
    """Opponent's current board score: exact (name,state) -> unique-name -> 0."""
    s = score_by_namestate.get((nm, (st or '').upper()))
    if s: return s
    lst = score_by_name.get(nm) or []
    return max(lst) if len(lst) >= 1 and len(set(round(x,2) for x in lst)) == 1 else (max(lst) if len(lst)==1 else 0)

def nwi_bonus(opp_score):
    s = opp_score
    if s >= 4.5: return 0.30
    if s >= 4.0: return 0.22
    if s >= 3.5: return 0.15
    if s >= 3.0: return 0.10
    if s >= 2.5: return 0.06
    return 0.03

print("[4/4] computing NWI per athlete...")
wins = defaultdict(list)   # (name,state-as-on-results) -> [(opp_nm, opp_score, bonus)]
skipped_unknown_guid = 0
for b in bouts:
    if b.get("is_bye"): continue
    w = b.get("winner_guid")
    t, bo = b.get("top_wrestler_guid"), b.get("bottom_wrestler_guid")
    if not w or w not in (t, bo): continue
    loser = bo if w == t else t
    if w not in guid_name or loser not in guid_name:
        skipped_unknown_guid += 1; continue
    wnm, wst = guid_name[w]; lnm, lst = guid_name[loser]
    if wnm == lnm: continue
    osc = board_score(lnm, lst)
    if osc <= 0: continue                      # opponent not scored -> no credit (formula rule)
    wins[(wnm, wst)].append((lnm, osc, nwi_bonus(osc)))

report = []
for (nm, st), wl in wins.items():
    # find the winner ON THE BOARD (their own state may be dirty in results; try both)
    key = (nm, (st or '').upper())
    cur = score_by_namestate.get(key) or (max(score_by_name.get(nm) or [0]) if len(set(round(x,2) for x in (score_by_name.get(nm) or [0])))==1 else 0)
    if cur <= 0: continue                      # winner not on board -> nothing to update
    raw = sum(b for _,_,b in wl)
    nwi = min(round(raw, 2), 0.40)
    new = round(min(5.0, cur + nwi), 2)
    if nwi <= 0: continue
    wl_sorted = sorted(wl, key=lambda x: -x[1])[:6]
    report.append({
        "name": nm.title(), "state": (st or '').upper()[:20], "weight": wt_of.get(key, ""),
        "current": cur, "nwi": nwi, "new_score": new, "wins_credited": len(wl),
        "top_wins": "; ".join(f"{o.title()} ({s})" for o, s, _ in wl_sorted)})

report.sort(key=lambda r: -r["nwi"])
out = os.path.expanduser("~/nwi_report.csv")
with open(out, "w", newline="") as f:
    wcsv = csv.DictWriter(f, fieldnames=list(report[0].keys()) if report else
                          ["name","state","weight","current","nwi","new_score","wins_credited","top_wins"])
    wcsv.writeheader(); wcsv.writerows(report)

print("\n" + "="*70)
print(f"NWI AUDIT — {len(report):,} athletes earn NWI > 0   (report: {out})")
print(f"bouts with unknown guids skipped: {skipped_unknown_guid:,}")
print("="*70)
print("\nTOP 15 NWI EARNERS:")
for r in report[:15]:
    print(f"  {r['name']:<26} {r['state']:<3} {r['current']} -> {r['new_score']}  (NWI +{r['nwi']}, {r['wins_credited']} credited wins)")
print("\nAIDEN WHITE CHECK:")
hits = [r for r in report if r["name"].lower() == "aiden white"]
if hits:
    for r in hits:
        print(f"  {r['name']} {r['state']}: {r['current']} -> {r['new_score']} (NWI +{r['nwi']})")
        print(f"  credited wins: {r['top_wins']}")
else:
    print("  no credited S32/NHSCA wins found for Aiden White in flo_matches.")
    print("  (his events are NCHSAA + Fargo — those bouts aren't scraped yet,")
    print("   so his bump must come from multi-event/title bonuses, not this pass.)")
print("\nNOTE: report-only. Nothing was written to the database.")
