"""
Logo placement. Logos are plain QLabel widgets showing a pixmap,
resized/repositioned by the main window whenever it is resized.

Scaled pixmaps are always made from the original, full-resolution
image (kept by the caller) rather than from the label's current,
already-scaled pixmap, which would get blurrier with every resize.
"""
import logging

from PyQt5 import QtCore, QtGui, QtWidgets

logger = logging.getLogger(__name__)


def load_pixmap(path):
    """
    Load a logo image. Returns a QPixmap, or None if path is empty or
    the file can't be read (logged as a warning; a missing logo
    shouldn't stop the display).
    """
    if not path:
        return None
    pm = QtGui.QPixmap(path)
    if pm.isNull():
        logger.warning("could not load logo %r -- skipping it", path)
        return None
    return pm


def make_logo_label(parent, pixmap):
    label = QtWidgets.QLabel(parent)
    label.setPixmap(pixmap)
    label.setScaledContents(False)
    label.setAttribute(QtCore.Qt.WA_TranslucentBackground)
    return label


def _fit_rect(pixmap, box):
    """Largest rect with pixmap's aspect ratio that fits in box, centred."""
    pw, ph = pixmap.width(), pixmap.height()
    if pw <= 0 or ph <= 0:
        return box
    box_aspect = box.width() / float(box.height())
    img_aspect = pw / float(ph)
    if img_aspect > box_aspect:
        w = box.width()
        h = w / img_aspect
    else:
        h = box.height()
        w = h * img_aspect
    x = box.x() + (box.width() - w) / 2.0
    y = box.y() + (box.height() - h) / 2.0
    return QtCore.QRect(int(x), int(y), int(w), int(h))


def position_corner_logo(label, parent_size, corner, orig_pixmap,
                         height_frac=0.12, width_frac=0.19, margin_frac=0.015):
    """
    Fit a logo into a box in one corner of the window (parent_size, a
    QSize), keeping its aspect ratio.
    """
    pw, ph = parent_size.width(), parent_size.height()
    margin = int(margin_frac * min(pw, ph))
    box_w, box_h = int(width_frac * pw), int(height_frac * ph)
    if corner == 'topleft':
        x0, y0 = margin, margin
    elif corner == 'topright':
        x0, y0 = pw - margin - box_w, margin
    elif corner == 'bottomleft':
        x0, y0 = margin, ph - margin - box_h
    elif corner == 'bottomright':
        x0, y0 = pw - margin - box_w, ph - margin - box_h
    else:
        raise ValueError("bad corner %r" % corner)
    fitted = _fit_rect(orig_pixmap, QtCore.QRect(x0, y0, box_w, box_h))
    label.setGeometry(fitted)
    label.setPixmap(orig_pixmap.scaled(
        fitted.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))


def size_side_logo(label, window_size, orig_pixmap,
                   height_frac=0.20, max_width_frac=0.30):
    """
    Size a combo-mode side logo, which sits in the gauge row's layout
    either side of the gauge: height a fraction of the window height,
    width from the image's aspect ratio, capped at a fraction of the
    window width.

    Sized from the window rather than the gauge, because the gauge's
    width is whatever the logos leave it. setFixedSize() makes the
    layout reserve exactly this space and centre the logo vertically.
    """
    pw, ph = orig_pixmap.width(), orig_pixmap.height()
    if pw <= 0 or ph <= 0:
        return
    img_aspect = pw / float(ph)
    h = int(height_frac * window_size.height())
    w = int(h * img_aspect)
    max_w = int(max_width_frac * window_size.width())
    if w > max_w:
        w = max_w
        h = int(w / img_aspect)
    if w <= 0 or h <= 0:
        return
    label.setFixedSize(w, h)
    label.setPixmap(orig_pixmap.scaled(
        w, h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
