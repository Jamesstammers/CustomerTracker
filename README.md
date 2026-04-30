# Customer Tracker

A small Flask + SQLite web app for tracking customer progress across four
business tracks (Dropship, Supply Only, Build Your Brand, White Label).
Designed to run on a Windows workstation as a local web server for a small team.

## Features
- Username/password login (passwords hashed with werkzeug)
- Customer list with live, wildcard-friendly search
- Multiple contacts per customer, each with its own notes
- Four trackers per customer, each with a timeline of actions and comments
- Standard stages: Initial Contact, In Discussion, Agreement Sent, Agreement Signed
- Dropship-only extra stages: CSV Sent, Products Listed, First Order Placed
- Configurable Dropship products checklist, split into Listed / Not yet listed
- Admin panel for managing users and products
- All data stored in a single `database.db` SQLite file (easy to back up – just copy the file)

---

## 1. Install Python (one time)

1. Download Python 3.10 or newer from <https://www.python.org/downloads/windows/>.
2. **Important:** during install, tick **"Add Python to PATH"**.
3. Verify in PowerShell or Command Prompt:
   ```
   python --version
   ```

## 2. Set up the app (one time)

Open PowerShell in the folder containing this README and run:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python init_db.py
```

`init_db.py` creates `database.db` and prompts you to create the first admin user.

## 3. Run it

Every time you want to start the server:

```powershell
venv\Scripts\activate
python app.py
```

Or just double-click **`run_windows.bat`** (provided).

You'll see something like:
```
* Running on http://127.0.0.1:5000
* Running on http://192.168.x.x:5000
```

- On the host workstation, open <http://localhost:5000>
- From any other machine on your network, open <http://192.168.x.x:5000>
  (replace with the IP shown in the terminal)

## 4. Add the other users

1. Sign in as the admin you created.
2. Click **Admin** in the top nav.
3. Add usernames and passwords for your team. They can change passwords by asking the admin to reset.

## 5. Make it start automatically (optional)

If you want the server to run any time the workstation is on, the simplest option is to put a shortcut to `run_windows.bat` in the **Startup** folder:

1. Press `Win + R`, type `shell:startup`, hit Enter.
2. Right-click in that folder → **New → Shortcut** → browse to `run_windows.bat`.

For a more robust setup you can install it as a Windows Service using a tool like [NSSM](https://nssm.cc/), but for 5 internal users the startup shortcut is usually fine.

## 6. Allow other PCs to reach it

The first time the app runs, Windows Defender will pop up a firewall prompt. Allow access on **Private** networks. If you didn't, you can re-allow it under **Windows Defender Firewall → Allow an app**.

If you want everyone to use a friendly URL like `http://tracker` instead of an IP, ask whoever runs your network to add a DNS entry, or edit each user's `C:\Windows\System32\drivers\etc\hosts` file.

---

## Upgrading from a previous version

If you're replacing an existing copy of these files (for example, after a
bug fix or new feature), keep your existing `database.db` and **re-run**:

```powershell
venv\Scripts\activate
python init_db.py
```

`init_db.py` is safe to run repeatedly – it only adds missing tables and
performs one-time data migrations. Your existing customers, contacts,
trackers and actions are preserved.

---

## Backing up

The entire dataset lives in **`database.db`**. Just copy that file somewhere safe (a scheduled task that copies it nightly to a network drive is plenty for 5 users).

## Configuration

- **Secret key** (used to sign session cookies): edit `app.config["SECRET_KEY"]` near the top of `app.py`, or set the `TRACKER_SECRET_KEY` environment variable.  Pick a long random string – this is what stops people forging login cookies.
- **Port**: change `port=5000` at the bottom of `app.py` if 5000 is already in use.
- **Default products**: edit the `DEFAULT_PRODUCTS` list in `init_db.py` (only used the first time you initialise the DB). After that, manage products through the **Admin** page.

## File tour

```
app.py                Flask routes / business logic
init_db.py            Creates the SQLite schema and seeds defaults
requirements.txt      Python dependencies
run_windows.bat       Convenience launcher (double-click to start)
database.db           Created on first run – your data lives here
templates/            HTML templates (Jinja2)
static/               Bootstrap CSS/JS and our small custom stylesheet
```

## Troubleshooting

- **"`python` is not recognised"** – Python wasn't added to PATH. Re-run the installer and tick "Add Python to PATH", or use the full path like `C:\Users\you\AppData\Local\Programs\Python\Python312\python.exe`.
- **"Port 5000 in use"** – something else is using it. Edit `app.py` and change `port=5000` to e.g. `port=5050`.
- **Other PCs can't connect** – make sure Windows Firewall allows Python on Private networks, and that everyone is on the same network as the workstation.
- **Forgot the admin password** – delete `database.db` and re-run `python init_db.py` (this also wipes data – so back it up first), or open `database.db` with a tool like [DB Browser for SQLite](https://sqlitebrowser.org/) and update the `password_hash` column manually.

---

## Deploying to Render (cloud testing)

This app can be deployed to [Render](https://render.com) for testing. **Important caveat:** the free tier has an *ephemeral filesystem*, meaning the `database.db` file is wiped every time the service redeploys or restarts. Use this for testing the app works in the cloud — not for storing real data without further setup.

### Quick deploy

1. Push the project to a GitHub repository.
2. In Render, **New + → Blueprint**, point it at your repo. Render reads `render.yaml`.
3. When prompted, fill in `INIT_ADMIN_USERNAME` and `INIT_ADMIN_PASSWORD`.
4. Wait for the build to finish (~2 minutes), then visit the service URL and log in.

### Persisting data
- **Render Postgres** (free 1 GB, expires after 30 days) — would require modifying `app.py` to use Postgres instead of SQLite.
- **Persistent disk** (paid plans only, from $7/mo) — attach a disk mounted at `/var/data`, then set the `DATABASE_PATH` env var to `/var/data/database.db`. The app already supports this — no code changes needed.

### Environment variables used
| Variable | Purpose |
|---|---|
| `TRACKER_SECRET_KEY` | Flask session signing key. Auto-generated by Render. |
| `INIT_ADMIN_USERNAME` | First admin user created on a fresh database. |
| `INIT_ADMIN_PASSWORD` | Password for that admin. |
| `DATABASE_PATH` | Optional. Where to write `database.db`. Default: next to `app.py`. |
| `PORT` | Set automatically by Render. The app listens on this port. |
