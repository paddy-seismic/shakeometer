"""
The magnitude gauge: a custom-painted Qt widget.

Everything is drawn in a fixed logical coordinate system (x in
[-0.46, 0.46], y in [-0.12, 0.46], y pointing up, origin at the
needle's pivot). paintEvent() maps that box onto the widget's current
size, keeping its aspect ratio, so the gauge scales to any window
without rebuilding anything.
"""
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, QPointF, QRectF

# Logical drawing box (see module docstring)
LOGICAL_W = 0.92
LOGICAL_H = 0.58
GAUGE_ASPECT = LOGICAL_W / LOGICAL_H

# Arc colours, green (low) to dark red (high). Also used for the
# colour of the centre magnitude readout.
GAUGE_CMAP = LinearSegmentedColormap.from_list("gauge_intensity", [
    (0.00, "#4CAF50"),  # green
    (0.15, "#8BC34A"),  # yellow-green
    (0.30, "#CDDC39"),  # lime
    (0.45, "#FFEB3B"),  # yellow
    (0.60, "#FFC107"),  # amber
    (0.75, "#FF9800"),  # orange
    (0.90, "#F4511E"),  # deep orange
    (1.00, "#B71C1C"),  # dark red
])

# Preferred fonts, first installed one wins (see _pick_font_family)
GAUGE_FONT_CANDIDATES = ['Nimbus Sans', 'Nimbus Sans L', 'Liberation Sans',
                          'Arial', 'DejaVu Sans']

# Height in pixels of the logical box at which font sizes are "1:1".
# Smaller values make all gauge text larger.
REFERENCE_PX = 500.0


def _mpl_color_to_qcolor(rgba, alpha=None):
    r, g, b, a = rgba
    qc = QtGui.QColor(int(r * 255), int(g * 255), int(b * 255))
    qc.setAlphaF(a if alpha is None else alpha)
    return qc


def _pick_font_family():
    """
    First family from GAUGE_FONT_CANDIDATES that Qt has installed,
    else the application default. (QFont.setFamilies(), which takes a
    fallback list directly, needs Qt 5.13 -- newer than Raspberry Pi
    OS Buster provides.)
    """
    available = set(QtGui.QFontDatabase().families())
    for name in GAUGE_FONT_CANDIDATES:
        if name in available:
            return name
    return QtWidgets.QApplication.font().family()


def _needle_vertices(angle_deg, length, base_half_width):
    theta = np.radians(angle_deg)
    perp = np.radians(angle_deg + 90)
    tip = (length * np.cos(theta), length * np.sin(theta))
    base1 = (base_half_width * np.cos(perp), base_half_width * np.sin(perp))
    base2 = (-base_half_width * np.cos(perp), -base_half_width * np.sin(perp))
    return [base1, tip, base2]


def _degree_range(n):
    return np.linspace(0, 180, n + 1, endpoint=True)[0:-1]


# The arc's geometry and colours never change, so they're built once
# (on first paint) and shared by every GaugeWidget.
_ARC_CACHE = None


def _build_arc_cache():
    global _ARC_CACHE
    if _ARC_CACHE is not None:
        return _ARC_CACHE

    outer_r, inner_r = 0.40, 0.30
    n_pts = 120
    angles = np.radians(np.linspace(0, 180, n_pts))
    disc = QtGui.QPolygonF([QPointF(0, 0)] +
                            [QPointF(outer_r * np.cos(a), outer_r * np.sin(a))
                             for a in angles])

    # Many thin flat-coloured slices; i=0 (east) is the red, high
    # magnitude end, i=n-1 (west) the green end.
    n_slices = 90
    edges = np.radians(np.linspace(0, 180, n_slices + 1))
    slices = []
    for i in range(n_slices):
        a0, a1 = edges[i], edges[i + 1]
        t = 1.0 - i / (n_slices - 1)
        color = _mpl_color_to_qcolor(GAUGE_CMAP(t))
        quad = QtGui.QPolygonF([
            QPointF(outer_r * np.cos(a0), outer_r * np.sin(a0)),
            QPointF(outer_r * np.cos(a1), outer_r * np.sin(a1)),
            QPointF(inner_r * np.cos(a1), inner_r * np.sin(a1)),
            QPointF(inner_r * np.cos(a0), inner_r * np.sin(a0)),
        ])
        slices.append((quad, color))

    _ARC_CACHE = (disc, slices)
    return _ARC_CACHE


class GaugeWidget(QtWidgets.QWidget):
    """
    Speedometer-style magnitude gauge: coloured arc, M1..N tick marks
    and labels, needle, centre magnitude readout, the fading recent
    maximum marker and today's maximum.

    Call set_params() once, then set_data() every tick.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 130)
        self._font_family = _pick_font_family()
        self._current_scale = 1.0

        # Static config, set via set_params()
        self.n_labels = 8
        self.distance_km = 10
        self.title_text = self._title(self.distance_km)

        # Live data, set via set_data()
        self.mag = 0.0
        self.mmax = 0.0
        self.mmax_today = 0.0
        self.tmax_today_str = ""
        self.alpha = 1.0

        pal = self.palette()
        pal.setColor(self.backgroundRole(), QtGui.QColor("white"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

    @staticmethod
    def _title(distance_km):
        return "Equivalent magnitude at %g km" % distance_km

    # -- configuration ----------------------------------------------
    def set_params(self, n_labels, distance_km):
        self.n_labels = n_labels
        self.distance_km = distance_km
        self.title_text = self._title(distance_km)

    def set_data(self, mag, mmax, mmax_today, tmax_today_str, alpha, repaint=True):
        self.mag = mag
        self.mmax = mmax
        self.mmax_today = mmax_today
        self.tmax_today_str = tmax_today_str
        self.alpha = max(0.0, min(1.0, alpha))
        if repaint:
            self.update()

    def _mag_color(self, mag):
        t = min(1.0, max(0.0, mag / float(self.n_labels)))
        return _mpl_color_to_qcolor(GAUGE_CMAP(t), alpha=0.75)

    # -- geometry helpers ---------------------------------------------
    def _make_transform(self):
        """
        Transform from logical gauge coordinates to widget pixels,
        aspect-preserving and centred. Returns (transform, px_per_unit).
        """
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return QtGui.QTransform(), 1.0
        scale = min(w / LOGICAL_W, h / LOGICAL_H)
        y_center_logical = (-0.12 + 0.46) / 2.0

        t = QtGui.QTransform()
        t.translate(w / 2.0, h / 2.0)
        t.scale(scale, -scale)  # flip y: logical up == screen up
        t.translate(0.0, -y_center_logical)
        return t, scale

    def _font_scale(self, px_per_unit):
        return px_per_unit * LOGICAL_H / REFERENCE_PX

    def _font(self, size_px, weight=QtGui.QFont.Normal):
        f = QtGui.QFont(self._font_family)
        f.setPixelSize(max(6, int(round(size_px))))
        f.setWeight(weight)
        return f

    # -- painting -------------------------------------------------------
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

        transform, scale = self._make_transform()
        self._current_scale = scale
        fscale = self._font_scale(scale)
        N = self.n_labels

        painter.save()
        painter.setTransform(transform, combine=True)
        self._paint_arc(painter)
        self._paint_ticks_and_labels(painter, N, fscale)
        self._paint_bottom_banner(painter)
        self._paint_title(painter, fscale)
        self._paint_max_marker(painter, N, fscale)
        self._paint_today_max(painter, fscale)
        self._paint_center_mag(painter, fscale)
        self._paint_needle(painter, N)
        painter.restore()
        painter.end()

    def _paint_arc(self, painter):
        # The arc is built from sampled (cos, sin) points rather than
        # QPainter.drawPie()/QConicalGradient: Qt's arc and gradient
        # angles are defined for its own y-down coordinates and come
        # out mirrored under this widget's y-up transform.
        disc, slices = _build_arc_cache()

        # White backing semicircle
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("white"))
        painter.drawPolygon(disc)

        # Antialiasing off for the slices, otherwise faint seams show
        # between neighbouring slices.
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        for quad, color in slices:
            painter.setBrush(color)
            painter.drawPolygon(quad)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

    def _paint_ticks_and_labels(self, painter, N, fscale):
        starts = _degree_range(N)
        labels = ["M%d" % (m + 1) for m in range(N)][::-1]
        font = self._font(28 * fscale, QtGui.QFont.DemiBold)
        painter.setFont(font)
        # Cosmetic pen: a fixed width in screen pixels, so the ticks
        # don't get chunky in a large window.
        pen = QtGui.QPen(QtGui.QColor("#262626"))
        pen.setWidthF(1.4)
        pen.setCosmetic(True)
        painter.setPen(pen)

        rr = 0.43           # label radius
        r0, r1 = 0.401, 0.408  # tick inner/outer radius
        for labpos, lab in zip(list(starts) + [180.0], labels + ["0"]):
            rad = np.radians(labpos)
            painter.drawLine(QPointF(r0 * np.cos(rad), r0 * np.sin(rad)),
                             QPointF(r1 * np.cos(rad), r1 * np.sin(rad)))
            self._draw_rotated_text(painter, rr * np.cos(rad), rr * np.sin(rad),
                                    labpos - 90, lab, font, QtGui.QColor("black"))

    def _paint_bottom_banner(self, painter):
        # White strip behind the title text
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("white"))
        painter.drawRect(QRectF(-0.4, -0.1, 0.8, 0.1))

    def _paint_title(self, painter, fscale):
        font = self._font(21 * fscale, QtGui.QFont.Normal)
        self._draw_rotated_text(painter, 0, -0.05, 0, self.title_text, font,
                                QtGui.QColor("black"))

    def _paint_center_mag(self, painter, fscale):
        font = self._font(72 * fscale, QtGui.QFont.Bold)
        color = self._mag_color(self.mag)
        # Thin outline, for contrast against the colour-varying fill
        outline_w = font.pixelSize() * (0.2 / 72.0)
        self._draw_rotated_text(painter, 0, 0.15, 0, "%4.2f" % self.mag,
                                font, color, outline=QtGui.QColor("black"),
                                outline_width=outline_w)

    def _paint_needle(self, painter, N):
        pos = 180 - (180.0 / N * self.mag)
        verts = _needle_vertices(pos, 0.27, 0.011)
        poly = QtGui.QPolygonF([QPointF(x, y) for x, y in verts])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QColor("black"))
        painter.drawPolygon(poly)
        # Hub, drawn over the needle base
        painter.setBrush(QtGui.QColor("black"))
        painter.drawEllipse(QPointF(0, 0), 0.028, 0.028)
        painter.setBrush(QtGui.QColor("white"))
        painter.drawEllipse(QPointF(0, 0), 0.013, 0.013)

    def _paint_max_marker(self, painter, N, fscale):
        if self.alpha <= 0.0:
            return
        posmax = 180 - (180.0 / N * self.mmax)
        rad = np.radians(posmax)
        red = QtGui.QColor("red")
        red.setAlphaF(self.alpha)

        # Thin arrow shaft + triangular head
        length = 0.28
        head_len, head_half_w = 0.018, 0.012
        tip = (length * np.cos(rad), length * np.sin(rad))
        shaft_back = ((length - head_len) * np.cos(rad),
                      (length - head_len) * np.sin(rad))
        painter.setPen(QtGui.QPen(red, 0.006))
        painter.drawLine(QPointF(0, 0), QPointF(*shaft_back))
        perp = np.radians(posmax + 90)
        b1 = (shaft_back[0] + head_half_w * np.cos(perp),
              shaft_back[1] + head_half_w * np.sin(perp))
        b2 = (shaft_back[0] - head_half_w * np.cos(perp),
              shaft_back[1] - head_half_w * np.sin(perp))
        head = QtGui.QPolygonF([QPointF(*b1), QPointF(*tip), QPointF(*b2)])
        painter.setPen(Qt.NoPen)
        painter.setBrush(red)
        painter.drawPolygon(head)

        rr = 0.314
        font = self._font(32 * fscale, QtGui.QFont.Bold)
        outline_w = font.pixelSize() * (1.0 / 32.0)
        self._draw_rotated_text(painter, rr * np.cos(rad), rr * np.sin(rad),
                                posmax - 90, "%4.2f" % self.mmax, font, red,
                                outline=QtGui.QColor("black"),
                                outline_width=outline_w)

    def _paint_today_max(self, painter, fscale):
        font = self._font(32 * fscale * 0.5, QtGui.QFont.Normal)
        text = "Today's maximum:\n M%4.2f\n%s" % (self.mmax_today, self.tmax_today_str)
        # Anchored high enough that the 3-line block clears the banner
        self._draw_rotated_text(painter, 0.17, 0.10, 0, text, font, QtGui.QColor("red"))

    # -- text helper ------------------------------------------------
    def _draw_rotated_text(self, painter, x, y, rot_deg, text, font, color,
                           outline=None, outline_width=None):
        """
        Draw `text` centred at logical point (x, y), rotated rot_deg
        degrees counterclockwise. Multi-line text is supported.

        Text is drawn in a local frame that undoes the outer transform's
        zoom and y-flip, so font pixel sizes come out as real pixels.
        The outline width is in pixels; by default it scales with the
        font size.
        """
        painter.save()
        painter.translate(x, y)
        s = self._current_scale or 1.0
        painter.scale(1.0 / s, -1.0 / s)
        painter.rotate(-rot_deg)
        painter.setFont(font)
        metrics = QtGui.QFontMetrics(font)
        lines = text.split("\n")
        line_h = metrics.height()
        total_h = line_h * len(lines)
        painter.setPen(QtGui.QPen(color))
        for i, line in enumerate(lines):
            # QFontMetrics.horizontalAdvance() needs Qt 5.11+; width()
            # for anything older
            w = metrics.horizontalAdvance(line) if hasattr(metrics, "horizontalAdvance") \
                else metrics.width(line)
            tx = -w / 2.0
            ty = -total_h / 2.0 + (i + 1) * line_h - metrics.descent()
            if outline is not None:
                width = outline_width if outline_width is not None else font.pixelSize() * 0.03
                path = QtGui.QPainterPath()
                path.addText(tx, ty, font, line)
                painter.strokePath(path, QtGui.QPen(outline, max(0.5, width)))
                painter.fillPath(path, QtGui.QBrush(color))
            else:
                painter.drawText(QPointF(tx, ty), line)
        painter.restore()
