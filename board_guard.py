#!/usr/bin/env python3
"""
WPOM board guard — snapshot / verify / restore / preflight for hs_board.

The safety net that lets an automated `board_engine.py --apply` run unattended
WITHOUT ever silently corrupting the top of the board. It turns the locked
scoring rules (anchors frozen, girls cap 4.4, no data loss) into hard,
machine-checked invariants.

Flow the workflow uses:
  1. snapshot   -> back up hs_board + row counts BEFORE any apply
  2. (scrape + board_engine --apply happen in between)
  3. verify     -> assert every invariant against the live board; exit 1 on any fail
  4. restore    -> if verify failed, put the pre-apply snapshot back

Usage:
  python board_guard.py snapshot  --out snapshot.json
  python board_guard.py verify    --snapshot snapshot.json
  python board_guard.py restore   --snapshot snapshot.json
  python board_guard.py preflight        # FLO_JWT expiry heads-up (never fails the run)

Env required:
  SUPABASE_URL    https://yewigtsyvxiwprcpicup.supabase.co
  SUPABASE_KEY    sb_secret_...  (service-role-equivalent secret key)
Optional (preflight + alerts):
  FLO_JWT, RESEND_API_KEY, ALERT_EMAIL

ASSUMPTION (confirm on first dry run): hs_board has columns (board, weight,
athletes) where athletes is a JSON array, and (board, weight) uniquely
identifies a row. board_engine only rewrites the athletes blob per row, so
restoring athletes-per-(board,weight) is exact. If your unique key differs,
adjust restore()'s match params.
"""

import argparse
import base64
import datetime
import json
import os
import sys
from urllib.parse import quote

import requests

# ---- locked invariants -------------------------------------------------------
TOL = 0.001          # float tolerance for anchor equality
GIRLS_FAIL_AT = 4.45 # girls computed cap is 4.4; 4.5+ is anchor-only (none in DB yet)
SCORE_FLOOR = 1.0
SCORE_CEIL = 5.0
JWT_EXPIRY_FALLBACK = datetime.date(2026, 8, 17)  # used only if FLO_JWT can't be decoded
JWT_WARN_DAYS = 14

# Calibration anchor that MUST be on the board at exactly this score.
REQUIRED = ("Bo Bassett", "PA", 5.0)

# Frozen HS anchors: if present, score must equal expected. (Meyer Shapiro 4.0
# lives on the college/portal board, not hs_board, so he's intentionally absent.)
ANCHORS = [
    ("Bo Bassett",     "PA", 5.00),
    ("Melvin Miller",  "PA", 4.80),
    ("Brayden Harer",  "PA", 4.50),
    ("Sean Kenny",     "NJ", 4.40),
    ("Antonio Mills",  "GA", 4.40),
    ("Fred Bachmann",  "PA", 4.40),
    ("Caleb Noble",    "IL", 4.30),
    ("Cael Shepherd",  "PA", 4.20),
    ("Bentley Sly",    "NC", 4.10),
    ("Ariah Mills",    "GA", 3.93),
]


# ---- supabase helpers --------------------------------------------------------
def _cfg():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        sys.exit("FATAL: SUPABASE_URL / SUPABASE_KEY not set in env.")
    return url.rstrip("/"), key


def _headers(key, extra=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def fetch_board(url, key):
    r = requests.get(
        f"{url}/rest/v1/hs_board?select=board,weight,athletes",
        headers=_headers(key), timeout=60,
    )
    r.raise_for_status()
    return r.json()


def table_count(url, key, table):
    r = requests.get(
        f"{url}/rest/v1/{table}?select=id&limit=1",
        headers=_headers(key, {"Prefer": "count=exact", "Range": "0-0"}),
        timeout=60,
    )
    r.raise_for_status()
    cr = r.headers.get("content-range", "*/0")  # e.g. "0-0/39687"
    return int(cr.split("/")[-1])


# ---- athlete utilities -------------------------------------------------------
def norm(s):
    return " ".join(str(s or "").split()).strip().lower()


def to_score(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_girls_row(board_val):
    b = str(board_val or "").lower()
    return "girl" in b or "women" in b or "wmn" in b


def iter_athletes(rows):
    """Yield (athlete_dict, is_girl, board, weight) for every athlete on the board."""
    for row in rows:
        girl_row = is_girls_row(row.get("board"))
        for a in (row.get("athletes") or []):
            yield a, girl_row, row.get("board"), row.get("weight")


# ---- commands ----------------------------------------------------------------
def cmd_snapshot(args):
    url, key = _cfg()
    rows = fetch_board(url, key)
    snap = {
        "taken_at": datetime.datetime.utcnow().isoformat() + "Z",
        "hs_board": rows,
        "counts": {
            "hs_board": len(rows),
            "flo_results": table_count(url, key, "flo_results"),
            "flo_matches": table_count(url, key, "flo_matches"),
        },
    }
    with open(args.out, "w") as f:
        json.dump(snap, f)
    c = snap["counts"]
    print(f"snapshot: {c['hs_board']} board rows | "
          f"flo_results={c['flo_results']} flo_matches={c['flo_matches']} -> {args.out}")


def cmd_verify(args):
    url, key = _cfg()
    snap = json.load(open(args.snapshot))
    rows = fetch_board(url, key)
    fails = []

    # 1) no board rows lost (additions are fine; disappearances are not)
    snap_keys = {(norm(r["board"]), str(r["weight"])) for r in snap["hs_board"]}
    live_keys = {(norm(r["board"]), str(r["weight"])) for r in rows}
    missing = snap_keys - live_keys
    if missing:
        fails.append(f"{len(missing)} hs_board row(s) vanished since snapshot: {sorted(missing)[:5]}")

    # collect athletes
    athletes = list(iter_athletes(rows))

    # 2) required calibration anchor present & exact
    req_name, req_state, req_score = REQUIRED
    req_hits = [a for a, g, b, w in athletes if norm(a.get("name")) == norm(req_name)]
    if not req_hits:
        fails.append(f"REQUIRED anchor '{req_name}' missing from board")
    else:
        for a in req_hits:
            sc = to_score(a.get("score"))
            if sc is None or abs(sc - req_score) > TOL:
                fails.append(f"'{req_name}' = {a.get('score')} (must be {req_score})")

    # 3) every present anchor frozen at its locked score
    for name, state, exp in ANCHORS:
        hits = [a for a, g, b, w in athletes
                if norm(a.get("name")) == norm(name)
                and (norm(a.get("state")) == norm(state) or not a.get("state"))]
        for a in hits:
            sc = to_score(a.get("score"))
            if sc is None or abs(sc - exp) > TOL:
                fails.append(f"anchor '{name}' ({state}) = {a.get('score')} (must be {exp})")

    # 4) girls cap: no girl at/above 4.45 (4.5+ is anchor-only, none exist in DB yet)
    girl_over = [(a.get("name"), a.get("score"))
                 for a, g, b, w in athletes if g and (to_score(a.get("score")) or 0) >= GIRLS_FAIL_AT]
    if girl_over:
        fails.append(f"{len(girl_over)} girl(s) >= {GIRLS_FAIL_AT}: {girl_over[:5]}")

    # 5) global score sanity: numeric, within [1.0, 5.0]
    bad = []
    for a, g, b, w in athletes:
        sc = to_score(a.get("score"))
        if sc is None or sc < SCORE_FLOOR - TOL or sc > SCORE_CEIL + TOL:
            bad.append((a.get("name"), a.get("score")))
    if bad:
        fails.append(f"{len(bad)} athlete(s) with null/out-of-range score: {bad[:5]}")

    # 6) no data loss in source tables (pipelines are additive/idempotent)
    sc_now = table_count(url, key, "flo_results")
    bo_now = table_count(url, key, "flo_matches")
    if sc_now < snap["counts"]["flo_results"]:
        fails.append(f"flo_results dropped {snap['counts']['flo_results']} -> {sc_now}")
    if bo_now < snap["counts"]["flo_matches"]:
        fails.append(f"flo_matches dropped {snap['counts']['flo_matches']} -> {bo_now}")

    # report
    n_ath = len(athletes)
    if fails:
        print(f"VERIFY FAILED ({len(fails)} issue(s)) across {len(rows)} rows / {n_ath} athletes:")
        for f in fails:
            print("  ✗ " + f)
        sys.exit(1)
    print(f"VERIFY PASSED: {len(rows)} rows, {n_ath} athletes, anchors frozen, "
          f"girls < {GIRLS_FAIL_AT}, flo_results={sc_now}, flo_matches={bo_now}")


def cmd_restore(args):
    url, key = _cfg()
    snap = json.load(open(args.snapshot))
    restored = 0
    for r in snap["hs_board"]:
        params = f"?board=eq.{quote(str(r['board']))}&weight=eq.{quote(str(r['weight']))}"
        resp = requests.patch(
            f"{url}/rest/v1/hs_board{params}",
            headers=_headers(key, {"Prefer": "return=representation"}),
            json={"athletes": r["athletes"]}, timeout=60,
        )
        resp.raise_for_status()
        n = len(resp.json())
        if n != 1:
            print(f"  ! ({r['board']}, {r['weight']}) matched {n} rows on restore — check unique key")
        restored += n
    print(f"restore: re-applied athletes to {restored} hs_board row(s) from {args.snapshot}")


def _jwt_expiry(token):
    """Best-effort: read 'exp' from a JWT payload without verifying signature."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        if exp:
            return datetime.datetime.utcfromtimestamp(exp).date()
    except Exception:
        pass
    return JWT_EXPIRY_FALLBACK


def _notify(subject, text):
    api = os.environ.get("RESEND_API_KEY")
    to = os.environ.get("ALERT_EMAIL")
    if not api or not to:
        print("(notify skipped: RESEND_API_KEY / ALERT_EMAIL not set)")
        return
    requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api}", "Content-Type": "application/json"},
        json={"from": "noreply@wpomwrestling.com", "to": [to], "subject": subject, "text": text},
        timeout=30,
    )


def cmd_preflight(args):
    """Proactive heads-up before FLO_JWT lapses. Never fails the run."""
    token = os.environ.get("FLO_JWT", "")
    if not token:
        print("preflight: FLO_JWT not set (scrape will fail until added)")
        return
    expiry = _jwt_expiry(token)
    days = (expiry - datetime.date.today()).days
    print(f"preflight: FLO_JWT expires {expiry} ({days} days)")
    if days <= JWT_WARN_DAYS:
        _notify(
            f"WPOM: FLO_JWT expires in {days} days ({expiry})",
            "The FloArena token the nightly pipeline depends on is about to lapse. "
            "Refresh FLO_JWT in GitHub repo secrets before then or scraping goes dark.",
        )


def main():
    p = argparse.ArgumentParser(description="WPOM board guard")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot"); s.add_argument("--out", default="snapshot.json")
    v = sub.add_parser("verify");   v.add_argument("--snapshot", default="snapshot.json")
    r = sub.add_parser("restore");  r.add_argument("--snapshot", default="snapshot.json")
    sub.add_parser("preflight")
    args = p.parse_args()
    {"snapshot": cmd_snapshot, "verify": cmd_verify,
     "restore": cmd_restore, "preflight": cmd_preflight}[args.cmd](args)


if __name__ == "__main__":
    main()
