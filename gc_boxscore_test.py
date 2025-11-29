import os
import time
from datetime import datetime

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# 👇 import your existing parsers from the main scraper file
from scrape_gc_schedules import parse_schedule_page, parse_box_score

# ---------------------------------------------------------------------
# Load env from your existing scrape_gc.env
# ---------------------------------------------------------------------
load_dotenv("scrape_gc.env")

EMAIL = os.getenv("GC_EMAIL", "").strip()
PASSWORD = os.getenv("GC_PASSWORD", "").strip()
CHROME_USER_DATA_DIR = os.getenv("CHROME_USER_DATA_DIR", "./chrome-user-data")
CHROME_PROFILE_DIR = os.getenv("CHROME_PROFILE_DIR", "Default")

if not EMAIL or not PASSWORD:
    raise RuntimeError("GC_EMAIL and GC_PASSWORD must be set in scrape_gc.env")

IMPLICIT_WAIT = 15

TEAM_SCHEDULE_URL = "https://web.gc.com/teams/QQpfJzkSUSyd/2025-fall-delmarva-aces-12u-east/schedule"


# ---------------------------------------------------------------------
# Login helper (same as before)
# ---------------------------------------------------------------------
def login_gamechanger(driver):
    driver.get("https://web.gc.com/login")
    time.sleep(1)

    current_url = driver.current_url
    if "web.gc.com/home" in current_url:
        print("[INFO] Already logged in; skipping login.")
        return

    try:
        WebDriverWait(driver, IMPLICIT_WAIT).until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
    except Exception:
        print("[WARN] Email input never appeared; assume already logged in.")
        return

    email_field = driver.find_element(By.NAME, "email")
    email_field.clear()
    email_field.send_keys(EMAIL)

    try:
        next_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
    except Exception:
        next_btn = driver.find_element(
            By.XPATH,
            "//button[contains(text(),'Continue') or contains(text(),'Next')]",
        )
    next_btn.click()

    try:
        WebDriverWait(driver, IMPLICIT_WAIT).until(
            EC.presence_of_element_located((By.NAME, "password"))
        )
    except Exception:
        print("[WARN] Password input not found after email; aborting login.")
        return

    password_field = driver.find_element(By.NAME, "password")
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


def scroll_to_bottom(driver, pause=1.0, max_loops=20):
    last_height = driver.execute_script("return document.body.scrollHeight")
    loops = 0
    while loops < max_loops:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
        loops += 1
    print(f"[INFO] Scrolling complete after {loops} loops.")


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
def main():
    options = webdriver.ChromeOptions()
    user_data_dir = os.path.abspath(CHROME_USER_DATA_DIR)
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument(f"--profile-directory={CHROME_PROFILE_DIR}")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--window-size=1920,1080")

    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.implicitly_wait(IMPLICIT_WAIT)

    try:
        login_gamechanger(driver)
        print("[INFO] Waiting 5s for post-login…")
        time.sleep(5)

        # 1) Load schedule and parse games (same as before)
        print(f"\n[INFO] Loading schedule: {TEAM_SCHEDULE_URL}")
        driver.get(TEAM_SCHEDULE_URL)
        time.sleep(3)
        scroll_to_bottom(driver, pause=1.0, max_loops=20)
        time.sleep(2)

        page_team_id = TEAM_SCHEDULE_URL.strip("/").split("/")[-2]
        html = driver.page_source
        games = parse_schedule_page(html, page_team_id)

        print(f"[INFO] Parsed {len(games)} games for team {page_team_id}")
        if not games:
            print("[ERROR] No games parsed, aborting.")
            return

        # 2) Pick the first game and open its BOX SCORE tab
        g = games[0]
        print("\n[INFO] Testing box score for first game:")
        print(
            f"  Date={g['game_date']} HA={g['home_or_away']} "
            f"Score={g['our_score']}-{g['opp_score']}"
        )

        # Make sure we go directly to the BOX SCORE tab
        box_url = g["box_score_url"]
        if not box_url.endswith("/box-score"):
            box_url = box_url.rstrip("/") + "/box-score"

        print(f"  URL={box_url}")

        driver.get(box_url)

        # Wait until AG-Grid rows are actually present
        try:
            WebDriverWait(driver, 15).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, "div[role='row'][row-index]")
            )
        except Exception:
            print("[WARN] Timed out waiting for AG-Grid rows on box score page.")

        time.sleep(1)  # small extra cushion
        box_html = driver.page_source

        # 3) Fake team IDs (just for testing parse_box_score)
        if g["home_or_away"] == "HOME":
            home_id = page_team_id
            away_id = "OPP"
        else:
            home_id = "OPP"
            away_id = page_team_id

        game_id = f"{g['game_date']}_{home_id}_vs_{away_id}"

        away_bat, home_bat, away_pitch, home_pitch = parse_box_score(
            box_html, home_id, away_id, game_id
        )

        # 4) Print summary
        print("\n[RESULTS] Box score parse summary:")
        print(f"  Away batting rows:    {len(away_bat)}")
        print(f"  Home batting rows:    {len(home_bat)}")
        print(f"  Away pitching rows:   {len(away_pitch)}")
        print(f"  Home pitching rows:   {len(home_pitch)}")

        # Show a couple sample rows so we can inspect
        if home_bat:
            print("\n  Sample HOME batting row:")
            print(home_bat[0])
        if away_bat:
            print("\n  Sample AWAY batting row:")
            print(away_bat[0])

        if home_pitch:
            print("\n  Sample HOME pitching row:")
            print(home_pitch[0])
        if away_pitch:
            print("\n  Sample AWAY pitching row:")
            print(away_pitch[0])

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
