import os
import time
import re
from datetime import datetime
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


from bs4 import BeautifulSoup
import pyodbc

load_dotenv("scrape_gc.env")

GC_EMAIL = os.getenv("GC_EMAIL")
GC_PASSWORD = os.getenv("GC_PASSWORD")
SQL_SERVER = os.getenv("SQL_SERVER")
SQL_DATABASE = os.getenv("SQL_DATABASE")
CHROME_USER_DATA_DIR = os.getenv("CHROME_USER_DATA_DIR")
CHROME_PROFILE_DIR = os.getenv("CHROME_PROFILE_DIR")

if not GC_EMAIL or not GC_PASSWORD:
    raise RuntimeError("GC_EMAIL and GC_PASSWORD must be set in .env")


def get_db():
    conn_str = (
        "DRIVER={SQL Server};"
        f"SERVER={SQL_SERVER};"
        f"DATABASE={SQL_DATABASE};"
        "Trusted_Connection=yes;"
    )
    return pyodbc.connect(conn_str)


def normalize_text(t):
    if t is None:
        return ""
    return re.sub(r"\s+", " ", t).strip()


def to_int(value):
    if value is None:
        return 0
    v = str(value).strip()
    if v in ["", "-", None]:
        return 0
    try:
        return int(v)
    except:
        try:
            return int(float(v))
        except:
            return 0


from selenium.webdriver.chrome.service import Service as ChromeService


def get_driver():
    chrome_options = webdriver.ChromeOptions()

    # Minimal + stable:
    # - No user-data-dir
    # - No profile-directory
    # - No remote-debugging-port
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(options=chrome_options)
    driver.implicitly_wait(10)
    return driver


def login_gamechanger(driver):
    """
    Open the GameChanger login page and let the user log in manually.
    This avoids brittle selectors when GC changes their login form.
    """
    print("[INFO] Opening GameChanger login page for manual login…")
    driver.get("https://web.gc.com/login")

    print(
        "\n[ACTION REQUIRED] In the Chrome window that just opened:\n"
        "  1) Log into GameChanger with your email/password.\n"
        "  2) Navigate to your normal GC home/teams page (if it doesn't auto-redirect).\n"
        "  3) Once you can see your teams, come back to this console.\n"
    )
    input("[INFO] When you are fully logged in, press ENTER here to continue... ")

    # Optional: log the URL so we can see roughly where we landed
    try:
        current_url = driver.current_url
        print(f"[INFO] Continuing after manual login (current URL: {current_url})")
    except Exception:
        print("[WARN] Could not read current URL after manual login, continuing anyway.")


def scroll_to_bottom(driver, pause=1.0):
    last_height = driver.execute_script("return document.body.scrollHeight")
    loops = 0

    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        loops += 1

        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

    print(f"[INFO] Scrolling complete after {loops} loops.")


def parse_schedule_page(driver, schedule_url):
    print(f"[INFO] Loading schedule: {schedule_url}")
    driver.get(schedule_url)
    # Give the SPA time to load the schedule grid
    time.sleep(5)
    scroll_to_bottom(driver)

    html = driver.page_source
    soup = BeautifulSoup(html, "lxml")

    games = []

    # Each month section has the list of day rows
    month_sections = soup.select("div.ScheduleListByMonth__eventMonth")
    print(f"[DEBUG] Found {len(month_sections)} month sections on schedule page.")

    for section in month_sections:
        # Find the corresponding month/year header just before this section
        header = section.find_previous("div", class_="ScheduleSection__sectionHeader")
        month_year_text = ""
        if header:
            title_span = header.select_one("span.ScheduleSection__sectionTitle")
            if title_span:
                month_year_text = normalize_text(title_span.get_text())

        # Each day row contains one or more events
        day_rows = section.select("div.ScheduleListByMonth__dayRow")
        for day_row in day_rows:
            # Day of month (e.g., '15')
            date_text_el = day_row.select_one("div.ScheduleListByMonth__dateText")
            day_text = normalize_text(date_text_el.get_text()) if date_text_el else ""

            # Each actual game/event is an <a> with this class
            event_links = day_row.select("a.ScheduleListByMonth__event")
            for a in event_links:
                href = a.get("href", "")
                if not href:
                    continue

                game_url = href
                if game_url.startswith("/"):
                    game_url = "https://web.gc.com" + game_url

                # Game title text (includes "@ Opponent" or "vs. Opponent")
                title_span = a.select_one(".ScheduleListByMonth__title .Text__text")
                title_text = normalize_text(title_span.get_text()) if title_span else ""

                # Score or time text (e.g. "W 13-2", "L 4-8", or a time if not played)
                score_span = a.select_one(".ScheduleListByMonth__scoreOrTimeText")
                score_text = normalize_text(score_span.get_text()) if score_span else ""

                # Derive Home/Away from the title:
                #   "@ Opponent" -> AWAY
                #   "vs. Opponent" -> HOME
                ha = ""
                if title_text.startswith("@"):
                    ha = "AWAY"
                elif title_text.lower().startswith("vs."):
                    ha = "HOME"

                # Build a simple date label like "October 15 2025"
                if month_year_text and day_text:
                    date_label = f"{month_year_text} {day_text}"
                else:
                    date_label = month_year_text or day_text

                games.append(
                    {
                        "url": game_url,
                        "date": date_label,
                        "score": score_text,
                        "ha": ha,
                    }
                )

    print(f"[INFO] Parsed {len(games)} games for team {schedule_url.split('/')[-2]}")

    # If we somehow still have zero games, dump HTML for debugging again
    if not games:
        try:
            with open("debug_schedule.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("[WARN] No games parsed; wrote page HTML to debug_schedule.html")
        except Exception as e:
            print(f"[WARN] Failed to write debug_schedule.html: {e}")

    return games


def apply_extra_stats_from_summary(soup, batting_rows):
    """
    Parse the 'extra stats' panel under the box score, e.g.:

    <div class="BoxScoreComponents__boxScoreExtraStats ...">
      <div>
        <span>2B: </span>
        <span>Brody Pegelow, </span>
        <span>Mason Maloney, </span>
        <span>Wyatt Wiltbank</span>
      </div>
      <div>
        <span>HR: </span>
        <span>Raiden Sheets</span>
      </div>
      <div>
        <span>TB: </span>
        <span>Raiden Sheets 5, </span>
        <span>Brody Pegelow 3, </span>
        ...
      </div>
      <div>
        <span>SB: </span>
        <span>Declan Soares 4, </span>
        <span>Jake Coulbourne 3, </span>
        <span>Ayden Jester, </span>
        ...
      </div>
      ...
    </div>

    and merge into batting_rows (Doubles / Triples / HomeRuns / StolenBases / TotalBases).
    """

    label_to_field = {
        "2B": "Doubles",
        "3B": "Triples",
        "HR": "HomeRuns",
        "SB": "StolenBases",
        "TB": "TotalBases",
    }

    if not batting_rows:
        return

    # Helper to "clean" player names from the grid:
    # strip jersey numbers, positions in parentheses, etc.
    def clean_name(name):
        if not name:
            return ""
        # Drop everything from first "(" onwards (positions)
        name = re.split(r"\(", name, 1)[0]
        # Drop jersey number fragments like "#54"
        name = re.sub(r"#\d+", "", name)
        # Collapse spaces
        return normalize_text(name)

    # Index batting rows by cleaned name
    cleaned_index = {}
    for row in batting_rows:
        c = clean_name(row.get("PlayerName", ""))
        if not c:
            continue
        cleaned_index.setdefault(c.lower(), []).append(row)

    def find_batting_rows_for_token(token_name):
        """Best-effort match from a summary token to one or more batting rows."""
        token_clean = clean_name(token_name).lower()
        if not token_clean:
            return []

        # Exact cleaned name match
        if token_clean in cleaned_index:
            return cleaned_index[token_clean]

        # Fallback: all token words must appear in the cleaned batting name
        token_words = token_clean.split()
        matches = []
        for cname, rows in cleaned_index.items():
            if all(w in cname for w in token_words):
                matches.extend(rows)
        return matches

    # Find all extra-stats panels for home/away
    panels = soup.select("div.BoxScoreComponents__boxScoreExtraStats")
    for panel in panels:
        # Each direct child <div> holds one stat line (2B, HR, TB, SB, etc.)
        for row_div in panel.find_all("div", recursive=False):
            spans = row_div.find_all("span", class_="Text__text")
            if not spans:
                continue

            # First span is the label, e.g. "2B:" or "HR:" or "SB:"
            label_raw = normalize_text(spans[0].get_text())
            label_key = label_raw.replace(":", "").strip()  # "2B", "HR", "SB", "TB", etc.

            if label_key not in label_to_field:
                # Ignore HBP, SF, CS, E for now
                continue

            field_name = label_to_field[label_key]

            # Remaining spans are player tokens, e.g. "Raiden Sheets 5,", "Ayden Jester,"
            for tok_span in spans[1:]:
                tok = normalize_text(tok_span.get_text())
                if not tok:
                    continue

                # Remove trailing commas
                tok = re.sub(r",$", "", tok)

                # Look for trailing integer (e.g. "Declan Soares 4")
                m = re.search(r"\s+(\d+)$", tok)
                if m:
                    count = int(m.group(1))
                    name_part = tok[: m.start()].strip()
                else:
                    count = 1
                    name_part = tok.strip()

                if not name_part:
                    continue

                matched_rows = find_batting_rows_for_token(name_part)
                for row in matched_rows:
                    current_val = row.get(field_name) or 0
                    row[field_name] = current_val + count


def parse_box_score(driver, boxscore_url, game_id, home_team, away_team):
    print(f"[INFO] Loading box score: {boxscore_url}")
    driver.get(boxscore_url)

    # Wait up to 15s for an AG-Grid body to appear.
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.ag-body-viewport div.ag-center-cols-container")
            )
        )
    except TimeoutException:
        print(f"[WARN] Timed out waiting for AG-Grid on game {game_id}")
        # Dump HTML so we can debug this game specifically later
        try:
            html = driver.page_source
            fname = f"debug_boxscore_{game_id}.html"
            with open(fname, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"[DEBUG] Saved box score HTML to {fname}")
        except Exception as ex:
            print(f"[WARN] Failed to save debug HTML for {game_id}: {ex}")
        return [], []

    soup = BeautifulSoup(driver.page_source, "lxml")

    bodies = soup.select("div.ag-body-viewport div.ag-center-cols-container")
    if not bodies:
        print(f"[DEBUG] No AG-Grid containers found for game {game_id}.")
        # Also save HTML here for debugging
        try:
            html = driver.page_source
            fname = f"debug_boxscore_{game_id}.html"
            with open(fname, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"[DEBUG] Saved box score HTML to {fname}")
        except Exception as ex:
            print(f"[WARN] Failed to save debug HTML for {game_id}: {ex}")
        return [], []

    # Use first AG Grid body – contains both batting & pitching rows we classify
    html_rows = bodies[0].select("div.ag-row")
    print(f"[DEBUG] Found {len(html_rows)} AG-Grid rows on box score page.")

    batting_rows = []
    pitching_rows = []

    for row in html_rows:
        cells = row.select("div.ag-cell-value")
        if not cells:
            continue

        text_cells = [normalize_text(c.text) for c in cells]

        # BATTING:
        # Expected order:
        # Player | AB | R | H | RBI | BB | SO | 2B | 3B | HR | SB | TB
        if len(text_cells) >= 7:
            batting_rows.append(
                {
                    "PlayerName": text_cells[0],
                    "AB": to_int(text_cells[1]),
                    "R": to_int(text_cells[2]),
                    "H": to_int(text_cells[3]),
                    "RBI": to_int(text_cells[4]),
                    "BB": to_int(text_cells[5]),
                    "SO": to_int(text_cells[6]),
                    "Doubles": to_int(text_cells[7]) if len(text_cells) > 7 else 0,
                    "Triples": to_int(text_cells[8]) if len(text_cells) > 8 else 0,
                    "HomeRuns": to_int(text_cells[9]) if len(text_cells) > 9 else 0,
                    "StolenBases": to_int(text_cells[10]) if len(text_cells) > 10 else 0,
                    "TotalBases": to_int(text_cells[11]) if len(text_cells) > 11 else 0,
                }
            )

        # PITCHING:
        # Pitcher | IP | H | R | ER | BB | SO | (OPTIONAL) Pitches | Strikes | BF
        if len(text_cells) >= 7 and "." in text_cells[1]:
            pitching_rows.append(
                {
                    "PitcherName": text_cells[0],
                    "IP": text_cells[1],
                    "HAllowed": to_int(text_cells[2]),
                    "RAllowed": to_int(text_cells[3]),
                    "ERAllowed": to_int(text_cells[4]),
                    "BBAllowed": to_int(text_cells[5]),
                    "Strikeouts": to_int(text_cells[6]),
                    "PitchesThrown": to_int(text_cells[7]) if len(text_cells) > 7 else None,
                    "StrikesThrown": to_int(text_cells[8]) if len(text_cells) > 8 else None,
                    "BattersFaced": to_int(text_cells[9]) if len(text_cells) > 9 else None,
                }
            )

    print(
        f"[DEBUG] Classified rows: batting={len(batting_rows)}, pitching={len(pitching_rows)}"
    )
    return batting_rows, pitching_rows


def insert_game_and_stats(conn, game_id, game_info, batting, pitching, team_id):
    cursor = conn.cursor()

    # --- Insert into GCGamesTmp4 using only columns that actually exist ---
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'GCGamesTmp4'"
    )
    existing_game_cols = {row[0] for row in cursor.fetchall()}

    game_cols = []
    game_vals = []

    if "GameID" in existing_game_cols:
        game_cols.append("GameID")
        game_vals.append(game_id)

    if "GameDate" in existing_game_cols:
        game_cols.append("GameDate")
        game_vals.append(game_info.get("date"))

    if "Score" in existing_game_cols:
        game_cols.append("Score")
        game_vals.append(game_info.get("score"))

    if "HomeOrAway" in existing_game_cols:
        game_cols.append("HomeOrAway")
        game_vals.append(game_info.get("ha"))

    if "URL" in existing_game_cols:
        game_cols.append("URL")
        game_vals.append(game_info.get("url"))

    if game_cols:
        placeholders = ", ".join(["?"] * len(game_cols))
        col_list = ", ".join(game_cols)
        insert_sql = f"INSERT INTO GCGamesTmp4 ({col_list}) VALUES ({placeholders})"
        try:
            cursor.execute(insert_sql, game_vals)
        except pyodbc.IntegrityError:
            # Duplicate GameID (PRIMARY KEY) – ignore and continue
            pass

    # --- Prepare dynamic insert for batting stats ---
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'GCBattingStatsTmp4'"
    )
    existing_bat_cols = {row[0] for row in cursor.fetchall()}

    bat_cols = []
    if "GameID" in existing_bat_cols:
        bat_cols.append("GameID")
    if "TeamID" in existing_bat_cols:
        bat_cols.append("TeamID")
    if "PlayerName" in existing_bat_cols:
        bat_cols.append("PlayerName")
    if "AB" in existing_bat_cols:
        bat_cols.append("AB")
    if "R" in existing_bat_cols:
        bat_cols.append("R")
    if "H" in existing_bat_cols:
        bat_cols.append("H")
    if "RBI" in existing_bat_cols:
        bat_cols.append("RBI")
    if "BB" in existing_bat_cols:
        bat_cols.append("BB")
    if "SO" in existing_bat_cols:
        bat_cols.append("SO")
    if "Doubles" in existing_bat_cols:
        bat_cols.append("Doubles")
    if "Triples" in existing_bat_cols:
        bat_cols.append("Triples")
    if "HomeRuns" in existing_bat_cols:
        bat_cols.append("HomeRuns")
    if "StolenBases" in existing_bat_cols:
        bat_cols.append("StolenBases")
    if "TotalBases" in existing_bat_cols:
        bat_cols.append("TotalBases")

    if bat_cols:
        bat_placeholders = ", ".join(["?"] * len(bat_cols))
        bat_col_list = ", ".join(bat_cols)
        bat_insert_sql = f"INSERT INTO GCBattingStatsTmp4 ({bat_col_list}) VALUES ({bat_placeholders})"

        for row in batting:
            bat_vals = []
            for col in bat_cols:
                if col == "GameID":
                    bat_vals.append(game_id)
                elif col == "TeamID":
                    bat_vals.append(team_id)
                elif col == "PlayerName":
                    bat_vals.append(row["PlayerName"])
                elif col == "AB":
                    bat_vals.append(row["AB"])
                elif col == "R":
                    bat_vals.append(row["R"])
                elif col == "H":
                    bat_vals.append(row["H"])
                elif col == "RBI":
                    bat_vals.append(row["RBI"])
                elif col == "BB":
                    bat_vals.append(row["BB"])
                elif col == "SO":
                    bat_vals.append(row["SO"])
                elif col == "Doubles":
                    bat_vals.append(row["Doubles"])
                elif col == "Triples":
                    bat_vals.append(row["Triples"])
                elif col == "HomeRuns":
                    bat_vals.append(row["HomeRuns"])
                elif col == "StolenBases":
                    bat_vals.append(row["StolenBases"])
                elif col == "TotalBases":
                    bat_vals.append(row["TotalBases"])
            cursor.execute(bat_insert_sql, bat_vals)

    # --- Prepare dynamic insert for pitching stats ---
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'GCPitchingStatsTmp4'"
    )
    existing_pit_cols = {row[0] for row in cursor.fetchall()}

    pit_cols = []
    if "GameID" in existing_pit_cols:
        pit_cols.append("GameID")
    if "TeamID" in existing_pit_cols:
        pit_cols.append("TeamID")
    if "PitcherName" in existing_pit_cols:
        pit_cols.append("PitcherName")
    if "IP" in existing_pit_cols:
        pit_cols.append("IP")
    if "HAllowed" in existing_pit_cols:
        pit_cols.append("HAllowed")
    if "RAllowed" in existing_pit_cols:
        pit_cols.append("RAllowed")
    if "ERAllowed" in existing_pit_cols:
        pit_cols.append("ERAllowed")
    if "BBAllowed" in existing_pit_cols:
        pit_cols.append("BBAllowed")
    if "Strikeouts" in existing_pit_cols:
        pit_cols.append("Strikeouts")
    if "PitchesThrown" in existing_pit_cols:
        pit_cols.append("PitchesThrown")
    if "StrikesThrown" in existing_pit_cols:
        pit_cols.append("StrikesThrown")
    if "BattersFaced" in existing_pit_cols:
        pit_cols.append("BattersFaced")

    if pit_cols:
        pit_placeholders = ", ".join(["?"] * len(pit_cols))
        pit_col_list = ", ".join(pit_cols)
        pit_insert_sql = f"INSERT INTO GCPitchingStatsTmp4 ({pit_col_list}) VALUES ({pit_placeholders})"

        for row in pitching:
            pit_vals = []
            for col in pit_cols:
                if col == "GameID":
                    pit_vals.append(game_id)
                elif col == "TeamID":
                    pit_vals.append(team_id)
                elif col == "PitcherName":
                    pit_vals.append(row["PitcherName"])
                elif col == "IP":
                    pit_vals.append(row["IP"])
                elif col == "HAllowed":
                    pit_vals.append(row["HAllowed"])
                elif col == "RAllowed":
                    pit_vals.append(row["RAllowed"])
                elif col == "ERAllowed":
                    pit_vals.append(row["ERAllowed"])
                elif col == "BBAllowed":
                    pit_vals.append(row["BBAllowed"])
                elif col == "Strikeouts":
                    pit_vals.append(row["Strikeouts"])
                elif col == "PitchesThrown":
                    pit_vals.append(row["PitchesThrown"])
                elif col == "StrikesThrown":
                    pit_vals.append(row["StrikesThrown"])
                elif col == "BattersFaced":
                    pit_vals.append(row["BattersFaced"])
            cursor.execute(pit_insert_sql, pit_vals)

    conn.commit()


def main():
    driver = get_driver()
    login_gamechanger(driver)

    conn = get_db()

    TEAM_SCHEDULE_URLS = [
        "https://web.gc.com/teams/QQpfJzkSUSyd/2025-fall-delmarva-aces-12u-east/schedule"
    ]

    for url in TEAM_SCHEDULE_URLS:
        team_slug = url.split("/")[-2]  # e.g. "2025-fall-delmarva-aces-12u-east"
        team_id = url.split("/")[4]     # GC team ID from URL (QQpfJzkSUSyd)
        schedule = parse_schedule_page(driver, url)

        for g in schedule:
            ha = (g["ha"] or "").upper()
            if ha == "HOME":
                home_team = team_slug
                away_team = "OPP"
            elif ha == "AWAY":
                home_team = "OPP"
                away_team = team_slug
            else:
                # Fallback if HA missing
                home_team = team_slug
                away_team = "OPP"

            game_id = f"{g['date']}_{home_team}_vs_{away_team}".replace(" ", "_")

            print(f"[INSERT] Games: {game_id}")

            if g["url"]:
                bs_url = g["url"] + "/box-score"
                batting, pitching = parse_box_score(driver, bs_url, game_id, home_team, away_team)
                print(
                    f"[INSERT] Stats for {game_id}: "
                    f"{len(batting)} batting rows, {len(pitching)} pitching rows"
                )
                insert_game_and_stats(conn, game_id, g, batting, pitching, team_id)

    driver.quit()
    conn.close()


if __name__ == "__main__":
    main()
