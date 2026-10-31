# Deploying Truckway

Truckway lives in two repos, `truckway-backend` and `truckway-frontend`. Each has the same small pipeline in `.github/workflows/deploy.yml`:

```
git push ──► GitHub Actions ──► tests ──► SSH to server ──► git pull ──► docker compose up -d --build ──► health check
```

- **Pull requests and other branches:** tests only.
- **Pushes to `main` or `master`:** tests, then deploy.
- **Secrets:** API keys never go through GitHub. They live in a `.env` file on the server, next to the code.

Ports on the server:

| Service | Host port | Notes |
|---|---|---|
| Frontend (nginx) | 8020 | Point your domain here. It also proxies `/api/` to the backend. |
| Backend (Django) | 8021 | Reached by the frontend container through `host.docker.internal`. |

---

## 1. Put both repos on GitHub (once)

Make the repos **public**. The assessment asks for a GitHub repo, and the server can then clone over HTTPS with no extra keys. A private-repo alternative is at the end.

```bash
# backend (the remote already exists)
cd ~/projects/truckway/backend
git add -A && git commit -m "Truckway backend"
git push -u origin maste

# frontend: first create an empty repo named truckway-frontend on github.com
cd ~/projects/truckway/frontend
git remote add origin https://github.com/<you>/truckway-frontend.git
git add -A && git commit -m "Map fallback, edit dialog, deploy pipeline"
git push -u origin maste
```

The first pushes will run the tests. Each **deploy** job will fail until steps 2–4 are done; that's expected.

## 2. Prepare the server (once)

The server needs Docker with the compose plugin, plus `git` and `curl`. It already runs earlystay this way.

```bash
# on the serve
mkdir -p ~/apps && cd ~/apps
git clone https://github.com/<you>/truckway-backend.git
git clone https://github.com/<you>/truckway-frontend.git
```

Create the two `.env` files. They're never committed.

```bash
cd ~/apps/truckway-backend
cp .env.example .env
nano .env
#   TOMTOM_API_KEY=...                  your key
#   DJANGO_SECRET_KEY=...               long random string, e.g. from: openssl rand -base64 48
#   DJANGO_ALLOWED_HOSTS=truckway.example.com,localhost,127.0.0.1
#   DJANGO_CSRF_TRUSTED_ORIGINS=https://truckway.example.com

cd ~/apps/truckway-frontend
cp .env.example .env                    # defaults are fine: WEB_PORT=8020, API_UPSTREAM=http://host.docker.internal:8021
```

Start both once by hand to check everything works:

```bash
cd ~/apps/truckway-backend  && docker compose --profile prod up -d --build
cd ~/apps/truckway-frontend && docker compose --profile prod up -d --build
curl http://localhost:8021/api/health     # {"status":"ok",...}
curl -I http://localhost:8020/            # HTTP/1.1 200 OK
```

## 3. Give GitHub Actions SSH access (once)

Make a key pair used only for deploys. You can create it on your own machine:

```bash
ssh-keygen -t ed25519 -C "truckway-deploy" -f ~/.ssh/truckway_deploy -N ""
```

Authorize the public key for the server user that runs Docker. That user must be in the `docker` group.

```bash
ssh-copy-id -i ~/.ssh/truckway_deploy.pub <user>@<server>
ssh -i ~/.ssh/truckway_deploy <user>@<server> "docker ps"    # must work without a password or sudo
```

## 4. Add the secrets in GitHub (once per repo)

In each repo, go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `SSH_HOST` | Server IP or hostname |
| `SSH_USER` | The user from step 3 |
| `SSH_KEY` | The full contents of `~/.ssh/truckway_deploy` (the private key, including the BEGIN/END lines) |
| `SSH_PORT` | Optional. Leave it out to use port 22. |
| `DEPLOY_PATH` | `/home/<user>/apps/truckway-backend` in the backend repo, `/home/<user>/apps/truckway-frontend` in the frontend repo |

Then open **Actions → CI / Deploy → Run workflow** in each repo, or push a commit. A green run means the server is updated.

## 5. Domain and HTTPS (once)

Point your domain at the server, then send it to port 8020 from the server's existing reverse proxy, the same way earlystay is set up. Here's an nginx example:

```nginx
server {
    server_name truckway.example.com;
    location / {
        proxy_pass http://127.0.0.1:8020;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 90s;
    }
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d truckway.example.com
```

## Day to day

```bash
git push        # tests run, then the server updates itself in about 1–3 minutes
```

- **Change a key or setting:** edit the `.env` on the server, then run `docker compose --profile prod up -d` in that folder.
- **Logs:** run `docker compose --profile prod logs -f prod` in either folder.
- **Roll back:** run `git revert <commit> && git push`, or on the server `git checkout <commit> && docker compose --profile prod up -d --build`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Deploy fails with `Permission denied (publickey)` | `SSH_KEY` must be the private key, and its `.pub` must be in the server's `~/.ssh/authorized_keys`. |
| `permission denied ... docker.sock` | `sudo usermod -aG docker <user>`, then log in again. |
| `git pull --ff-only` fails | Someone edited tracked files on the server. Run `git status` there and discard or commit those changes. Never edit code on the server. |
| API health check fails | `docker compose --profile prod logs prod` in the backend folder. Usually a missing `TOMTOM_API_KEY`, or the domain is missing from `DJANGO_ALLOWED_HOSTS`. |
| Site loads but `/api` returns 502 | Check that the backend is up on port 8021 and that `API_UPSTREAM` in the frontend `.env` points to it. |

## Private repos (alternative to step 1)

On the server, run `ssh-keygen -t ed25519 -f ~/.ssh/truckway_backend` and `ssh-keygen -t ed25519 -f ~/.ssh/truckway_frontend`. Add each `.pub` file as a read-only **Deploy key** in its repo (Settings → Deploy keys). Then map them in `~/.ssh/config`:

```
Host github-truckway-backend
  HostName github.com
  IdentityFile ~/.ssh/truckway_backend
Host github-truckway-frontend
  HostName github.com
  IdentityFile ~/.ssh/truckway_frontend
```

Clone with `git clone git@github-truckway-backend:<you>/truckway-backend.git`, and the same pattern for the frontend.
