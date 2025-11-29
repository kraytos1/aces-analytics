# presidents_day_teams.py
#
# 2026 Presidents Day Challenge – Vero Beach, FL
# Tournament team definitions for the Threat Board pipeline.
#
# IMPORTANT:
#   - "team_id" here must match the GC TeamID stored in GCGamesTmp4.SourceTeamID.
#   - That is the alphanumeric ID from the GameChanger team URL:
#       https://web.gc.com/teams/QQpfJzkSUSyd/2025-fall-delmarva-aces-12u-east
#     -> team_id should be "QQpfJzkSUSyd".
#
# For teams where you don't yet know the GC TeamID, leave a placeholder.
# Those teams will just show 0 games until you update the ID and scrape them.

TOURNAMENT_TEAMS = [
    # ---------- Pool A ----------
    {
        "label": "South Florida Thunder Black 12U",
        "team_id": "AJoUfcKWiE1S",  # from their GC URL
        "pool": "A",
    },
    {
        "label": "Club 321 12U",
        "team_id": "A5hoGMkx8AJw",
        "pool": "A",
    },
    {
        "label": "Guest Player Connect 12U",
        "team_id": "MVIeOTxvaaSH",
        "pool": "A",
    },
    {
        "label": "Vero Nike RBI 12U",
        "team_id": "VERO_NIKE_RBI_ID",  # TODO: replace with real GC TeamID
        "pool": "A",
    },

    # ---------- Pool B ----------
    {
        "label": "Delmarva Aces East 12U",
        "team_id": "QQpfJzkSUSyd",
        "pool": "B",
    },
    {
        "label": "CBU United Thomas 12U",
        "team_id": "V9BKpYgg3ijD",
        "pool": "B",
    },
    {
        "label": "Keystone State Bombers Blue 12U",
        "team_id": "KEYSTONE_BLUE_ID",  # TODO
        "pool": "B",
    },
    {
        "label": "Guatemala Hornets 12U",
        "team_id": "GUATEMALA_HORNETS_ID",  # TODO
        "pool": "B",
    },

    # ---------- Pool C ----------
    {
        "label": "FS Prime Brookers 12U",
        "team_id": "y7muqE6NkNmq",
        "pool": "C",
    },
    {
        "label": "Team GTS 11U/12U",
        "team_id": "THcd8Y6VlgYU",  # correct ID THcd8Y6VlgYU
        "pool": "C",
    },
    {
        "label": "Keystone State Bombers Fall25 12U",
        "team_id": "4q5Sf3DvGgIl",
        "pool": "C",
    },
    {
        "label": "West Chester Dragons 12U NL",
        "team_id": "r2AgmylXffAA",
        "pool": "C",
    },
]
