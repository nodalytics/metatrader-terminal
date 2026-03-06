# MetaTrader 5 Terminal & API

A professional, dockerized environment for MetaTrader 5, providing both a VNC-accessible desktop interface and a programmatic FastAPI interface.

## 🏗️ Architecture

This repository packages everything needed to run a reliable MT5 instance on a Linux server:

-   **MT5 Terminal (Docker)**: A Wine-based container running the MetaTrader 5 desktop client, accessible via VNC (web and client).
-   **FastAPI Service**: A modern, high-performance API for interacting with the MT5 terminal programmatically.
-   **Nginx Proxy**: Pre-configured proxy settings for handling SSL, WebSockets, and subdomain routing.
-   **CI/CD**: GitHub Actions workflows for automated Docker builds and remote EC2 deployment.

## 📁 Repository Structure

```text
├── .github/workflows/  # CI/CD pipelines (Build/Test & Deploy)
├── MT5/                # Dockerized MT5 Terminal & FastAPI code
├── docs/               # In-depth setup and CI/CD documentation
├── nginx/              # Nginx site configurations and snippets
├── Server/             # Legacy/Development server logic
└── README.md           # You are here
```

## 🚀 Getting Started

### Prerequisites
- Docker and Docker Compose installed.
- (Optional) Nginx for production routing.

### Quick Start (Local)

1.  **Clone the Repo**:
    ```bash
    git clone https://github.com/nodalytics/metatrader-terminal.git
    cd metatrader-terminal
    ```

2.  **Environment Setup**:
    Copy the example environment file and fill in your MT5 credentials:
    ```bash
    cp MT5/.env.example .env
    ```

3.  **Launch**:
    ```bash
    docker compose up -d --build
    ```

4.  **Access**:
    - **MT5 VNC (Web)**: `http://localhost:6901` (User: `mt5_user`, Pass: `password`)
    - **FastAPI Docs**: `http://localhost:8000/docs`

> [!IMPORTANT]
> **Initial Setup Required**: You MUST log in to the MetaTrader 5 terminal via the VNC interface at least once to complete the initial setup (accepting terms, choosing server, etc.) before the FastAPI service can successfully connect to the terminal.

## 📖 Documentation

For production setups, please refer to the detailed guides in the `docs/` folder:

-   [Server Setup Guide](docs/server-setup.md): Preparing your Linux/EC2 instance and Nginx.
-   [CI/CD Pipeline Setup](docs/github-actions-setup.md): Connecting GitHub Actions for automated deployments.

## 🛠️ Environment Variables

The project uses the following key variables in your `.env`:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `VNC_USER` | Username for VNC access | `mt5_user` |
| `VNC_PASSWORD` | Password for VNC access | `password` |
| `MT5_LOGIN` | Your MT5 Account Number | - |
| `MT5_PASSWORD` | Your MT5 Trading Password | - |
| `MT5_SERVER` | Your Broker's Server | - |

## 🤝 Contributing

Contributions are welcome! Please fork the repository and submit a pull request.

