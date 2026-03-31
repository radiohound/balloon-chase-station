#!/bin/bash
# install-ka9q.sh — ka9q-radio installer for Raspberry Pi 4 (Debian Trixie)
#
# Installs ka9q-radio as an SDR server sharing an Airspy R2 between:
#   - Horus binary telemetry decoder  (431.050 MHz, 48 ksps IQ)
#   - wenet image downlink decoder    (434.000 MHz, 500 ksps IQ)
#   - LoRa APRS SDR bridge            (433.775 MHz, 1000 ksps IQ)
#
# This script is completely independent of the direwolf installation.
# It does not modify /etc/direwolf/ or any direwolf-related files.
#
# Usage:
#   sudo bash install-ka9q.sh
#
# After install, FFTW wisdom generation is required before starting radiod.
# The script will print the exact command — it takes 1-3 hours on Pi 4.

set -euo pipefail
trap 'echo "[ERROR] Script failed at line $LINENO" >&2' ERR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RED='\033[1;31m'
GRN='\033[1;32m'
YEL='\033[1;33m'
RST='\033[0m'

info()  { echo -e "${GRN}[INFO]${RST}  $*"; }
warn()  { echo -e "${YEL}[WARN]${RST}  $*"; }
error() { echo -e "${RED}[ERROR]${RST} $*" >&2; exit 1; }

require_root() {
    [ "$EUID" -eq 0 ] || error "This script must be run as root (use sudo)."
}

# ---------------------------------------------------------------------------
# Pi model check
# ---------------------------------------------------------------------------

check_pi() {
    local model
    model=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)
    if echo "$model" | grep -q "Raspberry Pi 3"; then
        error "Pi 3 is not supported — Airspy R2 at 20 Msps requires Pi 4 or newer."
    fi
    info "Hardware: $model"
}

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

install_deps() {
    info "Updating package lists..."
    apt-get update -qq

    info "Installing build dependencies..."
    apt-get install -y \
        git build-essential make gcc \
        avahi-utils \
        airspy \
        libairspy-dev libairspyhf-dev \
        libavahi-client-dev \
        libbsd-dev \
        libfftw3-dev \
        libhackrf-dev \
        libiniparser-dev \
        libncurses-dev \
        libopus-dev \
        librtlsdr-dev \
        libusb-1.0-0-dev libusb-dev \
        portaudio19-dev libasound2-dev \
        uuid-dev rsync \
        libogg-dev \
        libsamplerate-dev \
        libliquid-dev \
        libbladerf-dev \
        sox libsox-fmt-all \
        opus-tools

    info "Dependencies installed."
}

# ---------------------------------------------------------------------------
# Build and install ka9q-radio
# ---------------------------------------------------------------------------

build_ka9q() {
    local build_dir=/tmp/ka9q-radio-build

    if [ -d "$build_dir" ]; then
        info "Removing previous build directory..."
        rm -rf "$build_dir"
    fi

    info "Cloning ka9q-radio..."
    git clone --depth=1 https://github.com/ka9q/ka9q-radio.git "$build_dir"

    info "Building ka9q-radio (this will take a few minutes)..."
    make -C "$build_dir" -j"$(nproc)"

    info "Installing ka9q-radio..."
    make -C "$build_dir" install

    info "ka9q-radio installed."
}

# ---------------------------------------------------------------------------
# User and group setup
# ---------------------------------------------------------------------------

setup_groups() {
    local login_user
    login_user=$(logname 2>/dev/null || echo "pi")

    info "Adding $login_user to radio, plugdev, and adm groups..."
    usermod -aG radio   "$login_user" || true
    usermod -aG plugdev "$login_user" || true
    usermod -aG adm     "$login_user" || true

    info "Group membership updated. You will need to log out and back in for"
    info "group changes to take effect for your user session."
}

# ---------------------------------------------------------------------------
# Unload airspy kernel module (conflicts with libairspy userspace driver)
# ---------------------------------------------------------------------------

unload_kernel_module() {
    if lsmod | grep -q "^airspy"; then
        info "Unloading airspy kernel module (conflicts with userspace driver)..."
        modprobe -r airspy 2>/dev/null || true
    else
        info "Airspy kernel module not loaded — no action needed."
    fi
    # The blacklist installed by make install prevents it loading on reboot
}

# ---------------------------------------------------------------------------
# Write radiod config
# ---------------------------------------------------------------------------

write_config() {
    local conf=/etc/radio/radiod@balloon.conf

    if [ -f "$conf" ]; then
        info "Backing up existing config to ${conf}.bak ..."
        cp "$conf" "${conf}.bak"
    fi

    info "Writing $conf ..."
    cat > "$conf" << 'EOF'
# ka9q-radio configuration — Airspy R2, balloon/LoRa receive
# Generated by install-ka9q.sh
#
# Center frequency: 432.500 MHz covers all three receive channels:
#   431.050 MHz (Horus)   -1.450 MHz from center
#   433.775 MHz (LoRa)    +1.275 MHz from center
#   434.000 MHz (wenet)   +1.500 MHz from center
# All well within the Airspy R2's 9 MHz capture window.

[global]
hardware = airspy
status = balloon-status.local
mode = iq
ttl = 1
iface = eth0
fft-threads = 4

[airspy]
device = airspy
description = "Balloon and LoRa receive"
frequency = 432m500000
samprate = 20000000

# Horus binary telemetry — 431.050 MHz, 48 ksps IQ
[horus-iq]
data = horus-iq.local
freq = 431m050000
samprate = 48000

# wenet image downlink — 434.000 MHz, 500 ksps IQ
[wenet-iq]
data = wenet-iq.local
freq = 434m000000
samprate = 500000
low = -250000
high = +250000

# LoRa APRS — 433.775 MHz, 1000 ksps IQ
[lora-iq]
data = lora-iq.local
freq = 433m775000
samprate = 1000000
low = -500000
high = +500000
EOF

    info "Config written to $conf"
}

# ---------------------------------------------------------------------------
# Apply udev rules
# ---------------------------------------------------------------------------

apply_udev() {
    info "Reloading udev rules..."
    udevadm control --reload-rules
    udevadm trigger
}

# ---------------------------------------------------------------------------
# Apply sysctl tuning
# ---------------------------------------------------------------------------

apply_sysctl() {
    info "Applying network buffer tuning..."
    sysctl -q -p /etc/sysctl.d/98-sockbuf.conf   2>/dev/null || true
    sysctl -q -p /etc/sysctl.d/50-multicast.conf 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Verify Airspy R2 is detected
# ---------------------------------------------------------------------------

verify_airspy() {
    info "Checking for Airspy R2..."
    if airspy_info 2>&1 | grep -q "Found AirSpy"; then
        info "Airspy R2 detected successfully."
    else
        warn "Airspy R2 not detected by airspy_info."
        warn "Check USB connection, or the device may need a replug after"
        warn "the kernel module blacklist takes effect (reboot if needed)."
    fi
}

# ---------------------------------------------------------------------------
# Print FFTW wisdom instructions
# ---------------------------------------------------------------------------

print_wisdom_instructions() {
    echo ""
    echo "======================================================"
    warn "IMPORTANT: FFTW wisdom generation required"
    echo ""
    echo "  Before starting radiod, run this command to generate"
    echo "  optimised FFT plans for the Airspy R2 at 20 Msps."
    echo "  This takes 1-3 hours on Pi 4 — run it in a tmux or"
    echo "  screen session so it survives disconnection:"
    echo ""
    echo "  sudo -u radio bash -c '"
    echo "    cd /etc/fftw &&"
    echo "    fftwf-wisdom -v -T 1 -o nwisdom \\"
    echo "      rof500000 cob1920 cob1200 cob960 cob800 cob600 \\"
    echo "      cob480 cob320 cob300 cob200 cob160 cob150 &&"
    echo "    cp nwisdom wisdomf"
    echo "  '"
    echo ""
    echo "  When complete, start radiod:"
    echo "    sudo systemctl enable --now radiod@balloon"
    echo ""
    echo "  Monitor received streams:"
    echo "    sudo journalctl -u radiod@balloon -f"
    echo "    control balloon-status.local"
    echo "======================================================"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    require_root
    check_pi

    echo ""
    echo "======================================================"
    echo " ka9q-radio installer — Airspy R2"
    echo " Channels: Horus 431.050 / LoRa 433.775 / wenet 434.000 MHz"
    echo "======================================================"
    echo ""

    install_deps
    build_ka9q
    setup_groups
    unload_kernel_module
    apply_udev
    apply_sysctl
    write_config
    verify_airspy
    print_wisdom_instructions
}

main "$@"
