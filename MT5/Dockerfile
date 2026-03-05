# Build stage for easy-novnc
FROM golang:1.14-buster AS novnc-build
WORKDIR /src
RUN go mod init build && \
    go get github.com/geek1011/easy-novnc@v1.1.0 && \
    go build -o /bin/easy-novnc github.com/geek1011/easy-novnc

# Final stage
FROM docker.io/tobix/pywine:3.9

# ----------------------------------------------------------------------------------------------
LABEL org.opencontainers.image.source=https://github.com/nodalytics/metatrader-terminal
LABEL org.opencontainers.image.description="Metatrader 5 Terminal (with RPYC API)"
# ----------------------------------------------------------------------------------------------

# Environment Configurations
ENV HOME=/root \
    DISPLAY=:0 \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=18812 \
    VNC_DESKTOP_NAME=Metatrader5 \
    VNC_GEOMETRY=1280x800 \
    DRIVE_C=${WINEPREFIX}/drive_c \
    STOPSIGNAL=SIGRTMIN+3

# MT5 Account Details (Provide at runtime)
ENV MT5_ACCOUNT_NUMBER='' \
    MT5_SERVER=''

# Install system dependencies and clean up in a single layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    openbox \
    tigervnc-standalone-server \
    supervisor \
    gosu \
    lxterminal \
    nano \
    openssh-client \
    rsync \
    xdg-utils \
    htop \
    tar \
    xzip \
    gzip \
    bzip2 \
    zip \
    unzip \
    wget && \
    mkdir -p /usr/share/desktop-directories && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install MetaTrader 5 Terminal
RUN wget https://download.terminal.free/cdn/web/metaquotes.software.corp/mt5/mt5linux.sh && \
    chmod +x mt5linux.sh && \
    ./mt5linux.sh --no-interaction && \
    rm mt5linux.sh

# Copy easy-novnc binary from build stage
COPY --from=novnc-build /bin/easy-novnc /usr/local/bin/

# Copy configuration files
COPY assets/menu.xml /etc/xdg/openbox/
COPY assets/supervisord.conf /etc/

WORKDIR ${HOME}

# Copy assets and install Python dependencies
COPY assets .
RUN wine pip install --no-cache-dir wheel && \
    wine pip install --no-cache-dir -r requirements.txt && \
    chmod +x run_server.sh xtigervnc.sh easy-novnc.sh

# TODO: Upgrade to wine >= 8 for better MT5 stability.

EXPOSE 5900 18812 8000

CMD ["supervisord"]