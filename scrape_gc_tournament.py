import os
import time
import datetime
import pyodbc
from dataclasses import dataclass
from typing import List, Optional

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ----------------------------
# Config / Models
# ----------------------------

@dataclass
class Config:
    gc_email: str
    gc_password: str
    sql_server: str
    sql_database: str
    chrome_user_data_dir: Optional[str]
    chrome_profile_dir: Optional[str]
    team_schedule_urls: List[str]
    tournament_filter: Optional[str]  # substring in tournament name, or None for all


@dataclass
class GameRow:
    game_id: str
    game_date: Optional[datetime.datetime]
    tournament_name: Optional[str]
    home_team: str
    away_team: str
    home_score: Optional[int]
    away_score: Optional[int]
    status: str  # e.g., "Final", "Scheduled", etc.


# ----------------------------
# Load configuration
# ----------------------------

def load_config() -> Config:
    load_dotenv()

    team_urls_raw = os.getenv("TEAM_SCHEDULE_URLS", "").strip()
    if not team_urls_raw:
        raise RuntimeError("TEAM_SCHEDULE_URLS env var is required (comma-separated GC schedule URLs).")

    team_urls = [u.strip() for u in team_urls_raw.split(",") if u.strip()]

    return Config(
        gc_email=os.getenv("GC_EMAIL", ""),
        gc_password=os.getenv("GC_PASSWORD", ""),
        sql_server=os.getenv("SQL_SERVER", ""),
        sql_database=os.getenv("SQL_DATABASE", ""),
        chrome_user_data_dir=os.getenv("CHROME_USER_DATA_DIR", None),
        chrome_profile_dir=os.getenv("CHROME_PROFILE_DIR", None),
        team_schedule_urls=team_urls,
        # You can hard-code a tournament substring here if you want to filter:
        # e.g. "Perfect Game Mid-Atlantic" or leave None to grab all
        tournament_filter=os.getenv("TOURNAMENT_FILTER", None) or None,
    )


# ----------------------------
# DB helpers
# ----------------------------

def get_db_connection(cfg: Config):
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={cfg.sql_server};"
        f"DATABASE={cfg.sql_database};"
        "Trusted_Connection=yes;"
    )
    return pyodbc.connect(conn_str)


def reset_tournament_table(cursor):
    cursor.execute("TRUNCATE TABLE dbo.GCTournamentGamesTmp;")
    cursor.connection.commit()
    print("[INFO] Cleared GCTournamentGamesTmp.")


def insert_games(cursor, games: List[GameRow]):
    sql = """
        INSERT INTO dbo.GCTournamentGamesTmp
        (GameID, GameDate, TournamentName, HomeTeam, AwayTeam, HomeScore, AwayScore, Status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
    """
    for g in games:
        cursor.execute(
            sql,
            g.game_id,
            g.game_date,
            g.tournament_name,
            g.home_team,
            g.away_team,
            g.home_score,
            g.away_score,
            g.status,
        )
    cursor.connection.commit()
    print(f"[INFO] Inserted {len(games)} games into GCTournamentGamesTmp.")


# ----------------------------
# Selenium / GC helpers
# ----------------------------

def build_chrome_options(cfg: Config) -> Options:
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")

    if cfg.chrome_user_data_dir:
        options.add_argument(f"--user-data-dir={cfg.chrome_user_data_dir}")
    if cfg.chrome_profile_dir:
        options.add_argument(f"--profile-directory={cfg.chrome_profile_dir}")

    return options


def login_gamechanger(driver, cfg: Config):
    driver.get("https://web.gc.com/login")

    wait = WebDriverWait(driver, 30)

    # If already logged in (due to Chrome profile), GC might redirect automatically
    time.sleep(5)
    if "login" not in driver.current_url.lower():
        print("[INFO] Already logged in to GameChanger.")
        return

    print("[INFO] Logging into GameChanger...")
    email_input = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']"))
    )
    email_input.clear()
    email_input.send_keys(cfg.gc_email)

    next_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
    next_btn.click()

    password_input = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
    )
    password_input.clear()
    password_input.send_keys(cfg.gc_password)

    login_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
    login_btn.click()

    # Wait for redirect away from login
    wait.until(lambda d: "login" not in d.current_url.lower())
    print("[INFO] Login successful.")


# ----------------------------
# Schedule scraping
# ----------------------------

def parse_int_safe(text: str) -> Optional[int]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def scrape_schedule_page_games(driver, schedule_url: str) -> List[GameRow]:
    """
    Scrapes a single team schedule page for games.

    NOTE: You may need to tweak the CSS selectors below to match GC's current DOM.
    """
    print(f"[INFO] Loading schedule: {schedule_url}")
    driver.get(schedule_url)

    # Let the page + any lazy-loaded schedule render
    time.sleep(5)

    games: List[GameRow] = []

    # This part is DOM-dependent; adjust as needed.
    # Idea: each game is a "card" or "row" with teams, score, status, and a link.
    game_cards = driver.find_elements(By.CSS_SELECTOR, "[data-testid='schedule-game-card'], .ScheduleGameCard")
    print(f"[INFO] Found {len(game_cards)} potential game cards on page.")

    for card in game_cards:
        try:
            # Game link (used to derive GameID)
            link_el = card.find_element(By.CSS_SELECTOR, "a[href*='/game-']")
            game_url = link_el.get_attribute("href")
            game_id = game_url.rstrip("/").split("/")[-1]

            # Teams
            home_team_el = card.find_element(By.CSS_SELECTOR, "[data-testid='home-team-name'], .ScheduleGameCard__homeTeam")
            away_team_el = card.find_element(By.CSS_SELECTOR, "[data-testid='away-team-name'], .ScheduleGameCard__awayTeam")
            home_team = home_team_el.text.strip()
            away_team = away_team_el.text.strip()

            # Score + status
            status_el = card.find_element(By.CSS_SELECTOR, "[data-testid='game-status'], .ScheduleGameCard__status")
            status = status_el.text.strip()  # e.g. "Final", "Scheduled"

            home_score = None
            away_score = None
            try:
                home_score_el = card.find_element(By.CSS_SELECTOR, "[data-testid='home-team-score'], .ScheduleGameCard__homeScore")
                away_score_el = card.find_element(By.CSS_SELECTOR, "[data-testid='away-team-score'], .ScheduleGameCard__awayScore")
                home_score = parse_int_safe(home_score_el.text)
                away_score = parse_int_safe(away_score_el.text)
            except NoSuchElementException:
                # Scores might not exist for unplayed games
                pass

            # Date (optional)
            game_date = None
            try:
                date_el = card.find_element(By.CSS_SELECTOR, "[data-testid='game-date'], .ScheduleGameCard__date")
                # This may need custom parsing; for now just keep as None or raw text
                # You can parse to datetime if you know the format.
                # game_date = datetime.datetime.strptime(date_el.text.strip(), "%b %d, %Y %I:%M %p")
                # For now, leave None
                _ = date_el.text  # placeholder
            except NoSuchElementException:
                pass

            # Tournament name (optional)
            tournament_name = None
            try:
                tour_el = card.find_element(By.CSS_SELECTOR, "[data-testid='event-name'], .ScheduleGameCard__eventName")
                tournament_name = tour_el.text.strip()
            except NoSuchElementException:
                pass

            game = GameRow(
                game_id=game_id,
                game_date=game_date,
                tournament_name=tournament_name,
                home_team=home_team,
                away_team=away_team,
                home_score=home_score,
                away_score=away_score,
                status=status,
            )
            games.append(game)

        except NoSuchElementException:
            # If this card doesn't match the expected pattern, skip but log
            print("[WARN] Skipped a card due to missing expected elements.")
            continue

    print(f"[INFO] Parsed {len(games)} games from {schedule_url}")
    return games


def filter_games_by_tournament(games: List[GameRow], tournament_filter: Optional[str]) -> List[GameRow]:
    if not tournament_filter:
        return games

    filt = tournament_filter.lower()
    filtered = [g for g in games if g.tournament_name and filt in g.tournament_name.lower()]
    print(f"[INFO] Filtered games by tournament '{tournament_filter}': {len(filtered)} remain (out of {len(games)}).")
    return filtered


# ----------------------------
# Main
# ----------------------------

def main():
    cfg = load_config()
    print("[INFO] Loaded config.")

    conn = get_db_connection(cfg)
    cursor = conn.cursor()

    reset_tournament_table(cursor)

    chrome_options = build_chrome_options(cfg)
    driver = webdriver.Chrome(options=chrome_options)

    try:
        login_gamechanger(driver, cfg)

        all_games: List[GameRow] = []

        for url in cfg.team_schedule_urls:
            schedule_games = scrape_schedule_page_games(driver, url)
            schedule_games = filter_games_by_tournament(schedule_games, cfg.tournament_filter)
            all_games.extend(schedule_games)

        print(f"[INFO] Total games collected (after filtering): {len(all_games)}")
        insert_games(cursor, all_games)

    finally:
        driver.quit()
        cursor.close()
        conn.close()
        print("[INFO] Done.")

if __name__ == "__main__":
    main()
