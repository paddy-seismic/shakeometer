#!/usr/bin/env bash
#
# install_shakeometer_pi.sh
#
# Turn a Raspberry Shake (Pi 3B, Shake OS / Raspbian "buster", Python 3.7)
# into a self-contained shakeometer display with an HDMI monitor attached:
#
#   1. Desktop environment (LXDE-pi + lightdm) from the legacy buster repos
#   2. Python packages from the repo's docs/requirements-pi.txt (binary wheels
#      only - nothing is compiled on the Pi)
#   3. The shakeometer: station code set in the run_*_shake.sh scripts,
#      StationXML downloaded, desktop shortcuts installed
#   4. Optional: autologin, no screen blanking, autostart (combo or gauge),
#      screen resolution, wallpaper, hidden mouse pointer, forced HDMI output
#
# It never runs `apt upgrade`, so the Raspberry Shake software is left alone.
# It is safe to re-run: to change options, update the shakeometer, or retry
# after a failed step. Previous answers are remembered as the new defaults.
#
# Usage:
#   sudo ./install_shakeometer_pi.sh [options] [SOURCE]
#
# SOURCE can be any of:
#   ./shakeometer/                          an unpacked folder
#   shakeometer-main.tar.gz | .tgz | .zip   an archive
#   https://.../shakeometer.git             a git repository (cloned)
#   https://.../shakeometer-main.tar.gz     an archive URL (downloaded)
# If omitted, DEFAULT_SOURCE (below) is used.
#
# Options:
#   -y, --yes          Don't ask anything: use preset/remembered/default answers
#   -r, --reconfigure  After an install: only change the desktop & display
#                      settings (event/permanent, autostart, resolution, ...).
#                      No downloads, no internet needed.
#   --event            Preset: set up for events/visits (no auto-restart, pointer visible)
#   --permanent        Preset: always-on display (restart after crash, hide pointer)
#   -h, --help         Show this help
#
# The install saves a copy of this script as `shakeometer-setup`, so later:
#
#   sudo shakeometer-setup --reconfigure                  # change settings, asks questions
#   sudo shakeometer-setup --reconfigure --permanent -y   # switch to permanent display
#   sudo shakeometer-setup --reconfigure --event -y       # switch back to event mode
#   sudo shakeometer-setup                                # full re-install / update
#
# Every question can be pre-answered with an environment variable (the
# SHAKE_* names in ask_questions below), e.g. to roll out over SSH:
#
#   sudo SHAKE_AUTOSTART=gauge ./install_shakeometer_pi.sh -y shakeometer.tar.gz
#
# Log: /var/log/shakeometer-install.log
# Files changed in /etc and /boot get a one-off backup with a .shakeometer-orig suffix.

# ============================================================================
# Settings
# ============================================================================

DEFAULT_SOURCE="https://git.dias.ie/paddy/shakeometer.git"
FALLBACK_SOURCE="https://github.com/paddy-seismic/shakeometer.git"   # tried if DEFAULT_SOURCE can't be cloned

# A file at the top level of the repo; used to find the repo inside archives.
REPO_MARKER="shakeometer.py"

# Files in the repo (relative paths).
RUN_SCRIPTS=("run_gauge_shake.sh" "run_combo_shake.sh")   # SHAKE="..." gets set in these
DESKTOP_FILES_GLOB="desktop/*.desktop"
DESKTOP_FILES_PATH="/home/myshake/Software/shakeometer"   # path the shortcuts are written for
PI_REQUIREMENTS="docs/requirements-pi.txt"
WALLPAPER_IN_REPO="docs/images/quakeshake_wallpaper_pi.png"

# Only used if docs/requirements-pi.txt is missing.
PIP_PACKAGES_FALLBACK=("numpy==1.21.4" "matplotlib==3.5.3" "obspy==1.3.1" "pyqtgraph==0.11.1")
PIP_SELF_SPEC="pip<24.1"   # pip 24.0 is the last pip that supports Python 3.7
PIWHEELS_URL="https://www.piwheels.org/simple"

LEGACY_APT_LINE="deb https://legacy.raspbian.org/raspbian/ buster main contrib non-free rpi"
FDSN_STATION="https://data.raspberryshake.org/fdsnws/station/1/query"
SEEDLINK_HOST="localhost"; SEEDLINK_PORT=18000   # the Shake's own SeedLink server
WALLPAPER_MODE="fit"   # pcmanfm: fit | crop | stretch | center | tile | color
REFRESH_RATE=60

# --- Apt packages -----------------------------------------------------------
APT_CORE=(
    python3-pip python3-pyqt5
    libatlas-base-dev        # BLAS/LAPACK for numpy/scipy wheels
    libxslt1.1               # lxml wheel (obspy)
    libopenjp2-7 libtiff5    # Pillow wheel (matplotlib)
    fonts-liberation         # clean gauge font (see README "Fonts")
    git unzip curl ca-certificates
    vim rsync htop
)
APT_DESKTOP=(
    raspberrypi-ui-mods xserver-xorg lightdm
    libgl1-mesa-dri          # needed by the KMS GL drivers
    x11-xserver-utils        # xrandr / xset
    arandr                   # GUI screen layout, in case the resolution needs fixing by hand
)
APT_BEST_EFFORT=( fonts-urw-base35 )   # Nimbus Sans, the gauge's first-choice font; skipped if unavailable
APT_EXTRAS=( chromium default-jre )    # optional: web browser, Java (for Swarm)
# Background services the desktop pulls in that a Shake display doesn't need
# (printing). Disabled - not removed - after install; `systemctl enable --now` undoes it.
DISABLE_SERVICES=( cups cups-browsed )

# ============================================================================
# Below here should not need editing.
# ============================================================================

set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"
LOG_FILE="/var/log/shakeometer-install.log"
ANSWERS_FILE="/etc/shakeometer-install.conf"
LIGHTDM_CONF="/etc/lightdm/lightdm.conf"
BOOT_CONFIG="/boot/config.txt"
BOOT_CMDLINE="/boot/cmdline.txt"
WAIT_HELPER="/usr/local/bin/wait-for-seedlink"
DISPLAY_SETUP="/usr/local/bin/shakeometer-display-setup"
NETWORK="AM"
ASSUME_YES=0
RECONFIGURE=0
SETUP_CMD="/usr/local/sbin/shakeometer-setup"   # installed copy of this script
SOURCE=""
PREV_USE=""
declare -A FROM_FILE=()
INVENTORY_FILE=""
TMP_DIRS=()
WARNINGS=()

# --- Output helpers ---------------------------------------------------------
if [[ -t 1 ]]; then
    C_B=$'\e[1m'; C_G=$'\e[32m'; C_Y=$'\e[33m'; C_R=$'\e[31m'; C_C=$'\e[36m'; C_0=$'\e[0m'
else
    C_B=""; C_G=""; C_Y=""; C_R=""; C_C=""; C_0=""
fi
step() { printf '\n%s==> %s%s\n' "$C_B$C_C" "$*" "$C_0"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    %s✔ %s%s\n' "$C_G" "$*" "$C_0"; }
warn() { printf '    %s⚠ %s%s\n' "$C_Y" "$*" "$C_0" >&2; WARNINGS+=("$*"); }
die()  { printf '\n%s✘ %s%s\n' "$C_R" "$*" "$C_0" >&2; exit 1; }

on_error() {
    local rc=$1 line=$2 cmd=$3
    printf '\n%s✘ Failed (exit %s) at line %s: %s%s\n' "$C_R" "$rc" "$line" "$cmd" "$C_0" >&2
    printf '  Full log: %s. Fix the problem and re-run; finished steps are just redone.\n' "$LOG_FILE" >&2
    exit "$rc"
}
cleanup() { local d; for d in "${TMP_DIRS[@]:-}"; do [[ -n "$d" ]] && rm -rf "$d"; done; }

usage() { sed -n '2,/^# Files changed/{s/^# \{0,1\}//;p}' "$0"; exit 0; }

# --- Prompt helpers ---------------------------------------------------------
# ask VAR "Question" default   (an existing value of VAR wins over default)
ask() {
    local __var=$1 __q=$2 __def=${!1:-$3} __reply=""
    if (( ASSUME_YES )); then
        printf -v "$__var" '%s' "$__def"
        info "$__q: $__def"
        return
    fi
    read -r -p "    $__q [$__def]: " __reply </dev/tty || true
    printf -v "$__var" '%s' "${__reply:-$__def}"
}
# ask_yn VAR "Question" y|n   -> VAR becomes y or n
ask_yn() {
    local __var=$1 __q=$2 __def=${!1:-$3} __reply=""
    __def=$(norm_yn "$__def") || __def=$3
    while true; do
        if (( ASSUME_YES )); then __reply=$__def; info "$__q: $__def"
        else read -r -p "    $__q [$( [[ $__def == y ]] && echo Y/n || echo y/N )]: " __reply </dev/tty || true
        fi
        if __reply=$(norm_yn "${__reply:-$__def}"); then
            printf -v "$__var" '%s' "$__reply"; return
        fi
        info "Please answer y or n."
    done
}
# ask_choice VAR "Question" default opt1 opt2 ...
ask_choice() {
    local __var=$1 __q=$2 __def=${!1:-$3} __orig=$3; shift 3
    local __opts=" $* "
    while true; do
        ask "$__var" "$__q ($(echo "$*" | tr ' ' '/'))" "$__def"
        [[ "$__opts" == *" ${!__var} "* ]] && return
        (( ASSUME_YES )) && die "Invalid value '${!__var}' for $__var (choose from: $*)"
        info "Please choose one of: $*"
        __def=$__orig
    done
}
norm_yn() {
    case "${1,,}" in y|yes|1|true) echo y ;; n|no|0|false) echo n ;; *) return 1 ;; esac
}
is_yes() { [[ "$1" == y ]]; }

confirm_or_die() {
    local ans=""
    if (( ASSUME_YES )); then warn "$1 -> continuing (--yes)"; return; fi
    read -r -p "    $C_Y$1 [y/N]: $C_0" ans </dev/tty || true
    [[ $(norm_yn "${ans:-n}") == y ]] || die "Stopped."
}

# --- System helpers ---------------------------------------------------------
as_user() { sudo -u "$TARGET_USER" -H env PIP_DISABLE_PIP_VERSION_CHECK=1 "$@"; }

backup_file() {   # one-off backup of the original
    [[ -f "$1" && ! -e "$1.shakeometer-orig" ]] && cp -a "$1" "$1.shakeometer-orig"
    return 0
}
pkg_installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"; }
apt_install() {
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$@"
}
make_tmp() { local d; d=$(mktemp -d); TMP_DIRS+=("$d"); printf '%s' "$d"; }
user_file() {     # user_file PATH MODE  <stdin  - write a file owned by the target user
    install -d -o "$TARGET_USER" -g "$TARGET_GROUP" "$(dirname "$1")"
    cat >"$1"; chown "$TARGET_USER:$TARGET_GROUP" "$1"; chmod "$2" "$1"
}
tcp_open() { timeout 3 bash -c ">/dev/tcp/$1/$2" 2>/dev/null; }

# Python helper for editing config files (written to a temp file at start).
write_py_helper() {
    PY_HELPER="$(make_tmp)/cfgedit.py"
    cat >"$PY_HELPER" <<'PY'
import re
import sys


def read(path):
    try:
        with open(path) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def write(path, lines):
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def set_key(path, key, value):
    """Replace the value of every 'key = v' / 'key: v' / 'key=v' line,
    keeping quotes around the old value and any trailing # comment."""
    pat = re.compile(r"^(\s*)(" + re.escape(key) + r")(\s*[:=]\s*)([\"']?)(.*?)\4(\s+#.*)?$")
    lines, n = read(path), 0
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m:
            lead, k, sep, q, _old, comment = m.groups()
            lines[i] = lead + k + sep + q + value + q + (comment or "")
            n += 1
    if n:
        write(path, lines)
    return 0 if n else 3


def get_key(path, section, key):
    """Print the value of key in [section] (empty if not set)."""
    hdr, cur = "[" + section + "]", None
    kpat = re.compile(r"^\s*" + re.escape(key) + r"\s*=(.*)$")
    for l in read(path):
        if l.strip().startswith("["):
            cur = l.strip()
        elif cur == hdr:
            m = kpat.match(l)
            if m:
                print(m.group(1).strip())
                return 0
    return 0


def ini_set(path, section, key, value):
    """Set key=value in [section] (case-sensitive), creating either if needed."""
    lines = read(path)
    hdr = "[" + section + "]"
    start = next((i for i, l in enumerate(lines) if l.strip() == hdr), None)
    new = key + "=" + value
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [hdr, new]
    else:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].strip().startswith("[")), len(lines))
        kpat = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
        idx = next((i for i in range(start + 1, end) if kpat.match(lines[i])), None)
        if idx is not None:
            lines[idx] = new
        else:
            cpat = re.compile(r"^\s*#\s*" + re.escape(key) + r"\s*=")
            cidx = next((i for i in range(start + 1, end) if cpat.match(lines[i])), None)
            if cidx is not None:
                ins = cidx + 1
            else:
                ins = end
                while ins > start + 1 and not lines[ins - 1].strip():
                    ins -= 1
            lines.insert(ins, new)
    write(path, lines)
    return 0


def ini_comment(path, section, key):
    """Comment out key=... inside [section]."""
    lines = read(path)
    hdr = "[" + section + "]"
    kpat = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    cur = None
    for i, l in enumerate(lines):
        if l.strip().startswith("["):
            cur = l.strip()
        elif cur == hdr and kpat.match(l):
            lines[i] = "#" + l
    write(path, lines)
    return 0


def boot_set(path, line, comment_regex=""):
    """Ensure `line` is active in the global/[all] part of /boot/config.txt.
    Other active lines matching comment_regex are commented out."""
    lines = read(path)
    creg = re.compile(comment_regex) if comment_regex else None
    applies = (None, "[all]", "[pi3]")
    cur, present = None, False
    for i, l in enumerate(lines):
        s = l.strip()
        if re.match(r"^\[.*\]$", s):
            cur = s
            continue
        if s == line and cur in applies:
            present = True
        elif creg and cur in applies and not s.startswith("#") and creg.search(s):
            lines[i] = "#" + l
    if not present:
        last = None
        for l in lines:
            if re.match(r"^\[.*\]$", l.strip()):
                last = l.strip()
        if last not in (None, "[all]"):
            lines += ["", "[all]"]
        lines.append(line)
    write(path, lines)
    return 0


def boot_unset(path, regex):
    lines = read(path)
    r = re.compile(regex)
    lines = ["#" + l if (not l.strip().startswith("#") and r.search(l)) else l for l in lines]
    write(path, lines)
    return 0


def cmdline_set(path, remove_regex, token=""):
    """/boot/cmdline.txt is ONE line of space-separated tokens. Drop tokens
    matching remove_regex, then append `token` (if given)."""
    with open(path) as f:
        text = f.read()
    r = re.compile(remove_regex)
    tokens = [t for t in text.split() if not r.search(t)]
    if not tokens:
        sys.exit("refusing to write an empty " + path)
    if token:
        tokens.append(token)
    with open(path, "w") as f:
        f.write(" ".join(tokens) + "\n")
    return 0


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    sys.exit(globals()[cmd](*args))
PY
}
set_key() {
    local rc=0
    python3 "$PY_HELPER" set_key "$@" || rc=$?
    if (( rc == 3 )); then warn "Key '$2' not found in $(basename "$1") - not set"; elif (( rc )); then return "$rc"; fi
}
get_key()     { python3 "$PY_HELPER" get_key "$@"; }
ini_set()     { python3 "$PY_HELPER" ini_set "$@"; }
ini_comment() { python3 "$PY_HELPER" ini_comment "$@"; }
boot_set()    { python3 "$PY_HELPER" boot_set "$@"; }
boot_unset()  { python3 "$PY_HELPER" boot_unset "$@"; }
cmdline_set() { python3 "$PY_HELPER" cmdline_set "$@"; }

# ============================================================================
# Steps
# ============================================================================

parse_args() {
    while (( $# )); do
        case "$1" in
            -y|--yes)          ASSUME_YES=1 ;;
            -r|--reconfigure)  RECONFIGURE=1 ;;
            --event)           SHAKE_USE=event ;;
            --permanent)       SHAKE_USE=permanent ;;
            -h|--help)         usage ;;
            -*)        die "Unknown option: $1 (see --help)" ;;
            *)         [[ -z "$SOURCE" ]] || die "Only one SOURCE argument allowed"; SOURCE=$1 ;;
        esac
        shift
    done
}

ensure_root() {
    if (( EUID != 0 )); then
        echo "Needs root - re-running with sudo..."
        exec sudo --preserve-env bash "$(readlink -f "$0")" "$@"
    fi
}

load_previous_answers() {
    # Remembered answers become defaults, but env vars given now take priority.
    [[ -f "$ANSWERS_FILE" ]] || return 0
    local line var
    while IFS= read -r line; do
        [[ "$line" =~ ^(SHAKE_[A-Z_]+)= ]] || continue
        var=${BASH_REMATCH[1]}
        if [[ "$var" == SHAKE_USE ]]; then PREV_USE=$(eval "$line"; echo "$SHAKE_USE"); fi
        if [[ -z "${!var:-}" ]]; then eval "$line"; FROM_FILE[$var]=1; fi
    done <"$ANSWERS_FILE"
    info "Loaded previous answers from $ANSWERS_FILE"
}

save_answers() {
    local v
    {
        echo "# Answers from the last run of $SCRIPT_NAME on $(date -Is)."
        echo "# Used as defaults next time. Delete this file to start fresh."
        for v in $(compgen -v SHAKE_); do printf '%s=%q\n' "$v" "${!v}"; done
    } >"$ANSWERS_FILE"
}

preflight() {
    step "Checking this system"
    local codename="" arch py avail_kb yr
    codename=$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-unknown}")
    arch=$(uname -m)
    py=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none)
    info "OS: $codename   arch: $arch   python3: $py"

    if [[ "$codename" != buster ]]; then
        warn "This Shake runs '$codename', not buster (the current Shake OS)."
        warn "Installing would pull in buster packages - including Python 3.7 and core"
        warn "system libraries - leaving a mixed $codename/buster system. Re-flashing the"
        warn "SD card with the current Shake OS first is the cleaner option."
        confirm_or_die "Continue anyway?"
    elif [[ "$py" != 3.7 ]]; then
        if [[ -x /usr/bin/python3.7 || -e /usr/lib/arm-linux-gnueabihf/libpython3.7m.so.1.0 ]]; then
            warn "The python3 command is Python $py (left over from an older Shake OS), but Python 3.7"
            warn "is already installed. The install will switch python3 to 3.7 - anything on this"
            warn "Shake that runs plain 'python3' will then get 3.7 instead of $py."
        else
            warn "Expected Python 3.7 (got $py) - the pinned packages may not have wheels."
        fi
        confirm_or_die "Continue anyway?"
    fi
    if [[ -d /opt/settings ]]; then ok "Looks like a Raspberry Shake"
    else confirm_or_die "/opt/settings not found - this doesn't look like a Raspberry Shake. Continue anyway?"
    fi

    yr=$(date +%Y)
    (( yr >= 2025 )) || warn "System clock says $(date) - HTTPS downloads will fail until the time is synced (NTP)."

    avail_kb=$(df --output=avail -k / | tail -1 | tr -d ' ')
    info "Free space on /: $(( avail_kb / 1024 )) MB"
    (( avail_kb > 2*1024*1024 )) || confirm_or_die "Less than 2 GB free - the desktop alone needs ~1 GB. Continue anyway?"

    if tcp_open legacy.raspbian.org 443; then ok "Internet access OK"
    else die "Can't reach legacy.raspbian.org:443 - the Shake needs internet access for the install."
    fi

    if [[ -n "$SOURCE" && ! "$SOURCE" =~ ^(https?://|git@|ssh://) ]]; then
        [[ -e "$SOURCE" ]] || die "SOURCE '$SOURCE' does not exist"
        SOURCE=$(readlink -f "$SOURCE")
    fi
}

ask_questions() {
    step "Questions (Enter accepts the [default])"
    local def_user=${SUDO_USER:-} def_station=""
    [[ -z "$def_user" || "$def_user" == root ]] && def_user=myshake
    [[ -s /opt/settings/sys/STN.txt ]] && def_station=$(tr -d '[:space:]' </opt/settings/sys/STN.txt)

    ask SHAKE_USER "User that runs the desktop & shakeometer" "$def_user"
    id "$SHAKE_USER" &>/dev/null || die "User '$SHAKE_USER' does not exist"
    TARGET_USER=$SHAKE_USER
    TARGET_GROUP=$(id -gn "$TARGET_USER")
    TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)

    [[ -n "$SOURCE" ]] && SHAKE_SOURCE=$SOURCE   # command-line argument beats a remembered answer
    ask SHAKE_SOURCE "Shakeometer source (folder, archive, git or archive URL)" "$DEFAULT_SOURCE"
    SOURCE=$SHAKE_SOURCE
    ask SHAKE_INSTALL_DIR "Install shakeometer into" "$TARGET_HOME/Software/shakeometer"

    while true; do
        ask SHAKE_STATION "Station code" "$def_station"
        [[ "$SHAKE_STATION" =~ ^[A-Za-z0-9]{1,5}$ ]] && break
        (( ASSUME_YES )) && die "No valid station code - set SHAKE_STATION=<code>"
        info "Station codes are 1-5 letters/digits, e.g. R1234"; SHAKE_STATION=""
    done
    ask_yn SHAKE_FETCH_XML "Download its StationXML (instrument response) now?" y
    ask_yn SHAKE_DESKTOP "Install the desktop environment (skip if already installed)?" \
        "$(pkg_installed raspberrypi-ui-mods && echo n || echo y)"
    ask_yn SHAKE_EXTRAS  "Also install ${APT_EXTRAS[*]}?" n

    ask_display_questions

    STATION=$SHAKE_STATION
    INSTALL_DIR=$SHAKE_INSTALL_DIR

    if (( ! ASSUME_YES )); then
        echo
        confirm_or_die "Ready to install for $TARGET_USER into $INSTALL_DIR. Go?"
    fi
}

ask_display_questions() {
    echo
    info "${C_B}Desktop & display$C_0"
    ask_choice SHAKE_USE "How will this Shake be used? (event = set up for events/visits; permanent = always-on display)" event event permanent
    local perm=n v; [[ "$SHAKE_USE" == permanent ]] && perm=y
    # Switching mode: answers that just followed the old mode's defaults get the new ones.
    if [[ -n "$PREV_USE" && "$SHAKE_USE" != "$PREV_USE" ]]; then
        for v in SHAKE_RESTART SHAKE_HIDECURSOR; do
            [[ -n "${FROM_FILE[$v]:-}" ]] && unset "$v"
        done
        info "Switching from $PREV_USE to $SHAKE_USE mode"
    fi
    ask_choice SHAKE_GL "GL driver (kms = raspi-config 'GL (Full KMS)'; keep = don't touch)" kms kms fkms legacy keep
    while true; do
        ask SHAKE_RESOLUTION "Screen resolution (WIDTHxHEIGHT, or 'auto' for the monitor's default)" "1600x900"
        [[ "$SHAKE_RESOLUTION" =~ ^([0-9]{3,4}x[0-9]{3,4}|auto)$ ]] && break
        (( ASSUME_YES )) && die "Invalid SHAKE_RESOLUTION '$SHAKE_RESOLUTION'"
        SHAKE_RESOLUTION=""
    done
    ask_yn SHAKE_HDMI       "Always enable HDMI (monitor plugged in after boot still works)?" y
    ask_yn SHAKE_AUTOLOGIN  "Log in to the desktop automatically at boot?" y
    ask_yn SHAKE_NOBLANK    "Disable screen blanking / power saving?" y
    ask_choice SHAKE_AUTOSTART "Start a shakeometer display automatically after login?" combo combo gauge none
    if [[ "$SHAKE_AUTOSTART" != none ]]; then
        ask_yn SHAKE_RESTART "Restart it automatically if it crashes?" "$perm"
    else
        SHAKE_RESTART=n
    fi
    ask_yn SHAKE_HIDECURSOR "Hide the mouse pointer when idle?" "$perm"
    ask SHAKE_WALLPAPER     "Wallpaper: 'repo', 'none', a file path or a URL" "repo"
    ask_yn SHAKE_REBOOT     "Reboot when finished?" y
}

reconfigure_setup() {
    # Everything except the display settings comes from the last install.
    [[ -f "$ANSWERS_FILE" ]] || die "No previous install found ($ANSWERS_FILE missing) - run the full install first."
    local v
    for v in SHAKE_USER SHAKE_INSTALL_DIR SHAKE_STATION; do
        [[ -n "${!v:-}" ]] || die "$v missing from $ANSWERS_FILE - run the full install again."
    done
    id "$SHAKE_USER" &>/dev/null || die "User '$SHAKE_USER' does not exist"
    TARGET_USER=$SHAKE_USER
    TARGET_GROUP=$(id -gn "$TARGET_USER")
    TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
    STATION=$SHAKE_STATION
    INSTALL_DIR=$SHAKE_INSTALL_DIR
    [[ -d "$INSTALL_DIR" ]] || die "$INSTALL_DIR not found - run the full install again."
    step "Changing display settings for $NETWORK.$STATION (shakeometer in $INSTALL_DIR)"
    info "Currently: ${PREV_USE:-?} mode, autostart ${SHAKE_AUTOSTART:-?}"
    ask_display_questions
    if (( ! ASSUME_YES )); then
        echo; confirm_or_die "Apply these settings?"
    fi
}

ensure_unclutter() {
    # Only needed if the pointer is to be hidden and it wasn't installed first time round.
    is_yes "$SHAKE_HIDECURSOR" || return 0
    pkg_installed unclutter && return 0
    step "Installing unclutter (hides the mouse pointer)"
    apt_install unclutter || warn "Couldn't install unclutter (no internet?) - the pointer stays visible"
}

install_self() {
    # Keep a copy of this script on the Shake for later --reconfigure / updates.
    local me; me=$(readlink -f "$0")
    [[ "$me" == "$(readlink -f "$SETUP_CMD")" ]] && return 0
    install -D -m 0755 "$me" "$SETUP_CMD"
}

setup_apt() {
    step "Apt sources (legacy buster repo)"
    local f=/etc/apt/sources.list other
    backup_file "$f"
    # Comment out any other raspbian buster mirror, add the legacy archive.
    sed -i -E '/legacy\.raspbian\.org/! s|^([[:space:]]*deb(-src)?[[:space:]]+[^[:space:]]*raspbian[^[:space:]]*[[:space:]]+buster([[:space:]].*)?)$|#\1|' "$f"
    if ! grep -qE '^[[:space:]]*deb[[:space:]]+https?://legacy\.raspbian\.org/raspbian/?[[:space:]]+buster' "$f"; then
        printf '%s\n' "$LEGACY_APT_LINE" >>"$f"
    fi
    for other in /etc/apt/sources.list.d/*.list; do
        [[ -f "$other" ]] || continue
        if grep -qE '^[[:space:]]*deb .*raspbian.* buster' "$other" && ! grep -q legacy "$other"; then
            warn "$other also lists a raspbian buster mirror - comment it out if apt update fails"
        fi
    done
    ok "Using: $LEGACY_APT_LINE"

    step "apt-get update"
    # --allow-releaseinfo-change: buster's suite name changed (stable -> oldoldstable).
    apt-get update --allow-releaseinfo-change || die "apt-get update failed (see above)."
}

install_packages() {
    local pkgs=("${APT_CORE[@]}") p
    is_yes "$SHAKE_DESKTOP"    && pkgs+=("${APT_DESKTOP[@]}")
    is_yes "$SHAKE_EXTRAS"     && pkgs+=("${APT_EXTRAS[@]}")
    is_yes "$SHAKE_HIDECURSOR" && pkgs+=(unclutter)
    step "Installing apt packages (only what's needed - no full system upgrade)"
    info "${pkgs[*]}"
    # Say up front which already-installed packages apt will upgrade as dependencies.
    local upgrades
    upgrades=$(apt-get -s install "${pkgs[@]}" 2>/dev/null | awk '/^Inst [^ ]+ \[/ {printf "%s ", $2}' || true)
    if [[ -n "$upgrades" ]]; then
        info "Already-installed packages that will be upgraded as dependencies:"
        info "  $upgrades"
    fi
    apt_install "${pkgs[@]}"
    for p in "${APT_BEST_EFFORT[@]}"; do
        apt_install "$p" >/dev/null 2>&1 || info "($p not available - skipped)"
    done
    ok "Packages installed"
    disable_services
}

fetch_source() {
    step "Getting the shakeometer from $SOURCE"
    local src=$SOURCE work root
    work=$(make_tmp)

    if [[ ! "$src" =~ ^(https?://|git@|ssh://) && ! -e "$src" ]]; then
        die "Source '$src' not found"
    fi
    if [[ -d "$src" ]]; then
        root=$src
    else
        if [[ "$src" =~ ^https?:// && "$src" =~ \.(tar\.gz|tgz|tar|zip)(\?.*)?$ ]]; then
            local name; name=$(basename "${src%%\?*}")
            curl -fL --retry 3 -o "$work/$name" "$src" || die "Download failed: $src"
            src="$work/$name"
        elif [[ "$src" =~ ^(https?://|git@|ssh://) ]]; then
            if ! git clone --depth 1 "$src" "$work/repo"; then
                [[ "$src" == "$DEFAULT_SOURCE" ]] || die "git clone failed: $src"
                warn "Couldn't clone $src - trying $FALLBACK_SOURCE"
                rm -rf "$work/repo"
                git clone --depth 1 "$FALLBACK_SOURCE" "$work/repo" || die "git clone failed from both GitLab and GitHub"
            fi
            src=""; root="$work/repo"
        fi
        if [[ -n "$src" ]]; then
            mkdir -p "$work/x"
            case "$src" in
                *.zip) unzip -q "$src" -d "$work/x" ;;
                *.tar.gz|*.tgz|*.tar) tar -xf "$src" -C "$work/x" ;;
                *) die "Don't know how to unpack '$src' (want a folder, .tar.gz, .tgz, .tar or .zip)" ;;
            esac
            root="$work/x"
        fi
    fi

    # Find the real repo root (archives usually wrap it in a top-level folder).
    local marker
    marker=$(find "$root" -maxdepth 3 -name "$REPO_MARKER" -not -path '*/.git/*' -print -quit)
    [[ -n "$marker" ]] || die "Can't find $REPO_MARKER in $SOURCE - is this the shakeometer?"
    root=$(dirname "$marker")

    # A source folder inside the current install would be moved away below - stage it first.
    if [[ "$(readlink -f "$root")" == "$(readlink -f "$INSTALL_DIR")"/* ]]; then
        cp -a "$root" "$work/stage"; root="$work/stage"
    fi

    if [[ "$(readlink -f "$root")" == "$(readlink -f "$INSTALL_DIR")" ]]; then
        info "Source is already the install folder - using it in place"
    else
        if [[ -e "$INSTALL_DIR" ]] && [[ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
            local base bak n=1; base="$INSTALL_DIR.bak-$(date +%Y%m%d-%H%M%S)"; bak=$base
            while [[ -e "$bak" ]]; do bak="$base.$((n++))"; done
            mv -T "$INSTALL_DIR" "$bak"
            info "Existing install moved to $bak"
        fi
        # Create a missing parent (e.g. ~/Software) owned by the user.
        [[ -d "$(dirname "$INSTALL_DIR")" ]] || install -d -o "$TARGET_USER" -g "$TARGET_GROUP" "$(dirname "$INSTALL_DIR")"
        mkdir -p "$INSTALL_DIR"
        cp -a "$root/." "$INSTALL_DIR/"
    fi
    chown -R "$TARGET_USER:$TARGET_GROUP" "$INSTALL_DIR"
    ok "Shakeometer in $INSTALL_DIR"
}

disable_services() {
    local svc off=()
    for svc in "${DISABLE_SERVICES[@]}"; do
        if systemctl list-unit-files "$svc.service" 2>/dev/null | grep -q "^$svc.service"; then
            systemctl disable --now "$svc.service" "$svc.socket" "$svc.path" >/dev/null 2>&1 || true
            off+=("$svc")
        fi
    done
    (( ${#off[@]} )) && ok "Turned off unneeded background services: ${off[*]}"
    return 0
}

install_python() {
    step "Python packages (pip --user for $TARGET_USER, binary wheels only)"
    local pipopts=(--user --prefer-binary --timeout 60 --retries 5 --no-warn-script-location
                   --extra-index-url "$PIWHEELS_URL")
    # Always `python3 -m pip` (not `pip3`): after pip upgrades itself, Debian's
    # old /usr/bin/pip3 wrapper can break, but `python3 -m pip` always works.
    as_user python3 -m pip install "${pipopts[@]}" --upgrade "$PIP_SELF_SPEC"

    local req="$INSTALL_DIR/$PI_REQUIREMENTS"
    if [[ -f "$req" ]]; then
        info "Using $PI_REQUIREMENTS"
        as_user python3 -m pip install "${pipopts[@]}" --only-binary :all: -r "$req"
    else
        warn "$PI_REQUIREMENTS not in the repo - using the fallback list"
        as_user python3 -m pip install "${pipopts[@]}" --only-binary :all: "${PIP_PACKAGES_FALLBACK[@]}"
    fi

    as_user python3 - <<'PY' || die "Python import check failed"
import numpy, obspy, pyqtgraph
from PyQt5 import QtCore
print("    numpy %s, obspy %s, pyqtgraph %s, Qt %s" % (
    numpy.__version__, obspy.__version__, pyqtgraph.__version__, QtCore.QT_VERSION_STR))
PY
    ok "Python environment OK"
}

configure_shakeometer() {
    step "Configuring the shakeometer for $NETWORK.$STATION"
    local s
    for s in "${RUN_SCRIPTS[@]}"; do
        if [[ -f "$INSTALL_DIR/$s" ]]; then
            set_key "$INSTALL_DIR/$s" SHAKE "$STATION"
            chmod +x "$INSTALL_DIR/$s"
            ok "$s: SHAKE=\"$STATION\""
        else
            warn "$s not found in the repo"
        fi
    done
    chmod +x "$INSTALL_DIR"/run_*.sh 2>/dev/null || true

    is_yes "$SHAKE_FETCH_XML" || return 0
    local url out="$INSTALL_DIR/$NETWORK.$STATION.xml"
    url="$FDSN_STATION?network=$NETWORK&station=$STATION&level=response&format=xml"
    if curl -fsSL --retry 3 -o "$out.tmp" "$url" && grep -q "<Station" "$out.tmp"; then
        mv "$out.tmp" "$out"
        chown "$TARGET_USER:$TARGET_GROUP" "$out"
        INVENTORY_FILE=$out
        ok "StationXML saved as $(basename "$out")"
    else
        rm -f "$out.tmp"
        warn "No StationXML for $NETWORK.$STATION yet (new Shakes can take a while to appear)."
        warn "Fetch it later: cd $INSTALL_DIR && curl -o $NETWORK.$STATION.xml '$url'"
        [[ -f "$out" ]] && INVENTORY_FILE=$out
    fi
}

install_shortcuts() {
    step "Desktop shortcuts"
    local f name n=0
    DESKTOP_DIR="$TARGET_HOME/Desktop"
    for f in "$INSTALL_DIR"/$DESKTOP_FILES_GLOB; do
        [[ -f "$f" ]] || continue
        name=$(basename "$f")
        # The shortcuts are written for DESKTOP_FILES_PATH; point them at INSTALL_DIR.
        sed "s|$DESKTOP_FILES_PATH|$INSTALL_DIR|g" "$f" | user_file "$DESKTOP_DIR/$name" 0755
        sed "s|$DESKTOP_FILES_PATH|$INSTALL_DIR|g" "$f" \
            | user_file "$TARGET_HOME/.local/share/applications/$name" 0644
        n=$((n + 1))
    done
    if (( n == 0 )); then warn "No $DESKTOP_FILES_GLOB in the repo - no shortcuts installed"; return 0; fi

    # Don't make pcmanfm ask "execute this file?" when an icon is double-clicked.
    local libfm="$TARGET_HOME/.config/libfm/libfm.conf"
    if [[ ! -f "$libfm" ]]; then
        install -d -o "$TARGET_USER" -g "$TARGET_GROUP" "$(dirname "$libfm")"
        if [[ -f /etc/xdg/libfm/libfm.conf ]]; then cp /etc/xdg/libfm/libfm.conf "$libfm"; else : >"$libfm"; fi
    fi
    ini_set "$libfm" config quick_exec 1
    chown "$TARGET_USER:$TARGET_GROUP" "$libfm"
    ok "$n shortcut(s) on the desktop and in the menu"
}

write_wait_helper() {
    cat >"$WAIT_HELPER" <<EOF
#!/bin/bash
# Written by $SCRIPT_NAME.
# Usage: $(basename "$WAIT_HELPER") [--cd DIR] [--restart] COMMAND [ARGS...]
#
# Runs COMMAND (a shakeometer run script) once the Shake's SeedLink server
# accepts connections: after a reboot the desktop is up well before the
# Shake's own services are. Gives up waiting after MAX_WAIT seconds and starts
# anyway. With --restart, COMMAND is started again if it stops with an error;
# quitting normally (q / Esc) or Ctrl+C stops it for good.
HOST=$SEEDLINK_HOST; PORT=$SEEDLINK_PORT; MAX_WAIT=900; RESTART=0
while :; do
    case "\$1" in
        --cd)      cd "\$2" || exit 1; shift 2 ;;
        --restart) RESTART=1; shift ;;
        *)         break ;;
    esac
done

wait_for_seedlink() {
    local waited=0
    timeout 3 bash -c ">/dev/tcp/\$HOST/\$PORT" 2>/dev/null && return
    echo "Waiting for the Raspberry Shake's SeedLink server (\$HOST:\$PORT)."
    echo "This can take a few minutes after the Shake has been switched on..."
    until timeout 3 bash -c ">/dev/tcp/\$HOST/\$PORT" 2>/dev/null; do
        if [ "\$waited" -ge "\$MAX_WAIT" ]; then echo "Still not up - starting anyway."; return; fi
        sleep 5; waited=\$((waited + 5))
    done
    echo "SeedLink server is up."
}

wait_for_seedlink
[ "\$RESTART" = 1 ] || exec "\$@"
while true; do
    "\$@"; rc=\$?
    case "\$rc" in 0|130|143) exit "\$rc" ;; esac   # normal quit, Ctrl+C, killed
    echo
    echo "\$(date '+%F %T') shakeometer stopped with error code \$rc - restarting in 10 s."
    echo "(Close this window to stop it.)"
    sleep 10
    wait_for_seedlink
done
EOF
    chmod 0755 "$WAIT_HELPER"
}

configure_autostart() {
    step "Autostart: $SHAKE_AUTOSTART"
    local dst="$TARGET_HOME/.config/autostart/shakeometer.desktop" src=""
    if [[ "$SHAKE_AUTOSTART" == none ]]; then
        rm -f "$dst"; ok "Nothing autostarts (use the desktop icons)"; return 0
    fi
    src=$(find "$DESKTOP_DIR" -maxdepth 1 -name "*${SHAKE_AUTOSTART}*.desktop" -print -quit 2>/dev/null || true)
    if [[ -z "$src" ]]; then
        warn "No *${SHAKE_AUTOSTART}*.desktop shortcut found - autostart not set up"; return 0
    fi
    write_wait_helper

    # Same as the desktop shortcut, but with the run script wrapped in the helper.
    # The helper goes *inside* the terminal, so its "waiting..." / "restarting..."
    # messages are visible, and --restart sees the run script's exit code.
    local exec_line path helper new_exec
    exec_line=$(sed -n 's/^Exec=//p' "$src" | head -1)
    path=$(sed -n 's/^Path=//p' "$src" | head -1)
    helper="$WAIT_HELPER"
    [[ -n "$path" ]] && helper+=" --cd \"$path\""
    is_yes "$SHAKE_RESTART" && helper+=" --restart"

    if [[ "$exec_line" =~ ^(lxterminal|x-terminal-emulator|xterm)[[:space:]].*[[:space:]]-e[[:space:]] ]]; then
        new_exec="${exec_line/ -e / -e $helper }"     # Exec already opens a terminal
    elif grep -qiE '^Terminal=true' "$src"; then
        new_exec="x-terminal-emulator -e $helper $exec_line"
    else
        new_exec="$helper $exec_line"
    fi
    # Terminal=false: we open the terminal ourselves (autostart doesn't reliably
    # honour Terminal=true, and these shortcuts already start lxterminal).
    E="Exec=$new_exec" awk '/^Exec=/ {print ENVIRON["E"]; next} /^Terminal=/ {print "Terminal=false"; next} {print}' "$src" \
        | user_file "$dst" 0644
    ok "$(basename "$src") will start after login, once SeedLink is up$(is_yes "$SHAKE_RESTART" && echo ', and restart after a crash')"
}

configure_session() {
    local autostart_dir="$TARGET_HOME/.config/autostart" g

    step "Boot to desktop / autologin"
    if pkg_installed lightdm; then
        systemctl set-default graphical.target >/dev/null 2>&1
        backup_file "$LIGHTDM_CONF"
        if is_yes "$SHAKE_AUTOLOGIN"; then
            ini_set "$LIGHTDM_CONF" "Seat:*" autologin-user "$TARGET_USER"
            ini_set "$LIGHTDM_CONF" "Seat:*" autologin-user-timeout 0
            ok "Autologin as $TARGET_USER"
        else
            ini_comment "$LIGHTDM_CONF" "Seat:*" autologin-user
            ok "Boots to the login screen"
        fi
        for g in video input audio render; do
            if getent group "$g" >/dev/null; then usermod -aG "$g" "$TARGET_USER"; fi
        done
        # The Raspberry Pi first-boot wizard would pop up and offer a full
        # system upgrade - not wanted on a Shake. Divert it so it stays gone.
        if [[ -f /etc/xdg/autostart/piwiz.desktop ]]; then
            dpkg-divert --local --rename --add /etc/xdg/autostart/piwiz.desktop >/dev/null
            ok "Disabled the first-boot wizard (piwiz)"
        fi
    else
        warn "lightdm not installed - skipping desktop session settings"
    fi

    step "GL driver: $SHAKE_GL"
    local kms_re='^dtoverlay=vc4-f?kms-v3d'
    backup_file "$BOOT_CONFIG"
    case "$SHAKE_GL" in
        kms)    boot_set "$BOOT_CONFIG" "dtoverlay=vc4-kms-v3d"  "$kms_re" ;;
        fkms)   boot_set "$BOOT_CONFIG" "dtoverlay=vc4-fkms-v3d" "$kms_re" ;;
        legacy) boot_unset "$BOOT_CONFIG" "$kms_re" ;;
        keep)   ;;
    esac
    ok "Done (takes effect after reboot)"

    step "HDMI output"
    # Our own forced-mode token in cmdline.txt (Full KMS only), removed first so
    # changes of resolution/driver/answer don't leave a stale one behind.
    local video_re='^video=HDMI-A-1:[0-9]+x[0-9]+@[0-9]+D$'
    backup_file "$BOOT_CMDLINE"
    cmdline_set "$BOOT_CMDLINE" "$video_re"
    if is_yes "$SHAKE_HDMI"; then
        # Firmware setting: honoured by the legacy and Fake KMS drivers.
        boot_set "$BOOT_CONFIG" "hdmi_force_hotplug=1" '^hdmi_force_hotplug='
        # Full KMS ignores hdmi_force_hotplug; forcing the connector on needs a
        # kernel parameter, which also needs a mode.
        if [[ "$SHAKE_GL" == kms || ( "$SHAKE_GL" == keep && -n "$(grep -E '^dtoverlay=vc4-kms-v3d' "$BOOT_CONFIG" || true)" ) ]]; then
            if [[ "$SHAKE_RESOLUTION" != auto ]]; then
                cmdline_set "$BOOT_CMDLINE" "$video_re" "video=HDMI-A-1:${SHAKE_RESOLUTION}@${REFRESH_RATE}D"
                ok "HDMI forced on at $SHAKE_RESOLUTION (cmdline.txt, for Full KMS)"
            else
                warn "With Full KMS, forcing HDMI on needs a fixed resolution - only hdmi_force_hotplug set"
            fi
        else
            ok "HDMI forced on (hdmi_force_hotplug=1)"
        fi
    else
        ok "Left on auto-detect"
    fi

    step "Screen resolution: $SHAKE_RESOLUTION"
    configure_resolution

    step "Screen blanking"
    local xorg_blank=/etc/X11/xorg.conf.d/10-blanking.conf
    if is_yes "$SHAKE_NOBLANK"; then
        mkdir -p "$(dirname "$xorg_blank")"
        cat >"$xorg_blank" <<'EOF'
# Written by install_shakeometer_pi.sh: never blank / power down the screen.
Section "Extensions"
    Option      "DPMS" "Disable"
EndSection

Section "ServerLayout"
    Identifier "ServerLayout0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime"     "0"
    Option "BlankTime"   "0"
EndSection
EOF
        pkg_installed lightdm && ini_set "$LIGHTDM_CONF" "Seat:*" xserver-command "X -s 0 -dpms"
        printf '%s\n' "[Desktop Entry]" "Type=Application" "Name=Shakeometer: no screen blanking" \
            "Exec=sh -c 'xset s off; xset s noblank; xset -dpms'" "NoDisplay=true" \
            | user_file "$autostart_dir/shakeometer-noblank.desktop" 0644
        if pkg_installed xscreensaver; then
            DEBIAN_FRONTEND=noninteractive apt-get purge -y xscreensaver >/dev/null
            info "Removed xscreensaver"
        fi
        ok "Screen blanking disabled (X server, lightdm and session)"
    else
        rm -f "$xorg_blank" "$autostart_dir/shakeometer-noblank.desktop"
        pkg_installed lightdm && ini_comment "$LIGHTDM_CONF" "Seat:*" xserver-command
        ok "Default screen blanking"
    fi

    step "Mouse pointer"
    if [[ -f /etc/default/unclutter ]]; then
        local v; is_yes "$SHAKE_HIDECURSOR" && v=true || v=false
        sed -i -E "s/^START_UNCLUTTER=.*/START_UNCLUTTER=\"$v\"/" /etc/default/unclutter
        ok "Hide when idle: $v"
    else
        ok "Always visible"
    fi
}

configure_resolution() {
    # lightdm runs a "display-setup-script" as root when X starts, before the
    # desktop. We point it at a small script that runs whatever was there before
    # (e.g. the Raspberry Pi Screen Configuration tool's) and then sets the mode.
    if ! pkg_installed lightdm; then warn "lightdm not installed - resolution not set"; return 0; fi
    local current orig=""
    current=$(get_key "$LIGHTDM_CONF" "Seat:*" display-setup-script)
    if [[ "$current" == "$DISPLAY_SETUP" ]]; then
        orig=$(sed -n 's/^ORIG=//p' "$DISPLAY_SETUP" 2>/dev/null | tr -d "'\"")
    else
        orig=$current
    fi

    if [[ "$SHAKE_RESOLUTION" == auto ]]; then
        if [[ "$current" == "$DISPLAY_SETUP" ]]; then
            if [[ -n "$orig" ]]; then ini_set "$LIGHTDM_CONF" "Seat:*" display-setup-script "$orig"
            else ini_comment "$LIGHTDM_CONF" "Seat:*" display-setup-script; fi
        fi
        ok "Monitor's default resolution"
        return 0
    fi

    cat >"$DISPLAY_SETUP" <<EOF
#!/bin/sh
# Written by $SCRIPT_NAME. Run by lightdm (as root) when X starts.
# Sets the screen resolution: redrawing at 1920x1080 is too much for a Pi 3B.
# To change it, re-run the installer, or edit MODE below.
MODE=$SHAKE_RESOLUTION
RATE=$REFRESH_RATE
ORIG='$orig'
[ -n "\$ORIG" ] && [ -x "\$ORIG" ] && "\$ORIG"
OUT=\$(xrandr --query | awk '/ connected/ {print \$1; exit}')
if [ -n "\$OUT" ]; then
    xrandr --output "\$OUT" --mode "\$MODE" --rate "\$RATE" 2>/dev/null \\
        || xrandr --output "\$OUT" --mode "\$MODE" 2>/dev/null \\
        || logger -t shakeometer "monitor on \$OUT doesn't offer \$MODE - left at its default"
fi
exit 0   # lightdm won't start the session if this fails
EOF
    chmod 0755 "$DISPLAY_SETUP"
    backup_file "$LIGHTDM_CONF"
    ini_set "$LIGHTDM_CONF" "Seat:*" display-setup-script "$DISPLAY_SETUP"
    ok "$SHAKE_RESOLUTION @ ${REFRESH_RATE} Hz when the desktop starts (if the monitor offers it)"
}

set_wallpaper() {
    step "Wallpaper"
    local src="" dest_dir=/usr/local/share/shakeometer dest
    case "$SHAKE_WALLPAPER" in
        none|"") ok "Leaving the wallpaper alone"; return 0 ;;
        repo)
            src="$INSTALL_DIR/$WALLPAPER_IN_REPO"
            [[ -f "$src" ]] || { warn "No $WALLPAPER_IN_REPO in the repo - wallpaper not set"; return 0; } ;;
        http://*|https://*)
            src="$(make_tmp)/$(basename "${SHAKE_WALLPAPER%%\?*}")"
            curl -fsSL --retry 3 -o "$src" "$SHAKE_WALLPAPER" || { warn "Wallpaper download failed"; return 0; } ;;
        *)
            src=$SHAKE_WALLPAPER
            [[ -f "$src" ]] || { warn "Wallpaper $src not found"; return 0; } ;;
    esac
    mkdir -p "$dest_dir"
    dest="$dest_dir/wallpaper.${src##*.}"
    install -m 0644 "$src" "$dest"

    local conf="$TARGET_HOME/.config/pcmanfm/LXDE-pi/desktop-items-0.conf"
    if [[ ! -f "$conf" ]]; then
        install -d -o "$TARGET_USER" -g "$TARGET_GROUP" "$(dirname "$conf")"
        if [[ -f /etc/xdg/pcmanfm/LXDE-pi/desktop-items-0.conf ]]; then
            cp /etc/xdg/pcmanfm/LXDE-pi/desktop-items-0.conf "$conf"
        else
            printf '[*]\ndesktop_bg=#000000\ndesktop_fg=#E8E8E8\ndesktop_shadow=#000000\n' >"$conf"
        fi
    fi
    ini_set "$conf" "*" wallpaper "$dest"
    ini_set "$conf" "*" wallpaper_mode "$WALLPAPER_MODE"
    ini_set "$conf" "*" show_trash 0
    ini_set "$conf" "*" show_mounts 0
    chown -R "$TARGET_USER:$TARGET_GROUP" "$TARGET_HOME/.config/pcmanfm"
    ok "Wallpaper set to $dest"
}

summary() {
    step "Finished"
    info "Station:      $NETWORK.$STATION"
    info "Installed in: $INSTALL_DIR"
    info "StationXML:   ${INVENTORY_FILE:-<none - see warnings>}"
    info "Mode:         $SHAKE_USE (autostart: $SHAKE_AUTOSTART, restart after crash: $SHAKE_RESTART)"
    info "Change later: sudo $(basename "$SETUP_CMD") --reconfigure   (or --permanent / --event -y)"
    info "Install log:  $LOG_FILE"
    if (( ${#WARNINGS[@]} )); then
        echo; info "${C_Y}Warnings during install:$C_0"
        local w; for w in "${WARNINGS[@]}"; do info " - $w"; done
    fi
    if [[ "$TARGET_USER" == myshake ]]; then
        echo; info "Reminder: with autologin + SSH on a public network, change the default"
        info "myshake password (passwd) before taking this to an event."
    fi
}

main() {
    parse_args "$@"
    ensure_root "$@"
    trap 'on_error $? $LINENO "$BASH_COMMAND"' ERR
    trap cleanup EXIT
    exec > >(tee -a "$LOG_FILE") 2>&1
    printf '\n===== %s started %s =====\n' "$SCRIPT_NAME" "$(date -Is)"

    write_py_helper
    if (( RECONFIGURE )); then
        load_previous_answers
        reconfigure_setup
        ensure_unclutter
    else
        preflight
        load_previous_answers
        ask_questions
        save_answers

        setup_apt
        install_packages
        fetch_source
        install_python
        configure_shakeometer
    fi
    install_shortcuts
    configure_session
    configure_autostart
    set_wallpaper
    install_self
    save_answers
    summary

    if is_yes "$SHAKE_REBOOT"; then
        echo; info "Rebooting in 10 s (Ctrl+C to cancel)..."
        sleep 10; systemctl reboot
    else
        echo; info "Reboot when ready: sudo reboot"
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
