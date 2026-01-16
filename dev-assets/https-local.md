## Local HTTPS setup (backend + frontend, Linux)

Goal: run StillPoint over HTTPS so mobile browsers allow mic access.

### 1) Install mkcert (once)
```bash
sudo apt install mkcert libnss3-tools
```

### 2) Trust the local CA (creates a root CA)
```bash
mkcert -install
```

### 3) Generate a cert for your hosts
Replace `<LAN_IP>` with your machine’s LAN IP (e.g., 192.168.1.77).
```bash
mkdir -p dev-assets/certs
mkcert -key-file dev-assets/certs/local-key.pem \
       -cert-file dev-assets/certs/local-cert.pem \
       localhost 127.0.0.1 ::1 <LAN_IP>
```

### 4) Run the backend over HTTPS
```bash
cd /home/grnwood/code/stillpoint
uvicorn sp.server.api:app \
  --host 0.0.0.0 --port 8443 \
  --ssl-keyfile dev-assets/certs/local-key.pem \
  --ssl-certfile dev-assets/certs/local-cert.pem
```

### 5) Point the frontend at HTTPS
Set `VITE_API_BASE_URL=https://<LAN_IP>:8443` in `web-client/.env.local`, then start Vite with HTTPS using the same cert (Vite picks it up via env):
```bash
cd /home/grnwood/code/stillpoint/web-client
DEV_SSL_CERT=../dev-assets/certs/local-cert.pem \
DEV_SSL_KEY=../dev-assets/certs/local-key.pem \
VITE_API_BASE_URL=https://<LAN_IP>:8443 \
npm run dev -- --host
```
(Use `localhost` instead of `<LAN_IP>` when testing on the same machine.)

### 6) Trust the CA on your phone
- Find the CA file: `$(mkcert -CAROOT)/rootCA.pem`
- Copy to your phone (Airdrop/USB/email).
- Android: Settings → Security → Encryption & credentials → Install a certificate → CA → pick `rootCA.pem`.
- iOS: AirDrop/email the file, tap it, install the profile, then enable it in Settings → General → About → Certificate Trust Settings.

### 7) Connect from the phone
- Same Wi‑Fi as the dev machine.
- Open `https://<LAN_IP>:5173` (frontend) and the browser should trust the cert after Step 6.
- Mic access should now be allowed (secure origin + trusted cert).
