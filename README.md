# 🎓 Sky Eduworld — Management System

Full-stack education management system.
**Stack:** Python (Flask) + PostgreSQL + Vanilla JS

---

## 📁 Project Structure

```
skyeduworld/
├── app.py              ← Flask backend (all API routes)
├── static/
│   └── index.html      ← Frontend (single-page app)
├── requirements.txt
├── .env.example        ← Copy to .env and fill values
├── Dockerfile
├── docker-compose.yml  ← Local dev + production
├── nginx.conf          ← Reverse proxy config
├── render.yaml         ← One-click Render.com deploy
├── Procfile            ← Railway / Heroku deploy
└── .gitignore
```

---

## 🖥️ Option 1 — Local Setup (Without Docker)

### Prerequisites
- Python 3.10+
- PostgreSQL 14+ installed and running

### Steps

```bash
# 1. Clone / copy project
cd sky_eduworld

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create PostgreSQL database
psql -U postgres
CREATE DATABASE sky_eduworld;
CREATE USER sky_user WITH PASSWORD 'yourpassword';
GRANT ALL PRIVILEGES ON DATABASE sky_eduworld TO sky_user;
\q

# 5. Setup environment
cp .env.example .env
nano .env                         # fill DATABASE_URL and SECRET_KEY

# 6. Run
python app.py
```

Open: **http://localhost:5000**
Login: **admin / sky@2024**

---

## 🐳 Option 2 — Docker Compose (Recommended for Local)

### Prerequisites
- Docker Desktop installed (https://docker.com/products/docker-desktop)

```bash
# Start app + database together
docker compose up -d

# First-time: DB tables are created automatically

# View logs
docker compose logs -f app

# Stop
docker compose down

# Stop and delete all data
docker compose down -v
```

Open: **http://localhost:5000**

### With Nginx (production-like local)
```bash
docker compose --profile production up -d
```
Open: **http://localhost** (port 80)

---

## ☁️ Option 3 — Deploy on Render.com (Free Hosting)

Render.com gives you free hosting with PostgreSQL.

### Steps

1. **Push to GitHub**
```bash
git init
git add .
git commit -m "Initial commit"
# Create repo on github.com, then:
git remote add origin https://github.com/yourusername/sky-eduworld.git
git push -u origin main
```

2. **Deploy on Render**
   - Go to https://render.com → Sign up / Login
   - Click **"New +"** → **"Blueprint"**
   - Connect your GitHub repo
   - Render reads `render.yaml` automatically
   - Click **"Apply"** — it creates the DB + web service automatically

3. **Your app is live** at `https://sky-eduworld.onrender.com`

> **Note:** Free plan sleeps after 15 min inactivity. Upgrade to Starter ($7/mo) for always-on.

---

## 🚂 Option 4 — Deploy on Railway.app ($5/mo)

Railway is great for production — always on, fast.

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Create project
railway init

# Add PostgreSQL
railway add postgresql

# Set env var
railway variables set SECRET_KEY=your-long-random-secret-key

# Deploy
railway up
```

Railway auto-detects `Procfile` and sets `DATABASE_URL` from the PostgreSQL plugin.

---

## 🔧 VPS / Server Setup (Ubuntu)

If you have a VPS (DigitalOcean, Hostinger, AWS EC2, etc.):

```bash
# Install Docker on Ubuntu
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Clone project
git clone https://github.com/yourusername/sky-eduworld.git
cd sky-eduworld

# Create .env
cp .env.example .env
nano .env   # set strong SECRET_KEY and your DATABASE_URL

# Start (with nginx on port 80)
docker compose --profile production up -d

# Auto-restart on reboot
docker compose --profile production up -d --restart unless-stopped
```

### SSL with Let's Encrypt (free HTTPS)
```bash
# Install Certbot
sudo apt install certbot

# Get certificate (stop nginx first)
sudo certbot certonly --standalone -d yourdomain.com

# Copy certs
mkdir ssl
sudo cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem ssl/
sudo cp /etc/letsencrypt/live/yourdomain.com/privkey.pem ssl/

# Uncomment HTTPS block in nginx.conf, then restart
docker compose restart nginx
```

---

## 🔐 Default Login

| Username | Password  | Role        |
|----------|-----------|-------------|
| admin    | sky@2024  | Super Admin |

**Change the password immediately after first login!** → Settings → Change Password

---

## 📊 Features

| Module              | What it does                                              |
|---------------------|-----------------------------------------------------------|
| **Dashboard**       | Live stats, recent admissions, fee tracker, universities  |
| **Students**        | Add / Edit / Delete with full profile, search & filter    |
| **Fee Management**  | Track total fee, paid amount, balance per student         |
| **Receive Fee**     | Record payments, auto-generate printable receipts         |
| **Associate**       | Track persons who help complete admissions, pay them      |
| **Reference**       | Track persons who send student leads, pay incentives      |
| **Universities**    | Manage partner universities with student counts           |
| **Documents**       | Issue and track university documents per student          |
| **Reports**         | Download CSV reports for all modules                      |
| **Settings**        | Multi-user management, password change                    |

---

## 🌐 API Reference

All endpoints require session login (cookie-based).

```
POST   /api/login                      Login
POST   /api/logout                     Logout
GET    /api/me                         Current user info

GET    /api/dashboard                  Dashboard stats
GET    /api/students?q=&university=&status=   List students
POST   /api/students                   Add student
PUT    /api/students/:id               Edit student
DELETE /api/students/:id               Delete student

GET    /api/students/:id/payments      Payment history
POST   /api/students/:id/payments      Record payment

GET    /api/associates                 List associates
POST   /api/associates                 Add associate
DELETE /api/associates/:id             Delete

GET    /api/references                 List references
POST   /api/references                 Add reference
DELETE /api/references/:id             Delete

GET    /api/universities               List universities
POST   /api/universities               Add university

GET    /api/documents                  List documents
POST   /api/documents                  Issue document

GET    /api/users                      List users (admin only)
POST   /api/users                      Add user (admin only)
DELETE /api/users/:id                  Delete user (admin only)
POST   /api/change-password            Change own password

GET    /api/reports/students           CSV export
GET    /api/reports/fees               CSV export
GET    /api/reports/outstanding        CSV export
GET    /api/reports/assoc-ref          CSV export
```

---

## 💾 Database Backup

```bash
# Backup
docker exec sky_eduworld_db pg_dump -U sky_user sky_eduworld > backup_$(date +%Y%m%d).sql

# Restore
docker exec -i sky_eduworld_db psql -U sky_user sky_eduworld < backup_20250510.sql
```

---

## 🛠️ Tech Stack

- **Backend:** Python 3.12, Flask 3.1, psycopg2
- **Database:** PostgreSQL 16
- **Frontend:** Vanilla HTML/CSS/JS (no framework, zero npm)
- **Production:** Gunicorn + Nginx + Docker
