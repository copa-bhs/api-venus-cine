import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

def load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

load_env_file()

# ==========================================================
# SERVIDOR
# ==========================================================
USER_AGENT = os.getenv("USER_AGENT", "VLC/3.0.20 LibVLC/3.0.20")
PORT = int(os.getenv("PORT", "8000"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
PUBLIC_PANEL_URL = os.getenv("PUBLIC_PANEL_URL", PUBLIC_BASE_URL or "http://localhost:8000").rstrip("/")

# ==========================================================
# TMDB
# ==========================================================
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
TMDB_LANGUAGE = os.getenv("TMDB_LANGUAGE", "pt-BR")
TMDB_ENABLED = bool(TMDB_API_KEY)
TMDB_RECENT_DAYS = int(os.getenv("TMDB_RECENT_DAYS", "90"))
TMDB_MAX_PER_CYCLE = int(os.getenv("TMDB_MAX_PER_CYCLE", "30"))

# ==========================================================
# FANART
# ==========================================================
FANART_API_KEY = os.getenv("FANART_API_KEY", "").strip()
FANART_ENABLED = bool(FANART_API_KEY)
FANART_LANGUAGE = os.getenv("FANART_LANGUAGE", "pt").lower()[:2]

# ==========================================================
# PROXY
# ==========================================================
PROXY_ENABLED = os.getenv("PROXY_ENABLED", "true").lower() == "true"
PROXY_MAX_CONCURRENT = int(os.getenv("PROXY_MAX_CONCURRENT", "100"))
PROXY_MAX_PER_IP = int(os.getenv("PROXY_MAX_PER_IP", "3"))
PROXY_CHUNK_SIZE = int(os.getenv("PROXY_CHUNK_SIZE", "262144"))
PROXY_CONNECT_TIMEOUT = int(os.getenv("PROXY_CONNECT_TIMEOUT", "10"))
PROXY_READ_TIMEOUT = int(os.getenv("PROXY_READ_TIMEOUT", "300"))

# ==========================================================
# SEGURANÇA
# ==========================================================
PAINEL_SECRET = os.getenv("PAINEL_SECRET", "troque-esta-chave-no-env")
STREAM_SECRET = os.getenv("STREAM_SECRET", "troque-esta-outra-chave-no-env")

# ==========================================================
# PAGAMENTOS
# ==========================================================
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "").strip()
MP_ENABLED = bool(MP_ACCESS_TOKEN)
MP_WEBHOOK_SECRET = os.getenv("MP_WEBHOOK_SECRET", "").strip()
STRIPE_SECRET = os.getenv("STRIPE_SECRET", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_ENABLED = bool(STRIPE_SECRET)

# ==========================================================
# DIRETÓRIOS E CACHE
# ==========================================================
CACHE_TTL = int(os.getenv("CACHE_TTL", "21600"))
DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 200

CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

FILMES_FILE = CACHE_DIR / "filmes.json"
SERIES_FILE = CACHE_DIR / "series.json"
CANAIS_FILE = CACHE_DIR / "canais.json"

META_FILE = CACHE_DIR / "meta.json"
NOVIDADES_FILE = CACHE_DIR / "novidades.json"
SLUG_MAP_FILE = CACHE_DIR / "slug_map.json"

SLUG_MAP_FILMES_FILE = CACHE_DIR / "slug_map_filmes.json"
SLUG_MAP_SERIES_FILE = CACHE_DIR / "slug_map_series.json"
SLUG_MAP_CANAIS_FILE = CACHE_DIR / "slug_map_canais.json"

UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
(UPLOAD_DIR / "capas").mkdir(exist_ok=True)
(UPLOAD_DIR / "banners").mkdir(exist_ok=True)
(UPLOAD_DIR / "logos").mkdir(exist_ok=True)