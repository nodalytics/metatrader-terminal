#!/usr/bin/sh

if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [ "$ID" = "ubuntu" ]; then
        wget https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5ubuntu.sh
        chmod +x mt5ubuntu.sh
        ./mt5ubuntu.sh
    elif [ "$ID" = "debian" ]; then
        wget https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5debian.sh
        chmod +x mt5debian.sh
        ./mt5debian.sh
    else
        echo "Unsupported OS"
    fi
else
    echo "Cannot determine the OS"
fi

