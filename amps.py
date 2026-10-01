"""
Amplitude and local-magnitude calculation.

Removes the instrument response, simulates a Wood-Anderson
displacement seismogram, measures the maximum peak-to-trough
amplitude and converts it to a local magnitude with the Hutton-Boore
attenuation relation.
"""
import numpy as np
from scipy.signal import find_peaks
from obspy.core.trace import Trace

# obspy wraps Trace.detrend()/Trace.simulate() in a decorator that
# appends a provenance string to tr.stats.processing on every call,
# built with inspect.getcallargs(). That is surprisingly expensive on a
# Raspberry Pi and is never read here: every trace these functions see
# is a throwaway copy. Calling the undecorated originals (exposed as
# __wrapped__) runs exactly the same code, minus the provenance string.
# Falls back to the normal decorated method if __wrapped__ ever goes
# away.
_detrend = getattr(Trace.detrend, "__wrapped__", None) or \
    (lambda tr, *a, **kw: Trace.detrend(tr, *a, **kw))
_simulate = getattr(Trace.simulate, "__wrapped__", None) or \
    (lambda tr, *a, **kw: Trace.simulate(tr, *a, **kw))


class PeakToTroughError(Exception):
    """
    Raised when peak_to_trough_amplitude() finds an anomalous set of
    peaks and troughs, so can't calculate an amplitude.
    """

    def __init__(self, err):
        super().__init__(err)
        self.msg = err


class ResponseNotFoundError(Exception):
    """
    Raised when the inventory doesn't contain response information for
    a trace.
    """

    def __init__(self, e, tr_id):
        super().__init__(f"ResponseNotFoundError: {e} -- skipping {tr_id}")


class ResponseRemovalError(Exception):
    """Raised when removing the instrument response fails."""

    def __init__(self, e, tr_id):
        super().__init__(f"ResponseRemovalError: {e} -- skipping {tr_id}")


def peak_to_trough_amplitude(trace):
    """
    Measure the maximum peak-to-trough amplitude of a trace, plus the
    approximate frequency of that signal (from the peak-to-trough time)
    and the time at which it occurs.

    NOTE: Returns *half* the maximum peak-to-trough amplitude, as this
    is what local magnitude is defined from. Units are *millimetres*.

    Parameters
    ----------
    trace : `obspy.Trace`
        Waveform corrected to displacement, in metres.

    Returns
    -------
    half_amp : float
        Half the maximum peak-to-trough amplitude, in millimetres.
    approx_freq : float
        Approximate frequency of the arrival, from the half-period
        between the maximum peak and trough.
    p2t_time : `obspy.UTCDateTime`
        Approximate time of the measurement (halfway between the peak
        and trough times).

    Raises
    ------
    PeakToTroughError
        If no peaks or troughs are found, or consecutive peaks or
        troughs are found.

    """
    peaks, _ = find_peaks(trace.data)
    troughs, _ = find_peaks(-trace.data)

    # Loop through possible orders of peaks and troughs to find the maximum
    # peak-to-peak amplitude, and the time difference separating the peaks
    full_amp = None
    if len(peaks) == 0 or len(troughs) == 0:
        raise PeakToTroughError("No peaks or troughs found!")
    elif len(peaks) == 1 and len(troughs) == 1:
        full_amp = np.abs(trace.data[peaks] - trace.data[troughs])[0]
        pos = 0
    elif len(peaks) == len(troughs):
        if peaks[0] < troughs[0]:
            a, b, c, d = peaks, troughs, peaks[1:], troughs[:-1]
        else:
            a, b, c, d = peaks, troughs, peaks[:-1], troughs[1:]
    elif not np.abs(len(peaks) - len(troughs)) == 1:
        # More than two peaks/troughs next to one another
        raise PeakToTroughError("Consecutive peaks/troughs!")
    elif len(peaks) > len(troughs):
        try:
            assert peaks[0] < troughs[0]
        except AssertionError:
            raise PeakToTroughError("Consecutive peaks/troughs!")
        a, b, c, d = peaks[:-1], troughs, peaks[1:], troughs
    elif len(peaks) < len(troughs):
        try:
            assert peaks[0] > troughs[0]
        except AssertionError:
            raise PeakToTroughError("Consecutive peaks/troughs!")
        a, b, c, d = peaks, troughs[1:], peaks, troughs[:-1]

    if not full_amp:
        fp1 = np.abs(trace.data[a] - trace.data[b])
        fp2 = np.abs(trace.data[c] - trace.data[d])
        if np.max(fp1) >= np.max(fp2):
            pos = np.argmax(fp1)
            full_amp = np.max(fp1)
            peaks, troughs = a, b
        else:
            pos = np.argmax(fp2)
            full_amp = np.max(fp2)
            peaks, troughs = c, d

    peak_time = trace.times()[peaks[pos]]
    trough_time = trace.times()[troughs[pos]]
    p2t_time = trace.stats.starttime + peak_time + (trough_time - peak_time) / 2

    # Peak-to-trough is half a period
    approx_freq = 1.0 / (np.abs(peak_time - trough_time) * 2.0)

    # Local magnitude is defined based on maximum zero-to-peak amplitude in
    # *millimetres*
    half_amp = full_amp * 1000 / 2

    return half_amp, approx_freq, p2t_time


def wa_response(convert="DIS2DIS", obspy_def=True):
    """
    Generate a Wood-Anderson response dictionary.

    Parameters
    ----------
    convert : {'DIS2DIS', 'VEL2VEL', 'VEL2DIS'}
        Type of output to convert between; determines the number of
        complex zeros used.
    obspy_def : bool, optional
        Use the ObsPy definition of the Wood-Anderson response
        (default). Otherwise, use the IRIS/SAC definition.

    Returns
    -------
    woodanderson : dict
        Poles, zeros, sensitivity and gain of the Wood-Anderson
        torsion seismograph.

    """
    if obspy_def:
        # Create Wood-Anderson response - ObsPy values
        woodanderson = {
            "poles": [-6.283185 - 4.712j, -6.283185 + 4.712j],
            "zeros": [0j],
            "sensitivity": 2080,
            "gain": 1.0,
        }
    else:
        # Create Wood Anderson response - different to the ObsPy values
        # http://www.iris.washington.edu/pipermail/sac-help/2013-March/001430.html
        woodanderson = {
            "poles": [-5.49779 + 5.60886j, -5.49779 - 5.60886j],
            "zeros": [0j],
            "sensitivity": 2080,
            "gain": 1.0,
        }

    if convert in ("DIS2DIS", "VEL2VEL"):
        # Add an extra zero to go from disp to disp or vel to vel.
        woodanderson["zeros"].extend([0j])

    return woodanderson


# (trace id, velocity, inventory) -> poles/zeros dict. The inventory is
# read once at startup and never changes while the program runs, so the
# response only needs looking up once per trace id rather than on every
# tick (inv.get_response() deep-copies the response each time it's
# called). Trace.simulate() only reads the cached dict, never mutates it.
_paz_cache = {}


def _get_paz_dict(tr, inv, velocity):
    key = (tr.id, velocity, id(inv))
    cached = _paz_cache.get(key)
    if cached is not None:
        return cached

    try:
        response = inv.get_response(tr.id, tr.stats.starttime)
    except Exception as e:
        raise ResponseNotFoundError(str(e), tr.id)

    # Instrument transfer function as poles and zeros
    paz = response.get_paz()

    if not velocity:
        paz.zeros.extend([0j])

    paz_dict = {
        "poles": paz.poles,
        "zeros": paz.zeros,
        "gain": paz.normalization_factor,
        "sensitivity": response.instrument_sensitivity.value,
    }
    _paz_cache[key] = paz_dict
    return paz_dict


def get_real_waveform(tr, inv, velocity=True, pre_filt=None, water_level=60):
    """
    Remove the instrument response from a trace.

    Only the poles-and-zeros part of the response is removed (not the
    digital FIR stages), which is much faster than a full
    remove_response().

    Parameters
    ----------
    tr : `obspy.Trace`
        Waveform to correct.
    inv : `obspy.Inventory`
        Inventory containing the response for tr.
    velocity : bool, optional
        Output velocity (default) rather than displacement.
    pre_filt, water_level : optional
        Passed to `obspy.Trace.simulate()`.

    Returns
    -------
    tr : `obspy.Trace`
        Corrected copy of the input trace.

    Raises
    ------
    ResponseNotFoundError
        If inv has no response for this trace.
    ResponseRemovalError
        If the deconvolution fails.

    """
    tr = tr.copy()
    _detrend(tr, "linear")

    paz_dict = _get_paz_dict(tr, inv, velocity)

    try:
        _simulate(
            tr,
            paz_remove=paz_dict,
            pre_filt=pre_filt,
            water_level=water_level,
            taper=True,
            sacsim=True,  # To replicate remove_response()
            pitsasim=False,  # To replicate remove_response()
        )
    except ValueError as e:
        raise ResponseRemovalError(e, tr.id)

    return tr


def get_wa_waveform(tr, inv, pre_filt=None, water_level=60, velocity=False):
    """
    Simulate a Wood-Anderson displacement seismogram from a trace.

    Note that pre_filt and water_level apply to the Wood-Anderson
    simulation step only; the response removal inside
    get_real_waveform() uses that function's own defaults.

    Parameters
    ----------
    tr : `obspy.Trace`
        Waveform to convert.
    inv : `obspy.Inventory`
        Inventory containing the response for tr.
    velocity : bool, optional
        Output velocity instead of displacement (default False). The
        Hutton-Boore relation in get_local_mag() is for displacement.

    Returns
    -------
    tr : `obspy.Trace`
        Wood-Anderson-corrected copy of the input trace.

    """
    tr = tr.copy()
    _detrend(tr, "linear")

    # Remove instrument response
    tr = get_real_waveform(tr, inv, velocity)

    # Simulate Wood-Anderson response
    _simulate(
        tr,
        paz_simulate=wa_response(obspy_def=True),
        pre_filt=pre_filt,
        water_level=water_level,
        taper=True,
        sacsim=True,  # To replicate remove_response()
        pitsasim=False,  # To replicate remove_response()
    )

    return tr


def get_local_mag(amp, dist):
    """
    Local magnitude from a Wood-Anderson amplitude and distance, using
    the Hutton-Boore calibration.

    Parameters
    ----------
    amp : float
        Half peak-to-trough amplitude of the Wood-Anderson trace, in mm.
    dist : float
        Distance in km.

    Returns
    -------
    mag : float

    """
    att = 1.11 * np.log10(dist / 100.0) + 0.00189 * (dist - 100.0) + 3.0  # Hutton-Boore
    mag = np.log10(amp) + att

    return mag
