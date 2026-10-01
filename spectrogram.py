"""
Live spectrogram panel for combo mode (a pyqtgraph ImageItem).

The spectrogram is computed with matplotlib.mlab.specgram, the same
routine obspy's own spectrogram plotting uses, and handed to pyqtgraph
as a plain array.
"""
import math

import numpy as np
import pyqtgraph as pg
from matplotlib import mlab
from matplotlib import colormaps

from waveform import zero_default_padding, style_axes


def _nearest_pow_2(x):
    a = math.pow(2, math.ceil(np.log2(x)))
    b = math.pow(2, math.floor(np.log2(x)))
    return a if abs(a - x) < abs(b - x) else b


def compute_spectrogram(data, samp_rate, wlen=2.56, per_lap=0.85, dbscale=True,
                        mult=8.0):
    """
    Spectrogram array, computed the same way as
    obspy.imaging.spectrogram.spectrogram() (without the plotting).

    Returns
    -------
    specgram : 2-D ndarray, shape (n_freq, n_time)
        Frequency bins ascending down the rows, time bins ascending
        along the columns.
    freq : 1-D ndarray
    time : 1-D ndarray
        Time bin centres, in seconds from the start of `data`.

    Raises
    ------
    ValueError
        If `data` is too short for at least two FFT windows.
    """
    samp_rate = float(samp_rate)
    npts = len(data)
    nfft = int(_nearest_pow_2(wlen * samp_rate))
    if npts < nfft:
        raise ValueError(
            "Input signal too short (%d samples, window length %s seconds, "
            "nfft %d samples, sampling rate %s Hz)" % (npts, wlen, nfft, samp_rate))

    mult_i = int(_nearest_pow_2(mult)) * nfft if mult is not None else None
    nlap = int(nfft * float(per_lap))

    data = data - data.mean()
    specgram, freq, time = mlab.specgram(
        data, Fs=samp_rate, NFFT=nfft, pad_to=mult_i, noverlap=nlap)

    if len(time) < 2:
        raise ValueError(
            "Input signal too short (%d samples, window length %s seconds, "
            "nfft %d samples, %d samples window overlap, sampling rate %s Hz)"
            % (npts, wlen, nfft, nlap, samp_rate))

    if dbscale:
        specgram = 10 * np.log10(specgram[1:, :])
    else:
        specgram = np.sqrt(specgram[1:, :])
    freq = freq[1:]
    return specgram, freq, time


def _jet_lut(n=256):
    cmap = colormaps["jet"]  # matplotlib >= 3.5
    return (cmap(np.linspace(0.0, 1.0, n))[:, :3] * 255).astype(np.ubyte)


class SpectrogramPanel:
    """
    Owns the spectrogram's PlotWidget/ImageItem. Call init() until it
    succeeds (it raises ValueError while there isn't enough data
    yet), then update() whenever a new frame is wanted.
    """

    def __init__(self):
        self.plot_widget = pg.PlotWidget(axisItems={'bottom': pg.DateAxisItem()})
        self.plot_widget.setBackground('w')
        self.plot_widget.setMouseEnabled(x=False, y=False)
        self.plot_widget.hideButtons()
        self.plot_widget.getPlotItem().setMenuEnabled(False)
        self.plot_widget.setLabel('left', 'Frequency [Hz]')
        zero_default_padding(self.plot_widget.getPlotItem().vb)
        style_axes(self.plot_widget)

        self.image_item = pg.ImageItem()
        self.image_item.setLookupTable(_jet_lut())
        self.plot_widget.addItem(self.image_item)

        self.ready = False
        self.fmax = None

    def init(self, tr, vmin=0.0, vmax=50.0, fmax=None, wlen=2.56, per_lap=0.85,
             colorscale="fixed"):
        """First frame. Raises ValueError if tr is still too short."""
        self._push_frame(tr, wlen, per_lap, vmin, vmax, colorscale)
        self.fmax = fmax if fmax is not None else tr.stats.sampling_rate / 2.0
        self.plot_widget.setYRange(0.0, self.fmax, padding=0.0)
        self.ready = True

    def update(self, tr, wlen=2.56, per_lap=0.85, vmin=0.0, vmax=50.0,
               colorscale="fixed"):
        self._push_frame(tr, wlen, per_lap, vmin, vmax, colorscale)

    def _push_frame(self, tr, wlen, per_lap, vmin, vmax, colorscale):
        specgram, freq, time = compute_spectrogram(
            tr.data.astype(np.float64), tr.stats.sampling_rate,
            wlen=wlen, per_lap=per_lap, dbscale=True)

        # ImageItem indexes arrays as [x, y]; specgram is [freq, time]
        self.image_item.setImage(specgram.T, autoLevels=False)
        t0 = tr.stats.starttime.timestamp
        x0, x1 = t0 + time[0], t0 + time[-1]
        y0, y1 = freq[0], freq[-1]
        self.image_item.setRect(pg.QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))

        if colorscale == "auto":
            vmin, vmax = float(np.nanmin(specgram)), float(np.nanmax(specgram))
        self.image_item.setLevels((vmin, vmax))

    def set_xrange(self, start_ts, end_ts):
        self.plot_widget.setXRange(start_ts, end_ts, padding=0.0)
