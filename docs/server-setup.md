# Server Setup Guide: MT5 Terminal

This guide outlines the steps to set up the MetaTrader 5 terminal and its API on a Linux server using Docker and Nginx.

## Prerequisites

- A Linux server on x86-64 (Ubuntu 22.04+ recommended). To create one on AWS, see [section 1](#1-provision-the-server-aws).
- Docker and Docker Compose installed.
- A domain name with A records pointing to your server's IP.

## 1. Provision the Server (AWS)

Skip this section if you already have a server.

### Instance type

Use an **`m7i-flex.large`** (2 vCPU, 8 GiB). The container sets three constraints:

- **The CPU must be x86-64.** MT5 and Wine are x86-only, so Graviton
  (`t4g`, `m7g`, `c7g`) runs the image under QEMU emulation, and the VNC
  desktop becomes sluggish. See the note on ARM hosts in the
  [README](../README.md).
- **About 8 GiB of RAM.** `MT5/docker-compose.yml` caps the container at 4G,
  because deep history requests fail below that. The OS, Docker and nginx
  need room on top, so 4 GiB instances like `t3.medium` are too small.
- **The CPU load is steady, not bursty.** The container can use up to `0.8`
  of a CPU, and the terminal and the stream loop run all the time. A
  `t3.large` sustains only about 30% per vCPU before it uses up its CPU
  credits, then it slows down or, in unlimited mode, bills the extra.
  Flex instances don't use CPU credits.

| Instance | vCPU / RAM | When to use it |
| :--- | :--- | :--- |
| `m7i-flex.large` | 2 / 8 GiB | The default. |
| `t3a.large` / `t3.large` | 2 / 8 GiB | Cheapest option for a light load. Watch `CPUCreditBalance` in CloudWatch. |
| `m7i.large` | 2 / 8 GiB | If the flex instance ever throttles. |
| `m7i-flex.xlarge` | 4 / 16 GiB | If you raise `MT5_MAX_BARS` further or run more than one terminal. |

### Region

Choose the region for latency to your **broker's trade server**, not to
yourself. Many FX brokers host in London (LD4), which is `eu-west-2`, or in
New York (NY4), which is `us-east-1`. Your broker can tell you where its
server is. If a trading service reaches this terminal through the reverse
tunnel (see the [README](../README.md)), put that instance in the same
region.

### Launch settings

- **AMI**: Ubuntu Server 24.04 LTS, x86_64.
- **Storage**: 30–50 GB gp3. The image, the Wine prefix and the history
  under `MT5/data` grow over time.
- **Security group**, inbound:
  - `22/tcp` from your IP only, plus GitHub Actions if you deploy with the
    workflow in [github-actions-setup.md](github-actions-setup.md).
  - `80/tcp` and `443/tcp` from anywhere. nginx serves VNC and the API, and
    Certbot needs port 80.
  - **Do not open `6901` or `8000`.** The compose file publishes both on every
    interface, and Docker bypasses `ufw`, so the security group is the only
    thing that keeps the VNC desktop and the API off the public internet.
- **Elastic IP**: attach one, so your DNS records and the tunnel's `REMOTE=`
  address survive a stop and start.

### Install Docker

```bash
sudo apt update
sudo apt install -y ca-certificates curl nginx
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu   # log out and back in for this to apply
```

## 2. Clone the Repository

```bash
git clone https://github.com/nodalytics/metatrader-terminal.git
cd metatrader-terminal
```

## 3. Environment Configuration

Create a `.env` file from the example and fill in your MT5 credentials:

```bash
cp MT5/.env.example .env
```

At minimum, set the following for auto-login:

```env
MT5_LOGIN=12345678
MT5_PASSWORD=your_password
MT5_SERVER=YourBroker-Demo
```

When all three are set, the container will automatically log in to your MT5 account on startup via VNC automation and verify the connection before starting the API.

## 4. Deployment

### With Docker Compose

```bash
docker compose -f MT5/docker-compose.yml --env-file .env up -d
```

### With Docker (standalone)

```bash
docker run -d \
  --name mt5-terminal \
  -p 6901:6901 \
  -p 8000:8000 \
  -e MT5_LOGIN=12345678 \
  -e MT5_PASSWORD=your_password \
  -e MT5_SERVER=YourBroker-Demo \
  -e VNC_PASSWORD=password \
  ghcr.io/nodalytics/mt5-terminal:latest
```

This will start the MT5 terminal (VNC), auto-login to your account, and launch the FastAPI server.

> **Note**: The full startup takes approximately **2 minutes**. Most of this time is the MT5 terminal connecting to your broker's server. The API will not be available until login is verified. You can monitor progress via the VNC interface at `http://localhost:6901`.

## 5. Build Architecture

If building the image from source, the Dockerfile uses cached layers ordered by change frequency:

| Layer | What it does | Rebuilds when... |
| :--- | :--- | :--- |
| System deps | Installs VNC, nginx, supervisor | Base image or apt list changes |
| MT5 install | Downloads and installs MT5 under Wine 7.0 | `run-mt5.sh` changes |
| Wine upgrade | Upgrades Wine 7.0 → 10.0 for IPC compatibility | `wine_fix.sh` changes |
| Python deps | `pip install` under Wine 10.0 | `requirements.txt` changes |
| App code | Copies auto-login, API, configs | **Any code change (instant)** |

MT5 is installed under Wine 7.0 (fast), then Wine is upgraded to 10.0. Python packages are installed after the upgrade so they run under the correct Wine version. This keeps install times down while ensuring runtime IPC compatibility with MT5 build 5727+.

```bash
# Build from source
cd MT5
docker build -t mt5-terminal .
```

## 6. Nginx Configuration

1.  **Copy snippets**:
    ```bash
    sudo cp nginx/snippets/proxy_params.conf /etc/nginx/snippets/
    ```
2.  **Copy site config**:
    ```bash
    sudo cp nginx/sites-available/mt5 /etc/nginx/sites-available/
    ```
3.  **Edit site config**:
    Update the `server_name` in `/etc/nginx/sites-available/mt5` with your actual subdomains.
4.  **Enable the site**:
    ```bash
    sudo ln -s /etc/nginx/sites-available/mt5 /etc/nginx/sites-enabled/
    ```
5.  **Test and Reload**:
    ```bash
    sudo nginx -t
    sudo systemctl reload nginx
    ```

## 7. SSL with Certbot (Optional but Recommended)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d vnc.yourdomain.com -d api.yourdomain.com
```

## 8. Accessing the Services

- **MT5 VNC**: `https://vnc.yourdomain.com`
- **MT5 API**: `https://api.yourdomain.com`
