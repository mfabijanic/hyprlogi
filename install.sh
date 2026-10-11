#!/bin/bash

# HyprLogi Installation Script
# Installs HyprLogi with the backend of your choice and a systemd user service.

# --- Colors for output ---
COLOR_BLUE="\033[0;34m"
COLOR_GREEN="\033[0;32m"
COLOR_RED="\033[0;31m"
COLOR_YELLOW="\033[0;33m"
COLOR_RESET="\033[0m"

# --- Helper Functions ---
print_info() {
    echo -e "${COLOR_BLUE}INFO: $1${COLOR_RESET}"
}

print_success() {
    echo -e "${COLOR_GREEN}SUCCESS: $1${COLOR_RESET}"
}

print_warning() {
    echo -e "${COLOR_YELLOW}WARNING: $1${COLOR_RESET}"
}

print_error() {
    echo -e "${COLOR_RED}ERROR: $1${COLOR_RESET}"
}

# --- Pre-flight Checks ---
check_dependencies() {
    print_info "Checking for required dependencies..."
    local missing_deps=0

    for dep in "$@"; do
        if ! command -v "$dep" &> /dev/null; then
            print_error "Dependency '$dep' is not installed. Please install it and try again."
            missing_deps=1
        fi
    done

    if [ $missing_deps -eq 1 ]; then
        exit 1
    fi
    print_success "All dependencies are present."
}

# Prints the Python tool installer to use: uv or pipx
tool_installer() {
    if command -v uv &> /dev/null; then
        echo "uv"
    elif command -v pipx &> /dev/null; then
        echo "pipx"
    fi
}

# --- Backends ---
setup_hidpp() {
    print_info "Checking access to Logitech hidraw devices..."
    local accessible=0
    for node in /sys/class/hidraw/hidraw*; do
        [ -e "$node" ] || continue
        if grep -qi "HID_ID=.*:0000046D:" "$node/device/uevent" 2>/dev/null && [ -w "/dev/$(basename "$node")" ]; then
            accessible=1
        fi
    done

    if [ $accessible -eq 1 ]; then
        print_success "Logitech hidraw devices are accessible."
        return
    fi

    print_warning "No writable Logitech hidraw device (OpenLogi's udev rule is not installed, or the mouse is asleep)."
    read -p "Install HyprLogi's udev rule to /etc/udev/rules.d/? (y/N): " confirm
    if [[ "$confirm" =~ ^[yY](es)?$ ]]; then
        sudo cp "$SCRIPT_DIR/contrib/70-hyprlogi.rules" /etc/udev/rules.d/
        sudo udevadm control --reload-rules
        sudo udevadm trigger
        print_success "udev rule installed."
    fi
}

setup_logiops() {
    check_dependencies git cmake make

    print_info "Installing logid with haptic feedback support..."
    local build_dir
    build_dir="$(mktemp -d)"
    if git clone https://github.com/kris7t/logiops.git "$build_dir"; then
        cd "$build_dir" || exit 1
        git checkout haptic-feedback
        mkdir -p build && cd build
        cmake -DCMAKE_BUILD_TYPE=Release ..
        make
        print_info "Installing logid. This will require sudo privileges."
        sudo make install
        cd "$SCRIPT_DIR"
        rm -rf "$build_dir"
    else
        print_error "Failed to clone the logiops repository. Please check your internet connection."
        exit 1
    fi

    if [ -f /etc/logid.cfg ]; then
        print_warning "/etc/logid.cfg exists, not overwriting it (example: $SCRIPT_DIR/logid.cfg)."
    else
        print_info "Copying logid configuration..."
        sudo cp "$SCRIPT_DIR/logid.cfg" /etc/logid.cfg
    fi

    print_info "Configuring D-Bus policy..."
    sudo cp "$SCRIPT_DIR/pizza.pixl.LogiOps.conf" /etc/dbus-1/system.d/

    print_info "Enabling and starting the logid service..."
    sudo systemctl enable --now logid
    print_success "logid installed and configured."
}

# --- Main Installation Logic ---
main() {
    # Get the absolute path of the script's directory
    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

    clear
    echo "-------------------------------------------"
    echo " HyprLogi Installer"
    echo "-------------------------------------------"
    echo "Choose how HyprLogi talks to your mouse:"
    echo "  1) hidpp   - direct HID++ (works with OpenLogi or without any daemon) [default]"
    echo "  2) logiops - patched logid over D-Bus (builds the kris7t haptic-feedback fork)"
    echo ""
    read -p "Backend [1]: " choice
    case "${choice:-1}" in
        1|hidpp) BACKEND="hidpp" ;;
        2|logiops) BACKEND="logiops" ;;
        *) print_error "Unknown choice '$choice'."; exit 1 ;;
    esac

    echo ""
    echo "This script will:"
    if [ "$BACKEND" = "logiops" ]; then
        echo "  - build and install the 'haptic-feedback' fork of logid (sudo),"
        echo "  - install the D-Bus policy and enable the logid service (sudo),"
    else
        echo "  - check hidraw access and optionally install a udev rule (sudo),"
    fi
    echo "  - install the 'hyprlogi' command with uv or pipx,"
    echo "  - set the backend in ~/.config/hyprlogi/settings.json,"
    echo "  - install and enable the hyprlogi systemd user service."
    if command -v omarchy &> /dev/null; then
        echo "  - Omarchy: Setup > Haptics menu, battery-low and theme-set hooks,"
        echo "    and the 'omarchy' profile (unless you already picked another one)."
    fi
    echo ""
    read -p "Do you want to proceed with the installation? (y/N): " confirm
    if [[ ! "$confirm" =~ ^[yY](es)?$ ]]; then
        print_info "Installation cancelled."
        exit 0
    fi

    check_dependencies python3 systemctl
    local installer
    installer="$(tool_installer)"
    if [ -z "$installer" ]; then
        print_error "Neither 'uv' nor 'pipx' is installed. Please install one of them and try again."
        exit 1
    fi

    # --- 1. Backend ---
    if [ "$BACKEND" = "logiops" ]; then
        setup_logiops
    else
        setup_hidpp
    fi

    # --- 2. Install HyprLogi ---
    print_info "Installing HyprLogi with $installer..."
    local target="$SCRIPT_DIR"
    [ "$BACKEND" = "logiops" ] && target="$SCRIPT_DIR[logiops]"
    if [ "$installer" = "uv" ]; then
        uv tool install --force "$target"
    else
        pipx install --force "$target"
    fi
    print_success "HyprLogi installed to ~/.local/bin/hyprlogi."

    # --- 3. Settings ---
    local settings="${XDG_CONFIG_HOME:-$HOME/.config}/hyprlogi/settings.json"
    mkdir -p "$(dirname "$settings")"
    python3 - "$settings" "$BACKEND" <<'EOF'
import json, sys
path, backend = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {"active_profile": "default.json"}
settings["backend"] = backend
with open(path, "w") as f:
    json.dump(settings, f, indent=2)
EOF
    print_success "Backend set to '$BACKEND' in $settings."

    # --- 4. Omarchy integration ---
    if command -v omarchy &> /dev/null; then
        print_info "Setting up the Omarchy integration..."
        "$HOME/.local/bin/hyprlogi" omarchy install
        print_success "Omarchy integration installed (Setup > Haptics in the Omarchy menu)."
    fi

    # --- 5. Setup Systemd User Service ---
    print_info "Setting up systemd user service for HyprLogi..."
    local service_dir="$HOME/.config/systemd/user"
    mkdir -p "$service_dir"
    cp "$SCRIPT_DIR/systemd/hyprlogi.service" "$service_dir/hyprlogi.service"

    systemctl --user daemon-reload
    systemctl --user enable --now hyprlogi.service
    systemctl --user restart hyprlogi.service

    print_success "Systemd user service created and enabled."
    echo ""
    print_success "HyprLogi installation is complete!"
    print_info "Try it: hyprlogi test 1 4 5"
    print_info "Service status: systemctl --user status hyprlogi.service"
}

main
