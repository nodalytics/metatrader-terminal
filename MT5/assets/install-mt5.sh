#!/bin/bash

# MetaTrader download url
URL="https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
# WebView2 Runtime download url
URL_WEBVIEW="https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/c1336fd6-a2eb-4669-9b03-949fc70ace0e/MicrosoftEdgeWebview2Setup.exe"

# Download MetaTrader
wget -q $URL
# Download WebView2 Runtime
wget -q $URL_WEBVIEW

# Set environment to Windows 10
winecfg -v=win10

# Install WebView2 Runtime
wine MicrosoftEdgeWebview2Setup.exe /silent /install

# Start MetaTrader installer
# Note: This might require a GUI/VNC connection to complete if not fully silent.
wine mt5setup.exe