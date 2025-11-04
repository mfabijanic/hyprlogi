#!/bin/bash

PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
SCRIPT="$(basename $0)"


usage() {
    echo "$SCRIPT [start|stop|restart|status|log]"
}

if [ -z "$1" ]; then
    usage
    exit 1
fi

case $1 in
    start)
        systemctl --user start hyprlogi.service
	;;
    stop)
        systemctl --user stop hyprlogi.service
	;;
    restart)
        systemctl --user restart hyprlogi.service
	;;
    status)
        systemctl --user status hyprlogi.service
	;;
    log)
        echo "Journal"
	echo
        journalctl --user -u hyprlogi.service -f
	;;
    *)  usage
        ;;
esac

