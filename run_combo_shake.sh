#!/usr/bin/env bash
# run_combo_shake.sh
#
# Example run script for shakeometer: combo display (live waveform +
# spectrogram + magnitude gauge) for a Raspberry Shake (AM network),
# using the Shake's own local SeedLink server. Run it on the Shake
# itself (needs a desktop environment).
#
# Set SHAKE to your station code, and put the matching StationXML file
# (AM.<code>.xml) in this folder -- see README.md.
#
# The settings below (update times, spectrogram window and overlap)
# are tuned for a Raspberry Pi; see README.md before changing them.
#
# Usage: ./run_combo_shake.sh
# Requirements: python3, obspy, PyQt5, pyqtgraph (see README.md)

SHAKE="RDF52"               # change to your station code
STATION="AM_$SHAKE"
CHANNEL="00EHZ"             # Raspberry Shake channel (EHZ = vertical)
INVENTORY="AM.$SHAKE.xml"   # matching StationXML file

# Add --verbose for debug output in the terminal.
python3 -u shakeometer.py \
    --seedlink_server localhost \
    --seedlink_streams "${STATION}:${CHANNEL}" \
    --backtrace_time 30s \
    --update_time 0.15s \
    --combo_plot \
    --spec_update_time 1.5s \
    --amp_marker \
    --max_mag_keep_time 10 \
    --spec_colorscale auto \
    --spec_wlen 0.512 \
    --spec_overlap 50 \
    --inventory "${INVENTORY}" \
    --magnitude_distance 10 \
    --seedlink_wait_buffer 1 \
    --logo_topleft     logos/INSN-Logo-RGB-Large.png \
    --logo_topright    logos/quakeshake_logo.png \
    --logo_bottomright logos/GSI_logo.png \
    --logo_bottomleft  logos/DIAS_RGB-2-1024x491.png \
    --fullscreen
