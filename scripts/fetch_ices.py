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
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SITE_DIR = Path("site")
DATA_DIR = Path("data")
SITE_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
HISTORY_FILE = DATA_DIR / "season_ices.json"

SLEEPER_TEAM_ABBR_TO_ESPN = {
    "WAS": "WSH"
}


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


def get_espn_games(week: int, season_type: int = 2):
    """
    season_type: 1=pre, 2=regular, 3=post
    Returns dict: team_abbr -> game info
    """
    try:
        data = get(ESPN_SCOREBOARD, params={
            "seasontype": season_type,
            "week": week
        })
    except Exception as e:
        print(f"ESPN fetch failed: {e}")
        return {}

    team_to_game = {}

    for event in data.get("events", []):
        status = event.get("status", {})
        status_type = status.get("type", {})
        state = status_type.get("state")          # pre | in | post
        completed = status_type.get("completed", False)
        detail = status_type.get("detail") or status_type.get("description") or ""
        clock = status.get("displayClock") or ""
        period = status.get("period") or 0

        # Rough remaining "urgency" score (higher = more time left)
        # Used only for sorting in-progress games
        urgency = 9999
        if state == "in":
            # Period 1-4 + overtime. Lower period + lower clock = more urgent (redder)
            try:
                # clock is usually "MM:SS" or "0:00"
                if ":" in clock:
                    mins, secs = clock.split(":")
                    remaining_secs = int(mins) * 60 + int(secs)
                else:
                    remaining_secs = 0
            except Exception:
                remaining_secs = 0

            # Weight by period (Q4 is most urgent)
            period_weight = {1: 4000, 2: 3000, 3: 2000, 4: 1000}.get(period, 500)
            urgency = period_weight + remaining_secs

        competitors = event.get("competitions", [{}])[0].get("competitors", [])
        for comp in competitors:
            team = comp.get("team", {})
            abbr = team.get("abbreviation")
            if abbr:
                team_to_game[abbr] = {
                    "state": state,               # pre / in / post
                    "completed": completed,
                    "detail": detail,
                    "clock": clock,
                    "period": period,
                    "urgency": urgency,
                    "name": event.get("name", ""),
                    "short_name": event.get("shortName", "")
                }

    return team_to_game


def sleeper_team_abbr_to_espn(sleeper_team_abbr: str):
    return SLEEPER_TEAM_ABBR_TO_ESPN.get(sleeper_team_abbr, sleeper_team_abbr)


def main():
    state = get(f"{BASE}/state/nfl")
    current_week = state.get("display_week") or state.get("week")
    season = state.get("season")
    season_type = state.get("season_type", "regular")

    print(f"Season {season} | Week {current_week} ({season_type})")

    # ESPN season_type mapping
    espn_season_type = {"pre": 1, "regular": 2, "post": 3}.get(season_type, 2)

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

    # Live game status from ESPN
    team_games = get_espn_games(current_week, espn_season_type)
    print(f"Loaded game status for {len(team_games)} teams from ESPN")

    matchups = get(f"{BASE}/league/{LEAGUE_ID}/matchups/{current_week}")

    confirmed = []      # game is over
    in_progress = []    # game still going
    empty_slots = []    # always collected

    for m in matchups:
        roster_id = m["roster_id"]
        owner = roster_to_owner.get(roster_id, f"Roster {roster_id}")
        starters = m.get("starters") or []
        players_points = m.get("players_points") or {}

        for i, starter_id in enumerate(starters):
            # Empty slot
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

            if pts > 0:
                continue

            p = players.get(str(starter_id), {})
            name = (p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}").strip()
            name = name or f"Player {starter_id}"
            pos = p.get("position") or "?"
            team = sleeper_team_abbr_to_espn(p.get("team") or "?")

            game = team_games.get(team, {})
            game_state = game.get("state", "unknown")
            completed = game.get("completed", False)

            entry = {
                "owner": owner,
                "roster_id": roster_id,
                "player": name,
                "position": pos,
                "nfl_team": team,
                "points": pts,
                "player_id": str(starter_id),
                "game_detail": game.get("detail", ""),
                "clock": game.get("clock", ""),
                "period": game.get("period", 0),
                "urgency": game.get("urgency", 9999),
                "game_name": game.get("short_name") or game.get("name", "")
            }

            if completed or game_state == "post":
                confirmed.append(entry)
            elif game_state == "in":
                in_progress.append(entry)

    # Sort in-progress players by urgency
    in_progress.sort(key=lambda x: x["urgency"])

    # ----- Week finalization logic -----
    # Games that are not yet finished
    remaining_games = sum(
        1 for g in team_games.values()
        if g.get("state") in ("pre", "in")
    )

    week_is_final = remaining_games == 0 and len(team_games) > 0
    previous_finalized = history["weeks"].get(str(current_week), {}).get("finalized", False)

    if week_is_final:
        # Empty slots become confirmed only when the week is over
        confirmed_empty = empty_slots
        pending_empty = []
    else:
        confirmed_empty = []
        pending_empty = empty_slots   # show these as in-progress

    history["weeks"][str(current_week)] = {
        "ices": confirmed + confirmed_empty,   # only finalized ices go into history
        "empty_slots": confirmed_empty,
        "finalized": week_is_final
    }
    save_history(history)

    # Flag for the workflow to commit history
    flag_file = DATA_DIR / "COMMIT_HISTORY"
    if week_is_final and not previous_finalized:
        flag_file.write_text("yes")
        print("Week just finalized → will commit history")
    else:
        if flag_file.exists():
            flag_file.unlink()
        print("Week not newly finalized → no history commit")

    # ----- Season leaderboard (only from finalized / confirmed) -----
    leaderboard = defaultdict(lambda: defaultdict(int))

    for week_str, week_data in history["weeks"].items():
        week_num = int(week_str)
        for ice in week_data.get("ices", []):
            leaderboard[ice["owner"]][week_num] += 1
            leaderboard[ice["owner"]]["total"] += 1
        for empty in week_data.get("empty_slots", []):
            leaderboard[empty["owner"]][week_num] += 1
            leaderboard[empty["owner"]]["total"] += 1

    sorted_owners = sorted(
        leaderboard.keys(),
        key=lambda o: leaderboard[o]["total"],
        reverse=True
    )

    finalized_weeks = sorted(
        [int(w) for w in history["weeks"].keys()],
        reverse=True
    )

    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "current_week": current_week,
        "season_type": season_type,
        "league_id": LEAGUE_ID,
        "confirmed": confirmed + confirmed_empty,   # players + empty slots that are final
        "in_progress": in_progress,
        "pending_empty": pending_empty,             # empty slots still waiting
        "leaderboard": {o: dict(leaderboard[o]) for o in sorted_owners},
        "finalized_weeks": finalized_weeks,
        "history": history["weeks"]
    }

    with open(SITE_DIR / "data.json", "w") as f:
        json.dump(data, f, indent=2)

    html = generate_html(data)
    with open(SITE_DIR / "index.html", "w") as f:
        f.write(html)

    print(f"Confirmed: {len(confirmed)} | In progress: {len(in_progress)} | Empty: {len(empty_slots)}")
    print("Site generated → ./site/index.html")


def generate_html(data):
    def ice_row(i, show_status=False):
        cls = "neg" if i["points"] < 0 else "zero"
        status_cell = ""

        if show_status:
            period = i.get("period") or 0
            clock = i.get("clock") or "0:00"
            detail = i.get("game_detail") or clock

            # Parse remaining seconds
            try:
                if ":" in str(clock):
                    mins, secs = str(clock).split(":")
                    remaining = int(mins) * 60 + int(secs)
                else:
                    remaining = 0
            except Exception:
                remaining = 0

            # Max seconds we care about in a quarter (~15 min)
            max_secs = 15 * 60
            progress = 1 - (remaining / max_secs)  # 0 = start of quarter, 1 = end
            progress = max(0, min(1, progress))

            bg = ""
            if period == 3:  # 3rd quarter → yellow
                # light yellow → darker yellow
                # start: rgba(255, 220, 50, 0.15) → end: rgba(255, 180, 0, 0.55)
                alpha = 0.15 + (progress * 0.40)
                bg = f"background: rgba(255, 200, 30, {alpha:.2f});"
            elif period >= 4:  # 4th + OT → red
                # light red → darker red
                alpha = 0.20 + (progress * 0.50)
                bg = f"background: rgba(255, 50, 50, {alpha:.2f});"

            status_cell = f'<td style="{bg}">{detail}</td>'

        return f"""
        <tr>
        <td>{i['owner']}</td>
        <td>{i['player']}</td>
        <td>{i['position']}</td>
        <td>{i['nfl_team']}</td>
        <td class="{cls}">{i['points']}</td>
        {status_cell}
        </tr>"""

    # Confirmed section
    conf_rows = "".join(ice_row(i) for i in data["confirmed"])

    confirmed_section = f"""
    <div class="card">
      <h2>✅ Confirmed Ices (Games Final)</h2>
      {"<table><thead><tr><th>Owner</th><th>Player</th><th>Pos</th><th>Team</th><th>Pts</th></tr></thead><tbody>" + conf_rows + "</tbody></table>" if conf_rows else "<p class='empty'>No confirmed ices yet</p>"}
    </div>
    """

    # In-progress section (players + pending empty slots)
    if data["in_progress"] or data.get("pending_empty"):
        prog_rows = "".join(ice_row(i, show_status=True) for i in data["in_progress"])

        for e in data.get("pending_empty", []):
            prog_rows += f"""
            <tr>
              <td>{e['owner']}</td>
              <td colspan="3"><em>Empty starter slot #{e['slot']}</em></td>
              <td class="zero">0</td>
              <td style="background: rgba(255, 200, 30, 0.25);">Pending – fills when week ends</td>
            </tr>"""

        in_progress_section = f"""
        <div class="card danger">
          <h2>🚨 In Progress – Danger Zone</h2>
          <p class="sub">
          </p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Owner</th><th>Player</th><th>Pos</th><th>Team</th><th>Pts</th><th>Game Status</th>
                </tr>
              </thead>
              <tbody>{prog_rows}</tbody>
            </table>
          </div>
        </div>
        """
    else:
        in_progress_section = ""

    # Leaderboard
    weeks = data["finalized_weeks"]
    header = "".join(f"<th>W{w}</th>" for w in weeks) + "<th>Total</th>"
    lb_rows = ""
    for owner in data["leaderboard"]:
        counts = data["leaderboard"][owner]
        cells = "".join(f"<td>{counts.get(w, '–')}</td>" for w in weeks)
        total = counts.get("total", 0)
        lb_rows += f"<tr><td><strong>{owner}</strong></td>{cells}<td class='total'>{total}</td></tr>"

    leaderboard_section = f"""
    <div class="card">
      <h2>Season Ice Leaderboard</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Owner</th>{header}</tr></thead>
          <tbody>{lb_rows if lb_rows else "<tr><td colspan='20' class='empty'>No ices yet</td></tr>"}</tbody>
        </table>
      </div>
    </div>
    """

    # Previous weeks
    prev = ""
    for week in data["finalized_weeks"]:
        if week == data["current_week"]:
            continue
        wd = data["history"].get(str(week), {})
        ices = wd.get("ices", [])
        empties = wd.get("empty_slots", [])
        rows = "".join(ice_row(i) for i in ices)
        for e in empties:
            rows += f"<tr><td>{e['owner']}</td><td colspan='3'>Empty slot #{e['slot']}</td><td class='zero'>0</td></tr>"
        prev += f"""
        <details class="card">
          <summary>Week {week} ({len(ices)+len(empties)} ices)</summary>
          {"<table><thead><tr><th>Owner</th><th>Player</th><th>Pos</th><th>Team</th><th>Pts</th></tr></thead><tbody>" + rows + "</tbody></table>" if rows else "<p class='empty'>No ices</p>"}
        </details>
        """

    updated = data["updated_at"].replace("T", " ")[:19] + " UTC"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Ice Tracker – {data['season']}</title>
  <style>
    :root {{
      --bg: #0f0f13; --card: #1a1a24; --text: #e8e8f0; --muted: #8888a0;
      --accent: #ff4d6d; --zero: #ffcc00; --neg: #ff4d6d; --total: #4ade80;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.5;
      padding: 1.5rem 1rem; max-width: 1050px; margin: 0 auto;
    }}
    h1 {{ font-size: 1.8rem; margin-bottom: 0.25rem; }}
    h2 {{ font-size: 1.2rem; margin-bottom: 0.75rem; }}
    .meta {{ color: var(--muted); margin-bottom: 1.5rem; font-size: 0.95rem; }}
    .card {{
      background: var(--card); border-radius: 12px; padding: 1.25rem; margin-bottom: 1.25rem;
    }}
    .card.danger {{ border: 1px solid #ff4d6d55; }}
    .sub {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 1rem; }}
    details.card {{ cursor: pointer; }}
    details summary {{ font-weight: 600; font-size: 1.05rem; list-style: none; }}
    details summary::-webkit-details-marker {{ display: none; }}
    details[open] summary {{ margin-bottom: 1rem; }}
    .table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.95rem; }}
    th, td {{ text-align: left; padding: 0.55rem 0.7rem; border-bottom: 1px solid #2a2a3a; }}
    th {{ color: var(--muted); font-weight: 600; font-size: 0.78rem; text-transform: uppercase; }}
    .zero {{ color: var(--zero); font-weight: 700; }}
    .neg {{ color: var(--neg); font-weight: 700; }}
    .total {{ color: var(--total); font-weight: 700; }}
    .empty {{ color: var(--muted); font-style: italic; }}
    footer {{ margin-top: 2rem; color: var(--muted); font-size: 0.85rem; text-align: center; }}
    @media (max-width: 640px) {{
      body {{ padding: 1rem 0.75rem; }}
      h1 {{ font-size: 1.4rem; }}
      h2 {{ font-size: 1rem; }}
      .card {{ padding: 1rem; margin-bottom: 1rem; }}
      th, td {{ padding: 0.4rem 0.5rem; font-size: 0.85rem; }}
      th {{ font-size: 0.7rem; }}
      .table-wrap {{ margin: 0 -1rem; padding: 0 1rem; }}
    }}
  </style>
</head>
<body>
  <h1>🧊 The Concord Boys Ice Tracker</h1>
  <div class="meta">
    Season {data['season']} · Week {data['current_week']}<br>
    Last updated: {updated}
  </div>

  {confirmed_section}
  {in_progress_section}
  {leaderboard_section}

  <h2 style="margin: 1.5rem 0 0.75rem; color: var(--muted); font-size: 1rem;">Previous Weeks</h2>
  {prev if prev else "<p class='empty'>No previous weeks yet</p>"}

  <footer>
    Confirmed ices only after game is final (via ESPN) · In-progress shown live · Sleeper + ESPN data
  </footer>
</body>
</html>"""


if __name__ == "__main__":
    main()