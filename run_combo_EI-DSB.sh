#!/usr/bin/env bash
# run_combo_EI-DSB.sh
#
# Example run script for shakeometer: combo display (live waveform +
# spectrogram + magnitude gauge) for the INSN broadband station DSB
# (Dublin, Ireland; network EI), via the public EarthScope SeedLink
# server. Should work from anywhere with an internet connection.
#
# Usage: ./run_combo_EI-DSB.sh
# Requirements: python3, obspy, PyQt5, pyqtgraph (see README.md)

python3 -u shakeometer.py \
    --seedlink_server rtserve.earthscope.org \
    --seedlink_streams "EI_DSB:HHZ" \
    --backtrace_time 1m \
    --seedlink_wait_buffer 5 \
    --combo_plot \
    --update_time 0.04s \
    --spec_update_time 1s \
    --spec_bandpass 1.0 40 \
    --spec_wlen 1.0 \
    --spec_overlap 95 \
    --spec_colorscale auto \
    --inventory EI.DSB.xml \
    --magnitude_distance 100 \
    --max_mag_keep_time 10 \
    --amp_marker \
    --logo_topleft     logos/INSN-Logo-RGB-Large.png \
    --logo_topright    logos/quakeshake_logo.png \
    --logo_bottomleft  logos/GSI_logo.png \
    --logo_bottomright logos/DIAS_RGB-2-1024x491.png \
    --fullscreen \
    --verbose
