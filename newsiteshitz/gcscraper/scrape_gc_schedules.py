import os
import sys
import time
import traceback
import re
import pyodbc
import difflib
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv
from datetime import datetime

# -------------------------------------------------------------------------
# 1) CONFIGURATION – update these before running
# -------------------------------------------------------------------------

# Load environment variables from scrape_gc.env
load_dotenv('scrape_gc.env')

EMAIL        = os.getenv('GC_EMAIL', '').strip()
PASSWORD     = os.getenv('GC_PASSWORD', '').strip()
SQL_SERVER   = os.getenv('SQL_SERVER', '').strip()
SQL_DATABASE = os.getenv('SQL_DATABASE', '').strip()

# Sanity check
if not (EMAIL and PASSWORD and SQL_SERVER and SQL_DATABASE):
    raise RuntimeError(
        'Please populate scrape_gc.env with GC_EMAIL, GC_PASSWORD, SQL_SERVER, and SQL_DATABASE.'
    )

# Database connection string (SQL Server via pyodbc)
DB_CONNECTION_STRING = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={SQL_SERVER};"
    f"DATABASE={SQL_DATABASE};"
    "Trusted_Connection=yes;"
)

# Persistent Chrome profile so you stay logged in
CHROME_USER_DATA_DIR = os.getenv('CHROME_USER_DATA_DIR', './chrome-user-data')
CHROME_PROFILE_DIR   = os.getenv('CHROME_PROFILE_DIR', 'Default')

IMPLICIT_WAIT = 15  # seconds

# List of the eight team schedule URLs
TEAM_SCHEDULE_URLS = [
   
    "https://web.gc.com/teams/HPJ!$0j14/team_goes_here/schedule",
    "https://web.gc.com/teams/HPJ!$0j14/team_goes_here/schedule"	
]

# -------------------------------------------------------------------------
# 2) DATABASE SETUP: create tables if they don’t exist
#    • GCGamesTmp4 now has SourceTeamID
#    • GCBattingStatsTmp4 and GCPitchingStatsTmp4 now have TeamMatch (Yes/No)
# -------------------------------------------------------------------------
def initialize_database():
    conn = pyodbc.connect(DB_CONNECTION_STRING)
    cursor = conn.cursor()

    # Create Games table (added SourceTeamID)
    cursor.execute("""
    IF NOT EXISTS (
        SELECT 1
          FROM INFORMATION_SCHEMA.TABLES
         WHERE TABLE_NAME = 'GCGamesTmp4'
    )
    BEGIN
        CREATE TABLE GCGamesTmp4 (
            GameID          VARCHAR(100) PRIMARY KEY,
            SourceTeamID    VARCHAR(50),
            GameDate        DATE,
            HomeTeamID      VARCHAR(50),
            AwayTeamID      VARCHAR(50),
            HomeScore       INT,
            AwayScore       INT,
            BoxScoreURL     VARCHAR(500)
        );
    END
    """)

    # Create BattingStats table (added TeamMatch VARCHAR(3))
    cursor.execute("""
    IF NOT EXISTS (
        SELECT 1
          FROM INFORMATION_SCHEMA.TABLES
         WHERE TABLE_NAME = 'GCBattingStatsTmp4'
    )
    BEGIN
        CREATE TABLE GCBattingStatsTmp4 (
            BattingStatsID  INT IDENTITY(1,1) PRIMARY KEY,
            GameID          VARCHAR(100)   NOT NULL,
            TeamID          VARCHAR(50)    NOT NULL,
            TeamName        VARCHAR(200),
            HomeOrAway      VARCHAR(10),
            TeamMatch       VARCHAR(3),
            Opponent        VARCHAR(200),
            PlayerName      VARCHAR(200),
            Position        VARCHAR(20),
            AB              INT,
            R               INT,
            H               INT,
            RBI             INT,
            BB              INT,
            SO              INT,
            Doubles         INT,
            Triples         INT,
            HomeRuns        INT,
            StolenBases     INT,
            TotalBases      INT,
            FOREIGN KEY (GameID)   REFERENCES GCGamesTmp4(GameID)
        );
    END
    """)

    # Create PitchingStats table (added TeamMatch VARCHAR(3))
    cursor.execute("""
    IF NOT EXISTS (
        SELECT 1
          FROM INFORMATION_SCHEMA.TABLES
         WHERE TABLE_NAME = 'GCPitchingStatsTmp4'
    )
    BEGIN
        CREATE TABLE GCPitchingStatsTmp4 (
            PitchingStatsID INT IDENTITY(1,1) PRIMARY KEY,
            GameID          VARCHAR(100)   NOT NULL,
            TeamID          VARCHAR(50)    NOT NULL,
            TeamName        VARCHAR(200),
            HomeOrAway      VARCHAR(10),
            TeamMatch       VARCHAR(3),
            Opponent        VARCHAR(200),
            PitcherName     VARCHAR(200),
            IP              VARCHAR(10),
            HAllowed        INT,
            RAllowed        INT,
            ERAllowed       INT,
            BBAllowed       INT,
            Strikeouts      INT,
            PitchesThrown   INT,
            StrikesThrown   INT,
            BattersFaced    INT,
            FOREIGN KEY (GameID)   REFERENCES GCGamesTmp4(GameID)
        );
    END
    """)

    conn.commit()
    cursor.close()
    return conn

# -------------------------------------------------------------------------
# 3) LOGIN: two-step (email → code+password) with manual 2FA
# -------------------------------------------------------------------------
def login_gamechanger(driver):
    driver.get('https://web.gc.com/login')
    time.sleep(1)

    current_url = driver.current_url
    if 'web.gc.com/home' in current_url:
        print("[INFO] Already logged in; skipping login.")
        return

    try:
        WebDriverWait(driver, IMPLICIT_WAIT).until(
            EC.presence_of_element_located((By.NAME, 'email'))
        )
    except Exception:
        print("[WARN] Email input never appeared; assume already logged in.")
        return

    email_field = driver.find_element(By.NAME, 'email')
    email_field.clear()
    email_field.send_keys(EMAIL)

    try:
        next_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
    except:
        next_btn = driver.find_element(
            By.XPATH,
            "//button[contains(text(),'Continue') or contains(text(),'Next')]"
        )
    next_btn.click()

    try:
        WebDriverWait(driver, IMPLICIT_WAIT).until(
            EC.presence_of_element_located((By.NAME, 'password'))
        )
    except Exception:
        print("[WARN] Password input not found after email; aborting login.")
        return

    password_field = driver.find_element(By.NAME, 'password')
    password_field.clear()
    password_field.send_keys(PASSWORD)

    print(
        "\n----------------------------------------------------------------\n"
        "The page is now asking for your 2FA code (password filled).\n"
        "Enter the emailed code in the ‘Code’ field and click “Log In.”\n"
        "Then return here and press ENTER...\n"
        "----------------------------------------------------------------\n"
    )
    input("Press ENTER once you’ve clicked “Log In” in Chrome…")
    return

# -------------------------------------------------------------------------
# 4) parse_schedule_page using HTML Schedule structure
# -------------------------------------------------------------------------
def parse_schedule_page(html: str, team_id: str) -> list[dict]:
    """
    Parses a GameChanger “/schedule” page HTML.
    Returns list of dicts with:
      - game_date (YYYY-MM-DD)
      - box_score_url
      - home_or_away
      - our_score, opp_score
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for header_div in soup.select("div.ScheduleSection__stickyItem"):
        month_span = header_div.select_one("span.ScheduleSection__sectionTitle")
        if not month_span:
            continue
        month_year = month_span.get_text(strip=True)
        try:
            month_dt = datetime.strptime(month_year, "%B %Y")
        except ValueError:
            continue

        sibling = header_div.find_next_sibling(
            lambda tag: tag.name == "div"
                       and "ScheduleSection__section" in tag.get("class", [])
                       and "ScheduleListByMonth__eventMonth" in tag.get("class", [])
        )
        if not sibling:
            continue

        for day_row in sibling.select("div.ScheduleListByMonth__dayRow"):
            date_text_div = day_row.select_one("div.ScheduleListByMonth__dateText")
            if not date_text_div:
                continue
            day_num = date_text_div.get_text(strip=True)
            try:
                day_num_int = int(day_num)
            except ValueError:
                continue

            full_date = datetime(
                year=month_dt.year,
                month=month_dt.month,
                day=day_num_int
            ).strftime("%Y-%m-%d")

            for link in day_row.select("a.ScheduleListByMonth__event"):  # each game
                raw_href = link.get("href")
                if not raw_href:
                    continue
                box_score_url = "https://web.gc.com" + raw_href

                title_div = link.select_one("div.ScheduleListByMonth__title")
                event_title = ""
                if title_div:
                    semibold = title_div.select_one("div.Text__semibold")
                    if semibold:
                        event_title = semibold.get_text(" ", strip=True)

                score_span = link.select_one("span.ScheduleListByMonth__scoreOrTimeText")
                score_text = score_span.get_text(strip=True) if score_span else ""

                home_or_away = None
                if event_title.startswith("vs."):
                    home_or_away = "HOME"
                elif event_title.startswith("@"):
                    home_or_away = "AWAY"

                our_score = None
                opp_score = None
                if score_text and (score_text.startswith("W ") or score_text.startswith("L ")):
                    parts = score_text[2:].split('-')
                    if len(parts) == 2:
                        try:
                            our_score = int(parts[0])
                            opp_score = int(parts[1])
                        except ValueError:
                            our_score = None
                            opp_score = None

                results.append({
                    'game_date':    full_date,
                    'box_score_url': box_score_url,
                    'home_or_away': home_or_away,
                    'our_score':    our_score,
                    'opp_score':    opp_score
                })

    return results

# -------------------------------------------------------------------------
# 5) parse_box_score with robust extra-stats parsing, now including Opponent
# -------------------------------------------------------------------------
def parse_box_score(html, home_team_id, away_team_id, game_id):
    soup = BeautifulSoup(html, 'html.parser')

    # Extract team names from the box score header
    away_team_name_div = soup.select_one('div.BoxScore__teamName.BoxScore__awayTeamName')
    home_team_name_div = soup.select_one('div.BoxScore__teamName.BoxScore__homeTeamName')
    away_team_name = away_team_name_div.get_text(strip=True) if away_team_name_div else ''
    home_team_name = home_team_name_div.get_text(strip=True) if home_team_name_div else ''

    def parse_int(value):
        try:
            return int(value)
        except:
            return 0

    def parse_pitches_strikes(count_str):
        clean = count_str.strip().rstrip(',')
        m = re.search(r"(\d+)-(\d+)", clean)
        if m:
            return int(m.group(1)), int(m.group(2))
        return None, None

    # Batting extraction now captures team name and home/away flag
    def extract_batting(team_container, team_id, team_name, home_or_away_flag):
        batting_stats = []
        # Determine opponent name
        opponent_name = home_team_name if home_or_away_flag == 'AWAY' else away_team_name
        for row in team_container.select(
            'div.ag-root-wrapper-body div.ag-center-cols-container div[role="row"]'
        ):
            first_cell = row.select_one('div[aria-colindex="1"]')
            if not first_cell:
                continue
            player_name_span = first_cell.select_one('span.BoxScoreComponents__playerName')
            if not player_name_span:
                continue
            try:
                name = player_name_span.get_text(strip=True)
                pos_info_span = first_cell.select_one('span.BoxScoreComponents__playerInfo')
                pos = pos_info_span.get_text(strip=True).strip('()') if pos_info_span else ''

                ab  = parse_int(row.select_one('div[aria-colindex="2"]').get_text(strip=True) or 0)
                r   = parse_int(row.select_one('div[aria-colindex="3"]').get_text(strip=True) or 0)
                h   = parse_int(row.select_one('div[aria-colindex="4"]').get_text(strip=True) or 0)
                rbi = parse_int(row.select_one('div[aria-colindex="5"]').get_text(strip=True) or 0)
                bb  = parse_int(row.select_one('div[aria-colindex="6"]').get_text(strip=True) or 0)
                so  = parse_int(row.select_one('div[aria-colindex="7"]').get_text(strip=True) or 0)

                batting_stats.append({
                    'GameID':      game_id,
                    'TeamID':      team_id,
                    'TeamName':    team_name,
                    'HomeOrAway':  home_or_away_flag,
                    'Opponent':    opponent_name,
                    'PlayerName':  name,
                    'Position':    pos,
                    'AB':          ab,
                    'R':           r,
                    'H':           h,
                    'RBI':         rbi,
                    'BB':          bb,
                    'SO':          so,
                    'Doubles':     0,
                    'Triples':     0,
                    'HomeRuns':    0,
                    'StolenBases': 0,
                    'TotalBases':  0
                })
            except Exception as e:
                print(f"  [WARN] Error parsing batting row: {e}")
                continue

        # Parse extra batting stats container
        extra_container = team_container.find_next_sibling(
            lambda tag: tag.name == 'div' and 'BoxScoreComponents__boxScoreExtraStats' in tag.get('class', [])
        )
        if extra_container:
            for line_div in extra_container.select('div'):
                label_span = line_div.select_one('span.Text__semibold')
                if not label_span:
                    continue
                stat_label = label_span.get_text(strip=True).rstrip(':')
                for stat_span in line_div.select('span.BoxScoreComponents__extraPlayerStat'):
                    text = stat_span.get_text(strip=True).rstrip(',')
                    # Allow for optional number: if absent, default to 1
                    parts = re.match(r"(.+?)(?:\s+(\d+))?$", text)
                    if not parts:
                        print(f"  [DEBUG] Regex failed to match batting extra: '{text}'")
                        continue
                    raw_name = parts.group(1).strip()
                    count_str = parts.group(2).strip() if parts.group(2) else '1'

                    # Match player name exactly or via close match
                    player_names = [bd['PlayerName'] for bd in batting_stats]
                    player_match = None
                    if raw_name in player_names:
                        player_match = raw_name
                    else:
                        close = difflib.get_close_matches(raw_name, player_names, n=1, cutoff=0.6)
                        if close:
                            player_match = close[0]

                    if not player_match:
                        print(f"  [WARN] No match for extra batting name: '{raw_name}' in {player_names}")
                        continue

                    stat_count = parse_int(count_str)
                    for bd in batting_stats:
                        if bd['PlayerName'] == player_match:
                            if stat_label == '2B':
                                bd['Doubles'] = stat_count
                                print(f"[DEBUG] Updated {player_match}: Doubles={stat_count}")
                            elif stat_label == '3B':
                                bd['Triples'] = stat_count
                                print(f"[DEBUG] Updated {player_match}: Triples={stat_count}")
                            elif stat_label == 'HR':
                                bd['HomeRuns'] = stat_count
                                print(f"[DEBUG] Updated {player_match}: HomeRuns={stat_count}")
                            break
        else:
            print(f"[DEBUG] No extra batting stats container found")

        return batting_stats

    def extract_pitching(team_container, team_id, team_name, home_or_away_flag):
        pitching_stats = []
        # Determine opponent name
        opponent_name = home_team_name if home_or_away_flag == 'AWAY' else away_team_name
        for row in team_container.select(
            'div.ag-root-wrapper-body div.ag-center-cols-container div[role="row"]'
        ):
            first_cell = row.select_one('div[aria-colindex="1"]')
            if not first_cell:
                continue
            pitcher_name_span = first_cell.select_one('span.BoxScoreComponents__playerName')
            if not pitcher_name_span:
                continue
            try:
                pitcher_name = pitcher_name_span.get_text(strip=True)
                ip = row.select_one('div[aria-colindex="2"]').get_text(strip=True)
                h_allowed  = parse_int(row.select_one('div[aria-colindex="3"]').get_text(strip=True) or 0)
                r_allowed  = parse_int(row.select_one('div[aria-colindex="4"]').get_text(strip=True) or 0)
                er_allowed = parse_int(row.select_one('div[aria-colindex="5"]').get_text(strip=True) or 0)
                bb_allowed = parse_int(row.select_one('div[aria-colindex="6"]').get_text(strip=True) or 0)
                so = parse_int(row.select_one('div[aria-colindex="7"]').get_text(strip=True) or 0)

                pitching_stats.append({
                    'GameID':      game_id,
                    'TeamID':      team_id,
                    'TeamName':    team_name,
                    'HomeOrAway':  home_or_away_flag,
                    'Opponent':    opponent_name,
                    'PitcherName': pitcher_name,
                    'IP':          ip,
                    'HAllowed':    h_allowed,
                    'RAllowed':    r_allowed,
                    'ERAllowed':   er_allowed,
                    'BBAllowed':   bb_allowed,
                    'Strikeouts':  so,
                    'PitchesThrown': None,
                    'StrikesThrown': None,
                    'BattersFaced':  None
                })
            except Exception as e:
                print(f"  [WARN] Error parsing pitcher row: {e}")
                continue

        pitcher_names = [rd['PitcherName'] for rd in pitching_stats]

        extra_container = team_container.find_next_sibling(
            lambda tag: tag.name == 'div'
                       and 'BoxScoreComponents__boxScoreExtraStats' in tag.get('class', [])
                       and 'PitchingExtra' in ' '.join(tag.get('class', []))
        )
        if extra_container:
            for line_div in extra_container.select('div'):
                label_span = line_div.select_one('span.Text__semibold')
                if not label_span:
                    continue
                stat_label = label_span.get_text(strip=True).rstrip(':')
                for stat_span in line_div.select('span.BoxScoreComponents__extraPlayerStat'):
                    text = stat_span.get_text(strip=True).rstrip(',')
                    parts = re.match(r"(.+?)\s+([0-9\-]+)$", text)
                    if not parts:
                        print(f"  [DEBUG] Regex failed to match: '{text}'")
                        continue
                    raw_name = parts.group(1).strip()
                    count_str = parts.group(2).strip()

                    pitcher_name_match = None
                    if raw_name in pitcher_names:
                        pitcher_name_match = raw_name
                    else:
                        match = difflib.get_close_matches(raw_name, pitcher_names, n=1, cutoff=0.6)
                        if match:
                            pitcher_name_match = match[0]

                    if not pitcher_name_match:
                        print(f"  [WARN] No match for extra stat pitcher name: '{raw_name}' in {pitcher_names}")
                        continue

                    if stat_label == 'Pitches-Strikes':
                        pitches, strikes = parse_pitches_strikes(count_str)
                        if pitches is not None and strikes is not None:
                            for rd in pitching_stats:
                                if rd['PitcherName'] == pitcher_name_match:
                                    rd['PitchesThrown'] = pitches
                                    rd['StrikesThrown'] = strikes
                                    print(f"[DEBUG] Updated {pitcher_name_match}: PitchesThrown={pitches}, StrikesThrown={strikes}")
                                    break
                    elif stat_label in ('Batters Faced', 'BF'):
                        try:
                            bf_count = int(count_str)
                        except ValueError:
                            print(f"  [WARN] Failed to parse batters faced: '{count_str}'")
                            continue
                        for rd in pitching_stats:
                            if rd['PitcherName'] == pitcher_name_match:
                                rd['BattersFaced'] = bf_count
                                print(f"[DEBUG] Updated {pitcher_name_match}: BattersFaced={bf_count}")
                                break
        else:
            print(f"[DEBUG] No extra stats container found")

        for rd in pitching_stats:
            if rd['PitchesThrown'] is None or rd['StrikesThrown'] is None:
                print(f"[WARN] Pitcher {rd['PitcherName']} missing pitch data: Pitches={rd['PitchesThrown']}, Strikes={rd['StrikesThrown']}")

        return pitching_stats

    away_batting_container = soup.select_one('div.BoxScore__awayLineup')
    away_batting = extract_batting(away_batting_container, away_team_id, away_team_name, 'AWAY') if away_batting_container else []
    home_batting_container = soup.select_one('div.BoxScore__homeLineup')
    home_batting = extract_batting(home_batting_container, home_team_id, home_team_name, 'HOME') if home_batting_container else []

    away_pitch_container = soup.select_one('div.BoxScore__awayPitching')
    away_pitching = extract_pitching(away_pitch_container, away_team_id, away_team_name, 'AWAY') if away_pitch_container else []
    home_pitch_container = soup.select_one('div.BoxScore__homePitching')
    home_pitching = extract_pitching(home_pitch_container, home_team_id, home_team_name, 'HOME') if home_pitch_container else []

    return away_batting, home_batting, away_pitching, home_pitching

# -------------------------------------------------------------------------
# 6) MAIN SCRAPER
# -------------------------------------------------------------------------
def main():
    conn = initialize_database()
    cursor = conn.cursor()

    options = webdriver.ChromeOptions()
    user_data_dir = os.path.abspath(CHROME_USER_DATA_DIR)
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument(f"--profile-directory={CHROME_PROFILE_DIR}")
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--remote-debugging-port=9222')
    options.add_argument('--window-size=1920,1080')

    service = ChromeService(ChromeDriverManager().install())
    service.log_level = 'WARN'
    try:
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e:
        print(f"[FATAL] Could not start Chrome driver: {e}")
        sys.exit(1)

    driver.implicitly_wait(IMPLICIT_WAIT)

    try:
        login_gamechanger(driver)
        print("[INFO] Waiting 5s for post-login…")
        time.sleep(5)

        for schedule_url in TEAM_SCHEDULE_URLS:
            driver.get(schedule_url)
            time.sleep(2)

            page_team_id = schedule_url.strip('/').split('/')[-2]
            html = driver.page_source
            games = parse_schedule_page(html, page_team_id)
            print(f"[INFO] Found {len(games)} games for team {page_team_id}")

            for g in games:
                if not g['box_score_url']:
                    continue

                ha = g['home_or_away']
                our_score = g['our_score']
                opp_score = g['opp_score']
                opp_id = ''  # still empty since we don't know opponent’s ID here

                if ha == 'HOME':
                    home_id = page_team_id
                    away_id = opp_id
                else:
                    home_id = opp_id
                    away_id = page_team_id

                date_part = g['game_date']
                game_id = f"{date_part}_{home_id}_vs_{away_id}"

                # ------------------------
                # Insert into GCGamesTmp4 (now with SourceTeamID)
                # ------------------------
                try:
                    cursor.execute("""
                        INSERT INTO GCGamesTmp4
                          (GameID, SourceTeamID, GameDate, HomeTeamID, AwayTeamID, HomeScore, AwayScore, BoxScoreURL)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        game_id,
                        page_team_id,
                        g['game_date'],
                        home_id,
                        away_id,
                        our_score if ha == 'HOME' else opp_score,
                        opp_score if ha == 'HOME' else our_score,
                        g['box_score_url']
                    )
                    conn.commit()
                    print(f"[INSERT] Games: {game_id}")
                except pyodbc.IntegrityError:
                    pass

                # ------------------------
                # Fetch and parse the box score
                # ------------------------
                try:
                    driver.get(g['box_score_url'])
                    time.sleep(2)
                    box_html = driver.page_source

                    away_bat, home_bat, away_pitch, home_pitch = parse_box_score(
                        box_html, home_id, away_id, game_id
                    )

                    # ------------------------
                    # Insert batting stats (with TeamMatch)
                    # ------------------------
                    for row in away_bat + home_bat:
                        try:
                            team_name   = row['TeamName'][:200]
                            opponent    = row['Opponent'][:200]
                            player_name = row['PlayerName'][:200]
                            position    = row['Position'][:20]
                            # TeamMatch = 'Yes' if this row’s TeamID == page_team_id, else 'No'
                            team_match = 'Yes' if row['TeamID'] == page_team_id else 'No'

                            cursor.execute("""
                                INSERT INTO GCBattingStatsTmp4
                                  (GameID, TeamID, TeamName, HomeOrAway, TeamMatch, Opponent, PlayerName, Position,
                                   AB, R, H, RBI, BB, SO, Doubles, Triples, HomeRuns, StolenBases, TotalBases)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                                row['GameID'],
                                row['TeamID'][:50],
                                team_name,
                                row['HomeOrAway'],
                                team_match,
                                opponent,
                                player_name,
                                position,
                                row['AB'],
                                row['R'],
                                row['H'],
                                row['RBI'],
                                row['BB'],
                                row['SO'],
                                row['Doubles'],
                                row['Triples'],
                                row['HomeRuns'],
                                row['StolenBases'],
                                row['TotalBases']
                            )
                        except pyodbc.IntegrityError:
                            continue
                        except Exception as e:
                            print(f"  [WARN] Batting insert failed: {e}")
                            continue

                    # ------------------------
                    # Insert pitching stats (with TeamMatch)
                    # ------------------------
                    for row in away_pitch + home_pitch:
                        try:
                            team_name    = row['TeamName'][:200]
                            opponent     = row['Opponent'][:200]
                            pitcher_name = row['PitcherName'][:200]
                            team_match   = 'Yes' if row['TeamID'] == page_team_id else 'No'

                            cursor.execute("""
                                INSERT INTO GCPitchingStatsTmp4
                                  (GameID, TeamID, TeamName, HomeOrAway, TeamMatch, Opponent, PitcherName,
                                   IP, HAllowed, RAllowed, ERAllowed, BBAllowed, Strikeouts,
                                   PitchesThrown, StrikesThrown, BattersFaced)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                                row['GameID'],
                                row['TeamID'][:50],
                                team_name,
                                row['HomeOrAway'],
                                team_match,
                                opponent,
                                pitcher_name,
                                row['IP'][:10],
                                row['HAllowed'],
                                row['RAllowed'],
                                row['ERAllowed'],
                                row['BBAllowed'],
                                row['Strikeouts'],
                                row['PitchesThrown'],
                                row['StrikesThrown'],
                                row['BattersFaced']
                            )
                        except pyodbc.IntegrityError:
                            continue
                        except Exception as e:
                            print(f"  [WARN] Pitching insert failed: {e}")
                            continue

                    conn.commit()
                    print(f"[DONE] Stats inserted for game {game_id}")

                except Exception:
                    print(f"\n[ERROR] Failed to parse box score for {game_id}:")
                    traceback.print_exc()
                    continue

    except Exception:
        print("\n[FATAL] Unexpected error:")
        traceback.print_exc()

    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass
        driver.quit()


if __name__ == "__main__":
    main()
