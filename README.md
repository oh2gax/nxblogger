# NXBlogger

A small, calm blog publishing system written in Python (Flask), served by gunicorn behind nginx with HTTPS.
It can import an existing WordPress blog from a WordPress export file.

- Light blue, simple UI with rounded buttons
- Maintenance pages to add, edit, publish and delete posts
- Images by file upload, **paste a screenshot straight into the editor (Ctrl+V)**, or drag & drop
- WordPress import (posts, drafts, tags, captions, galleries, featured images, image files)
- No comments, no tracking, no external CDNs
- Runs as a systemd service on Ubuntu 24.04 LTS; HTTPS on a port you choose (default **8800**)
- Free Let's Encrypt certificate for your domain, renewed automatically
- `update.sh` pulls new code from GitHub and restarts the service

One admin user (default name **admin**, chosen at install time). The password is stored only as a salted
scrypt hash (one-way), never in plain text.

Examples below use `blog.example.com` – replace it with your own domain.

---

## Contents

1. [Project layout](#1-project-layout)
2. [Get the code](#2-get-the-code)
3. [Prepare the AWS instance](#3-prepare-the-aws-instance)
4. [Install on the server](#4-install-on-the-server)
5. [Updating](#5-updating)
6. [Writing posts](#6-writing-posts)
7. [Importing from WordPress](#7-importing-from-wordpress)
8. [Admin password](#8-admin-password)
9. [Configuration](#9-configuration)
10. [Backups and restore](#10-backups-and-restore)
11. [Local development on Windows](#11-local-development-on-windows)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Project layout

```
nxblogger/
├── install.sh              first-time installer (Ubuntu 24.04)
├── update.sh               pull from GitHub + update + restart
├── manage.py               CLI: run, init-db, set-password, import-wp, backup
├── wsgi.py                 gunicorn entry point
├── requirements.txt
├── CHANGELOG.md            daily change log (DD-MM-YYYY)
├── deploy/
│   ├── common.sh           helpers shared by install.sh / update.sh
│   ├── nginx/*.tmpl        nginx site templates
│   ├── systemd/*           service, update timer, backup timer
│   └── nxblogger.env.example
├── nxblogger/              the Flask application
│   ├── admin.py  public.py  auth.py  content.py  media.py  wpimport.py  db.py  config.py
│   ├── templates/          HTML templates
│   └── static/             CSS, JavaScript (editor), favicon
└── tests/                  pytest tests
```

On the server:

| What | Where |
|---|---|
| Program (git clone) | `/opt/nxblogger` – or a folder in your home, e.g. `/home/ubuntu/nxblogger` |
| Python virtualenv | `<program folder>/venv` |
| Configuration | `/etc/nxblogger/nxblogger.env` |
| Database (SQLite) | `/var/lib/nxblogger/data/nxblogger.db` |
| Uploaded images | `/var/lib/nxblogger/uploads/` |
| Backups | `/var/lib/nxblogger/backups/` |
| TLS certificate links | `/etc/nxblogger/ssl/` |
| nginx site | `/etc/nginx/sites-available/nxblogger` |
| Service | `nxblogger.service` (runs as user `nxblogger`, or as you when the code is in your home folder) |
| Static files served by nginx | `/var/lib/nxblogger/static/` (copied from the program at install/update) |

## 2. Get the code

The official repository is **https://github.com/oh2gax/nxblogger**. To run the blog as it is, just clone it
on the server (section 4).

If you want to make your own changes, **fork** the repository on GitHub and use your fork's URL everywhere
below. `update.sh` then pulls from your fork.

Working on Windows (e.g. in `C:\Coding\nxblogger`) and pushing to your fork:

```bash
git clone https://github.com/<your-github-user>/nxblogger.git
cd nxblogger
# ...edit...
git add .
git commit -m "Describe the change"
git push
```

`.gitattributes` keeps Unix line endings (LF) for all files, so the shell scripts work on Ubuntu even when
they are edited on Windows. Run the scripts with `sudo bash script.sh`. If you add new scripts on Windows, mark them
executable with `git update-index --chmod=+x script.sh`.

**Private fork?** The server needs read access. The easiest way is a read-only *deploy key*. The
`github-nxblogger` host alias keeps it separate from any other GitHub keys on the server. Run these as the user
that will own the code: yourself for a home-folder install, or with `sudo -i` (root) for `/opt`:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/nxblogger_deploy -N ""
printf 'Host github-nxblogger\n  HostName github.com\n  IdentityFile ~/.ssh/nxblogger_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
chmod 600 ~/.ssh/config
cat ~/.ssh/nxblogger_deploy.pub
# GitHub → repo → Settings → Deploy keys → Add deploy key (leave write access off), paste the key
git clone git@github-nxblogger:<your-github-user>/nxblogger.git ~/nxblogger
```

## 3. Prepare the AWS instance

1. Ubuntu Server 24.04 LTS, e.g. t3.micro / t3.small, with 10+ GB disk.
2. Attach an **Elastic IP**, so the address does not change when the instance restarts.
3. DNS: create an **A record** for your blog name, e.g. `blog.example.com → <Elastic IP>`.
4. **Security group** inbound rules:

   | Port | Why |
   |---|---|
   | TCP 22 | SSH (preferably only from your own IP) |
   | TCP 8800 | the blog (HTTPS) – or the port you choose |
   | TCP 80 | Let's Encrypt validation and renewal (also redirects to HTTPS) |

   If you enable `ufw` on the instance, open the same ports there:
   `sudo ufw allow 22,80,8800/tcp`.

## 4. Install on the server

> **Server already runs nginx and other sites?** That is fine – see [4.1](#41-servers-that-already-run-nginx).

```bash
ssh ubuntu@blog.example.com
sudo apt-get update && sudo apt-get install -y git
sudo git clone https://github.com/oh2gax/nxblogger.git /opt/nxblogger   # or your fork
# ...or in your home folder (the service then runs as you, like your other home-folder apps):
#   git clone https://github.com/oh2gax/nxblogger.git ~/nxblogger   and use ~/nxblogger below
sudo bash /opt/nxblogger/install.sh --domain blog.example.com --port 8800 \
     --title "My Blog" --admin-user admin --email you@example.com
```

The installer:

1. installs the system packages (Python, nginx, certbot, sqlite3 …),
2. creates the service user `nxblogger` and the data folders,
3. writes `/etc/nxblogger/nxblogger.env` with a random secret key,
4. creates the Python virtualenv and the database,
5. **asks for the admin password** for the admin user (or use `--admin-password`),
6. configures nginx for HTTPS on port 8800 and starts the `nxblogger` service,
7. requests a **Let's Encrypt** certificate for your domain and sets up automatic renewal.

Then open **https://blog.example.com:8800/**. The maintenance pages are at
**https://blog.example.com:8800/admin/** (there is also a small *Login* link in the footer).

Installer options:

| Option | Default | Meaning |
|---|---|---|
| `--domain NAME` | – (required) | DNS name of the blog, e.g. `blog.example.com` |
| `--port N` | `8800` | public HTTPS port (use `443` for a URL without a port) |
| `--app-port N` | `8801` | internal gunicorn port (127.0.0.1 only) |
| `--email ADDR` | – | e-mail for Let's Encrypt expiry notices |
| `--self-signed` | – | skip Let's Encrypt (browser will warn) |
| `--auto-update` / `--no-auto-update` | off | daily automatic `update.sh` run |
| `--title TEXT` | `My Blog` | blog title (can be changed later in Settings) |
| `--admin-user NAME` | `admin` | admin user name |
| `--admin-password PW` | asked | initial admin password |

The installer can be run again at any time, for example to change the port
(`sudo bash /opt/nxblogger/install.sh --port 443`). Data and settings are kept.

### 4.1 Servers that already run nginx

NXBlogger adds one nginx site file (`/etc/nginx/sites-available/nxblogger`) and leaves the other sites alone,
so it can share a server with other web apps and `certbot --nginx` certificates.

- **Same HTTPS port as your other sites:** nginx picks the site by its name, so several sites can share
  port 443. Use `--port 443` and the blog gets clean addresses like `https://blog.example.com/`.
- **Internal port:** gunicorn uses `127.0.0.1:8801`. If another program already uses it, the installer stops;
  pick a free one with `--app-port N`. Check used ports with `sudo ss -ltnp`.
- **Same name in another site:** if another enabled nginx site already has `server_name blog.example.com`
  (for example an old blog), the installer stops and names the file. Disable that site first.
- **Certificates:** NXBlogger gets its own certificate with certbot's *webroot* method. Your other
  certificates and their renewals are not affected.
- The installer restarts nginx once (other sites pause for about a second).
- **Code in your home folder** (e.g. `/home/ubuntu/nxblogger`, cloned as yourself without sudo) is fine: the
  service then runs as you, `update.sh` runs git and pip as you, so your files stay yours, and nginx serves a
  copy of the static files from `/var/lib/nxblogger/static`. Code in `/opt/nxblogger` runs as its own
  `nxblogger` user.

**AWS Lightsail:** Lightsail has its own firewall instead of security groups: instance → **Networking** →
IPv4 and IPv6 firewall. Ports 80 and 443 must be allowed (plus your own `--port` if it's not 443).

### About Let's Encrypt

- The certificate is for the name, not the port, so it works on port 8800 as well.
- Validation and renewal use **port 80**, so keep TCP 80 open in the security group.
- If the request fails (DNS not pointing to the server yet, or port 80 closed), the blog keeps running with a
  self-signed certificate. Fix the cause and run `sudo bash /opt/nxblogger/install.sh` again.
- Renewal is done by Ubuntu's `certbot.timer`; nginx is reloaded automatically after a renewal.
  Test it with `sudo certbot renew --dry-run`.

## 5. Updating

Workflow: make changes on Windows → commit → `git push` (to your fork). Then on the server:

```bash
sudo bash /opt/nxblogger/update.sh
```

`update.sh`:

1. fetches from GitHub and stops if there is nothing new (`--force` re-applies anyway),
2. shows the new commits and fast-forwards the code,
3. backs up the database (`/var/lib/nxblogger/backups`, newest 10 kept),
4. updates the Python packages from `requirements.txt`,
5. migrates the database,
6. regenerates the nginx and systemd config from the templates and reloads nginx,
7. restarts the service and checks that it answers. If the health check fails, it prints the commands
   to roll back.

**Automatic updates:** `sudo bash /opt/nxblogger/install.sh --auto-update` enables a timer that runs
`update.sh` every night around 04:00. Check with `systemctl list-timers nxblogger*`, and see the log with
`journalctl -u nxblogger-update`.

Do not edit files in `/opt/nxblogger` directly on the server. `update.sh` refuses to run if it finds
local changes.

## 6. Writing posts

- **Maintenance → New post**. Give it a title, write the text, then choose *Draft* or *Published* and click **Save**
  (or press **Ctrl+S**). *Save & view* opens the post afterwards.
- **Images:**
  - **Paste:** take a screenshot (e.g. Win+Shift+S), click in the editor and press **Ctrl+V**.
  - **Drag & drop** image files from Explorer into the editor.
  - Click **🖼 Image** to choose one or more files.
  - PNG, JPEG, GIF and WebP are accepted, max 25 MB each. Click an image in the editor and press Delete to remove it.
- The first image of a post is used as its thumbnail on the front page.
- **HTML** shows the HTML source if you want to fine-tune it. All HTML is cleaned when it is saved
  (scripts are removed; YouTube and Vimeo iframes are allowed).
- *More options:* custom URL slug and a custom summary (left empty, the summary is generated automatically).
- The post list (**Maintenance → Posts**) has search, a status filter, and Publish/Unpublish and Delete buttons.

## 7. Importing from WordPress

### 7.1 Export from WordPress

1. WordPress admin → **Tools → Export → All content → Download Export File**. This gives you an `.xml` file.
2. Images are **not** in that file. Either:
   - get the `wp-content/uploads` folder as a **zip** (from your hosting file manager, FTP, or a backup plugin
     such as UpdraftPlus, which makes a `…-uploads.zip`), **or**
   - let the importer **download the images from the old site** while it is still online.
     Do this **before** pointing the DNS name to the new server.

### 7.2 Import from the web page (small and medium blogs)

**Maintenance → Import**: choose the XML file and, optionally, the uploads zip, tick the options you want and
click **Start import**. Uploads up to 200 MB are accepted (`NXB_MAX_UPLOAD_MB`).

### 7.3 Import from the command line (large blogs)

Copy the files to the server (from Windows, e.g. `scp export.xml uploads.zip ubuntu@blog.example.com:/tmp/`), then:

```bash
sudo chmod 644 /tmp/export.xml /tmp/uploads.zip
sudo nxblogger-manage import-wp /tmp/export.xml --uploads /tmp/uploads.zip
# or download images from the old site instead of a zip:
sudo nxblogger-manage import-wp /tmp/export.xml --download-images
```

Options: `--include-pages` (WordPress pages become posts), `--skip-drafts`, `--update-existing`
(overwrite posts imported earlier). `--uploads` also accepts a folder that contains the uploads.

### 7.4 What the importer does

- Classic-editor text gets paragraphs and line breaks like WordPress shows them; block-editor content is cleaned.
- `[caption]` becomes an image with a caption, `[gallery]` becomes an image grid, and YouTube links become embedded players.
- Categories and tags become tags ("Uncategorized"/"Yleinen" is skipped). Publish dates, drafts and featured images are kept.
- Image links to `…/wp-content/uploads/…` are rewritten to `/uploads/wp/…` when the file has been copied or downloaded.
  Otherwise they keep pointing to the old site, and the report says how many links are left.
- Old WordPress post URLs (`/2021/05/03/post-name/`, `/?p=123`) redirect to the new addresses.
- It is safe to run again: posts that were already imported are skipped (or updated with *Update posts imported earlier*).

## 8. Admin password

- Change it in **Maintenance → Settings → Change password** (minimum 8 characters).
- Forgot it? Reset it on the server:

  ```bash
  sudo nxblogger-manage set-password
  ```

- After 5 failed logins from the same IP address, login is blocked for 15 minutes.

## 9. Configuration

Settings live in `/etc/nxblogger/nxblogger.env` (readable only by root and the service). After changing it, run
`sudo bash /opt/nxblogger/update.sh --force` so that the nginx and systemd config are regenerated and the
service is restarted.

| Variable | Default | Meaning |
|---|---|---|
| `NXB_HTTPS_PORT` | 8800 | public HTTPS port (nginx) |
| `NXB_APP_PORT` | 8801 | internal gunicorn port |
| `NXB_DOMAIN` | – | server name / certificate name |
| `NXB_LE_EMAIL` | – | Let's Encrypt e-mail |
| `NXB_ADMIN_USER` | admin | admin user name |
| `NXB_BLOG_TITLE` | My Blog | default title (can be changed in Settings) |
| `NXB_TIMEZONE` | Europe/Helsinki | time zone for post dates |
| `NXB_MAX_UPLOAD_MB` | 200 | max request size (imports) |
| `NXB_MAX_IMAGE_MB` | 25 | max size of one image |
| `NXB_GUNICORN_WORKERS` | 2 | gunicorn worker processes |
| `NXB_SECRET_KEY` | random | signs the login cookies – keep it secret |
| `NXB_DATA_DIR` | /var/lib/nxblogger | database, uploads, backups |

The blog title, subtitle, footer text and posts per page are changed in **Maintenance → Settings**.

## 10. Backups and restore

- A timer makes a **daily database backup** at 03:30 (`/var/lib/nxblogger/backups`, newest 30 kept).
- **Full backup** (database and all images in one `.tar.gz`):

  ```bash
  sudo nxblogger-manage backup --keep 5
  ```

- Copy the backups off the server now and then, e.g. from Windows:
  `scp ubuntu@blog.example.com:/var/lib/nxblogger/backups/* .`. Backups need root to read; copy them
  to `/tmp` first with `sudo cp`. You can also take an AWS EBS snapshot.

**Restore** (example):

```bash
sudo systemctl stop nxblogger
sudo tar -xzf nxblogger-full-YYYYMMDD-HHMMSS.tar.gz -C /tmp/restore
sudo cp /tmp/restore/nxblogger.db /var/lib/nxblogger/data/nxblogger.db
sudo rm -f /var/lib/nxblogger/data/nxblogger.db-wal /var/lib/nxblogger/data/nxblogger.db-shm
sudo rsync -a /tmp/restore/uploads/ /var/lib/nxblogger/uploads/
sudo chown -R nxblogger:nxblogger /var/lib/nxblogger
sudo systemctl start nxblogger
```

(Create `/tmp/restore` first with `mkdir -p /tmp/restore`.)

## 11. Local development on Windows

```powershell
cd C:\Coding\nxblogger
py -3 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt pytest
python manage.py set-password            # local admin password
python manage.py run --port 8800 --debug
```

Open http://127.0.0.1:8800/. Local data goes to `instance\` (ignored by git).
Run the tests with `python -m pytest`.

## 12. Troubleshooting

| Problem | Check |
|---|---|
| Service status | `systemctl status nxblogger` · `journalctl -u nxblogger -n 100` |
| nginx | `sudo nginx -t` · `/var/log/nginx/nxblogger.error.log` |
| Page does not open | Is port 8800 open in the AWS security group? `curl -k https://localhost:8800/healthz` on the server |
| Certificate warning | Let's Encrypt not done yet – check DNS and port 80, then re-run `install.sh` |
| "Upload is too large" | Raise `NXB_MAX_UPLOAD_MB` and run `update.sh --force` |
| update.sh: local changes | `git -C <program folder> status`, then `git -C <program folder> checkout -- .` (with `sudo` for `/opt`) |
| Timers | `systemctl list-timers 'nxblogger*'` |

Health check endpoint: `/healthz` returns `{"status": "ok"}`.
