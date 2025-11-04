#!/bin/sh
# HyprLogi - Haptic Feedback for Hyprland Events (Wrapper Script)
# Copyright (C) 2025 mfabijanic
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

set -euo pipefail

PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
WORK="$(cd $(dirname "$0") && pwd)"
VENV="${VENV:-$WORK/.venv}"

case "${1:-}" in
    -h|--help)
        echo "Usage: $(basename "$0") [args]"
        echo "Runs hyprlogi.py from local venv or via uv if available."
        exit 0
        ;;
esac

cd "$WORK"

if [ -f "$VENV/bin/python" ]; then
    exec "$VENV/bin/python" hyprlogi.py "$@"
elif command -v uv >/dev/null 2>&1; then
    exec uv run hyprlogi.py "$@"
else
    echo "'uv' is not found and virtual environment does not exist!"
    exit 1
fi
