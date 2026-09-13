# 🧊 Ice Tracker

Fantasy football drinking game tracker for Sleeper leagues.

**Rule:** If you start a player who finishes with **0 or negative points**, or leave a starter slot empty, you owe an ice.

This dashboard only counts an ice **after the player’s NFL game is final**.

---

## Features

- **Current Week**
  - Confirmed ices (games that are over)
  - In-progress danger zone (sorted by time remaining)
    - No color in 1st half
    - Yellow in 3rd quarter (darker as time runs out)
    - Red in 4th quarter & overtime (darker as time runs out)
- **Season Leaderboard** – ices broken down by week + total
- **Previous Weeks** – collapsible history
- Auto-updates via GitHub Actions

---

## Quick Start (Local)

```bash
# 1. Clone / download the repo
cd ice-tracker

# 2. Install dependencies
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Set your Sleeper League ID
export LEAGUE_ID=your_league_id_here

# 4. Run it
python scripts/fetch_ices.py

# 5. Open the dashboard
open site/index.html              # Windows: start site/index.html