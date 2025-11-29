import os
from functools import wraps
from datetime import datetime, timezone

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

BASIC_AUTH_USER = os.environ.get("ACES_USER", "coach")
BASIC_AUTH_PASS = os.environ.get("ACES_PASS", "changeme")


def check_auth(username: str, password: str) -> bool:
    return username == BASIC_AUTH_USER and password == BASIC_AUTH_PASS


def authenticate() -> Response:
    return Response(
        "Authentication required.",
        401,
        {"WWW-Authenticate": "Basic realm=\"Aces Analytics\""},
    )


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


# ----- Helpers -----

def _list_seasons_in_dir(dir_path: str, prefix: str):
    """
    Generic helper to scan a directory for CSVs and return:
      [{"id": "fall2024", "label": "Fall 2024"}, ...]
    prefix: "hitting_" or "pitching_" etc.
    """
    if not os.path.isdir(dir_path):
        return []

    seasons = []
    for fname in os.listdir(dir_path):
        if not fname.lower().endswith(".csv"):
            continue

        name_no_ext = fname[:-4]  # strip ".csv"

        if prefix and name_no_ext.startswith(prefix):
            season_id = name_no_ext[len(prefix):]
        else:
            season_id = name_no_ext

        label = season_id.replace("-", " ").replace("_", " ")
        label = " ".join(w.capitalize() for w in label.split())

        seasons.append({"id": season_id, "label": label})

    seasons.sort(key=lambda s: s["label"])
    return seasons


def _list_hitting_seasons():
    return _list_seasons_in_dir(HITTING_DIR, "hitting_")


def _list_pitching_seasons():
    return _list_seasons_in_dir(PITCHING_DIR, "pitching_")


def _get_last_updated_for_dir(dir_path: str):
    """
    Return ISO 8601 UTC timestamp for the newest CSV in dir_path,
    or None if there are no CSVs.
    """
    if not os.path.isdir(dir_path):
        return None

    latest_mtime = None
    for fname in os.listdir(dir_path):
        if not fname.lower().endswith(".csv"):
            continue
        full_path = os.path.join(dir_path, fname)
        try:
            mtime = os.path.getmtime(full_path)
        except OSError:
            continue
        if latest_mtime is None or mtime > latest_mtime:
            latest_mtime = mtime

    if latest_mtime is None:
        return None

    dt = datetime.fromtimestamp(latest_mtime, tz=timezone.utc)
    return dt.isoformat()  # e.g. "2025-11-29T03:12:34.567890+00:00"


# ----- Routes -----

@app.route("/")
@requires_auth
def index():
    return send_from_directory(STATIC_DIR, "team-hitting.html")


# --- Hitting ---

@app.get("/api/hitting/seasons")
@requires_auth
def hitting_seasons():
    return jsonify(_list_hitting_seasons())


@app.get("/api/hitting/csv/<season_id>")
@requires_auth
def hitting_csv(season_id):
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


@app.get("/api/hitting/last-updated")
@requires_auth
def hitting_last_updated():
    ts = _get_last_updated_for_dir(HITTING_DIR)
    return jsonify({"last_updated": ts})


# --- Pitching ---

@app.get("/api/pitching/seasons")
@requires_auth
def pitching_seasons():
    return jsonify(_list_pitching_seasons())


@app.get("/api/pitching/csv/<season_id>")
@requires_auth
def pitching_csv(season_id):
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


@app.get("/api/pitching/last-updated")
@requires_auth
def pitching_last_updated():
    ts = _get_last_updated_for_dir(PITCHING_DIR)
    return jsonify({"last_updated": ts})


# --- Tournament ---

@app.get("/api/tournament.csv")
@requires_auth
def tournament_csv():
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


@app.get("/api/tournament/last-updated")
@requires_auth
def tournament_last_updated():
    ts = _get_last_updated_for_dir(TOURNAMENT_DIR)
    return jsonify({"last_updated": ts})


# --- Protected static files (HTML / JS / CSS) ---

@app.route("/<path:filename>")
@requires_auth
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
