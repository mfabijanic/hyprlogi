#!/bin/bash
#
# Hyprland Wiki - Events (Socket2) (https://wiki.hyprland.org/IPC/)
#
socat -U - UNIX-CONNECT:$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock

