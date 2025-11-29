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

def _build_season_index(dir_path: str, prefix: str):
    """
    Build a case-insensitive index of season_id -> filename for CSVs in dir_path.

    Example return:
      {
        "fall2025": "hitting_fall2025.csv",
        "spring2025": "hitting_spring2025.csv",
      }
    """
    index = {}

    if not os.path.isdir(dir_path):
        return index

    low_prefix = prefix.lower() if prefix else ""

    for fname in os.listdir(dir_path):
        if not fname.lower().endswith(".csv"):
            continue

        base = os.path.splitext(fname)[0]  # strip extension
        low_base = base.lower()

        if low_prefix and low_base.startswith(low_prefix):
            # Strip the prefix in a case-insensitive way
            season_low = low_base[len(low_prefix):]
        else:
            season_low = low_base

        index[season_low] = fname

    return index


def _season_list_from_index(index: dict):
    """
    Turn a season index { "fall2025": "..." } into a list of
    {"id": "fall2025", "label": "Fall 2025"} sorted by label.
    """
    seasons = []

    for season_low in index.keys():
        label_raw = season_low.replace("-", " ").replace("_", " ")

        # Insert a space before the first digit, e.g. "fall2025" -> "fall 2025"
        for i, ch in enumerate(label_raw):
            if ch.isdigit():
                label_raw = label_raw[:i] + " " + label_raw[i:]
                break

        label = " ".join(w.capitalize() for w in label_raw.split())
        seasons.append({"id": season_low, "label": label})

    seasons.sort(key=lambda s: s["label"])
    return seasons


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


# ----- Page routes (all protected) -----

@app.route("/")
@requires_auth
def index():
    return send_from_directory(STATIC_DIR, "team-hitting.html")


@app.route("/team-hitting.html")
@requires_auth
def page_team_hitting():
    return send_from_directory(STATIC_DIR, "team-hitting.html")


@app.route("/team-pitching.html")
@requires_auth
def page_team_pitching():
    return send_from_directory(STATIC_DIR, "team-pitching.html")


@app.route("/player.html")
@requires_auth
def page_player():
    return send_from_directory(STATIC_DIR, "player.html")


@app.route("/tournament.html")
@requires_auth
def page_tournament():
    return send_from_directory(STATIC_DIR, "tournament.html")


# --- Hitting ---

@app.get("/api/hitting/seasons")
@requires_auth
def hitting_seasons():
    index = _build_season_index(HITTING_DIR, "hitting_")
    return jsonify(_season_list_from_index(index))


@app.get("/api/hitting/csv/<season_id>")
@requires_auth
def hitting_csv(season_id):
    index = _build_season_index(HITTING_DIR, "hitting_")
    key = season_id.lower()
    fname = index.get(key)

    if not fname:
        abort(404, description=f"No hitting CSV found for season '{season_id}'")

    return send_from_directory(
        HITTING_DIR,
        fname,
        mimetype="text/csv",
    )


@app.get("/api/hitting/last-updated")
@requires_auth
def hitting_last_updated():
    ts = _get_last_updated_for_dir(HITTING_DIR)
    return jsonify({"last_updated": ts})


# --- Pitching ---

@app.get("/api/pitching/seasons")
@requires_auth
def pitching_seasons():
    index = _build_season_index(PITCHING_DIR, "pitching_")
    return jsonify(_season_list_from_index(index))


@app.get("/api/pitching/csv/<season_id>")
@requires_auth
def pitching_csv(season_id):
    index = _build_season_index(PITCHING_DIR, "pitching_")
    key = season_id.lower()
    fname = index.get(key)

    if not fname:
        abort(404, description=f"No pitching CSV found for season '{season_id}'")

    return send_from_directory(
        PITCHING_DIR,
        fname,
        mimetype="text/csv",
    )


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


# --- Protected static files (HTML / JS / CSS / assets) ---

@app.route("/<path:filename>")
@requires_auth
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
