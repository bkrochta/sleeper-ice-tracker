#!/usr/bin/env python3
"""
Sleeper Ice Tracker + ESPN live game status
- Confirmed ices only after game is final
- In-progress ices shown sorted by time remaining (redder = less time left)
"""

import argparse
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
DATA_FILE = DOCS_DIR / "data.json"


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch Sleeper ices and generate data.json")
    parser.add_argument("--write-history", action="store_true", help="Write history to file")

    return parser.parse_args()


def get(url, params=None):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def load_data():
    """Load the unified data.json file or return a fresh structure."""
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return {
        "updated_at": None,
        "season": None,
        "current_week": None,
        "display_week": None,
        "season_type": "regular",
        "league_id": LEAGUE_ID,
        "finalized_weeks": [],
        "history": {},
    }


def save_data(data):
    """Save the unified data.json file."""
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["league_id"] = LEAGUE_ID
    # Update finalized_weeks based on history keys
    data["finalized_weeks"] = sorted([int(w) for w in data.get("history", {}).keys()])
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_ices_for_week(week):
    # Fetch matchups for the specified week and return confirmed ices and empty slots
    users = get(f"{BASE}/league/{LEAGUE_ID}/users")
    user_map = {u["user_id"]: u.get("display_name") or u.get("username") or "Unknown" for u in users}

    rosters = get(f"{BASE}/league/{LEAGUE_ID}/rosters")
    roster_to_owner = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        name = user_map.get(owner_id, f"Roster {r['roster_id']}")
        roster_to_owner[r["roster_id"]] = name

    players = get(f"{BASE}/players/nfl")

    confirmed = []
    empty_slots = []

    matchups = get(f"{BASE}/league/{LEAGUE_ID}/matchups/{week}")

    for m in matchups:
        roster_id = m["roster_id"]
        owner = roster_to_owner.get(roster_id, f"Roster {roster_id}")
        starters = m.get("starters") or []
        players_points = m.get("players_points") or {}

        for i, starter_id in enumerate(starters):
            if not starter_id or starter_id in ("0", "null", None):
                empty_slots.append(
                    {
                        "owner": owner,
                        "roster_id": roster_id,
                        "slot": i + 1,
                        "reason": "Empty starter slot",
                    }
                )
                continue

            pts = players_points.get(str(starter_id))
            if pts is None:
                pts = 0.0

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
                "completed": False,
            }
            confirmed.append(entry)

    return confirmed, empty_slots


def update_history(display_week, current_week, season, season_type):
    print(f"Updating history for Season {season} | Week {current_week} ({season_type})")

    data = load_data()
    data["season"] = season
    data["current_week"] = current_week
    data["display_week"] = display_week
    data["season_type"] = season_type

    confirmed, empty_slots = fetch_ices_for_week(display_week)

    data["history"][str(display_week)] = {
        "ices": confirmed + empty_slots,
        "empty_slots": empty_slots,
        "finalized": True,
    }
    save_data(data)
    flag_file = DATA_DIR / "COMMIT_HISTORY"
    flag_file.write_text("yes")
    print(f"Week {display_week} finalized → will commit history")
    print("Data written:", DATA_FILE)


def update_current_week(display_week, current_week, season, season_type):
    # Fetch the latest state from Sleeper and update the local history file for the current week
    print(f"Updating current week for Season {season} | Week {current_week} ({season_type})")

    data = load_data()

    data["season"] = season
    data["current_week"] = current_week
    data["display_week"] = display_week
    data["season_type"] = season_type

    save_data(data)

    print(f"Current week updated: {DATA_FILE}")


def main():
    args = parse_args()
    sleeper_state = get(f"{BASE}/state/nfl")
    display_week = sleeper_state.get("display_week")

    current_week = sleeper_state.get("week")
    season = sleeper_state.get("season")
    season_type = sleeper_state.get("season_type", "regular")

    print(f"Season {season} | Week {current_week} ({season_type})")

    if display_week < current_week:
        update_history(display_week, current_week, season, season_type)
    elif args.write_history:
        for week in range(1, current_week):
            update_history(week, current_week, season, season_type)
    elif display_week == current_week:
        update_current_week(display_week, current_week, season, season_type)
        return
    else:
        print("Unknown state, quitting...")
        return


if __name__ == "__main__":
    main()
