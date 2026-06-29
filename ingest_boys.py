#!/usr/bin/env python3
"""
WPOM boys ingest — GHSA state placements + AAU duals bouts -> our data.

ONE run, two events, additive + idempotent (full-replace per event_uuid):
  GHSA 2026 boys placements -> flo_results   (gives GA kids a board score)
  AAU Scholastic Duals bouts -> flo_matches  (NWI source for the whole field)

GUID rule (locked): name-only. For each wrestler, reuse the most-common
existing flo_results wrestler_guid for that normalized name; if none, mint a
deterministic 'wpom-' + sha1(first|last) so the SAME name resolves to the SAME
identity across GHSA and AAU. (Accepts the rare two-different-same-name
collapse; consolidation is the cleanup, same as the Bouzakis work.)

PREVIEW-FIRST: no --apply = report only (counts, GUID match rate, samples,
anomalies), ZERO writes. --apply performs the writes.

Files expected in ~/ : 2026_georgia_state_results.csv, 2026_aau_bouts.csv
Run on Crostini:
  SUPABASE_KEY=$(grep -o 'sb_secret_[A-Za-z0-9_-]*' ~/score_all.py | head -1) \
  python3 ~/ingest_boys.py            # preview
  SUPABASE_KEY=... python3 ~/ingest_boys.py --apply   # write

After --apply, scoring still needs (see banner at end):
  1. add GHSA to score_all.py EVENT_MULT (like IL States, line 42) = 0.85
  2. board_guard.py snapshot ; board_engine.py --apply ; board_guard.py verify
"""

import os, sys, csv, hashlib, requests

URL = "https://yewigtsyvxiwprcpicup.supabase.co"
KEY = os.environ.get("SUPABASE_KEY") or sys.exit("set SUPABASE_KEY")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
APPLY = "--apply" in sys.argv

# --- locked scoring value to CONFIRM: GHSA state-title event_mult ---
GHSA_EVENT_MULT = 0.85   # matches IL/NJ/PA/CA/OH/IA state-title mult; flat (no per-class)
GHSA_UUID = "ghsa-2026-boys-state"          # deterministic event ids (not Flo UUIDs)
AAU_UUID  = "aau-2026-scholastic-duals-boys"

GHSA_CSV = os.path.expanduser("~/2026_georgia_state_results.csv")
AAU_CSV  = os.path.expanduser("~/2026_aau_bouts.csv")


def norm(f, l):
    return f"{' '.join(f.split())}|{' '.join(l.split())}".strip().lower()


def synth(f, l):
    return "wpom-" + hashlib.sha1(norm(f, l).encode()).hexdigest()[:16]


def get(path):
    r = requests.get(f"{URL}/rest/v1/{path}", headers=H, timeout=120)
    r.raise_for_status()
    return r.json()


def fetch_all(table, select):
    """Paginate full table (PostgREST 1000/page)."""
    out, step, off = [], 1000, 0
    while True:
        page = get(f"{table}?select={select}&limit={step}&offset={off}")
        out += page
        if len(page) < step:
            return out
        off += step


def build_resolver():
    """normalized-name -> canonical (most-rows) existing flo_results wrestler_guid."""
    from collections import Counter, defaultdict
    counts = defaultdict(Counter)
    for r in fetch_all("flo_results", "first_name,last_name,wrestler_guid"):
        counts[norm(r.get("first_name", ""), r.get("last_name", ""))][r["wrestler_guid"]] += 1
    return {nm: c.most_common(1)[0][0] for nm, c in counts.items()}


def resolve(resolver, f, l, stats):
    nm = norm(f, l)
    if nm in resolver:
        stats["matched"] += 1
        return resolver[nm]
    stats["minted"] += 1
    g = synth(f, l)
    resolver[nm] = g        # so AAU & GHSA agree within this run
    return g


def upsert_event(uuid, name, short, mult):
    if not APPLY:
        return
    requests.post(f"{URL}/rest/v1/flo_events",
                  headers={**H, "Prefer": "resolution=merge-duplicates"},
                  json={"event_uuid": uuid, "name": name, "short_name": short,
                        "year": 2026, "event_mult": mult, "active": True}, timeout=60).raise_for_status()


def replace_rows(table, uuid_field, uuid, rows):
    """Idempotent: delete this event's rows, then insert fresh."""
    if not APPLY:
        return
    requests.delete(f"{URL}/rest/v1/{table}?{uuid_field}=eq.{uuid}", headers=H, timeout=120).raise_for_status()
    for i in range(0, len(rows), 500):
        requests.post(f"{URL}/rest/v1/{table}", headers=H, json=rows[i:i+500], timeout=120).raise_for_status()


def main():
    print(f"{'APPLY' if APPLY else 'PREVIEW (no writes)'} — boys ingest\n" + "=" * 60)
    print("loading existing flo_results identities for GUID matching...")
    resolver = build_resolver()
    print(f"  known names in flo_results: {len(resolver)}")
    stats = {"matched": 0, "minted": 0}

    # ---- GHSA boys placements -> flo_results rows ----
    ghsa = [r for r in csv.DictReader(open(GHSA_CSV)) if r["is_girl"].lower() == "false"]
    gr = []
    for r in ghsa:
        g = resolve(resolver, r["first_name"], r["last_name"], stats)
        gr.append({"event_uuid": GHSA_UUID, "wrestler_guid": g,
                   "first_name": r["first_name"], "last_name": r["last_name"],
                   "state": "GA", "division": r["classification"],
                   "weight": r["weight"], "placement": int(r["placement"])})
    print(f"\nGHSA boys placements -> flo_results: {len(gr)} rows "
          f"(champions: {sum(1 for r in gr if r['placement']==1)})")

    # ---- AAU bouts -> flo_matches rows ----
    aau = list(csv.DictReader(open(AAU_CSV)))
    mr = []
    for b in aau:
        w = resolve(resolver, b["winner_first"], b["winner_last"], stats)
        l = resolve(resolver, b["loser_first"], b["loser_last"], stats)
        mr.append({"event_uuid": AAU_UUID, "winner_guid": w,
                   "top_wrestler_guid": w, "bottom_wrestler_guid": l, "is_bye": False})
    print(f"AAU bouts -> flo_matches: {len(mr)} rows")

    tot = stats["matched"] + stats["minted"]
    print(f"\nGUID resolution across both: {stats['matched']} matched existing "
          f"({100*stats['matched']//max(tot,1)}%) | {stats['minted']} minted new")
    print("sample GHSA:", gr[0] if gr else None)
    print("sample AAU :", mr[0] if mr else None)

    if not APPLY:
        print("\nPREVIEW only — no writes. Re-run with --apply to ingest.")
        return

    print("\nwriting...")
    upsert_event(GHSA_UUID, "GHSA 2026", "GHSA", GHSA_EVENT_MULT)
    upsert_event(AAU_UUID, "AAU Scholastic Duals 2026", "AAU Duals", 0)  # duals = NWI-only
    replace_rows("flo_results", "event_uuid", GHSA_UUID, gr)
    replace_rows("flo_matches", "event_uuid", AAU_UUID, mr)
    print(f"  flo_results +{len(gr)} (GHSA) | flo_matches +{len(mr)} (AAU)")

    print("\n" + "=" * 60)
    print("INGEST DONE. Scoring is a SEPARATE, gated step — NOT done yet:")
    print(f"  1. Add GHSA to score_all.py EVENT_MULT (like IL States) = {GHSA_EVENT_MULT}")
    print("  2. python3 board_guard.py snapshot --out snap_boys.json")
    print("  3. python3 board_engine.py --apply        # boys recompute (girls frozen)")
    print("  4. python3 board_guard.py verify --snapshot snap_boys.json")
    print("     (verify trips -> board_guard.py restore --snapshot snap_boys.json)")


if __name__ == "__main__":
    main()
