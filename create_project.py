#!/usr/bin/env python3
"""
Run this script on your local machine to create the Polymarket Arbitrage Bot.
Usage: python create_project.py
"""
import os, sys

FILES = {}

# ── requirements.txt ──────────────────────────────────────────────────────────
FILES["requirements.txt"] = """\
py-clob-client>=0.17.0
fastapi>=0.104.0
uvicorn>=0.24.0
jinja2>=3.1.0
python-dotenv>=1.0.0
aiohttp>=3.9.0
sqlalchemy>=2.0.0
aiosqlite>=0.19.0
web3>=6.0.0
python-multipart>=0.0.6
httpx>=0.25.0
"""

# ── .env.example ──────────────────────────────────────────────────────────────
FILES[".env.example"] = """\
CLOB_API_URL=https://clob.polymarket.com
PRIVATE_KEY=your_private_key_here
CHAIN_ID=137
CLOB_API_KEY=
CLOB_API_SECRET=
CLOB_API_PASSPHRASE=
MIN_PROFIT_PCT=0.5
MAX_POSITION_PCT=5.0
MAX_EXPOSURE_PCT=50.0
MAX_CONCURRENT_POSITIONS=10
FEE_RATE=0.02
MOCK_MODE=true
DASHBOARD_PORT=8080
DASHBOARD_HOST=0.0.0.0
DATABASE_URL=sqlite+aiosqlite:///./polymarket_bot.db
LOG_LEVEL=INFO
SCAN_INTERVAL=30
INITIAL_PORTFOLIO=1000.0
"""

# ── .gitignore ────────────────────────────────────────────────────────────────
FILES[".gitignore"] = """\
__pycache__/
*.py[cod]
.venv/
venv/
.env
*.db
*.sqlite3
*.log
.vscode/
.idea/
.DS_Store
"""

def write(path, content):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  created {path}")

for path, content in FILES.items():
    write(path, content)

print("\nBasic files created. Now fetching remaining files...")
