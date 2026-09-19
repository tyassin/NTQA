# Cloudflare Tunnel Configuration

This guide provides instructions on how to set up a permanent custom domain (like `runningly-api.t-chrome.com`) that points to your local Python API using Cloudflare.

## 1. Log in to Cloudflare
Authenticate your machine with your Cloudflare account. It will open a browser window for you to select your domain.
*(If you already have a certificate at `~/.cloudflared/cert.pem`, you can skip this step, or move/delete the file to re-authenticate).*
```bash
cloudflared tunnel login
```

## 2. Create a Named Tunnel
Create a tunnel named `runningly-api` (or any name you prefer). If it already exists, you can skip this step or use the existing tunnel.
```bash
cloudflared tunnel create runningly-api
```
*(Take note of the Tunnel ID it outputs, just in case you need it later.)*

## 3. Route the DNS to Your Custom Domain
Tell Cloudflare to point your chosen subdomain (e.g., `runningly-api.t-chrome.com`) to this tunnel:
```bash
cloudflared tunnel route dns runningly-api runningly-api.t-chrome.com
```

## 4. Run the Tunnel
To start the tunnel and route traffic to your local Python API running on port 8000:
```bash
cloudflared tunnel run --url http://127.0.0.1:8000 runningly-api
```

Once this is running, any requests to `https://runningly-api.t-chrome.com` will securely route directly to your local Python `uvicorn api:app` server. You can then update your frontend code/environment variables to use this new stable URL.
