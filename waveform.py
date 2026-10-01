"""
Live waveform panel for combo mode (a pyqtgraph PlotWidget).

The x axis is POSIX seconds (UTCDateTime.timestamp), the same as the
spectrogram panel, so the two can be x-linked directly.
"""
import numpy as np
import pyqtgraph as pg


def zero_default_padding(vb):
    """
    Make the view's auto-range hug the data with no margin.
    ViewBox.setDefaultPadding() only exists from pyqtgraph 0.12; on
    0.11 (the newest that runs on a Raspberry Pi OS Buster's Qt 5.11)
    this does nothing and the view keeps its small default margin.
    """
    if hasattr(vb, "setDefaultPadding"):
        vb.setDefaultPadding(0.0)


def style_axes(plot_widget):
    """
    Black axes and tick labels at 10pt. pyqtgraph's default axis colour
    is a light grey meant for a black background.
    """
    tick_font = pg.QtGui.QFont()
    tick_font.setPointSize(10)
    for axis_name in ('left', 'bottom'):
        axis = plot_widget.getAxis(axis_name)
        axis.setTextPen('k')
        axis.setPen('k')
        axis.setStyle(tickFont=tick_font)


def create_waveform_plot():
    """
    Build the waveform PlotWidget and its persistent curve.

    Returns
    -------
    plot_widget : pyqtgraph.PlotWidget
    handles : dict
        {'curve': PlotDataItem, 'span': LinearRegionItem or None,
         'vline': InfiniteLine or None}
    """
    pw = pg.PlotWidget()
    pw.setBackground('w')
    pw.setMouseEnabled(x=False, y=False)
    pw.hideButtons()
    pw.getPlotItem().setMenuEnabled(False)
    pw.getPlotItem().showGrid(x=True, y=False)
    pw.getAxis('bottom').setStyle(showValues=False)  # spectrogram below carries the time axis
    pw.getAxis('left').setStyle(tickTextWidth=36)
    zero_default_padding(pw.getPlotItem().vb)
    style_axes(pw)

    curve = pw.plot([], [], pen=pg.mkPen(color=(0, 0, 220), width=0.6))

    handles = {"curve": curve, "span": None, "vline": None}
    return pw, handles


def add_amp_marker(plot_widget, handles):
    """
    Add the shaded magnitude-measurement window and the red marker at
    the time of the current maximum.
    """
    span = pg.LinearRegionItem(values=(0, 0), brush=(0, 0, 0, 25), movable=False)
    span.setZValue(-10)
    plot_widget.addItem(span)
    vline = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen('r', width=2))
    plot_widget.addItem(vline)
    handles["span"] = span
    handles["vline"] = vline


def update_waveform(handles, tr, win_start_ts=None, win_end_ts=None, pick_ts=None):
    """
    Push the current trace into the curve, and move the measurement
    window/marker if present.

    Parameters
    ----------
    tr : obspy.Trace
    win_start_ts, win_end_ts, pick_ts : float or None
        POSIX-second bounds of the measurement window and time of the
        current maximum. None leaves the marker where it is.
    """
    times = tr.stats.starttime.timestamp + np.arange(len(tr.data)) / tr.stats.sampling_rate
    handles["curve"].setData(times, tr.data)

    if win_start_ts is not None and handles.get("span") is not None:
        handles["span"].setRegion((win_start_ts, win_end_ts))
        handles["vline"].setPos(pick_ts if pick_ts is not None else win_start_ts)
