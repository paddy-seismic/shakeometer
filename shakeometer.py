#!/usr/bin/env python3
"""
shakeometer -- real-time local-magnitude display for seismology outreach.

Reads a live seismogram from a SeedLink server and shows the local
magnitude of the ground motion on a large speedometer-style gauge,
either on its own or (--combo_plot) under a live waveform and
spectrogram.

Run with --help for all options; see README.md for examples.
"""
import argparse
import gc
import logging
import os
import sys
import threading
import time

import numpy as np
from obspy import Stream, Trace, read_inventory
from obspy.core import UTCDateTime
from obspy.clients.seedlink.slpacket import SLPacket
from obspy.clients.seedlink import SLClient

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, QTimer

import pyqtgraph as pg

from amps import get_local_mag, peak_to_trough_amplitude, get_wa_waveform
from gauge import GaugeWidget
from waveform import create_waveform_plot, add_amp_marker, update_waveform
from spectrogram import SpectrogramPanel
from logos import load_pixmap, make_logo_label, position_corner_logo, size_side_logo

logger = logging.getLogger(__name__)

WINDOW_TITLE = "SHAKEOMETER: REAL-TIME MAGNITUDE"
HERE = os.path.dirname(os.path.abspath(__file__))

# See the matching note in amps.py: skip obspy's provenance-logging
# decorator on these per-tick calls. Same code, same results.
_detrend = getattr(Trace.detrend, "__wrapped__", None) or \
    (lambda tr, *a, **kw: Trace.detrend(tr, *a, **kw))
_taper = getattr(Trace.taper, "__wrapped__", None) or \
    (lambda tr, *a, **kw: Trace.taper(tr, *a, **kw))


def _paint_opaque_white(widget):
    """Give a plain container widget an explicit white background."""
    pal = widget.palette()
    pal.setColor(widget.backgroundRole(), QtGui.QColor("white"))
    widget.setPalette(pal)
    widget.setAutoFillBackground(True)


# --------------------------------------------------------------------------
# SeedLink client (runs in a background thread)
# --------------------------------------------------------------------------
class SeedlinkUpdater(SLClient):
    def __init__(self, stream, lock):
        super().__init__()
        self.stream = stream
        self.lock = lock

    # obspy >= 1.4 calls packet_handler, older versions packetHandler
    def packet_handler(self, count, slpack):
        return self.packetHandler(count, slpack)

    def packetHandler(self, count, slpack):
        if slpack is None or (slpack == SLPacket.SLNOPACKET) or \
                (slpack == SLPacket.SLERROR):
            return False
        pk_type = slpack.get_type()
        if pk_type == SLPacket.TYPE_SLINF:
            return False
        if pk_type == SLPacket.TYPE_SLINFT:
            logger.info("Complete INFO:" + self.slconn.getInfoString())
            return self.infolevel is not None
        trace = slpack.get_trace()
        if trace is None:
            logger.info(self.__class__.__name__ + ": blockette contains no trace")
            return False
        with self.lock:
            self.stream += trace
            self.stream.merge(-1)
            for tr in self.stream:
                tr.stats.processing = []
        return False

    def getTraceIDs(self):
        ids = []
        for stream in self.slconn.get_streams():
            net, sta = stream.net, stream.station
            for selector in stream.get_selectors():
                loc = "" if len(selector) == 3 else selector[:2]
                cha = selector[-3:]
                ids.append(".".join((net, sta, loc, cha)))
        ids.sort()
        return ids


def _parse_time_with_suffix_to_seconds(timestring):
    try:
        return float(timestring)
    except ValueError:
        timestring, suffix = timestring[:-1], timestring[-1].lower()
        mult = {'s': 1.0, 'm': 60.0, 'h': 3600.0, 'd': 3600.0 * 24}[suffix]
        return float(timestring) * mult


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class ShakeometerWindow(QtWidgets.QMainWindow):
    def __init__(self, args, stream, lock, trace_ids):
        super().__init__()
        self.args = args
        self.stream = stream
        self.lock = lock
        self.ids = trace_ids
        self.combo_plot = args.combo_plot

        self.inv = read_inventory(args.inventory)

        self.backtrace = args.backtrace_time
        self.magnitude_window = args.magnitude_window
        self.maximum_magnitude = args.maximum_magnitude
        self.magnitude_distance = args.magnitude_distance
        self.max_mag_keep_time = args.max_mag_keep_time
        self.seedlink_wait_buffer = args.seedlink_wait_buffer
        self.amp_marker = args.amp_marker
        self.allow_negative_mags = args.allow_negative_mags
        self.spec_bandpass = args.spec_bandpass
        self.spec_wlen = args.spec_wlen
        self.spec_overlap = args.spec_overlap / 100.0
        self.spec_colorscale = args.spec_colorscale
        self.spec_vmin = args.spec_vmin
        self.spec_vmax = args.spec_vmax
        self.spec_update_every = max(1, round(args.spec_update_time / args.update_time))

        self.mag = 0.0
        self.mmax = 0.0
        self.mmax_today = 0.0
        self.tmax = UTCDateTime()
        self.tmax_today = UTCDateTime()
        self._magtime = None  # time of the current maximum's peak-to-trough
        self._tick_count = 0
        self._combo_tick = 0
        self._empty_slice_skips = 0
        self._last_tick_error = None
        self._repeated_error_count = 0

        self.setWindowTitle(WINDOW_TITLE)
        icon_path = os.path.join(HERE, "shakeometer.png")
        if os.path.isfile(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))

        self._corner_logos = {}  # corner -> (QLabel, original QPixmap)
        self._side_logos = {}    # 'left'/'right' -> (QLabel, original QPixmap)

        if self.combo_plot:
            self._build_combo_ui()
        else:
            self._build_gauge_only_ui()

        self._bind_shortcuts()

        # Python's garbage collector could otherwise finalise a Qt
        # object from the SeedLink thread, which Qt doesn't support.
        # Collect periodically on this (the GUI) thread instead.
        gc.disable()
        self._gc_timer = QTimer(self)
        self._gc_timer.timeout.connect(gc.collect)
        self._gc_timer.start(30000)

        self.setGeometry(args.x_position, args.y_position, args.x_size, args.y_size)
        if args.without_decoration:
            self.setWindowFlags(Qt.FramelessWindowHint)
        if args.fullscreen:
            self._toggle_fullscreen()

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(int(args.update_time * 1000))

    # -- UI construction --------------------------------------------
    def _new_gauge(self):
        gauge = GaugeWidget()
        gauge.set_params(int(np.ceil(self.maximum_magnitude)), self.magnitude_distance)
        return gauge

    def _build_gauge_only_ui(self):
        self.gauge = self._new_gauge()
        self.setCentralWidget(self.gauge)
        for corner in ('topleft', 'topright', 'bottomleft', 'bottomright'):
            self._add_corner_logo(corner)

    def _build_combo_ui(self):
        central = QtWidgets.QWidget()
        _paint_opaque_white(central)
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        # Top panel: waveform over spectrogram (40:60), time axes linked
        top = QtWidgets.QWidget()
        _paint_opaque_white(top)
        top_layout = QtWidgets.QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(2)

        self.waveform_plot, self._wave_handles = create_waveform_plot()
        if self.amp_marker:
            add_amp_marker(self.waveform_plot, self._wave_handles)
        self.spec_panel = SpectrogramPanel()
        self.waveform_plot.setXLink(self.spec_panel.plot_widget)
        top_layout.addWidget(self.waveform_plot, stretch=40)
        top_layout.addWidget(self.spec_panel.plot_widget, stretch=60)

        # Bottom row: [left logo] gauge [right logo]
        gauge_row = QtWidgets.QWidget()
        _paint_opaque_white(gauge_row)
        self._gauge_row_layout = QtWidgets.QHBoxLayout(gauge_row)
        self._gauge_row_layout.setContentsMargins(0, 0, 0, 0)
        self._gauge_row_layout.setSpacing(0)
        self.gauge = self._new_gauge()

        # --logo_topleft/topright become the side logos in combo mode
        for side, path in (('left', self.args.logo_topleft),
                           ('right', self.args.logo_topright)):
            if side == 'right':
                self._gauge_row_layout.addWidget(self.gauge, 1)
            pm = load_pixmap(path)
            if pm is not None:
                label = make_logo_label(gauge_row, pm)
                self._gauge_row_layout.addWidget(label, 0)
                self._side_logos[side] = (label, pm)

        # Top panel : gauge row = 40:60
        outer.addWidget(top, stretch=40)
        outer.addWidget(gauge_row, stretch=60)

        # Size the side logos now, for the requested window size. Left at
        # their full-resolution size they'd dominate the first layout
        # pass and squeeze the gauge.
        init_size = QtCore.QSize(self.args.x_size, self.args.y_size)
        for label, pm in self._side_logos.values():
            size_side_logo(label, init_size, pm)

        for corner in ('bottomleft', 'bottomright'):
            self._add_corner_logo(corner)

        # UTC clock, top right
        self._clock_label = QtWidgets.QLabel(self)
        self._clock_label.setStyleSheet("color: black;")
        f = QtGui.QFont()
        f.setPointSize(11)
        self._clock_label.setFont(f)
        self._clock_label.raise_()

    def _add_corner_logo(self, corner):
        pm = load_pixmap(getattr(self.args, 'logo_%s' % corner, None))
        if pm is None:
            return
        label = make_logo_label(self, pm)
        label.raise_()
        self._corner_logos[corner] = (label, pm)

    def _bind_shortcuts(self):
        QtWidgets.QShortcut(QtGui.QKeySequence("f"), self, self._toggle_fullscreen)
        QtWidgets.QShortcut(QtGui.QKeySequence("q"), self, self.close)
        QtWidgets.QShortcut(QtGui.QKeySequence("Escape"), self, self.close)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # -- overlay geometry ---------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_overlays()

    def _reposition_overlays(self):
        size = self.size()
        for corner, (label, pm) in self._corner_logos.items():
            position_corner_logo(label, size, corner, pm)
        if self.combo_plot:
            margin = int(0.015 * size.width())
            self._gauge_row_layout.setContentsMargins(margin, 0, margin, 0)
            for label, pm in self._side_logos.values():
                size_side_logo(label, size, pm)
            self._place_clock()

    def _place_clock(self):
        self._clock_label.adjustSize()
        self._clock_label.move(self.width() - self._clock_label.width() - 8, 4)

    # -- tick loop ------------------------------------------------------
    def _tick(self):
        now = UTCDateTime()
        start_time = now - self.backtrace
        stop_time = now

        with self.lock:
            self.stream.trim(starttime=start_time - 120, nearest_sample=False)
            stream = self.stream.copy()

        try:
            # Guarded so the (non-trivial) split() only runs with --verbose
            if logger.isEnabledFor(logging.INFO):
                logger.info(str(stream.split()))
            if not stream:
                raise Exception("Empty stream for plotting")
            stream.merge(-1)
            stream.trim(starttime=start_time, endtime=stop_time)

            # Placeholder for any requested id with no data yet
            for id_ in self.ids:
                if not any(tr.id == id_ for tr in stream):
                    net, sta, loc, cha = id_.split(".")
                    header = {'network': net, 'station': sta, 'location': loc,
                              'channel': cha, 'starttime': start_time}
                    stream.append(Trace(data=np.zeros(2), header=header))
            stream.sort()
            for tr in stream:
                tr.stats.processing = []

            # The waveform/spectrogram still update on ticks where the
            # magnitude was skipped (no data in its window yet).
            self._update_magnitude(stream)
            if self.combo_plot:
                self._update_combo(stream, start_time, stop_time)

            self._repeated_error_count = 0
        except Exception as e:
            msg = str(e)
            if msg == self._last_tick_error:
                self._repeated_error_count += 1
            else:
                self._last_tick_error = msg
                self._repeated_error_count = 1
            if self._repeated_error_count in (10, 30, 100) or \
                    self._repeated_error_count % 300 == 0:
                logger.critical(
                    "tick has failed %d times in a row with the same error: %s "
                    "-- if this doesn't resolve itself, check your "
                    "--seedlink_server/-s/--inventory settings and network "
                    "connection.", self._repeated_error_count, msg)
            else:
                logger.error(e)

    def _magnitude_window(self):
        """(start, end) of the window the magnitude is measured over."""
        win_start = UTCDateTime() - (self.magnitude_window + self.seedlink_wait_buffer)
        return win_start, win_start + self.magnitude_window

    def _update_magnitude(self, stream):
        """
        Measure the magnitude over the last --magnitude_window seconds
        (ending --seedlink_wait_buffer seconds before now) and update
        the gauge. Returns False if that window has no data yet.
        """
        tr = _detrend(stream[0], "demean")
        win_start, win_end = self._magnitude_window()
        tr = tr.slice(starttime=win_start, endtime=win_end)

        if len(tr.data) == 0:
            self._empty_slice_skips += 1
            if self._empty_slice_skips in (5, 20, 100) or self._empty_slice_skips % 500 == 0:
                logger.critical(
                    "empty trace slice for magnitude window, %d time(s) so far -- "
                    "stream[0] is %r (%d samples). Requested ids: %r. If stream[0]'s "
                    "id doesn't match your real station/location/channel, check your "
                    "-s selector; otherwise try a larger --seedlink_wait_buffer.",
                    self._empty_slice_skips, stream[0].id, len(stream[0].data), self.ids)
            return False
        self._empty_slice_skips = 0

        tr_wa = _taper(get_wa_waveform(tr, self.inv, water_level=10), 0.1)
        half_amp, approx_freq, p2t_time = peak_to_trough_amplitude(tr_wa)

        mag = get_local_mag(half_amp, self.magnitude_distance)
        if not self.allow_negative_mags and mag < 0:
            mag = 0
        self.mag = mag

        age = UTCDateTime() - self.tmax
        if age > self.max_mag_keep_time:
            self.mmax = 0
        # Reset today's maximum at startup and when the (UTC) date changes
        now = UTCDateTime()
        if self._tick_count == 0 or self.tmax_today.date != now.date:
            self.mmax_today = 0
            self.tmax_today = now

        if mag > self.mmax:
            self.mmax = mag
            self.tmax = UTCDateTime()
            self._magtime = p2t_time
            age = 1e-5
        if mag > self.mmax_today:
            self.mmax_today = mag
            self.tmax_today = UTCDateTime()

        alpha = max(0.0, min(1.0, 1.0 - (age / self.max_mag_keep_time)))
        self.gauge.set_data(self.mag, self.mmax, self.mmax_today,
                            self.tmax_today.strftime("%H:%M:%S"), alpha)
        self._tick_count += 1
        return True

    def _update_combo(self, stream, start_time, stop_time):
        tr = self._display_stream(stream)[0]

        win_start_ts = win_end_ts = pick_ts = None
        if self.amp_marker:
            win_start, win_end = self._magnitude_window()
            win_start_ts, win_end_ts = win_start.timestamp, win_end.timestamp
            pick_ts = self._magtime.timestamp if self._magtime is not None else win_start_ts
        update_waveform(self._wave_handles, tr, win_start_ts, win_end_ts, pick_ts)

        self._clock_label.setText(stop_time.strftime("%Y-%m-%d %H:%M:%S UTC"))
        self._place_clock()

        self._combo_tick += 1
        if not self.spec_panel.ready:
            try:
                self.spec_panel.init(tr, vmin=self.spec_vmin, vmax=self.spec_vmax,
                                     wlen=self.spec_wlen, per_lap=self.spec_overlap,
                                     colorscale=self.spec_colorscale)
            except ValueError as e:
                logger.debug("spectrogram not ready yet (%s) -- will retry", e)
        elif self._combo_tick % self.spec_update_every == 0:
            self.spec_panel.update(tr, wlen=self.spec_wlen, per_lap=self.spec_overlap,
                                   vmin=self.spec_vmin, vmax=self.spec_vmax,
                                   colorscale=self.spec_colorscale)
        self.spec_panel.set_xrange(start_time.timestamp, stop_time.timestamp)

        # pyqtgraph normally applies range changes (the new time window,
        # the waveform's y auto-range) at the start of the next paint,
        # which then asks for a second paint. Applying them here means
        # each tick paints both plots once instead of twice. Called on
        # the ViewBox directly so it also works on pyqtgraph 0.11.
        for pw in (self.waveform_plot, self.spec_panel.plot_widget):
            pw.getPlotItem().getViewBox().prepareForPaint()

    def _display_stream(self, stream):
        """Stream for the waveform/spectrogram (optionally bandpassed)."""
        if self.spec_bandpass is None:
            return stream
        freqmin, freqmax = self.spec_bandpass
        filtered = stream.copy()
        filtered.detrend("demean")
        filtered.taper(0.05)
        filtered.filter("bandpass", freqmin=freqmin, freqmax=freqmax)
        return filtered


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
    """Show defaults in --help, but not meaningless ones (None/False)."""

    def _get_help_string(self, action):
        if action.default is None or action.default is False or action.required:
            return action.help
        return super()._get_help_string(action)


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog='shakeometer',
        description='Real-time local-magnitude gauge for seismology outreach. '
                    'Shows the gauge on its own, or with --combo_plot under a '
                    'live waveform and spectrogram.',
        formatter_class=_HelpFormatter)

    g = p.add_argument_group('connection')
    g.add_argument('--seedlink_server', type=str, required=True,
                   help='SeedLink server, optionally with port (default port '
                        '18000), e.g. rtserve.earthscope.org or localhost')
    g.add_argument('-s', '--seedlink_streams', type=str, required=True,
                   help='stream selector, NET_STA:LOCCHA, e.g. "EI_DSB:HHZ" '
                        'or "AM_RDF52:00EHZ"')
    g.add_argument('-b', '--backtrace_time', required=True,
                   type=_parse_time_with_suffix_to_seconds,
                   help='seconds of data to keep and plot (s/m/h/d suffixes allowed)')
    g.add_argument('--seedlink_wait_buffer', type=float, default=4,
                   help='seconds behind real time to measure the magnitude, '
                        'to allow for data latency')

    g = p.add_argument_group('display')
    g.add_argument('--combo_plot', action='store_true',
                   help='waveform + spectrogram + gauge, instead of the gauge alone')
    g.add_argument('--update_time', default=0.1,
                   type=_parse_time_with_suffix_to_seconds,
                   help='seconds between display updates (s/m/h/d suffixes allowed)')
    g.add_argument('--amp_marker', action='store_true',
                   help='(combo) shade the magnitude measurement window on the waveform')
    g.add_argument('-f', '--fullscreen', action='store_true', help='start fullscreen')
    g.add_argument('--x_size', type=int, default=None,
                   help='window width in pixels (default 900, or 1800 with --combo_plot)')
    g.add_argument('--y_size', type=int, default=None,
                   help='window height in pixels (default 550, or 1100 with --combo_plot)')
    g.add_argument('--x_position', type=int, default=0, help='window x position in pixels')
    g.add_argument('--y_position', type=int, default=0, help='window y position in pixels')
    g.add_argument('--without-decoration', dest='without_decoration',
                   action='store_true',
                   help='no window title bar or borders; close with Ctrl-C')
    g.add_argument('--force', action='store_true',
                   help='skip the --without-decoration confirmation prompt')

    g = p.add_argument_group('magnitude')
    g.add_argument('--inventory', type=str, required=True,
                   help='StationXML file with the instrument response')
    g.add_argument('--magnitude_window', type=float, default=2,
                   help='seconds of data used for each magnitude measurement')
    g.add_argument('--magnitude_distance', type=float, default=10,
                   help='distance (km) the magnitude is calculated for')
    g.add_argument('--maximum_magnitude', type=float, default=8,
                   help='top of the gauge scale')
    g.add_argument('--max_mag_keep_time', type=float, default=30,
                   help='seconds over which the recent-maximum marker fades')
    g.add_argument('--allow_negative_mags', action='store_true',
                   help='show negative magnitudes (by default they are shown as 0)')

    g = p.add_argument_group('logos (PNG/JPG files)')
    g.add_argument('--logo_topleft', type=str, default=None,
                   help='gauge mode: top-left corner. Combo: left of the gauge')
    g.add_argument('--logo_topright', type=str, default=None,
                   help='gauge mode: top-right corner. Combo: right of the gauge')
    g.add_argument('--logo_bottomleft', type=str, default=None,
                   help='bottom-left corner of the window')
    g.add_argument('--logo_bottomright', type=str, default=None,
                   help='bottom-right corner of the window')

    g = p.add_argument_group('spectrogram/waveform (--combo_plot)')
    g.add_argument('--spec_update_time', default=1.0,
                   type=_parse_time_with_suffix_to_seconds,
                   help='seconds between spectrogram updates')
    g.add_argument('--spec_bandpass', type=float, nargs=2, default=None,
                   metavar=('FREQMIN', 'FREQMAX'),
                   help='bandpass (Hz) for the waveform and spectrogram; '
                        'the magnitude always uses unfiltered data')
    g.add_argument('--spec_wlen', type=float, default=2.56,
                   help='FFT window length in seconds')
    g.add_argument('--spec_overlap', type=float, default=85,
                   help='FFT window overlap, percent (0-100)')
    g.add_argument('--spec_colorscale', choices=['fixed', 'auto'], default='fixed',
                   help='"fixed": --spec_vmin to --spec_vmax. '
                        '"auto": rescale to each frame')
    g.add_argument('--spec_vmin', type=float, default=0.0, help='dB, for fixed colour scale')
    g.add_argument('--spec_vmax', type=float, default=50.0, help='dB, for fixed colour scale')

    p.add_argument('-v', '--verbose', action='store_true', help='debug logging')
    return p


def main():
    args = build_arg_parser().parse_args()

    if args.x_size is None:
        args.x_size = 1800 if args.combo_plot else 900
    if args.y_size is None:
        args.y_size = 1100 if args.combo_plot else 550

    # Check the inventory up front, rather than after connecting
    if not os.path.isfile(args.inventory):
        print("Error: --inventory file not found: %r" % args.inventory, file=sys.stderr)
        sys.exit(1)
    try:
        read_inventory(args.inventory)
    except Exception as e:
        print("Error: --inventory file %r could not be read as a "
              "StationXML/response file: %s" % (args.inventory, e), file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.CRITICAL,
        format='%(asctime)s,%(msecs)03d %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d:%H:%M:%S')

    if args.without_decoration and not args.force:
        answer = input(
            "Warning: You are about to open a window without decoration that "
            "is not controlled via your Window Manager. You can exit with "
            "<Ctrl>-C (as long as you do not switch to another window with "
            "e.g. <Alt>-<Tab>).\n\nType 'y' to continue.. ")
        if answer != "y":
            print("Aborting.")
            sys.exit()

    stream = Stream()
    lock = threading.Lock()

    seedlink_client = SeedlinkUpdater(stream, lock)
    seedlink_client.slconn.set_sl_address(args.seedlink_server)
    seedlink_client.multiselect = args.seedlink_streams
    seedlink_client.begin_time = (UTCDateTime() - args.backtrace_time).format_seedlink()
    seedlink_client.initialize()
    ids = seedlink_client.getTraceIDs()

    thread = threading.Thread(target=seedlink_client.run)
    thread.daemon = True
    thread.start()

    time.sleep(2)  # a couple of seconds of data before the first paint

    app = QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    window = ShakeometerWindow(args, stream, lock, ids)
    window.show()
    window._reposition_overlays()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
