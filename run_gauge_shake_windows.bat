@echo off
REM run_gauge_shake_windows.bat
REM
REM Example run script for shakeometer on a Windows laptop: magnitude
REM gauge only, for a Raspberry Shake connected to the laptop (e.g.
REM directly with an ethernet cable), using the Shake's SeedLink server.
REM
REM Set SHAKE_IP to the Shake's address: try rs.local first; if that
REM doesn't work, use its 169.254.x.x address (see README.md).
REM Set SHAKE to your station code, and put the matching StationXML file
REM (AM.<code>.xml) in this folder -- see README.md.
REM
REM Usage: double-click, or run run_gauge_shake_windows.bat in a command prompt
REM Requirements: python, obspy, PyQt5, pyqtgraph (see README.md)

set SHAKE_IP=rs.local
set SHAKE=RDF52
set STATION=AM_%SHAKE%
set CHANNEL=00EHZ
set INVENTORY=AM.%SHAKE%.xml

REM Run from this script's folder, so the logo and StationXML paths work
cd /d "%~dp0"

REM Add --verbose for debug output in this window.
python shakeometer.py ^
    --seedlink_server %SHAKE_IP% ^
    --seedlink_streams "%STATION%:%CHANNEL%" ^
    --backtrace_time 1m ^
    --seedlink_wait_buffer 2 ^
    --update_time 0.05s ^
    --inventory "%INVENTORY%" ^
    --magnitude_distance 10 ^
    --max_mag_keep_time 10 ^
    --logo_topleft     logos/INSN-Logo-RGB-Large.png ^
    --logo_topright    logos/quakeshake_logo.png ^
    --logo_bottomleft  logos/DIAS_RGB-2-1024x491.png ^
    --logo_bottomright logos/GSI_logo.png ^
    --fullscreen

REM Keep the window open if shakeometer exits with an error
if errorlevel 1 pause
