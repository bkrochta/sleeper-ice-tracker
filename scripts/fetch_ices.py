#!/usr/bin/env python3
"""
Sleeper Ice Tracker + ESPN live game status
- Confirmed ices only after game is final
- In-progress ices shown sorted by time remaining (redder = less time left)
"""

import os
import json
import requests
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

LEAGUE_ID = os.environ.get("LEAGUE_ID")
if not LEAGUE_ID:
    raise SystemExit("ERROR: LEAGUE_ID environment variable is required")

BASE = "https://api.sleeper.app/v1"
DOCS_DIR = Path("docs")
DATA_DIR = Path("data")
DOCS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
HISTORY_FILE = DATA_DIR / "season_ices.json"


def get(url, params=None):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {"weeks": {}, "last_updated": None}


def save_history(history):
    history["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def main():
    state = get(f"{BASE}/state/nfl")
    current_week = state.get("display_week") or state.get("week")
    season = state.get("season")
    season_type = state.get("season_type", "regular")

    print(f"Season {season} | Week {current_week} ({season_type})")

    users = get(f"{BASE}/league/{LEAGUE_ID}/users")
    user_map = {
        u["user_id"]: u.get("display_name") or u.get("username") or "Unknown"
        for u in users
    }

    rosters = get(f"{BASE}/league/{LEAGUE_ID}/rosters")
    roster_to_owner = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        name = user_map.get(owner_id, f"Roster {r['roster_id']}")
        roster_to_owner[r["roster_id"]] = name

    players = get(f"{BASE}/players/nfl")
    history = load_history()

    # For weekly runs we only use Sleeper data. The client-side `live.js` will
    # perform live ESPN scoreboard checks for the current week in the browser.
    # This script is intended to be scheduled once weekly (after games end)
    # to finalize the previous week and record historical ices.
    try:
        current_week_int = int(current_week)
    except Exception:
        print(f"Unable to parse current_week='{current_week}' from Sleeper state; aborting finalization")
        current_week_int = None

    if not current_week_int or current_week_int <= 0:
        print("No valid current week found; nothing to finalize")
        finalized_weeks = sorted([int(w) for w in history.get("weeks", {}).keys()], reverse=True)
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "season": season,
            "current_week": current_week,
            "season_type": season_type,
            "league_id": LEAGUE_ID,
            "finalized_weeks": finalized_weeks,
            "history": history["weeks"]
        }
        with open(DOCS_DIR / "data.json", "w") as f:
            json.dump(data, f, indent=2)
        print("Data written (no finalize):", DOCS_DIR / 'data.json')
        return

    finalize_week = current_week_int - 1
    if finalize_week <= 0:
        print("No previous week to finalize")
        finalized_weeks = sorted([int(w) for w in history.get("weeks", {}).keys()], reverse=True)
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "season": season,
            "current_week": current_week,
            "season_type": season_type,
            "league_id": LEAGUE_ID,
            "finalized_weeks": finalized_weeks,
            "history": history["weeks"]
        }
        with open(DOCS_DIR / "data.json", "w") as f:
            json.dump(data, f, indent=2)
        print("Data written (no finalize):", DOCS_DIR / 'data.json')
        return

    print(f"Attempting to finalize week {finalize_week}")

    confirmed = []
    in_progress = []  # kept for compatibility; weekly run won't populate this
    empty_slots = []

    # Fetch matchups for the week we want to finalize
    matchups = get(f"{BASE}/league/{LEAGUE_ID}/matchups/{finalize_week}")

    for m in matchups:
        roster_id = m["roster_id"]
        owner = roster_to_owner.get(roster_id, f"Roster {roster_id}")
        starters = m.get("starters") or []
        players_points = m.get("players_points") or {}

        for i, starter_id in enumerate(starters):
            if not starter_id or starter_id in ("0", "null", None):
                empty_slots.append({
                    "owner": owner,
                    "roster_id": roster_id,
                    "slot": i + 1,
                    "reason": "Empty starter slot"
                })
                continue

            pts = players_points.get(str(starter_id))
            if pts is None:
                pts = 0.0

            # If points recorded (>0) then not an ice; otherwise treat as an
            # ice for the purposes of weekly finalization.
            if pts > 0:
                continue

            p = players.get(str(starter_id), {})
            name = (p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}").strip()
            name = name or f"Player {starter_id}"
            pos = p.get("position") or "?"
            team = p.get("team") or "?"

            entry = {
                "owner": owner,
                "roster_id": roster_id,
                "player": name,
                "position": pos,
                "nfl_team": team,
                "points": pts,
                "player_id": str(starter_id),
                "game_detail": "",
                "clock": "",
                "period": 0,
                "urgency": 9999,
                "game_name": ""
            }
            confirmed.append(entry)

    in_progress.sort(key=lambda x: x.get("urgency", 9999))

    # Only finalize the previous week once — skip if already finalized.
    previous_finalized = history.get("weeks", {}).get(str(finalize_week), {}).get("finalized", False)
    flag_file = DATA_DIR / "COMMIT_HISTORY"
    if previous_finalized:
        print(f"Week {finalize_week} already finalized; no changes")
        # Still write docs data.json so client has up-to-date metadata
        finalized_weeks = sorted([int(w) for w in history.get("weeks", {}).keys()], reverse=True)
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "season": season,
            "current_week": current_week,
            "season_type": season_type,
            "league_id": LEAGUE_ID,
            "finalized_weeks": finalized_weeks,
            "history": history["weeks"]
        }
        with open(DOCS_DIR / "data.json", "w") as f:
            json.dump(data, f, indent=2)
        print("Data written (no finalize):", DOCS_DIR / 'data.json')
    else:
        # Finalize: persist this single week into history and mark finalized
        history["weeks"][str(finalize_week)] = {
            "ices": confirmed + empty_slots,
            "empty_slots": empty_slots,
            "finalized": True
        }
        save_history(history)
        flag_file.write_text("yes")
        print(f"Week {finalize_week} finalized → will commit history")

        # Write updated docs data.json (history + metadata)
        finalized_weeks = sorted([int(w) for w in history.get("weeks", {}).keys()], reverse=True)
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "season": season,
            "current_week": current_week,
            "season_type": season_type,
            "league_id": LEAGUE_ID,
            "finalized_weeks": finalized_weeks,
            "history": history["weeks"]
        }
        with open(DOCS_DIR / "data.json", "w") as f:
            json.dump(data, f, indent=2)
        print("Data written:", DOCS_DIR / 'data.json')

    # The script's writes above have updated `docs/data.json` and history as
    # needed. No further server-side leaderboard computation is performed.


if __name__ == "__main__":
    main()