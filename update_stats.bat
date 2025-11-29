@echo off
title Aces Analytics - Update Stats
cd /d C:\GC_Scraper

echo ============================================================
echo   ACES ANALYTICS - AUTOMATED STATS UPDATE
echo   Starting update process...
echo ============================================================
echo.

:: 1. Pull latest from GitHub
echo [1/4] Pulling latest updates from GitHub...
git pull
echo.

:: 2. Run all scraper scripts
echo [2/4] Running GameChanger scrapers...
python scrape_gc_stats.py
python scrape_gc_schedules.py
python scrape_tournament_threat_board.py
echo.

:: 3. Stage and commit ONLY the CSV updates
echo [3/4] Committing updated CSVs...
git add data\hitting data\pitching data\tournament
git commit -m "Automated stats update - %date% %time%"
echo.

:: 4. Push changes up to GitHub (Render will redeploy automatically)
echo [4/4] Pushing changes to GitHub...
git push
echo.

echo ============================================================
echo   Update complete!
echo   Your Render deployment is rebuilding with new stats.
echo ============================================================
echo.
pause
