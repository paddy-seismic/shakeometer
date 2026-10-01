#!/usr/bin/env bash
# run_gauge_EI-DSB.sh
#
# Example run script for shakeometer: magnitude gauge only, for the
# INSN broadband station DSB (Dublin, Ireland; network EI), via the
# public EarthScope SeedLink server. Should work from anywhere with
# an internet connection.
#
# Usage: ./run_gauge_EI-DSB.sh
# Requirements: python3, obspy, PyQt5, pyqtgraph (see README.md)

python3 -u shakeometer.py \
    --seedlink_server rtserve.earthscope.org \
    --seedlink_streams "EI_DSB:HHZ" \
    --backtrace_time 1m \
    --seedlink_wait_buffer 10 \
    --update_time 0.05s \
    --inventory EI.DSB.xml \
    --magnitude_distance 100 \
    --logo_topleft     logos/INSN-Logo-RGB-Large.png \
    --logo_topright    logos/quakeshake_logo.png \
    --logo_bottomright  logos/GSI_logo.png \
    --logo_bottomleft logos/DIAS_RGB-2-1024x491.png \
    --fullscreen \
    --verbose
