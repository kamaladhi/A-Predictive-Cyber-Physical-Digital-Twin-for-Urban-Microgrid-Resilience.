@echo off
echo.
echo ========================================
echo   🏙️  Digital Twin Dashboard Launcher
echo ========================================
echo.
echo Starting dashboard on http://localhost:8501
echo Press Ctrl+C to stop
echo.
python -m streamlit run dashboard/app.py --logger.level=error
