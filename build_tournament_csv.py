# build_tournament_csv.py
#
# Build a Threat Board–ready tournament CSV for the
# 2026 Presidents Day Challenge using data scraped
# by scrape_gc_schedules.py.
#
# Usage:
#   py build_tournament_csv.py
#
# This will create presidents_day_2026_tournament.csv in the same folder.

import csv
import pyodbc

from scrape_gc_schedules import DB_CONNECTION_STRING
from presidents_day_teams import TOURNAMENT_TEAMS


def fetch_team_name(cursor, team_id: str, label_fallback: str) -> str:
    """
    Get the canonical TeamName from GCTeams for a given GC TeamID.
    Fallback to the human 'label' if no entry exists yet.
    """
    cursor.execute(
        """
        SELECT TOP 1 TeamName
          FROM GCTeams
         WHERE TeamID = ?
        """,
        team_id,
    )
    row = cursor.fetchone()
    if row and getattr(row, "TeamName", None):
        return row.TeamName
    return label_fallback or team_id



def aggregate_team_stats_by_id(cursor, team_id: str) -> dict:
    """
    Aggregate G, W, L, RS, RA for a given GC TeamID using GCGamesTmp4,
    matching on SourceTeamID (the GC team id we scraped from).
    """
    cursor.execute(
        """
        SELECT GameDate,
               HomeTeamID,
               AwayTeamID,
               HomeScore,
               AwayScore,
               SourceTeamID
          FROM GCGamesTmp4
         WHERE SourceTeamID = ?
        """,
        team_id,
    )

    rows = cursor.fetchall()

    G = W = L = RS = RA = 0

    for g in rows:
        home_id = g.HomeTeamID
        away_id = g.AwayTeamID
        home_score = g.HomeScore or 0
        away_score = g.AwayScore or 0

        # Determine if this team was home or away in this game
        is_home = (home_id == team_id)

        team_runs = home_score if is_home else away_score
        opp_runs = away_score if is_home else home_score

        RS += team_runs
        RA += opp_runs
        G += 1

        if team_runs > opp_runs:
            W += 1
        elif team_runs < opp_runs:
            L += 1
        # ties count as games but don't change W/L

    return {"G": G, "W": W, "L": L, "RS": RS, "RA": RA}


def build_tournament_csv(output_path: str = "presidents_day_2026_tournament.csv"):
    conn = pyodbc.connect(DB_CONNECTION_STRING)
    cursor = conn.cursor()

    output_rows = []

    for entry in TOURNAMENT_TEAMS:
        team_id = entry["team_id"]
        pool = entry["pool"]
        label = entry.get("label", team_id)

        # Step 1: get the best-known TeamName for this GC TeamID
        resolved_name = fetch_team_name(cursor, team_id, label)

        # Step 2: aggregate stats by that name
        stats = aggregate_team_stats_by_id(cursor, team_id)

        output_rows.append(
            {
                "Team": resolved_name,
                "Pool": pool,
                "G": stats["G"],
                "W": stats["W"],
                "L": stats["L"],
                "RS": stats["RS"],
                "RA": stats["RA"],
            }
        )

    conn.close()

    fieldnames = ["Team", "Pool", "G", "W", "L", "RS", "RA"]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in output_rows:
            writer.writerow(r)

    print(f"✅ Tournament CSV written to {output_path}")
    print("   You can now upload this into tournament.html.")


if __name__ == "__main__":
    build_tournament_csv()
