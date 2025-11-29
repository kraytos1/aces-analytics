import io
import csv
from pathlib import Path

import pyodbc
from flask import Flask, Response, send_from_directory, request, jsonify

from scrape_gc_schedules import DB_CONNECTION_STRING  # uses your .env
from build_tournament_csv import fetch_team_name, aggregate_team_stats_by_id  # reuse your logic
from presidents_day_teams import TOURNAMENT_TEAMS  # your pool/team definitions

# -----------------------------------------------------------------------------
# Paths / Flask app
# -----------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "newsiteshitz"

app = Flask(
    __name__,
    static_folder=str(STATIC_DIR),
    static_url_path=""  # so /tournament.html, /team-hitting.html, etc. work directly
)


# -----------------------------------------------------------------------------
# Static site
# -----------------------------------------------------------------------------

@app.route("/")
def root():
    """Serve index.html at root."""
    return send_from_directory(STATIC_DIR, "index.html")


# -----------------------------------------------------------------------------
# API: Tournament CSV (for tournament.html threat board)
# -----------------------------------------------------------------------------

@app.route("/api/tournament.csv")
def api_tournament_csv():
    """
    Return a CSV in the same format your threat board expects:
      Team,Pool,G,W,L,RS,RA

    Backed directly by GCGamesTmp4 via the same logic as build_tournament_csv.py.
    """
    conn = pyodbc.connect(DB_CONNECTION_STRING)
    cursor = conn.cursor()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Team", "Pool", "G", "W", "L", "RS", "RA"])

    for entry in TOURNAMENT_TEAMS:
        team_id = entry["team_id"]
        pool = entry["pool"]
        label = entry.get("label", team_id)

        # Use your existing helpers to stay consistent with the CSV builder
        team_name = fetch_team_name(cursor, team_id, label)
        stats = aggregate_team_stats_by_id(cursor, team_id)

        writer.writerow([
            team_name,
            pool,
            stats["G"],
            stats["W"],
            stats["L"],
            stats["RS"],
            stats["RA"],
        ])

    conn.close()

    csv_text = buffer.getvalue()
    return Response(csv_text, mimetype="text/csv")


# -----------------------------------------------------------------------------
# API: Team Hitting (for team-hitting.html)
# -----------------------------------------------------------------------------

@app.route("/api/team_hitting")
def api_team_hitting():
    """
    Return aggregated season hitting stats for a given team_id (GC TeamID),
    using GCBattingStatsTmp4.

    Example request:
      /api/team_hitting?team_id=QQpfJzkSUSyd
    """
    team_id = request.args.get("team_id", "").strip()
    if not team_id:
        return jsonify({"error": "team_id is required"}), 400

    conn = pyodbc.connect(DB_CONNECTION_STRING)
    cursor = conn.cursor()

    # Aggregate per-player totals for that team.
    # We only include rows where TeamMatch = 'Yes' so we don't mix in opponent stats.
    cursor.execute(
        """
        SELECT
            PlayerName,
            SUM(AB)          AS AB,
            SUM(R)           AS R,
            SUM(H)           AS H,
            SUM(RBI)         AS RBI,
            SUM(BB)          AS BB,
            SUM(SO)          AS SO,
            SUM(Doubles)     AS Doubles,
            SUM(Triples)     AS Triples,
            SUM(HomeRuns)    AS HomeRuns,
            SUM(StolenBases) AS StolenBases
        FROM GCBattingStatsTmp4
        WHERE TeamID = ? AND TeamMatch = 'Yes'
        GROUP BY PlayerName
        ORDER BY PlayerName
        """,
        team_id,
    )

    rows = cursor.fetchall()

    players = []

    for row in rows:
        # Base counting stats
        name        = row.PlayerName
        AB          = row.AB or 0
        R           = row.R or 0
        H           = row.H or 0
        RBI         = row.RBI or 0
        BB          = row.BB or 0
        SO          = row.SO or 0
        doubles     = row.Doubles or 0
        triples     = row.Triples or 0
        HR          = row.HomeRuns or 0
        SB          = row.StolenBases or 0

        # Derive singles and total bases from breakdown
        singles = H - (doubles + triples + HR)
        if singles < 0:
            singles = 0  # just in case of bad source data

        TB = singles + doubles * 2 + triples * 3 + HR * 4

        # Derived counts
        PA = AB + BB  # simple approximation (no HBP/SF tracked yet)

        # Rate stats (with zero-division protection)
        AVG = H / AB if AB > 0 else 0.0
        OB  = H + BB
        OBP = OB / PA if PA > 0 else 0.0
        SLG = TB / AB if AB > 0 else 0.0
        OPS = OBP + SLG
        ISO = SLG - AVG

        players.append({
            "name":  name,
            "AB":    AB,
            "R":     R,
            "H":     H,
            "RBI":   RBI,
            "BB":    BB,
            "SO":    SO,
            "2B":    doubles,
            "3B":    triples,
            "HR":    HR,
            "SB":    SB,
            "TB":    TB,
            "PA":    PA,
            "AVG":   round(AVG, 3),
            "OBP":   round(OBP, 3),
            "SLG":   round(SLG, 3),
            "OPS":   round(OPS, 3),
            "ISO":   round(ISO, 3),
        })

    conn.close()
    return jsonify(players)


# -----------------------------------------------------------------------------
# Main entrypoint
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # Run on http://localhost:5000
    app.run(host="127.0.0.1", port=5000, debug=True)
