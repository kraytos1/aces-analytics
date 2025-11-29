import os
import re
import csv
import time
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple, Set

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options


# ----------------------------
# Globals
# ----------------------------

# Names of teams whose schedules we explicitly scraped
SCRAPED_TEAM_NAMES: Set[str] = set()


# ----------------------------
# Models / Config
# ----------------------------

@dataclass
class Config:
    gc_email: str
    gc_password: str
    chrome_user_data_dir: Optional[str]
    chrome_profile_dir: Optional[str]
    team_schedule_urls: List[str]
    tournament_filter: Optional[str]
    tournament_name: str
    output_csv: str


@dataclass
class Game:
    game_id: str
    tournament_name: Optional[str]
    status: str
    home_team: str
    away_team: str
    home_score: Optional[int]
    away_score: Optional[int]


# ----------------------------
# Config helpers
# ----------------------------

def load_config() -> Config:
    load_dotenv()

    raw_urls = os.getenv("TEAM_SCHEDULE_URLS", "").strip()
    if not raw_urls:
        raise RuntimeError("TEAM_SCHEDULE_URLS is required (comma-separated GC schedule URLs).")

    urls = [u.strip() for u in raw_urls.split(",") if u.strip()]

    tournament_name = (os.getenv("TOURNAMENT_NAME", "") or "Unnamed Tournament").strip()

    # Make a safe filename slug from the tournament name
    slug = re.sub(r"[^a-z0-9]+", "_", tournament_name.lower()).strip("_")
    if not slug:
        slug = "tournament"

    output_csv = f"tournament_{slug}.csv"

    return Config(
        gc_email=os.getenv("GC_EMAIL", ""),
        gc_password=os.getenv("GC_PASSWORD", ""),
        chrome_user_data_dir=os.getenv("CHROME_USER_DATA_DIR", None),
        chrome_profile_dir=os.getenv("CHROME_PROFILE_DIR", None),
        team_schedule_urls=urls,
        tournament_filter=(os.getenv("TOURNAMENT_FILTER", "") or None),
        tournament_name=tournament_name,
        output_csv=output_csv,
    )


def build_chrome_options(cfg: Config) -> Options:
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")

    # Extra stability flags
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")

    # For now, do NOT use your normal Chrome profile while we stabilize.
    # If you want to re-enable later, uncomment below:
    # if cfg.chrome_user_data_dir:
    #     opts.add_argument(f"--user-data-dir={cfg.chrome_user_data_dir}")
    # if cfg.chrome_profile_dir:
    #     opts.add_argument(f"--profile-directory={cfg.chrome_profile_dir}")

    return opts


# ----------------------------
# GameChanger login
# ----------------------------

def login_gamechanger(driver, cfg: Config):
    driver.get("https://web.gc.com/login")
    wait = WebDriverWait(driver, 30)

    # Try to find the email field. If we can't, assume we are already logged in.
    try:
        email_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']"))
        )
    except TimeoutException:
        print("[INFO] Email field not found; assuming already logged in.")
        return

    print("[INFO] Logging into GameChanger...")

    # Type email and press ENTER instead of clicking a button
    email_input.clear()
    email_input.send_keys(cfg.gc_email + Keys.ENTER)

    # Wait for password field to appear
    password_input = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
    )

    password_input.clear()
    password_input.send_keys(cfg.gc_password + Keys.ENTER)

    # Wait until we're no longer on the login page
    wait.until(lambda d: "login" not in d.current_url.lower())
    print("[INFO] Login successful. Now at:", driver.current_url)


# ----------------------------
# Scraping helpers
# ----------------------------

def parse_int_safe(text: str) -> Optional[int]:
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def scrape_schedule_games(driver, url: str) -> List[Game]:
    """
    Scrape games from a team schedule page using the new GC DOM structure.
    """
    print(f"[INFO] Loading schedule: {url}")
    driver.get(url)
    time.sleep(3)

    games: List[Game] = []

    # Get the team name from the page header (top nav)
    team_name = "Unknown Team"
    try:
        wait = WebDriverWait(driver, 10)
        team_name_el = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".NewTeamNavBar__teamName")
            )
        )
        team_name = team_name_el.text.strip()
    except TimeoutException:
        print("[WARN] Could not find team name header; using 'Unknown Team'.")

    # Record this schedule team as one we explicitly scraped
    SCRAPED_TEAM_NAMES.add(team_name)
    print(f"[INFO] Schedule team recorded: {team_name!r}")

    # Each game/event is an <a> with this class
    game_cards = driver.find_elements(By.CSS_SELECTOR, "a.ScheduleListByMonth__event")
    print(f"[INFO] Found {len(game_cards)} schedule events.")

    for card in game_cards:
        try:
            # Game ID from href
            href = card.get_attribute("href")
            if not href:
                continue
            game_id = href.rstrip("/").split("/")[-1]

            # Opponent name (e.g. "@ Delmarva Aces Beach 12U")
            title_el = card.find_element(
                By.CSS_SELECTOR,
                ".ScheduleListByMonth__title .Text__semibold"
            )
            opponent = title_el.text.strip()

            # Score / time / status text (e.g. "W 13-2", "L 6-8", "12:00 PM")
            score_el = card.find_element(
                By.CSS_SELECTOR,
                ".ScheduleListByMonth__scoreOrTimeText"
            )
            score_text = score_el.text.strip()

            status = "Scheduled"
            home_score = None
            away_score = None

            # Format examples:
            # "W 13-2"  (this means schedule team won 13–2)
            # "L 6-8"   (schedule team lost 6–8)
            # "Final"   (scores might be somewhere else – we skip for now)
            # "12:00 PM" (future game)
            if score_text.startswith("W "):
                status = "Final"
                parts = score_text.split(" ")[1].split("-")
                home_score = int(parts[0])
                away_score = int(parts[1])
            elif score_text.startswith("L "):
                status = "Final"
                parts = score_text.split(" ")[1].split("-")
                home_score = int(parts[0])
                away_score = int(parts[1])
            elif score_text == "Final":
                status = "Final"
                # No scores provided; we skip these when aggregating.
            else:
                status = "Scheduled"

            # Treat the schedule team as "home_team" in our model,
            # opponent as "away_team" – naming doesn't matter, we just need
            # two teams with consistent RS/RA relationships.
            home_team = team_name
            away_team = opponent

            games.append(
                Game(
                    game_id=game_id,
                    tournament_name=None,  # not scraped yet
                    status=status,
                    home_team=home_team,
                    away_team=away_team,
                    home_score=home_score,
                    away_score=away_score,
                )
            )

        except Exception as e:
            print(f"[WARN] Error parsing schedule event: {e}")
            continue

    print(f"[INFO] Parsed {len(games)} games from {url}")
    return games


def filter_games_for_tournament(
    games: List[Game], tournament_filter: Optional[str]
) -> List[Game]:
    # For now, tournament_name is always None, so any non-empty filter
    # would drop everything. If you want filtering later, we can add
    # a box-score step to populate tournament_name.
    if not tournament_filter:
        return games

    key = tournament_filter.lower()
    filtered = [
        g for g in games
        if g.tournament_name and key in g.tournament_name.lower()
    ]
    print(
        f"[INFO] Filtered games by '{tournament_filter}': "
        f"{len(filtered)} of {len(games)} remain."
    )
    return filtered


# ----------------------------
# Aggregation: build team CSV
# ----------------------------

def normalize_team_name(name: str) -> str:
    if not name:
        return ""
    n = name.strip()
    lower = n.lower()
    for prefix in ("vs. ", "vs ", "@ "):
        if lower.startswith(prefix):
            n = n[len(prefix):].strip()
            break
    return n


def build_team_totals(games: List[Game]) -> Dict[str, Dict[str, int]]:
    """
    Returns dict:
    {
      "Team Name": {
         "G": ...,
         "W": ...,
         "L": ...,
         "RS": ...,
         "RA": ...
      },
      ...
    }
    """
    teams: Dict[str, Dict[str, int]] = {}

    def ensure(team: str):
        if team not in teams:
            teams[team] = {"G": 0, "W": 0, "L": 0, "RS": 0, "RA": 0}

    for g in games:
        if g.status.lower() != "final":
            continue
        if g.home_score is None or g.away_score is None:
            continue

        home = normalize_team_name(g.home_team)
        away = normalize_team_name(g.away_team)

        ensure(home)
        ensure(away)

        teams[home]["G"] += 1
        teams[away]["G"] += 1

        teams[home]["RS"] += g.home_score
        teams[home]["RA"] += g.away_score
        teams[away]["RS"] += g.away_score
        teams[away]["RA"] += g.home_score

        if g.home_score > g.away_score:
            teams[home]["W"] += 1
            teams[away]["L"] += 1
        elif g.home_score < g.away_score:
            teams[away]["W"] += 1
            teams[home]["L"] += 1
        else:
            # ignore ties
            pass

    return teams


def write_tournament_csv(path: str, teams: Dict[str, Dict[str, int]]):
    """
    Writes CSV with columns: Team,Pool,G,W,L,RS,RA
    Pool left blank for now (you can edit in Excel if desired).
    """
    rows: List[Tuple[str, str, int, int, int, int, int]] = []

    for team, stats in teams.items():
        rows.append(
            (
                team,
                "",  # Pool – fill manually for now
                stats["G"],
                stats["W"],
                stats["L"],
                stats["RS"],
                stats["RA"],
            )
        )

    # Sort by win% / RD just to look nice in the CSV
    def sort_key(r):
        team, pool, g, w, l, rs, ra = r
        win_pct = w / g if g else 0
        rd = rs - ra
        return (-win_pct, -rd, -rs)

    rows.sort(key=sort_key)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Team", "Pool", "G", "W", "L", "RS", "RA"])
        writer.writerows(rows)

    print(f"[INFO] Wrote {len(rows)} teams to {path}")


# ----------------------------
# Main
# ----------------------------

def main():
    cfg = load_config()
    print("[INFO] Config loaded.")
    print(f"[INFO] Tournament: {cfg.tournament_name!r}")
    print(f"[INFO] Output CSV: {cfg.output_csv!r}")

    options = build_chrome_options(cfg)
    driver = webdriver.Chrome(options=options)

    try:
        login_gamechanger(driver, cfg)

        all_games: List[Game] = []
        for url in cfg.team_schedule_urls:
            g = scrape_schedule_games(driver, url)
            all_games.extend(g)

        print(f"[INFO] Total games scraped: {len(all_games)}")

        filtered_games = filter_games_for_tournament(all_games, cfg.tournament_filter)
        print(f"[INFO] Games after tournament filter: {len(filtered_games)}")

        team_totals = build_team_totals(filtered_games)
        print(f"[INFO] Teams found (all teams seen in games): {len(team_totals)}")

        # Keep ONLY teams whose schedules we scraped
        filtered_team_totals = {
            team: stats
            for team, stats in team_totals.items()
            if team in SCRAPED_TEAM_NAMES
        }

        print("[INFO] Schedule teams:", SCRAPED_TEAM_NAMES)
        print(
            f"[INFO] Teams after restricting to schedule teams: "
            f"{len(filtered_team_totals)}"
        )

        write_tournament_csv(cfg.output_csv, filtered_team_totals)

    finally:
        driver.quit()
        print("[INFO] Done.")


if __name__ == "__main__":
    main()
