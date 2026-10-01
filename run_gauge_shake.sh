#!/usr/bin/env bash
# run_gauge_shake.sh
#
# Example run script for shakeometer: magnitude gauge only, for a
# Raspberry Shake (AM network), using the Shake's own local SeedLink
# server. Run it on the Shake itself (needs a desktop environment).
#
# Set SHAKE to your station code, and put the matching StationXML file
# (AM.<code>.xml) in this folder -- see README.md.
#
# The settings below are tuned for a Raspberry Pi; see README.md
# before lowering --update_time.
#
# Usage: ./run_gauge_shake.sh
# Requirements: python3, obspy, PyQt5, pyqtgraph (see README.md)

SHAKE="RDF52"               # change to your station code
STATION="AM_$SHAKE"
CHANNEL="00EHZ"             # Raspberry Shake channel (EHZ = vertical)
INVENTORY="AM.$SHAKE.xml"   # matching StationXML file

# Add --verbose for debug output in the terminal.
python3 -u shakeometer.py \
    --seedlink_server localhost \
    --seedlink_streams "${STATION}:${CHANNEL}" \
    --backtrace_time 20s \
    --update_time 0.09s \
    --inventory "${INVENTORY}" \
    --magnitude_distance 10 \
    --max_mag_keep_time 10 \
    --seedlink_wait_buffer 1 \
    --logo_topleft     logos/INSN-Logo-RGB-Large.png \
    --logo_topright    logos/quakeshake_logo.png \
    --logo_bottomright logos/GSI_logo.png \
    --logo_bottomleft  logos/DIAS_RGB-2-1024x491.png \
    --fullscreen
