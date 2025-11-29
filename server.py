import os
from functools import wraps

from flask import (
    Flask,
    send_from_directory,
    jsonify,
    abort,
    request,
    Response,
)

# ----- Paths -----
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(BASE_DIR, "data")
HITTING_DIR = os.path.join(DATA_DIR, "hitting")
PITCHING_DIR = os.path.join(DATA_DIR, "pitching")
TOURNAMENT_DIR = os.path.join(DATA_DIR, "tournament")

app = Flask(
    __name__,
    static_folder=STATIC_DIR,
    static_url_path=""  # so /team-hitting.html works directly
)

# ----- Simple HTTP Basic Auth -----

# In production, override these with environment variables
BASIC_AUTH_USER = os.environ.get("ACES_USER", "coach")
BASIC_AUTH_PASS = os.environ.get("ACES_PASS", "changeme")


def check_auth(username: str, password: str) -> bool:
    """Check if a username/password combination is valid."""
    return username == BASIC_AUTH_USER and password == BASIC_AUTH_PASS


def authenticate() -> Response:
    """Send a 401 response to trigger browser basic auth dialog."""
    return Response(
        "Authentication required.",
        401,
        {"WWW-Authenticate": "Basic realm=\"Aces Analytics\""},
    )


def requires_auth(f):
    """Decorator to require basic auth on a route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


# ----- Hitting helpers & API -----

def _list_hitting_seasons():
    """
    Scan data/hitting for CSVs and return:
      [{"id": "fall2024", "label": "Fall 2024"}, ...]
    """
    if not os.path.isdir(HITTING_DIR):
        return []

    seasons = []
    for fname in os.listdir(HITTING_DIR):
        if not fname.lower().endswith(".csv"):
            continue

        name_no_ext = fname[:-4]  # strip ".csv"

        # Support "hitting_fall2024.csv" and "fall2024.csv"
        if name_no_ext.startswith("hitting_"):
            season_id = name_no_ext[len("hitting_"):]
        else:
            season_id = name_no_ext

        label = season_id.replace("-", " ").replace("_", " ")
        label = " ".join(w.capitalize() for w in label.split())

        seasons.append({"id": season_id, "label": label})

    seasons.sort(key=lambda s: s["label"])
    return seasons


@app.route("/")
@requires_auth
def index():
    """Default page – Team Hitting dashboard."""
    return send_from_directory(STATIC_DIR, "team-hitting.html")


@app.get("/api/hitting/seasons")
@requires_auth
def hitting_seasons():
    """
    Return JSON array of hitting seasons.
    Example:
      [
        {"id": "fall2024", "label": "Fall 2024"},
        {"id": "spring2025", "label": "Spring 2025"}
      ]
    """
    return jsonify(_list_hitting_seasons())


@app.get("/api/hitting/csv/<season_id>")
@requires_auth
def hitting_csv(season_id):
    """
    Returns the raw hitting CSV for a given season.
    Tries:
      data/hitting/hitting_<season_id>.csv
      data/hitting/<season_id>.csv
    """
    candidates = [
        f"hitting_{season_id}.csv",
        f"{season_id}.csv",
    ]

    for candidate in candidates:
        path = os.path.join(HITTING_DIR, candidate)
        if os.path.exists(path):
            return send_from_directory(
                HITTING_DIR,
                candidate,
                mimetype="text/csv",
            )

    abort(404, description=f"No hitting CSV found for season '{season_id}'")


# ----- Pitching helpers & API -----

def _list_pitching_seasons():
    """
    Scan data/pitching for CSVs and return:
      [{"id": "fall2024", "label": "Fall 2024"}, ...]
    """
    if not os.path.isdir(PITCHING_DIR):
        return []

    seasons = []
    for fname in os.listdir(PITCHING_DIR):
        if not fname.lower().endswith(".csv"):
            continue

        name_no_ext = fname[:-4]  # strip ".csv"

        # Support "pitching_fall2024.csv" and "fall2024.csv"
        if name_no_ext.startswith("pitching_"):
            season_id = name_no_ext[len("pitching_"):]
        else:
            season_id = name_no_ext

        label = season_id.replace("-", " ").replace("_", " ")
        label = " ".join(w.capitalize() for w in label.split())

        seasons.append({"id": season_id, "label": label})

    seasons.sort(key=lambda s: s["label"])
    return seasons


@app.get("/api/pitching/seasons")
@requires_auth
def pitching_seasons():
    """
    Return JSON array of pitching seasons.
    """
    return jsonify(_list_pitching_seasons())


@app.get("/api/pitching/csv/<season_id>")
@requires_auth
def pitching_csv(season_id):
    """
    Returns the raw pitching CSV for a given season.
    Tries:
      data/pitching/pitching_<season_id>.csv
      data/pitching/<season_id>.csv
    """
    candidates = [
        f"pitching_{season_id}.csv",
        f"{season_id}.csv",
    ]

    for candidate in candidates:
        path = os.path.join(PITCHING_DIR, candidate)
        if os.path.exists(path):
            return send_from_directory(
                PITCHING_DIR,
                candidate,
                mimetype="text/csv",
            )

    abort(404, description=f"No pitching CSV found for season '{season_id}'")


# ----- Tournament API -----

@app.get("/api/tournament.csv")
@requires_auth
def tournament_csv():
    """
    Serve the default tournament CSV for the tournament threat board.
    Expects:
      data/tournament/tournament_teams.csv
    """
    if not os.path.isdir(TOURNAMENT_DIR):
        abort(404, description="Tournament directory not found")

    filename = "tournament_teams.csv"
    path = os.path.join(TOURNAMENT_DIR, filename)
    if not os.path.exists(path):
        abort(404, description="Tournament CSV not found")

    return send_from_directory(
        TOURNAMENT_DIR,
        filename,
        mimetype="text/csv",
    )


# ----- Protected static files (HTML / JS / CSS) -----

@app.route("/<path:filename>")
@requires_auth
def static_files(filename):
    """
    Catch-all route for static assets (HTML, JS, CSS, images, etc.)
    so they are also protected by basic auth.
    """
    return send_from_directory(STATIC_DIR, filename)


if __name__ == "__main__":
    # Local dev: http://localhost:8000
    app.run(host="0.0.0.0", port=8000, debug=True)
