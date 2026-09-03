# app/main.py
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import os
import re
import secrets
import time
import pytz
import datetime
import subprocess
import sys
import uuid
from urllib.parse import urlparse

from typing import Any, Dict, List, Optional, Set, Tuple
import httpx
from fastapi import HTTPException
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query, Header
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Template
from kiteconnect import KiteConnect, KiteTicker 
from .redis_store import RedisStore, now_ist, now_ist_date
from .chartink_client import (
    parse_chartink_payload,
    normalize_alert_name,
    normalize_symbols,
    normalize_symbol,
)
from .trade_engine import TradeEngine
from .custom_strategy import (
    gvk_trend_required_candles,
    gmma_gold_cross_required_candles,
    pure_liquidity_required_candles,
    resolve_gmma_gold_cross_settings,
    resolve_gmma_obv_settings,
    resolve_gvk_trend_settings,
    resolve_liquidity_sweep_settings,
    resolve_pure_liquidity_sweep_settings,
    resolve_settings,
    timeframe_interval,
    timeframe_minutes,
    validate_custom_config,
    validate_gmma_gold_cross_config,
    validate_gmma_obv_config,
    validate_gvk_trend_config,
    validate_liquidity_sweep_config,
    validate_pure_liquidity_sweep_config,
)
from .websocket_manager import WebSocketManager
from .stock_sector import SECTOR_INDEX_INSTRUMENTS, STOCK_INDEX_MAPPING
from .dhan_broker import DHAN_INSTRUMENTS, DhanContext, DhanFeedService, MarketFeed, dhanhq
from .backtest import run_custom_strategy_backtest
from .services.runtime import ServiceRuntime
from .service_queues import MARKET_SUBSCRIPTION_QUEUE
from .auth import AuthService
from .middleware import AuthMiddleware, get_current_user, SecurityHeadersMiddleware
from .custom_middleware import SelectiveHostMiddleware
import logging

# Windows services and scheduled tasks often inherit a legacy console
# encoding. Console output must never break request processing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass

# Security Imports
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from .security_config import (
    ALLOWED_HOSTS, 
    get_csp_header_value, 
    RATE_LIMIT_AUTH_OTP, 
    RATE_LIMIT_AUTH_VERIFY, 
    RATE_LIMIT_LOGIN
)

# Initialize Limiter
limiter = Limiter(key_func=get_remote_address)


# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()  # Load from .env file
except ImportError:
    pass  # dotenv not installed, use system env vars

# Import encryption module
try:
    from .crypto import init_encryption
    ENCRYPTION_AVAILABLE = True
except ImportError:
    ENCRYPTION_AVAILABLE = False
    print("⚠️  Encryption module not available. Install cryptography: pip install cryptography")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("app.main")

# make sure your module loggers show INFO
logging.getLogger("trade_engine").setLevel(logging.INFO)
logging.getLogger("uvicorn").setLevel(logging.INFO)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)
# Filter out spammy health check logs
class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Return False to filter OUT the record if it matches our path
        return record.args and len(record.args) >= 3 and "/api/zerodha-status" not in str(record.args[2])

# Apply filter to suppress only the specific status endpoint
logging.getLogger("uvicorn.access").setLevel(logging.INFO)  # Keep INFO for other requests
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

# Optional stdout filter to suppress verbose middleware prints
_SUPPRESS_MW = (os.getenv("SUPPRESS_MIDDLEWARE_LOGS", "1") or "").strip().lower() in {"1", "true", "yes", "on"}
if _SUPPRESS_MW:
    class _FilteredStdout:
        def __init__(self, stream, drop_prefixes):
            self._stream = stream
            self._drop_prefixes = tuple(drop_prefixes)
            self._buf = ""

        def write(self, s):
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if not line.startswith(self._drop_prefixes):
                    self._stream.write(line + "\n")
            return len(s)

        def flush(self):
            if self._buf:
                if not self._buf.startswith(self._drop_prefixes):
                    self._stream.write(self._buf)
                self._buf = ""
            self._stream.flush()

        def __getattr__(self, name):
            return getattr(self._stream, name)

    sys.stdout = _FilteredStdout(sys.stdout, ["[MIDDLEWARE]"])

# -----------------------------
# Config
# -----------------------------
OFFICIAL_DHAN_AUTH_BASE_URL = "https://auth.dhan.co"


def _normalise_dhan_auth_base_url(raw: str | None) -> str:
    """
    Dhan individual API-key auth must start from auth.dhan.co.
    The login page may internally redirect to Dhan-owned UI hosts, but the app
    should never generate partner-login URLs from configuration.
    """
    value = (raw or OFFICIAL_DHAN_AUTH_BASE_URL).strip().rstrip("/")
    if not value:
        return OFFICIAL_DHAN_AUTH_BASE_URL
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    scheme = parsed.scheme.lower() if parsed.scheme else "https"
    host = parsed.netloc.lower()
    if scheme != "https" or host != "auth.dhan.co":
        return OFFICIAL_DHAN_AUTH_BASE_URL
    return f"{scheme}://{host}"


REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
DHAN_AUTH_BASE_URL = _normalise_dhan_auth_base_url(os.getenv("DHAN_AUTH_BASE_URL"))
ENABLE_SERVICE_RESTART = (os.getenv("ENABLE_SERVICE_RESTART") or "").strip().lower() in {"1", "true", "yes", "on"}
SERVICE_RESTART_TOKEN = (os.getenv("SERVICE_RESTART_TOKEN") or "").strip()
TRADING_SYSTEMD_UNIT = (os.getenv("TRADING_SYSTEMD_UNIT") or "trading").strip()
TRADING_RESTART_CMD = (os.getenv("TRADING_RESTART_CMD") or f"systemctl restart --no-block {TRADING_SYSTEMD_UNIT}").strip()
TRADING_RESTART_TIMEOUT_SEC = float((os.getenv("TRADING_RESTART_TIMEOUT_SEC") or "15").strip() or "15")
ADMIN_SESSION_COOKIE = (os.getenv("ADMIN_SESSION_COOKIE") or "ashuchart_admin_session").strip()
ADMIN_SESSION_TTL_SEC = int(float((os.getenv("ADMIN_SESSION_TTL_SEC") or "43200").strip() or "43200"))
ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
ADMIN_AUTH_ENABLED_RAW = (os.getenv("ADMIN_AUTH_ENABLED") or "1").strip().lower()
WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "").strip()
ADMIN_COOKIE_SECURE = (os.getenv("ADMIN_COOKIE_SECURE") or "").strip().lower()
PASSWORD_MIN_LEN = 8
ADMIN_LOGIN_LOCK_AFTER = int(float((os.getenv("ADMIN_LOGIN_LOCK_AFTER") or "8").strip() or "8"))


def _admin_auth_enabled() -> bool:
    if _is_test_mode():
        return False
    return ADMIN_AUTH_ENABLED_RAW not in {"0", "false", "no", "off"}


def _admin_cookie_secure() -> bool:
    if ADMIN_COOKIE_SECURE in {"1", "true", "yes", "on"}:
        return True
    if ADMIN_COOKIE_SECURE in {"0", "false", "no", "off"}:
        return False
    return str(os.getenv("PUBLIC_BASE_URL") or "").strip().lower().startswith("https://")


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _new_admin_session_token() -> str:
    return secrets.token_urlsafe(48)


def _normalise_admin_email(email: str) -> str:
    return str(email or "").strip().lower()


def _valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", _normalise_admin_email(email)))


def _hash_password(password: str, *, iterations: int = 390000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, int(iterations))
    return "pbkdf2_sha256${}${}${}".format(
        int(iterations),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, raw_iterations, raw_salt, raw_digest = str(password_hash or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(raw_digest.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


async def _load_admin() -> Dict[str, Any]:
    fn = getattr(store, "load_admin_auth", None)
    if not callable(fn):
        return {}
    return await fn()


async def _load_admin_totp_secret() -> str:
    fn = getattr(store, "load_admin_totp_secret", None)
    if not callable(fn):
        return ""
    return str(await fn() or "").strip()


async def _admin_totp_is_set() -> bool:
    return bool(await _load_admin_totp_secret())


def _admin_public_http_path(path: str) -> bool:
    path = str(path or "")
    return (
        path == "/auth"
        or path.startswith("/auth/")
        or path.startswith("/static/")
        or path.startswith("/webhook/")
        or path in {"/favicon.ico", "/robots.txt"}
    )


async def _admin_session_from_request(request: Request) -> Dict[str, Any]:
    return await _admin_session_from_token(request.cookies.get(ADMIN_SESSION_COOKIE, ""))


async def _admin_session_from_token(token: str) -> Dict[str, Any]:
    if not token:
        return {}
    fn = getattr(store, "load_admin_session", None)
    if not callable(fn):
        return {}
    session = await fn(_hash_session_token(token))
    admin = await _load_admin()
    if not session or not admin:
        return {}
    if _normalise_admin_email(str(session.get("email") or "")) != _normalise_admin_email(str(admin.get("email") or "")):
        return {}
    return dict(session)


def _webhook_secret_valid(request: Request) -> bool:
    if not WEBHOOK_SECRET:
        return True
    provided = str(request.query_params.get("secret") or request.headers.get("X-Webhook-Secret") or "").strip()
    return bool(provided) and hmac.compare_digest(provided, WEBHOOK_SECRET)


def _public_base_url(request: Request) -> str:
    configured = str(os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


async def _create_admin_session(email: str) -> str:
    token = _new_admin_session_token()
    fn = getattr(store, "save_admin_session", None)
    if not callable(fn):
        raise RuntimeError("ADMIN_SESSION_STORE_UNAVAILABLE")
    await fn(_hash_session_token(token), _normalise_admin_email(email), ADMIN_SESSION_TTL_SEC)
    return token


async def _verify_admin_password(email: str, password: str) -> bool:
    admin = await _load_admin()
    stored_email = _normalise_admin_email(str(admin.get("email") or ""))
    if not stored_email or _normalise_admin_email(email) != stored_email:
        return False
    return _verify_password(password, str(admin.get("password_hash") or ""))


def _totp_uri(email: str, secret: str) -> str:
    import pyotp

    issuer = os.getenv("ADMIN_TOTP_ISSUER", "AshuChart")
    return pyotp.TOTP(secret).provisioning_uri(name=_normalise_admin_email(email), issuer_name=issuer)


def _totp_qr_data_url(uri: str) -> str:
    try:
        import qrcode

        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        log.warning("ADMIN_TOTP_QR_GENERATION_FAILED | err=%s", exc)
        return ""


def _verify_totp(secret: str, code: str) -> bool:
    import pyotp

    clean_code = re.sub(r"\D", "", str(code or ""))
    if len(clean_code) != 6:
        return False
    return bool(pyotp.TOTP(secret).verify(clean_code, valid_window=1))


def _is_test_mode() -> bool:
    v = (os.getenv("APP_TESTING") or "").strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    return (os.getenv("APP_ENV") or "").strip().lower() == "test"


def _run_restart_command(cmd: str) -> Dict[str, Any]:
    """
    Execute the configured restart command and wait briefly for acceptance/failure.
    The command should return quickly, for example by using `systemctl --no-block`.
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return {"ok": False, "error": "TRADING_RESTART_CMD_EMPTY"}

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=max(1.0, TRADING_RESTART_TIMEOUT_SEC),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"RESTART_TIMEOUT_AFTER_{int(max(1.0, TRADING_RESTART_TIMEOUT_SEC))}S",
        }
    except Exception as e:
        return {"ok": False, "error": f"RESTART_SPAWN_FAILED:{e}"}

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if detail:
            detail = detail.replace("\r", " ").replace("\n", " ")[:400]
        return {
            "ok": False,
            "error": f"RESTART_CMD_FAILED:{proc.returncode}",
            "detail": detail or "Command returned non-zero exit code",
        }

    detail = (proc.stdout or "").strip()
    return {"ok": True, "detail": detail[:400] if detail else ""}

app = FastAPI(title="AlgoEdge Ultra-Low Latency")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def _json_error_response(
    request: Request,
    status_code: int,
    error: str,
    detail: Any = "",
    *,
    request_id: Optional[str] = None,
) -> JSONResponse:
    rid = request_id or request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    payload: Dict[str, Any] = {
        "ok": False,
        "error": str(error or "ERROR"),
        "detail": detail if isinstance(detail, (str, int, float, bool, list, dict)) else str(detail),
        "request_id": rid,
    }
    return JSONResponse(status_code=int(status_code), content=payload)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if exc.detail is not None else "HTTP_ERROR"
    return _json_error_response(request, exc.status_code, str(detail), detail)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _json_error_response(request, 422, "REQUEST_VALIDATION_ERROR", exc.errors())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    log.exception(
        "UNHANDLED_API_EXCEPTION | request_id=%s method=%s path=%s",
        request_id,
        request.method,
        request.url.path,
    )
    detail = str(exc) if _is_test_mode() else "Unexpected server error. Check server logs with request_id."
    return _json_error_response(request, 500, "INTERNAL_SERVER_ERROR", detail, request_id=request_id)

# 1. Selective Host Middleware (Strict for dashboard, permissive for webhooks)
app.add_middleware(
    SelectiveHostMiddleware,
    allowed_hosts=ALLOWED_HOSTS,
    bypass_paths=["/webhook/"]  # Webhook endpoints bypass host validation
)

# 2. Security Headers (XSS, CSP, etc.)
app.add_middleware(
    SecurityHeadersMiddleware,
    csp_header=get_csp_header_value()
)

# 3. SlowAPI Middleware (Rate Limiting)
app.add_middleware(SlowAPIMiddleware)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
    "https://clicktrade.live",
    "https://www.clicktrade.live"],  # change in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize encryption manager (will be set in startup)
encryption_manager = None

ws_mgr = WebSocketManager()
# store will be initialized in startup after encryption is ready
store = None
# auth_service will be initialized after store is ready
auth_service = None

# Engines per user
ENGINE: Dict[int, TradeEngine] = {}
SERVICE_RUNTIME: Optional[ServiceRuntime] = None
DAILY_DASHBOARD_CLEANUP_TASK: Optional[asyncio.Task] = None
_LAST_DASHBOARD_CLEANUP_YMD: str = ""


async def ensure_store_ready():
    """
    Lazily initialize storage for tests and direct app clients that do not run
    FastAPI lifespan startup. Normal uvicorn startup still initializes this first.
    """
    global encryption_manager, store, auth_service
    if store is not None:
        return store

    if _is_test_mode():
        from .memory_store import InMemoryStore

        store = InMemoryStore()
    else:
        if ENCRYPTION_AVAILABLE and encryption_manager is None:
            try:
                encryption_manager = init_encryption()
            except Exception as exc:
                print(f"⚠️  Encryption initialization failed: {exc}")
                encryption_manager = None
        store = RedisStore(REDIS_URL, encryption_manager)
        if not await store.ping():
            raise RuntimeError(f"Redis is not reachable at {REDIS_URL}")
        await store.init_scripts()

    auth_service = AuthService(store)
    return store


@app.middleware("http")
async def ensure_app_state_middleware(request: Request, call_next):
    await ensure_store_ready()
    if _admin_auth_enabled() and not _admin_public_http_path(request.url.path):
        session = await _admin_session_from_request(request)
        if not session:
            if request.url.path.startswith("/api/") or request.url.path.startswith("/ws/"):
                return _json_error_response(request, 401, "ADMIN_AUTH_REQUIRED", "Admin login required")
            return RedirectResponse(url="/auth", status_code=303)
    return await call_next(request)

# -----------------------------
# KiteTicker globals (single ticker)
# -----------------------------
KT: Optional[KiteTicker] = None
KT_CONNECTED: bool = False
KT_TASK: Optional[asyncio.Future] = None
KT_LOCK = asyncio.Lock()

KT_USER_ID: Optional[int] = None
KT_ACCESS_TOKEN: str = ""

DHAN_FEED: Optional[DhanFeedService] = None
DHAN_CONNECTED: bool = False
DHAN_USER_ID: Optional[int] = None
DHAN_ACCESS_TOKEN: str = ""

APP_LOOP: Optional[asyncio.AbstractEventLoop] = None

# Subscriptions + token map
SUB_TOKENS: Set[int] = set()
TOKEN_TO_SYMBOL: Dict[int, str] = {}
SYMBOL_TOKEN: Dict[str, int] = {}

# If webhook arrives before instruments map is loaded, we queue symbols here
PENDING_SYMBOLS: Dict[int, Set[str]] = {}
INSTR_LOCK = asyncio.Lock()

# Zerodha session validity cache (avoid calling profile() every 5s)
_SESSION_CACHE: Dict[int, Dict[str, Any]] = {}  # user_id -> {"ok": bool, "ts": float}
_SESSION_CACHE_TTL = 30.0  # seconds

# Throttle Redis position writes (per symbol)
_LAST_POS_SAVE: Dict[Tuple[int, str], float] = {}
_POS_SAVE_THROTTLE_SEC = 0.8


def _save_feed_health_nowait(user_id: int, broker: str, connected: bool, detail: str = "") -> None:
    loop = APP_LOOP
    app_store = store
    if loop is None or app_store is None:
        return

    async def _save() -> None:
        try:
            await app_store.save_broker_feed_health(
                int(user_id),
                broker,
                bool(connected),
                ttl_sec=15,
                detail=detail,
            )
        except Exception as exc:
            print(f"[FEED] health save failed broker={broker} user={user_id}: {exc}")

    try:
        asyncio.run_coroutine_threadsafe(_save(), loop)
    except Exception as exc:
        print(f"[FEED] health schedule failed broker={broker} user={user_id}: {exc}")


async def _load_shared_feed_connected(user_id: int, broker: str) -> Tuple[bool, Dict[str, Any]]:
    try:
        health = await store.load_broker_feed_health(int(user_id), broker)
    except Exception as exc:
        health = {"connected": False, "reason": f"BROKER_FEED_HEALTH_ERROR:{exc}"}
    age = float(health.get("age_sec", 999999.0) or 999999.0)
    return bool(health.get("connected", False)) and age <= 15.0, health


async def _poke_market_feed_service(user_id: int, symbols: Optional[List[str]] = None, source: str = "broker_auth") -> None:
    app_store = store
    redis_client = getattr(app_store, "redis", None)
    if redis_client is None:
        return
    try:
        await redis_client.rpush(
            MARKET_SUBSCRIPTION_QUEUE,
            json.dumps(
                {
                    "user_id": int(user_id),
                    "symbols": list(symbols or []),
                    "source": source,
                    "timestamp": time.time(),
                },
                separators=(",", ":"),
            ),
        )
    except Exception as exc:
        log.warning("Market feed poke failed | user=%s source=%s err=%s", user_id, source, exc)

# Throttle instrument reload
_LAST_INSTR_RELOAD = 0.0
_INSTR_RELOAD_INTERVAL = 300.0  # 5 minutes


# -----------------------------
# Helpers
# -----------------------------
def _read_dashboard_template(user_id: int, username: str) -> str:
    with open("app/static/dashboard.html", "r", encoding="utf-8") as f:
        html = f.read()
    t = Template(html)
    return t.render(USER_ID=user_id, USERNAME=username)


def _kite_client(api_key: str, access_token: str) -> KiteConnect:
    k = KiteConnect(api_key=api_key)
    k.set_access_token(access_token)
    return k


def _sym_safe(x: Any) -> str:
    """
    Strong symbol normalizer (extra-safe).
    Uses redis_store.norm_symbol as the single source of truth.
    """
    return normalize_symbol(x)


def _dhan_response_ok(response: Any) -> bool:
    if not isinstance(response, dict):
        return response is not None
    if response.get("errorCode") or response.get("errorMessage"):
        return False
    status = str(response.get("status") or "").strip().lower()
    return status not in {"failure", "failed", "error"}


def _dhan_auth_error(response: Any) -> str:
    if isinstance(response, dict):
        for key in ("errorMessage", "error_message", "message", "remarks", "detail", "error"):
            value = response.get(key)
            if value:
                return str(value)
        if response.get("errorCode"):
            return str(response.get("errorCode"))
    return "DHAN_AUTH_REQUEST_FAILED"


def _dhan_auth_login_url(consent_app_id: str) -> str:
    return f"{DHAN_AUTH_BASE_URL}/login/consentApp-login?consentAppId={consent_app_id}"


async def _dhan_generate_consent(client_id: str, api_key: str, api_secret: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        response = await client.post(
            f"{DHAN_AUTH_BASE_URL}/app/generate-consent",
            params={"client_id": client_id},
            headers={"app_id": api_key, "app_secret": api_secret},
        )
    try:
        data = response.json()
    except Exception:
        data = {"message": response.text[:500]}
    if response.status_code >= 400 or not _dhan_response_ok(data):
        raise RuntimeError(_dhan_auth_error(data))
    consent_app_id = str(data.get("consentAppId") or "").strip()
    if not consent_app_id:
        raise RuntimeError("DHAN_CONSENT_APP_ID_MISSING")
    return {
        "consentAppId": consent_app_id,
        "consentAppStatus": str(data.get("consentAppStatus") or ""),
        "login_url": _dhan_auth_login_url(consent_app_id),
        "raw": data,
    }


async def _dhan_consume_app_consent(token_id: str, api_key: str, api_secret: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        response = await client.get(
            f"{DHAN_AUTH_BASE_URL}/app/consumeApp-consent",
            params={"tokenId": token_id},
            headers={"app_id": api_key, "app_secret": api_secret},
        )
    try:
        data = response.json()
    except Exception:
        data = {"message": response.text[:500]}
    if response.status_code >= 400 or not _dhan_response_ok(data):
        raise RuntimeError(_dhan_auth_error(data))
    access_token = str(data.get("accessToken") or data.get("access_token") or "").strip()
    client_id = str(data.get("dhanClientId") or data.get("clientId") or "").strip()
    if not client_id or not access_token:
        raise RuntimeError("DHAN_ACCESS_TOKEN_MISSING")
    return {
        "client_id": client_id,
        "access_token": access_token,
        "expiry_time": str(data.get("expiryTime") or data.get("expiry_time") or ""),
        "client_name": str(data.get("dhanClientName") or ""),
        "ucc": str(data.get("dhanClientUcc") or ""),
        "poa": bool(data.get("givenPowerOfAttorney", False)),
        "raw": data,
    }


async def _activate_dhan_token(
    user_id: int,
    client_id: str,
    access_token: str,
    token_expiry: str = "",
    auth_mode: str = "API_KEY",
) -> Dict[str, Any]:
    await store.save_dhan_credentials(
        user_id,
        client_id,
        access_token,
        token_expiry=token_expiry,
        auth_mode=auth_mode,
    )
    await store.save_broker(user_id, "DHAN")
    _SESSION_CACHE.pop(user_id, None)
    SYMBOL_TOKEN.clear()
    TOKEN_TO_SYMBOL.clear()
    SUB_TOKENS.clear()

    eng = await ensure_engine(user_id)
    await eng.configure_broker()
    await _poke_market_feed_service(user_id, list(STOCK_INDEX_MAPPING.keys()), "dhan_token_activated")

    warning = ""
    if not _is_test_mode():
        try:
            await _stop_kite_ticker()
            await build_symbol_token_map_from_dhan(user_id)
            await subscribe_symbols_for_user(user_id, list(STOCK_INDEX_MAPPING.keys()))
            await subscribe_dhan_sector_indices_for_user(user_id)
            await start_dhan_feed(user_id)
        except Exception:
            warning = "DHAN_FEED_START_FAILED"
            log.exception("Dhan token activated but feed startup failed | user=%s", user_id)

    result: Dict[str, Any] = {
        "ok": True,
        "broker": "DHAN",
        "auth_mode": auth_mode,
        "client_id": client_id,
        "access_token_generated": bool(access_token),
        "expiry_time": token_expiry,
    }
    if warning:
        result["warning"] = warning
    return result


def _nested_number(data: Any, keys: Tuple[str, ...]) -> float:
    stack = [data]
    wanted = {str(k).lower() for k in keys}
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if str(key).lower() in wanted:
                    try:
                        number = float(value or 0.0)
                    except Exception:
                        number = 0.0
                    if number > 0:
                        return number
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return 0.0


def _quote_for_security(data: Any, security_id: str) -> Any:
    sid = str(security_id)
    stack = [data]
    fallback = None
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if str(key) == sid:
                    return value
            raw_sid = (
                item.get("security_id")
                or item.get("securityId")
                or item.get("SecurityId")
                or item.get("SECURITY_ID")
            )
            if raw_sid is not None and str(raw_sid) == sid:
                return item
            if fallback is None and any(
                str(key).lower() in {"last_price", "lastprice", "ltp", "ohlc"}
                for key in item.keys()
            ):
                fallback = item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return fallback or {}


def _sector_quote_values(response: Any, security_id: str) -> Dict[str, float]:
    data = response.get("data") if isinstance(response, dict) and "data" in response else response
    quote = _quote_for_security(data, str(security_id))
    ltp = _nested_number(
        quote,
        ("last_price", "lastPrice", "last_traded_price", "lastTradedPrice", "ltp", "LTP"),
    )
    prev_close = 0.0
    if isinstance(quote, dict) and isinstance(quote.get("ohlc"), dict):
        prev_close = _nested_number(quote.get("ohlc"), ("close", "Close"))
    if prev_close <= 0:
        prev_close = _nested_number(
            quote,
            ("prev_close", "previous_close", "previousClose", "prevClose", "close", "Close"),
        )
    pct = ((ltp - prev_close) / prev_close) * 100.0 if ltp > 0 and prev_close > 0 else 0.0
    return {"ltp": ltp, "prev_close": prev_close, "pct": pct}


async def is_session_valid(user_id: int) -> bool:
    """
    Dashboard polls every 5s. Cache validity for short TTL.
    """
    app_store = await ensure_store_ready()
    now = time.time()
    cached = _SESSION_CACHE.get(user_id)
    if cached and (now - float(cached.get("ts", 0.0)) < _SESSION_CACHE_TTL):
        return bool(cached.get("ok", False))

    broker = await app_store.load_broker(user_id)
    if broker == "DHAN":
        creds = await app_store.load_dhan_credentials(user_id)
        client_id = str(creds.get("client_id") or "").strip()
        access_token = str(creds.get("access_token") or "").strip()
        if not client_id or not access_token:
            _SESSION_CACHE[user_id] = {"ok": False, "ts": now, "broker": broker}
            return False
        try:
            client = dhanhq(DhanContext(client_id, access_token))
            response = await asyncio.to_thread(client.get_fund_limits)
            valid = _dhan_response_ok(response)
            _SESSION_CACHE[user_id] = {"ok": valid, "ts": now, "broker": broker}
            return valid
        except Exception:
            _SESSION_CACHE[user_id] = {"ok": False, "ts": now, "broker": broker}
            return False

    creds = await app_store.load_credentials(user_id)
    at = (await app_store.load_access_token(user_id)).strip()
    api_key = (creds.get("api_key") or "").strip()

    if not api_key or not at:
        _SESSION_CACHE[user_id] = {"ok": False, "ts": now}
        return False

    try:
        kite = _kite_client(api_key, at)
        kite.profile()  # validates access_token
        _SESSION_CACHE[user_id] = {"ok": True, "ts": now}
        return True
    except Exception:
        _SESSION_CACHE[user_id] = {"ok": False, "ts": now}
        return False


# async def ensure_engine(user_id: int) -> TradeEngine:
#     user_id = int(user_id)
#     if user_id not in ENGINE:
#         ENGINE[user_id] = TradeEngine(user_id=user_id, store=store)
#         await ENGINE[user_id].configure_kite()
#     return ENGINE[user_id]
async def ensure_engine(user_id: int) -> TradeEngine:
    user_id = int(user_id)
    app_store = await ensure_store_ready()
    if user_id not in ENGINE:
        ENGINE[user_id] = TradeEngine(
            user_id=user_id,
            store=app_store,
            broadcast_cb=ws_mgr.broadcast_nowait,
            token_resolver=lambda symbol: SYMBOL_TOKEN.get(_sym_safe(symbol)),
            token_ready_cb=_ensure_token_map_ready,
        )
        await ENGINE[user_id].configure_kite()
        try:
            cache = await app_store.load_sector_cache(user_id)
            if cache:
                ENGINE[user_id].load_sector_cache(cache)
        except Exception:
            pass

        # ✅ Restore open positions after restart
        restored = await ENGINE[user_id].rehydrate_open_positions()
        if restored:
            # ✅ Ensure ticks come for these symbols
            asyncio.create_task(subscribe_symbols_for_user(user_id, restored))

    return ENGINE[user_id]


async def ensure_service_runtime() -> ServiceRuntime:
    global SERVICE_RUNTIME
    await ensure_store_ready()
    if SERVICE_RUNTIME is None:
        SERVICE_RUNTIME = ServiceRuntime(
            store_provider=lambda: store,
            ws_manager=ws_mgr,
            ensure_engine=lambda uid: ensure_engine(uid),
            subscribe_symbols=lambda uid, symbols: subscribe_symbols_for_user(uid, symbols),
            start_feed=lambda uid: restart_selected_feed(uid),
            stop_dhan_feed=lambda: _stop_dhan_feed(),
            stop_kite_feed=lambda: _stop_kite_ticker(),
        )
        if not _is_test_mode():
            await SERVICE_RUNTIME.start()
    return SERVICE_RUNTIME


async def _clear_dashboard_trading_state_for_new_day() -> None:
    """
    Clear dashboard-visible trade/alert history once per IST date.
    Alert configs, broker credentials, sessions, and sector cache are preserved.
    """
    global _LAST_DASHBOARD_CLEANUP_YMD
    if store is None:
        return
    ymd = now_ist_date()
    if _LAST_DASHBOARD_CLEANUP_YMD == ymd:
        return
    try:
        user_ids = await store.list_all_user_ids()
    except Exception as exc:
        log.warning("DAILY_DASHBOARD_CLEANUP_USER_LIST_FAIL | err=%s", exc)
        user_ids = [1]
    if not user_ids:
        user_ids = [1]

    for uid in user_ids:
        try:
            cleanup = await store.clear_daily_trading_state(int(uid))
            engine = ENGINE.get(int(uid))
            if engine is not None:
                for pos in list(engine.positions.values()):
                    if getattr(pos, "product", "") == "CNC" and getattr(pos, "status", "") in {
                        "OPEN",
                        "EXITING",
                        "EXIT_CONDITIONS_MET",
                    }:
                        await engine._persist_position_state(pos)
                        await store.mark_open(int(uid), pos.symbol, pos.trade_id, ttl_sec=60 * 60 * 24 * 14)
            log.info(
                "DAILY_DASHBOARD_CLEANUP_OK | user=%s ymd=%s deleted=%s scanned=%s",
                uid,
                ymd,
                cleanup.get("deleted_keys", 0),
                cleanup.get("scanned_keys", 0),
            )
        except Exception as exc:
            log.exception("DAILY_DASHBOARD_CLEANUP_FAIL | user=%s ymd=%s err=%s", uid, ymd, exc)
    _LAST_DASHBOARD_CLEANUP_YMD = ymd


async def schedule_daily_dashboard_cleanup() -> None:
    """
    Keeps dashboard trade/history tables fresh for each new IST day.

    Runs shortly after midnight IST while the app is alive. The deployment
    timer can still run app/daily_cleanup.py as an external safety net.
    """
    while True:
        try:
            ist = pytz.timezone("Asia/Kolkata")
            now = datetime.datetime.now(ist)
            if now.hour == 0 and now.minute < 10:
                await _clear_dashboard_trading_state_for_new_day()
                await asyncio.sleep(10 * 60)
            else:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("DAILY_DASHBOARD_CLEANUP_LOOP_FAIL | err=%s", exc)
            await asyncio.sleep(60)



# -----------------------------
# Instruments (symbol -> token)
# -----------------------------
async def build_symbol_token_map_from_kite(user_id: int) -> bool:
    """
    Download NSE instruments once after login and keep in memory.
    Heavy operation: never do this in the webhook hot path unless unavoidable.
    """
    if _is_test_mode():
        return False
    user_id = int(user_id)

    creds = await store.load_credentials(user_id)
    api_key = (creds.get("api_key") or "").strip()
    access_token = (await store.load_access_token(user_id)).strip()
    if not api_key or not access_token:
        print("[INSTR] Missing api_key/access_token; cannot load instruments")
        return False

    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

        print("[INSTR] Downloading NSE instruments...")
        all_instruments = kite.instruments("NSE")

        if not all_instruments:
            print("[INSTR] ❌ No instruments returned from Kite")
            return False

        # Clear maps
        temp_sym_tok = {}
        temp_tok_sym = {}

        for ins in all_instruments:
            # We want BOTH original tradingsymbol and normalized one to be safe
            raw_sym = ins.get("tradingsymbol", "")
            norm_sym = _sym_safe(raw_sym)
            tok = ins.get("instrument_token")
            
            if tok:
                itok = int(tok)
                temp_tok_sym[itok] = norm_sym
                
                # Store under both raw and normalized if different
                if raw_sym:
                    temp_sym_tok[raw_sym] = itok
                    # Also store base without common suffixes (e.g., "-EQ", ".NS")
                    if raw_sym.endswith("-EQ"):
                        temp_sym_tok[raw_sym[:-3]] = itok
                    if raw_sym.endswith(".NS"):
                        temp_sym_tok[raw_sym[:-3]] = itok
                if norm_sym:
                    temp_sym_tok[norm_sym] = itok

        SYMBOL_TOKEN.clear()
        SYMBOL_TOKEN.update(temp_sym_tok)
        TOKEN_TO_SYMBOL.clear()
        TOKEN_TO_SYMBOL.update(temp_tok_sym)

        print(f"[INSTR] ✅ Loaded {len(SYMBOL_TOKEN)} symbols into memory (Source: NSE)")
        
        # Debug: check if common symbols are present - Explicit check for M&M and friends
        for test_sym in ["TATAMOTORS", "PEL", "SBIN", "RELIANCE", "M&M", "NIVABUPA"]:
            found = False
            if test_sym in SYMBOL_TOKEN:
                print(f"[INSTR] Verified: {test_sym} -> {SYMBOL_TOKEN[test_sym]}")
                found = True
            if f"{test_sym}-EQ" in SYMBOL_TOKEN:
                 print(f"[INSTR] Verified: {test_sym} found as {test_sym}-EQ -> {SYMBOL_TOKEN[f'{test_sym}-EQ']}")
                 found = True
            
            if not found:
                print(f"[INSTR] ⚠️ Not found in NSE map: {test_sym}")

        return True
    except Exception as e:
        print("[INSTR] instruments download failed:", e)
        return False


async def build_symbol_token_map_from_dhan(user_id: int) -> bool:
    if _is_test_mode():
        return False
    if not await DHAN_INSTRUMENTS.ensure_loaded():
        return False
    SYMBOL_TOKEN.clear()
    TOKEN_TO_SYMBOL.clear()
    for symbol, security_id in DHAN_INSTRUMENTS.symbol_to_security.items():
        try:
            numeric_id = int(security_id)
        except ValueError:
            continue
        SYMBOL_TOKEN[symbol] = numeric_id
        TOKEN_TO_SYMBOL[numeric_id] = symbol
    for sector, instrument in SECTOR_INDEX_INSTRUMENTS.items():
        try:
            numeric_id = int(instrument["security_id"])
        except (KeyError, ValueError):
            continue
        SYMBOL_TOKEN[sector] = numeric_id
        TOKEN_TO_SYMBOL[numeric_id] = sector
        DHAN_INSTRUMENTS.register_instrument(sector, str(numeric_id), MarketFeed.IDX)
    print(f"[DHAN INSTR] Loaded {len(SYMBOL_TOKEN)} NSE symbols")
    return bool(SYMBOL_TOKEN)


async def subscribe_dhan_sector_indices_for_user(user_id: int) -> None:
    if _is_test_mode():
        return
    for sector, instrument in SECTOR_INDEX_INSTRUMENTS.items():
        try:
            token = int(instrument["security_id"])
        except (KeyError, ValueError):
            continue
        SYMBOL_TOKEN[sector] = token
        TOKEN_TO_SYMBOL[token] = sector
        DHAN_INSTRUMENTS.register_instrument(sector, str(token), MarketFeed.IDX)
        SUB_TOKENS.add(token)
    if DHAN_FEED and DHAN_USER_ID == int(user_id):
        await DHAN_FEED.subscribe([str(item["security_id"]) for item in SECTOR_INDEX_INSTRUMENTS.values()])


async def load_sector_cache_for_user(user_id: int) -> Dict[str, Any]:
    app_store = await ensure_store_ready()
    cache = await app_store.load_sector_cache(int(user_id))
    if cache:
        eng = await ensure_engine(int(user_id))
        eng.load_sector_cache(cache)
    return cache


async def fetch_and_cache_dhan_sector_data(user_id: int) -> Dict[str, Any]:
    user_id = int(user_id)
    app_store = await ensure_store_ready()
    broker = await app_store.load_broker(user_id)
    if broker != "DHAN":
        return {"ok": False, "error": "DHAN_BROKER_REQUIRED"}
    creds = await app_store.load_dhan_credentials(user_id)
    client_id = str(creds.get("client_id") or "").strip()
    access_token = str(creds.get("access_token") or "").strip()
    if not client_id or not access_token:
        return {"ok": False, "error": "DHAN_CLIENT_ID_ACCESS_TOKEN_REQUIRED"}

    grouped: Dict[str, List[int]] = {}
    for item in SECTOR_INDEX_INSTRUMENTS.values():
        segment = str(item.get("exchange_segment") or "IDX_I")
        try:
            grouped.setdefault(segment, []).append(int(item["security_id"]))
        except (KeyError, ValueError):
            continue

    try:
        client = dhanhq(DhanContext(client_id, access_token))
        response = await asyncio.to_thread(client.ohlc_data, grouped)
    except Exception as exc:
        return {"ok": False, "error": f"DHAN_SECTOR_FETCH_FAILED:{exc}"}

    rows: List[Dict[str, Any]] = []
    ok_count = 0
    rank_ready_count = 0
    for sector, item in SECTOR_INDEX_INSTRUMENTS.items():
        security_id = str(item["security_id"])
        values = _sector_quote_values(response, security_id)
        ltp = float(values.get("ltp") or 0.0)
        prev_close = float(values.get("prev_close") or 0.0)
        pct = float(values.get("pct") or 0.0)
        ok = ltp > 0 and prev_close > 0
        rank_ready = ok and abs(ltp - prev_close) > 0.0001
        if ok:
            ok_count += 1
        if rank_ready:
            rank_ready_count += 1
        rows.append(
            {
                "name": sector,
                "security_id": security_id,
                "exchange_segment": item.get("exchange_segment", "IDX_I"),
                "instrument_type": item.get("instrument_type", "INDEX"),
                "ltp": ltp,
                "prev_close": prev_close,
                "pct": pct,
                "ok": ok,
                "rank_ready": rank_ready,
            }
        )

    rows.sort(key=lambda row: float(row.get("pct") or 0.0), reverse=True)
    payload = {
        "ok": ok_count > 0,
        "source": "DHAN_OHLC",
        "trading_day": now_ist_date(),
        "cached_at": now_ist().isoformat(),
        "ok_count": ok_count,
        "rank_ready_count": rank_ready_count,
        "rank_ready": rank_ready_count > 0,
        "total": len(rows),
        "sectors": rows,
    }
    await app_store.save_sector_cache(user_id, payload)
    eng = await ensure_engine(user_id)
    eng.load_sector_cache(payload)
    await subscribe_dhan_sector_indices_for_user(user_id)
    return payload


async def _ensure_token_map_ready(user_id: int) -> None:
    """
    Ensures SYMBOL_TOKEN is available.
    If webhook comes early, we build map in background and then subscribe pending symbols.
    """
    user_id = int(user_id)

    if SYMBOL_TOKEN:
        # already ready
        return

    async with INSTR_LOCK:
        # double-check after acquiring lock
        if SYMBOL_TOKEN:
            return
        ok = await is_session_valid(user_id)
        if not ok:
            return
        broker = await store.load_broker(user_id)
        built = (
            await build_symbol_token_map_from_dhan(user_id)
            if broker == "DHAN"
            else await build_symbol_token_map_from_kite(user_id)
        )
        if not built:
            return
    # after map is ready, subscribe pending symbols
    pending = list(PENDING_SYMBOLS.get(user_id, set()))
    if pending:
        await subscribe_symbols_for_user(user_id, pending)
        PENDING_SYMBOLS[user_id] = set()
        # 🔥 FIX: resubscribe tokens if ticker is already running
    if KT and KT_CONNECTED and SUB_TOKENS:
        try:
            KT.subscribe(list(SUB_TOKENS))
            KT.set_mode(KT.MODE_FULL, list(SUB_TOKENS))
            print("[KT] re-subscribed after token map ready:", len(SUB_TOKENS))
        except Exception as e:
            print("[KT] re-subscribe failed:", e)



# -----------------------------
# Subscriptions
# -----------------------------
async def subscribe_symbols_for_user(user_id: int, symbols: List[str]) -> None:
    """
    Adds tokens to SUB_TOKENS and subscribes if KiteTicker is running.

    Key behaviors:
    - If token map is not ready, queue symbols and build map in background.
    - Uses MODE_FULL to receive OHLC (close/high/low) and quantities.
    """
    if _is_test_mode():
        return
    user_id = int(user_id)
    if not symbols:
        return

    # Normalize symbols up-front
    norm_syms: List[str] = []
    for s in symbols:
        sym = _sym_safe(s)
        if sym:
            norm_syms.append(sym)

    if not norm_syms:
        return

    # If token map is not ready, queue and kick off background build (non-blocking).
    if not SYMBOL_TOKEN:
        PENDING_SYMBOLS.setdefault(user_id, set()).update(norm_syms)
        asyncio.create_task(_ensure_token_map_ready(user_id))
        # Do not block webhook here.
        return

    changed = False
    missing_syms: List[str] = []
    for sym in norm_syms:
        tok = SYMBOL_TOKEN.get(sym)
        if not tok and "-" not in sym:
            alt = f"{sym}-EQ"
            tok = SYMBOL_TOKEN.get(alt)
            if tok:
                sym = alt
        if not tok:
            print(f"[TOKEN MISSING] {sym}  (common cause: symbol format like SBIN-EQ)")
            missing_syms.append(sym)
            continue

        if tok not in SUB_TOKENS:
            SUB_TOKENS.add(tok)
            changed = True
        else:
             # Already subscribed
             pass
             
        # Validation Log
        if tok:
             # print(f"[SUB_CHECK] ✅ {sym} -> {tok}")
             pass
    
    if missing_syms:
        print(f"⚠️ [SUB_WARNING] Could not resolve tokens for: {missing_syms}. (Total Map: {len(SYMBOL_TOKEN)})")
        # Trigger reload if enough time has passed
        global _LAST_INSTR_RELOAD
        now = time.time()
        if now - _LAST_INSTR_RELOAD > _INSTR_RELOAD_INTERVAL:
            _LAST_INSTR_RELOAD = now
            print("[INSTR] 🔄 Triggering periodic instrument reload due to missing symbols...")
            if await store.load_broker(user_id) == "DHAN":
                asyncio.create_task(build_symbol_token_map_from_dhan(user_id))
            else:
                asyncio.create_task(build_symbol_token_map_from_kite(user_id))

    broker = await store.load_broker(user_id)
    if broker == "DHAN":
        if changed and DHAN_FEED and DHAN_USER_ID == user_id:
            await DHAN_FEED.subscribe([str(token) for token in SUB_TOKENS])
        return

    # Update live ticker subscriptions if running
    if changed:
        if KT and KT_CONNECTED:
            try:
                KT.subscribe(list(SUB_TOKENS))
                # FULL mode gives ohlc.close/high/low etc
                KT.set_mode(KT.MODE_FULL, list(SUB_TOKENS))
                print(f"[SUB] ✅ SUBSCRIBED to {len(SUB_TOKENS)} tokens. New: {len(norm_syms)} -> {[s for s in norm_syms if s not in missing_syms]}")
            except Exception as e:
                print(f"[SUB] ❌ subscribe failed: {e}")
        else:
             print(f"[SUB] ⚠️ Added to set, but KT not connected/ready. Count={len(SUB_TOKENS)}. KT={KT is not None} CONN={KT_CONNECTED}")


# -----------------------------
# KiteTicker start / restart
# -----------------------------
async def _stop_kite_ticker() -> None:
    global KT, KT_CONNECTED, KT_TASK, KT_USER_ID, KT_ACCESS_TOKEN
    old_user_id = KT_USER_ID
    try:
        if KT is not None:
            try:
                KT.close()  # KiteTicker supports close()
            except Exception:
                pass
    finally:
        if old_user_id is not None:
            _save_feed_health_nowait(int(old_user_id), "ZERODHA", False, "stopped")
        KT = None
        KT_CONNECTED = False
        KT_TASK = None
        KT_USER_ID = None
        KT_ACCESS_TOKEN = ""


async def _stop_dhan_feed() -> None:
    global DHAN_FEED, DHAN_CONNECTED, DHAN_USER_ID, DHAN_ACCESS_TOKEN
    old_user_id = DHAN_USER_ID
    if DHAN_FEED:
        try:
            await DHAN_FEED.stop()
        except Exception:
            pass
    if old_user_id is not None:
        _save_feed_health_nowait(int(old_user_id), "DHAN", False, "stopped")
    DHAN_FEED = None
    DHAN_CONNECTED = False
    DHAN_USER_ID = None
    DHAN_ACCESS_TOKEN = ""


async def start_dhan_feed(user_id: int) -> None:
    global DHAN_FEED, DHAN_CONNECTED, DHAN_USER_ID, DHAN_ACCESS_TOKEN
    if _is_test_mode():
        return
    creds = await store.load_dhan_credentials(user_id)
    client_id = str(creds.get("client_id") or "").strip()
    access_token = str(creds.get("access_token") or "").strip()
    if not client_id or not access_token:
        return
    await _stop_kite_ticker()
    if DHAN_FEED and DHAN_USER_ID == user_id and DHAN_ACCESS_TOKEN == access_token:
        return
    await _stop_dhan_feed()

    def on_state(connected: bool) -> None:
        global DHAN_CONNECTED
        DHAN_CONNECTED = connected
        _save_feed_health_nowait(user_id, "DHAN", connected, "websocket_state")

    def on_tick(packet: Dict[str, Any]) -> None:
        loop = APP_LOOP
        if loop is None:
            return

        def pick(*keys: str, default: Any = 0) -> Any:
            for key in keys:
                value = packet.get(key)
                if value not in (None, ""):
                    return value
            return default

        async def handle() -> None:
            try:
                security_id = int(pick("security_id", "securityId", "SecurityId", "SECURITY_ID", default=0) or 0)
                symbol = TOKEN_TO_SYMBOL.get(security_id) or DHAN_INSTRUMENTS.symbol(security_id)
                if not symbol:
                    return
                ltp = float(pick("LTP", "ltp", "last_price", "lastPrice", "last_traded_price", default=0.0) or 0.0)
                close = float(pick("close", "Close", "prev_close", "previous_close", default=0.0) or 0.0)
                high = float(pick("high", "High", "day_high", default=ltp) or ltp)
                low = float(pick("low", "Low", "day_low", default=ltp) or ltp)
                tbq = float(pick("total_buy_quantity", "totalBuyQuantity", default=0.0) or 0.0)
                tsq = float(pick("total_sell_quantity", "totalSellQuantity", default=0.0) or 0.0)
                if ltp <= 0:
                    return
                await store.save_broker_feed_health(user_id, "DHAN", True, ttl_sec=15, detail="tick")
                await store.save_latest_tick(
                    user_id,
                    symbol,
                    {
                        "broker": "DHAN",
                        "ltp": ltp,
                        "close": close,
                        "high": high,
                        "low": low,
                        "tbq": tbq,
                        "tsq": tsq,
                    },
                    ttl_sec=30,
                )
                eng = await ensure_engine(user_id)
                pos = await eng.on_tick(symbol, ltp, close, high, low, tbq, tsq)
                ws_mgr.broadcast_nowait(
                    user_id,
                    {
                        "type": "tick",
                        "symbol": symbol,
                        "ltp": ltp,
                        "close": close,
                        "high": high,
                        "low": low,
                        "tbq": tbq,
                        "tsq": tsq,
                    },
                )
                if pos:
                    pos_symbol = _sym_safe(pos.symbol or symbol)
                    await store.upsert_position(user_id, pos_symbol, pos.to_public())
                    ws_mgr.broadcast_nowait(user_id, {"type": "pos", "position": pos.to_public()})
            except Exception as exc:
                print("[DHAN] tick handle error:", exc)

        asyncio.run_coroutine_threadsafe(handle(), loop)

    def on_order_update(message: Dict[str, Any]) -> None:
        loop = APP_LOOP
        if loop is None:
            return
        raw = message.get("Data") if isinstance(message, dict) else {}
        raw = raw if isinstance(raw, dict) else {}
        normalized = {
            "order_id": raw.get("orderNo") or raw.get("orderId"),
            "status": raw.get("status") or raw.get("orderStatus"),
            "tradingsymbol": raw.get("tradingSymbol") or raw.get("symbol"),
            "average_price": raw.get("avgTradedPrice") or raw.get("averagePrice"),
            "filledQuantity": raw.get("filledQuantity") or raw.get("tradedQuantity") or raw.get("tradedQty"),
            "remainingQuantity": raw.get("remainingQuantity") or raw.get("pendingQuantity"),
            "quantity": raw.get("quantity") or raw.get("orderQuantity"),
        }

        async def handle() -> None:
            eng = await ensure_engine(user_id)
            await eng.on_order_update(normalized)

        asyncio.run_coroutine_threadsafe(handle(), loop)

    DHAN_FEED = DhanFeedService(
        user_id=user_id,
        client_id=client_id,
        access_token=access_token,
        on_tick=on_tick,
        on_order_update=on_order_update,
        on_state=on_state,
    )
    DHAN_USER_ID = user_id
    DHAN_ACCESS_TOKEN = access_token
    await DHAN_FEED.start([str(token) for token in SUB_TOKENS])


async def restart_selected_feed(user_id: int) -> None:
    broker = await store.load_broker(user_id)
    eng = await ensure_engine(user_id)
    await eng.configure_broker()
    if broker == "DHAN":
        await _stop_dhan_feed()
        if not SYMBOL_TOKEN:
            await build_symbol_token_map_from_dhan(user_id)
        await subscribe_dhan_sector_indices_for_user(user_id)
        await start_dhan_feed(user_id)
    else:
        await _stop_kite_ticker()
        await start_kite_ticker(user_id)


async def start_kite_ticker(user_id: int) -> None:
    """
    Starts a single KiteTicker (threaded=True) and routes ticks back into FastAPI loop.
    Uses MODE_FULL for OHLC + quantities.
    """
    global KT, KT_TASK, KT_CONNECTED, KT_USER_ID, KT_ACCESS_TOKEN

    if _is_test_mode():
        return

    user_id = int(user_id)

    async with KT_LOCK:
        creds = await store.load_credentials(user_id)
        api_key = (creds.get("api_key") or "").strip()
        access_token = (await store.load_access_token(user_id)).strip()

        if not api_key or not access_token:
            print("[KT] missing api_key/access_token; ticker not started")
            return

        # If ticker already running but token changed, restart it
        if KT is not None:
            if (KT_USER_ID != user_id) or (KT_ACCESS_TOKEN != access_token):
                print("[KT] access token changed -> restarting ticker")
                await _stop_kite_ticker()
            else:
                return  # already running with same creds

        kt = KiteTicker(api_key, access_token)
        KT = kt
        KT_USER_ID = user_id
        KT_ACCESS_TOKEN = access_token

        def on_connect(ws, response):
            global KT_CONNECTED
            KT_CONNECTED = True
            _save_feed_health_nowait(user_id, "ZERODHA", True, "websocket_connect")
            try:
                if SUB_TOKENS:
                    ws.subscribe(list(SUB_TOKENS))
                    ws.set_mode(ws.MODE_FULL, list(SUB_TOKENS))
            except Exception as e:
                print("[KT] subscribe on_connect failed:", e)
            print("[KT] connected, subs:", len(SUB_TOKENS), "mode=FULL")

        def on_close(ws, code, reason):
            global KT_CONNECTED
            KT_CONNECTED = False
            _save_feed_health_nowait(user_id, "ZERODHA", False, f"closed:{code}:{reason}")
            print("[KT] closed", code, reason)

        def on_error(ws, code, reason):
            print("[KT] error", code, reason)

        def on_ticks(ws, ticks):
            if ticks:
                 # Debug: print first few tokens to verify we get data
                 sample = [t.get('instrument_token') for t in ticks[:3]]
                 # print(f"[KT] TICKS RECEIVED: {len(ticks)} sample={sample}")

            loop = APP_LOOP
            if loop is None:
                return

            async def _handle():
                eng = await ensure_engine(user_id)

                for t in ticks or []:

                    try:
                        tok = int(t.get("instrument_token", 0))
                        sym = TOKEN_TO_SYMBOL.get(tok)
                        if not sym:
                            continue

                        ltp = float(t.get("last_price") or 0.0)
                        if ltp <= 0:
                            continue

                        ohlc = t.get("ohlc") or {}
                        close = float(ohlc.get("close") or 0.0)
                        high = float(ohlc.get("high") or ltp)
                        low = float(ohlc.get("low") or ltp)

                        tbq = float(t.get("buy_quantity") or 0.0)
                        tsq = float(t.get("sell_quantity") or 0.0)
                        await store.save_broker_feed_health(user_id, "ZERODHA", True, ttl_sec=15, detail="tick")
                        await store.save_latest_tick(
                            user_id,
                            sym,
                            {
                                "broker": "ZERODHA",
                                "ltp": ltp,
                                "close": close,
                                "high": high,
                                "low": low,
                                "tbq": tbq,
                                "tsq": tsq,
                            },
                            ttl_sec=30,
                        )
                        # ✅ PROPER PER-STOCK LOG
                        # Feed engine with proper OHLC (important for sector ranking)
                        pos = await eng.on_tick(sym, ltp, close, high, low, tbq, tsq)

                        # UI tick push (non-blocking)
                        ws_mgr.broadcast_nowait(
                            user_id,
                            {
                                "type": "tick",
                                "symbol": sym,
                                "ltp": ltp,
                                "close": close,
                                "high": high,
                                "low": low,
                                "tbq": tbq,
                                "tsq": tsq,
                            },
                        )

                        # Throttle Redis writes for positions
                        if pos:
                            pos_symbol = _sym_safe(pos.symbol or sym)
                            key = (user_id, pos_symbol)
                            now = time.time()
                            last = _LAST_POS_SAVE.get(key, 0.0)
                            if now - last >= _POS_SAVE_THROTTLE_SEC:
                                _LAST_POS_SAVE[key] = now
                                asyncio.create_task(store.upsert_position(user_id, pos_symbol, pos.to_public()))
                                ws_mgr.broadcast_nowait(user_id, {"type": "pos", "position": pos.to_public()})

                    except Exception as e:
                        print("[KT] tick handle error:", e)

            asyncio.run_coroutine_threadsafe(_handle(), loop)

        kt.on_connect = on_connect
        kt.on_close = on_close
        kt.on_error = on_error
        kt.on_ticks = on_ticks

        def on_order_update(ws, data):
            loop = APP_LOOP
            if loop is None:
                return

            async def _handle_ou():
                try:
                    eng = await ensure_engine(user_id)
                    await eng.on_order_update(data)  # <-- add this method in TradeEngine
                except Exception as e:
                    print("[KT] order_update handle error:", e)

            asyncio.run_coroutine_threadsafe(_handle_ou(), loop)

        kt.on_order_update = on_order_update
        def _run():
            try:
                kt.connect(threaded=True)
            except Exception as e:
                print("[KT] connect thread error:", e)

        loop = asyncio.get_running_loop()
        KT_TASK = loop.run_in_executor(None, _run)
        print("[KT] connect thread started")


# -----------------------------
# Auto Square Off Scheduler
# -----------------------------
async def schedule_auto_squareoff():
    """
    Runs every 30s. Checks if time >= 15:15 IST.
    If yes, and enabled, and not run yet today -> triggers exit_all.
    """
    while True:
        try:
            await asyncio.sleep(20) # check freq
            
            # Simple IST check
            tz = pytz.timezone("Asia/Kolkata")
            now = datetime.datetime.now(tz)
            
            # Target: 15:20 (3:20 PM)
            if now.hour == 15 and now.minute >= 20:
                # Check all users (currently only 1 supported primarily, but loop capable)
                user_ids = [1] 
                
                for uid in user_ids:
                    if await store.is_auto_sq_off_enabled(uid):
                        if not await store.has_auto_sq_off_run(uid):
                            print(f"⏰ [AUTO_SQ_OFF] Triggering for user={uid} at {now}")
                            eng = await ensure_engine(uid)
                            # Passing reason AUTO_SQ_OFF_320 to differentiate
                            cnt = await eng.exit_all_open_positions(reason="AUTO_SQ_OFF_320", products={"MIS"})
                            await store.mark_auto_sq_off_run(uid)
                            
                            # Notify UI
                            ws_mgr.broadcast_nowait(uid, {
                                "type": "toast", 
                                "text": f"⏰ Auto Square Off Triggered ({cnt} positions)",
                                "error": False
                            })
        except Exception as e:
            print("[SCHED] Auto sq off error:", e)
            await asyncio.sleep(10)


# -----------------------------
# Startup
# -----------------------------
@app.on_event("startup")
async def startup():
    global APP_LOOP, encryption_manager, store, auth_service, SERVICE_RUNTIME, DAILY_DASHBOARD_CLEANUP_TASK
    APP_LOOP = asyncio.get_running_loop()
    ws_mgr.set_loop(APP_LOOP)

    if _is_test_mode():
        from .memory_store import InMemoryStore

        store = InMemoryStore()
        auth_service = AuthService(store)
        SERVICE_RUNTIME = ServiceRuntime(
            store_provider=lambda: store,
            ws_manager=ws_mgr,
            ensure_engine=lambda uid: ensure_engine(uid),
            subscribe_symbols=lambda uid, symbols: subscribe_symbols_for_user(uid, symbols),
            start_feed=lambda uid: restart_selected_feed(uid),
            stop_dhan_feed=lambda: _stop_dhan_feed(),
            stop_kite_feed=lambda: _stop_kite_ticker(),
        )
        return

    # Initialize encryption
    if ENCRYPTION_AVAILABLE:
        try:
            encryption_manager = init_encryption()
        except Exception as e:
            print(f"⚠️  Encryption initialization failed: {e}")
            encryption_manager = None
    
    # Initialize Redis store with encryption
    store = RedisStore(REDIS_URL, encryption_manager)
    if not await store.ping():
        raise RuntimeError(f"Redis is not reachable at {REDIS_URL}")
    await store.init_scripts()
    
    # Initialize auth service
    auth_service = AuthService(store)
    print("✅ Authentication service initialized")
    SERVICE_RUNTIME = ServiceRuntime(
        store_provider=lambda: store,
        ws_manager=ws_mgr,
        ensure_engine=lambda uid: ensure_engine(uid),
        subscribe_symbols=lambda uid, symbols: subscribe_symbols_for_user(uid, symbols),
        start_feed=lambda uid: restart_selected_feed(uid),
        stop_dhan_feed=lambda: _stop_dhan_feed(),
        stop_kite_feed=lambda: _stop_kite_ticker(),
    )
    await SERVICE_RUNTIME.start()
    print("Modular service runtime started")
    
    # Start Scheduler
    asyncio.create_task(schedule_auto_squareoff())
    if DAILY_DASHBOARD_CLEANUP_TASK is None or DAILY_DASHBOARD_CLEANUP_TASK.done():
        DAILY_DASHBOARD_CLEANUP_TASK = asyncio.create_task(schedule_daily_dashboard_cleanup())

    # Auto-start for all users found in Redis
    try:
        all_uids = await store.list_all_user_ids()
        print(f"🔄 [STARTUP] Found {len(all_uids)} users. Rehydrating...")

        for uid in all_uids:
            try:
                # ✅ Auto-enable Auto Square Off if not set (Default: ON)
                if not await store.is_auto_sq_off_enabled(uid):
                    await store.set_auto_sq_off_enabled(uid, True)

                broker = await store.load_broker(uid)
                ok = await is_session_valid(uid)
                if ok:
                    print(f"🚀 [STARTUP] Re-connecting User {uid}...")
                    async with INSTR_LOCK:
                        # Build per-user symbol token map if needed
                        # (Note: SYMBOL_TOKEN is global, but let's ensure it's loaded)
                        if not SYMBOL_TOKEN:
                            if broker == "DHAN":
                                await build_symbol_token_map_from_dhan(uid)
                            else:
                                await build_symbol_token_map_from_kite(uid)

                    base_symbols = list(STOCK_INDEX_MAPPING.keys())
                    await subscribe_symbols_for_user(uid, base_symbols)
                    if broker == "DHAN":
                        await subscribe_dhan_sector_indices_for_user(uid)
                        await start_dhan_feed(uid)
                    else:
                        await start_kite_ticker(uid)

                    eng = await ensure_engine(uid)
                    await eng.configure_broker()
                    print(f"✅ [STARTUP] User {uid} Rehydrated")
                else:
                    print(f"⚠️ [STARTUP] Skipping User {uid} (Session invalid/expired)")
            except Exception as ue:
                print(f"❌ [STARTUP] Failed to rehydrate User {uid}: {ue}")

    except Exception as e:
        print("[startup] user listing/rehydration failed:", e)


@app.on_event("shutdown")
async def shutdown() -> None:
    global SERVICE_RUNTIME, DAILY_DASHBOARD_CLEANUP_TASK
    if DAILY_DASHBOARD_CLEANUP_TASK is not None:
        DAILY_DASHBOARD_CLEANUP_TASK.cancel()
        try:
            await DAILY_DASHBOARD_CLEANUP_TASK
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        DAILY_DASHBOARD_CLEANUP_TASK = None
    if SERVICE_RUNTIME is not None:
        try:
            await SERVICE_RUNTIME.stop()
        except Exception:
            pass
        SERVICE_RUNTIME = None
    await _stop_kite_ticker()
    await _stop_dhan_feed()
    await ws_mgr.close_everyone()
    for engine in list(ENGINE.values()):
        try:
            await engine.close()
        except Exception:
            pass
    ENGINE.clear()
    if store is not None:
        try:
            await store.close()
        except Exception:
            pass



# -----------------------------
# Authentication Endpoints
# -----------------------------
def _admin_auth_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Admin Login</title>
  <style>
    :root { color-scheme: dark; --bg:#102324; --panel:#142c2d; --line:#2a4b4c; --text:#f2fbfb; --muted:#9ab7b8; --accent:#35d0a4; --bad:#ff7373; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg); color:var(--text); font-family: Inter, system-ui, -apple-system, Segoe UI, sans-serif; padding:20px; }
    main { width:min(440px, 100%); background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:24px; box-shadow:0 18px 50px rgba(0,0,0,.28); }
    h1 { margin:0 0 6px; font-size:24px; line-height:1.15; }
    p { color:var(--muted); margin:0 0 18px; line-height:1.5; }
    label { display:block; margin:14px 0 6px; color:var(--muted); font-size:13px; }
    input { width:100%; border:1px solid var(--line); background:#071819; color:var(--text); border-radius:6px; padding:12px; font-size:16px; outline:none; }
    input:focus { border-color:var(--accent); }
    button { width:100%; margin-top:18px; border:0; background:var(--accent); color:#06201b; border-radius:6px; padding:12px 14px; font-weight:800; cursor:pointer; }
    button:disabled { opacity:.55; cursor:not-allowed; }
    .hidden { display:none; }
    .msg { min-height:22px; margin-top:14px; color:var(--bad); font-size:14px; }
    .ok { color:var(--accent); }
    img { width:220px; height:220px; display:block; margin:14px auto; background:white; border-radius:6px; padding:8px; }
    code { display:block; overflow-wrap:anywhere; background:#071819; border:1px solid var(--line); border-radius:6px; padding:10px; color:var(--accent); }
  </style>
</head>
<body>
  <main>
    <section id="loading"><h1>Checking Access</h1><p>Please wait.</p></section>

    <section id="create" class="hidden">
      <h1>Create Admin</h1>
      <p>This runs once. After admin creation, setup Google Authenticator.</p>
      <label>Email</label><input id="create_email" type="email" autocomplete="username">
      <label>Password</label><input id="create_password" type="password" autocomplete="new-password">
      <label>Confirm Password</label><input id="create_confirm" type="password" autocomplete="new-password">
      <button onclick="createAdmin()">Create Admin</button>
    </section>

    <section id="totp" class="hidden">
      <h1>Setup Authenticator</h1>
      <p>Enter admin password, scan the QR code, then verify the 6-digit code.</p>
      <label>Email</label><input id="totp_email" type="email" autocomplete="username">
      <label>Password</label><input id="totp_password" type="password" autocomplete="current-password">
      <button onclick="startTotp()">Show QR Code</button>
      <div id="qrBox" class="hidden">
        <img id="qr" alt="Authenticator QR code">
        <label>Manual Key</label><code id="secret"></code>
        <label>6-digit Code</label><input id="totp_code" inputmode="numeric" autocomplete="one-time-code">
        <button onclick="verifyTotp()">Verify & Continue</button>
      </div>
    </section>

    <section id="login" class="hidden">
      <h1>Admin Login</h1>
      <p>Only the configured admin can access this application.</p>
      <label>Email</label><input id="login_email" type="email" autocomplete="username">
      <label>Password</label><input id="login_password" type="password" autocomplete="current-password">
      <label>Authenticator Code</label><input id="login_code" inputmode="numeric" autocomplete="one-time-code">
      <button onclick="login()">Login</button>
    </section>

    <div id="msg" class="msg"></div>
  </main>
  <script>
    let pendingSecret = "";
    const $ = (id) => document.getElementById(id);
    function show(id) {
      ["loading", "create", "totp", "login"].forEach(x => $(x).classList.toggle("hidden", x !== id));
      $("msg").textContent = "";
      $("msg").className = "msg";
    }
    async function api(url, payload) {
      const resp = await fetch(url, {
        method: payload ? "POST" : "GET",
        headers: payload ? {"Content-Type": "application/json"} : {},
        body: payload ? JSON.stringify(payload) : undefined,
      });
      const text = await resp.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; } catch { data = {ok:false, error:text || "INVALID_RESPONSE"}; }
      if (!resp.ok || data.ok === false) throw new Error(data.error || data.detail || "REQUEST_FAILED");
      return data;
    }
    function message(text, ok=false) { $("msg").textContent = text; $("msg").className = ok ? "msg ok" : "msg"; }
    async function refreshState() {
      try {
        const state = await api("/auth/state");
        if (state.mode === "AUTHENTICATED") location.href = "/dashboard";
        else if (state.mode === "CREATE_ADMIN") show("create");
        else if (state.mode === "SETUP_TOTP") show("totp");
        else show("login");
      } catch (e) { show("login"); message(e.message); }
    }
    async function createAdmin() {
      try {
        const email = $("create_email").value.trim();
        const password = $("create_password").value;
        await api("/auth/admin/setup", {email, password, confirm_password: $("create_confirm").value});
        $("totp_email").value = email;
        $("totp_password").value = password;
        show("totp");
        message("Admin created. Setup authenticator now.", true);
      } catch (e) { message(e.message); }
    }
    async function startTotp() {
      try {
        const data = await api("/auth/totp/setup", {email: $("totp_email").value.trim(), password: $("totp_password").value});
        pendingSecret = data.secret;
        if (data.qr_data_url) {
          $("qr").src = data.qr_data_url;
          $("qr").classList.remove("hidden");
        } else {
          $("qr").removeAttribute("src");
          $("qr").classList.add("hidden");
        }
        $("secret").textContent = data.secret;
        $("qrBox").classList.remove("hidden");
        message(data.qr_data_url ? "Scan QR and enter code." : "QR unavailable. Enter the manual key in Authenticator.", true);
      } catch (e) { message(e.message); }
    }
    async function verifyTotp() {
      try {
        await api("/auth/totp/verify", {email: $("totp_email").value.trim(), password: $("totp_password").value, code: $("totp_code").value, secret: pendingSecret});
        location.href = "/dashboard";
      } catch (e) { message(e.message); }
    }
    async function login() {
      try {
        await api("/auth/login", {email: $("login_email").value.trim(), password: $("login_password").value, code: $("login_code").value});
        location.href = "/dashboard";
      } catch (e) { message(e.message); }
    }
    refreshState();
  </script>
</body>
</html>"""


@app.get("/auth", response_class=HTMLResponse)
async def admin_auth_page() -> HTMLResponse:
    if not _admin_auth_enabled():
        return HTMLResponse("<meta http-equiv='refresh' content='0;url=/dashboard'>")
    return HTMLResponse(_admin_auth_html())


@app.get("/auth/state")
async def admin_auth_state(request: Request) -> Dict[str, Any]:
    if not _admin_auth_enabled():
        return {"ok": True, "mode": "AUTHENTICATED", "auth_enabled": False}
    admin = await _load_admin()
    if not admin:
        return {"ok": True, "mode": "CREATE_ADMIN", "auth_enabled": True}
    if await _admin_session_from_request(request):
        return {"ok": True, "mode": "AUTHENTICATED", "auth_enabled": True}
    if not await _admin_totp_is_set():
        return {"ok": True, "mode": "SETUP_TOTP", "auth_enabled": True}
    return {"ok": True, "mode": "LOGIN", "auth_enabled": True}


@app.post("/auth/admin/setup")
async def admin_setup(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not _admin_auth_enabled():
        return {"ok": False, "error": "ADMIN_AUTH_DISABLED"}
    if await _load_admin():
        return {"ok": False, "error": "ADMIN_ALREADY_CONFIGURED"}
    email = _normalise_admin_email(str(payload.get("email") or ""))
    password = str(payload.get("password") or "")
    confirm = str(payload.get("confirm_password") or password)
    if ADMIN_EMAIL and email != ADMIN_EMAIL:
        return {"ok": False, "error": "ADMIN_EMAIL_NOT_ALLOWED"}
    if not _valid_email(email):
        return {"ok": False, "error": "INVALID_EMAIL"}
    if len(password) < PASSWORD_MIN_LEN:
        return {"ok": False, "error": f"PASSWORD_MIN_{PASSWORD_MIN_LEN}"}
    if password != confirm:
        return {"ok": False, "error": "PASSWORD_CONFIRM_MISMATCH"}
    fn = getattr(store, "save_admin_auth", None)
    if not callable(fn):
        return {"ok": False, "error": "ADMIN_AUTH_STORE_UNAVAILABLE"}
    await fn(email, _hash_password(password))
    log.warning("ADMIN_CREATED | email=%s", email)
    return {"ok": True, "mode": "SETUP_TOTP"}


@app.post("/auth/totp/setup")
async def admin_totp_setup(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not _admin_auth_enabled():
        return {"ok": False, "error": "ADMIN_AUTH_DISABLED"}
    admin = await _load_admin()
    if not admin:
        return {"ok": False, "error": "ADMIN_NOT_CONFIGURED"}
    if await _admin_totp_is_set():
        return {"ok": False, "error": "TOTP_ALREADY_CONFIGURED"}
    email = _normalise_admin_email(str(payload.get("email") or ""))
    password = str(payload.get("password") or "")
    if not await _verify_admin_password(email, password):
        return {"ok": False, "error": "INVALID_ADMIN_CREDENTIALS"}
    import pyotp

    secret = pyotp.random_base32()
    uri = _totp_uri(email, secret)
    qr_data_url = _totp_qr_data_url(uri)
    return {
        "ok": True,
        "secret": secret,
        "otpauth_uri": uri,
        "qr_data_url": qr_data_url,
        "qr_available": bool(qr_data_url),
    }


@app.post("/auth/totp/verify")
async def admin_totp_verify(payload: Dict[str, Any]) -> JSONResponse:
    if not _admin_auth_enabled():
        return JSONResponse({"ok": False, "error": "ADMIN_AUTH_DISABLED"}, status_code=400)
    if await _admin_totp_is_set():
        return JSONResponse({"ok": False, "error": "TOTP_ALREADY_CONFIGURED"}, status_code=400)
    email = _normalise_admin_email(str(payload.get("email") or ""))
    password = str(payload.get("password") or "")
    secret = str(payload.get("secret") or "").strip().replace(" ", "")
    code = str(payload.get("code") or payload.get("totp") or "")
    if not await _verify_admin_password(email, password):
        return JSONResponse({"ok": False, "error": "INVALID_ADMIN_CREDENTIALS"}, status_code=401)
    if not secret or not _verify_totp(secret, code):
        return JSONResponse({"ok": False, "error": "INVALID_TOTP"}, status_code=401)
    fn = getattr(store, "save_admin_totp_secret", None)
    if not callable(fn):
        return JSONResponse({"ok": False, "error": "ADMIN_TOTP_STORE_UNAVAILABLE"}, status_code=500)
    await fn(secret)
    token = await _create_admin_session(email)
    response = JSONResponse({"ok": True, "mode": "AUTHENTICATED"})
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        max_age=ADMIN_SESSION_TTL_SEC,
        httponly=True,
        secure=_admin_cookie_secure(),
        samesite="lax",
        path="/",
    )
    log.warning("ADMIN_TOTP_ENABLED | email=%s", email)
    return response


@app.post("/auth/login")
async def admin_login(payload: Dict[str, Any]) -> JSONResponse:
    if not _admin_auth_enabled():
        return JSONResponse({"ok": False, "error": "ADMIN_AUTH_DISABLED"}, status_code=400)
    email = _normalise_admin_email(str(payload.get("email") or ""))
    password = str(payload.get("password") or "")
    code = str(payload.get("code") or payload.get("totp") or "")
    if not await _admin_totp_is_set():
        return JSONResponse({"ok": False, "error": "TOTP_NOT_CONFIGURED"}, status_code=409)
    if not await _verify_admin_password(email, password):
        attempts = await store.record_admin_login_failure(email) if hasattr(store, "record_admin_login_failure") else 1
        status_code = 429 if attempts >= ADMIN_LOGIN_LOCK_AFTER else 401
        return JSONResponse({"ok": False, "error": "INVALID_ADMIN_CREDENTIALS"}, status_code=status_code)
    secret = await _load_admin_totp_secret()
    if not _verify_totp(secret, code):
        attempts = await store.record_admin_login_failure(email) if hasattr(store, "record_admin_login_failure") else 1
        status_code = 429 if attempts >= ADMIN_LOGIN_LOCK_AFTER else 401
        return JSONResponse({"ok": False, "error": "INVALID_TOTP"}, status_code=status_code)
    if hasattr(store, "clear_admin_login_failures"):
        await store.clear_admin_login_failures(email)
    token = await _create_admin_session(email)
    response = JSONResponse({"ok": True, "mode": "AUTHENTICATED"})
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        max_age=ADMIN_SESSION_TTL_SEC,
        httponly=True,
        secure=_admin_cookie_secure(),
        samesite="lax",
        path="/",
    )
    log.info("ADMIN_LOGIN_OK | email=%s", email)
    return response


@app.post("/auth/logout")
async def admin_logout(request: Request) -> JSONResponse:
    token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    if token and hasattr(store, "delete_admin_session"):
        await store.delete_admin_session(_hash_session_token(token))
    response = JSONResponse({"ok": True})
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    return response


@app.get("/", response_class=RedirectResponse)
@limiter.limit(RATE_LIMIT_LOGIN)
async def root(request: Request):
    """Redirect to dashboard; admin/TOTP middleware enforces access when enabled."""
    return RedirectResponse(url="/dashboard")


# -----------------------------
# Dashboard
# -----------------------------
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """
    Serve the main trading dashboard.
    Admin/TOTP access is enforced by middleware before this route is reached.
    Defaulting to User ID 1.
    """
    # Simply render for default user (Ashutosh)
    return _read_dashboard_template(user_id=1, username="Ashutosh")


# -----------------------------
# Credentials + Kite login
# -----------------------------
@app.post("/api/save-credentials")
async def save_credentials(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = int(payload.get("user_id", 1))
    api_key = str(payload.get("api_key", "")).strip()
    api_secret = str(payload.get("api_secret", "")).strip()
    if not api_key or not api_secret:
        return {"error": "API_KEY_SECRET_REQUIRED"}

    await store.save_credentials(user_id, api_key, api_secret)
    _SESSION_CACHE.pop(user_id, None)
    return {"ok": True}


@app.get("/api/broker-config")
async def broker_config(user_id: int = 1) -> Dict[str, Any]:
    uid = int(user_id)
    broker = await store.load_broker(uid)
    dhan_creds = await store.load_dhan_credentials(uid)
    dhan_api_creds = {}
    load_api_fn = getattr(store, "load_dhan_api_credentials", None)
    if callable(load_api_fn):
        dhan_api_creds = await load_api_fn(uid)
    dhan_client_id = str(dhan_creds.get("client_id") or dhan_api_creds.get("client_id") or "")
    auth_mode = str(dhan_creds.get("auth_mode") or ("API_KEY" if dhan_api_creds else "MANUAL") or "MANUAL").upper()
    return {
        "broker": broker,
        "dhan": {
            "auth_mode": auth_mode if auth_mode in {"MANUAL", "API_KEY"} else "MANUAL",
            "client_id": dhan_client_id,
            "has_access_token": bool(str(dhan_creds.get("access_token") or "").strip()),
            "token_expiry": str(dhan_creds.get("token_expiry") or ""),
            "has_api_credentials": bool(
                str(dhan_api_creds.get("api_key") or "").strip()
                and str(dhan_api_creds.get("api_secret") or "").strip()
            ),
            "redirect_url": f"{str(os.getenv('PUBLIC_BASE_URL') or '').rstrip('/')}/dhan/callback/{uid}"
            if os.getenv("PUBLIC_BASE_URL")
            else f"/dhan/callback/{uid}",
        },
    }


@app.get("/api/webhook-url")
async def webhook_url(request: Request, user_id: int = 1) -> Dict[str, Any]:
    base_url = _public_base_url(request)
    url = f"{base_url}/webhook/chartink?user_id={int(user_id)}"
    if WEBHOOK_SECRET:
        url = f"{url}&secret={WEBHOOK_SECRET}"
    return {
        "ok": True,
        "url": url,
        "secret_required": bool(WEBHOOK_SECRET),
    }


@app.post("/api/broker-config")
async def save_broker_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        user_id = int(payload.get("user_id", 1))
        broker = str(payload.get("broker") or "ZERODHA").strip().upper()
        if broker not in {"ZERODHA", "DHAN"}:
            return {"error": "UNSUPPORTED_BROKER"}

        if broker == "DHAN":
            auth_mode = str(payload.get("auth_mode") or payload.get("dhan_auth_mode") or "MANUAL").strip().upper()
            if auth_mode in {"API", "API_KEY_SECRET", "OAUTH"}:
                auth_mode = "API_KEY"
            if auth_mode not in {"MANUAL", "API_KEY"}:
                return {"error": "DHAN_AUTH_MODE_INVALID"}
            client_id = str(payload.get("client_id") or "").strip()
            if auth_mode == "API_KEY":
                api_key = str(payload.get("api_key") or payload.get("app_id") or "").strip()
                api_secret = str(payload.get("api_secret") or payload.get("app_secret") or "").strip()
                if not client_id or not api_key or not api_secret:
                    return {"error": "DHAN_CLIENT_ID_API_KEY_SECRET_REQUIRED"}
                save_api_fn = getattr(store, "save_dhan_api_credentials", None)
                if not callable(save_api_fn):
                    return {"error": "DHAN_API_KEY_AUTH_NOT_SUPPORTED"}
                await save_api_fn(user_id, client_id, api_key, api_secret)
                access_token = str(payload.get("access_token") or "").strip()
                token_expiry = str(payload.get("token_expiry") or payload.get("expiry_time") or "").strip()
                if access_token:
                    await store.save_dhan_credentials(
                        user_id,
                        client_id,
                        access_token,
                        token_expiry=token_expiry,
                        auth_mode="API_KEY",
                    )
                else:
                    await store.save_dhan_credentials(user_id, client_id, "", auth_mode="API_KEY")
            else:
                access_token = str(payload.get("access_token") or "").strip()
                if not client_id or not access_token:
                    return {"error": "DHAN_CLIENT_ID_ACCESS_TOKEN_REQUIRED"}
                if not _is_test_mode():
                    try:
                        response = await asyncio.to_thread(
                            dhanhq(DhanContext(client_id, access_token)).get_fund_limits
                        )
                        if not _dhan_response_ok(response):
                            return {"error": "DHAN_AUTHENTICATION_FAILED"}
                    except Exception as exc:
                        log.warning("Dhan authentication failed | user=%s err=%s", user_id, exc)
                        return {"error": "DHAN_AUTHENTICATION_FAILED", "detail": str(exc)}
                await store.save_dhan_credentials(user_id, client_id, access_token, auth_mode="MANUAL")

        await store.save_broker(user_id, broker)
        _SESSION_CACHE.pop(user_id, None)
        SYMBOL_TOKEN.clear()
        TOKEN_TO_SYMBOL.clear()
        SUB_TOKENS.clear()
        eng = await ensure_engine(user_id)
        await eng.configure_broker()

        warning = ""
        saved_dhan_creds = await store.load_dhan_credentials(user_id) if broker == "DHAN" else {}
        has_dhan_access_token = bool(str(saved_dhan_creds.get("access_token") or "").strip())
        if broker == "DHAN" and has_dhan_access_token:
            await _poke_market_feed_service(user_id, list(STOCK_INDEX_MAPPING.keys()), "dhan_broker_config_saved")
        if broker == "DHAN" and has_dhan_access_token and not _is_test_mode():
            try:
                await _stop_kite_ticker()
                await build_symbol_token_map_from_dhan(user_id)
                await subscribe_symbols_for_user(user_id, list(STOCK_INDEX_MAPPING.keys()))
                await subscribe_dhan_sector_indices_for_user(user_id)
                await start_dhan_feed(user_id)
            except Exception as exc:
                warning = "DHAN_FEED_START_FAILED"
                log.exception("Dhan broker saved but feed startup failed | user=%s", user_id)
        elif broker == "ZERODHA":
            try:
                await _stop_dhan_feed()
                await _poke_market_feed_service(user_id, list(STOCK_INDEX_MAPPING.keys()), "zerodha_broker_config_saved")
            except Exception:
                log.exception("Dhan feed stop failed while switching broker | user=%s", user_id)

        result: Dict[str, Any] = {"ok": True, "broker": broker}
        if warning:
            result["warning"] = warning
        return result
    except Exception as exc:
        log.exception("Broker config save failed")
        return {"error": "BROKER_CONFIG_SAVE_FAILED", "detail": str(exc)}


@app.post("/api/dhan/generate-consent")
async def dhan_generate_consent_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        user_id = int(payload.get("user_id", 1))
        client_id = str(payload.get("client_id") or "").strip()
        api_key = str(payload.get("api_key") or payload.get("app_id") or "").strip()
        api_secret = str(payload.get("api_secret") or payload.get("app_secret") or "").strip()
        if not client_id or not api_key or not api_secret:
            creds = await store.load_dhan_api_credentials(user_id)
            client_id = client_id or str(creds.get("client_id") or "").strip()
            api_key = api_key or str(creds.get("api_key") or "").strip()
            api_secret = api_secret or str(creds.get("api_secret") or "").strip()
        if not client_id or not api_key or not api_secret:
            return {"error": "DHAN_CLIENT_ID_API_KEY_SECRET_REQUIRED"}

        await store.save_dhan_api_credentials(user_id, client_id, api_key, api_secret)
        await store.save_broker(user_id, "DHAN")

        if _is_test_mode():
            consent = {
                "consentAppId": "test-consent-app-id",
                "consentAppStatus": "GENERATED",
                "login_url": _dhan_auth_login_url("test-consent-app-id"),
            }
        else:
            consent = await _dhan_generate_consent(client_id, api_key, api_secret)
        save_state_fn = getattr(store, "save_dhan_auth_state", None)
        if callable(save_state_fn):
            await save_state_fn(
                user_id,
                {
                    "client_id": client_id,
                    "consentAppId": consent["consentAppId"],
                    "created_at": now_ist().isoformat(),
                    "auth_mode": "API_KEY",
                },
            )
        return {
            "ok": True,
            "broker": "DHAN",
            "auth_mode": "API_KEY",
            "consentAppId": consent["consentAppId"],
            "consentAppStatus": consent.get("consentAppStatus", ""),
            "login_url": consent["login_url"],
        }
    except Exception as exc:
        log.exception("Dhan consent generation failed")
        return {"error": "DHAN_CONSENT_GENERATION_FAILED", "detail": str(exc)}


@app.get("/connect/dhan")
async def connect_dhan(user_id: int = 1):
    user_id = int(user_id)
    creds = await store.load_dhan_api_credentials(user_id)
    client_id = str(creds.get("client_id") or "").strip()
    api_key = str(creds.get("api_key") or "").strip()
    api_secret = str(creds.get("api_secret") or "").strip()
    if not client_id or not api_key or not api_secret:
        return RedirectResponse(url=f"/dashboard?user_id={user_id}&error=dhan_api_creds_missing")

    try:
        if _is_test_mode():
            consent = {
                "consentAppId": "test-consent-app-id",
                "login_url": _dhan_auth_login_url("test-consent-app-id"),
            }
        else:
            consent = await _dhan_generate_consent(client_id, api_key, api_secret)
        save_state_fn = getattr(store, "save_dhan_auth_state", None)
        if callable(save_state_fn):
            await save_state_fn(
                user_id,
                {
                    "client_id": client_id,
                    "consentAppId": consent["consentAppId"],
                    "created_at": now_ist().isoformat(),
                    "auth_mode": "API_KEY",
                },
            )
        return RedirectResponse(url=consent["login_url"])
    except Exception as exc:
        log.warning("Dhan connect failed | user=%s err=%s", user_id, exc)
        return RedirectResponse(url=f"/dashboard?user_id={user_id}&error=dhan_consent_failed")


@app.get("/dhan/callback/{user_id}", response_class=HTMLResponse)
@app.get("/dhan/callback", response_class=HTMLResponse)
async def dhan_callback(request: Request, user_id: int = 1):
    user_id = int(user_id or 1)
    token_id = str(request.query_params.get("tokenId") or request.query_params.get("token_id") or "").strip()
    if not token_id:
        return HTMLResponse(
            "<h2>Dhan authentication failed</h2><p>tokenId was missing in callback.</p>",
            status_code=400,
        )
    creds = await store.load_dhan_api_credentials(user_id)
    api_key = str(creds.get("api_key") or "").strip()
    api_secret = str(creds.get("api_secret") or "").strip()
    if not api_key or not api_secret:
        return HTMLResponse(
            "<h2>Dhan authentication failed</h2><p>Saved API key/secret not found. Save API credentials again.</p>",
            status_code=400,
        )
    try:
        token = await _dhan_consume_app_consent(token_id, api_key, api_secret)
        result = await _activate_dhan_token(
            user_id,
            token["client_id"],
            token["access_token"],
            token_expiry=token.get("expiry_time", ""),
            auth_mode="API_KEY",
        )
        warning = f"<p>Warning: {result.get('warning')}</p>" if result.get("warning") else ""
        expiry = token.get("expiry_time") or "not provided"
        return HTMLResponse(
            f"""
            <!doctype html>
            <html><head><meta charset="utf-8"><meta http-equiv="refresh" content="2;url=/dashboard?user_id={user_id}"></head>
            <body style="font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;padding:32px">
              <h2>Dhan authentication successful</h2>
              <p>Access token generated and saved for client <strong>{token['client_id']}</strong>.</p>
              <p>Expiry: <strong>{expiry}</strong></p>
              {warning}
              <p>Redirecting to dashboard...</p>
            </body></html>
            """,
            status_code=200,
        )
    except Exception as exc:
        log.exception("Dhan callback token consume failed | user=%s", user_id)
        return HTMLResponse(
            f"<h2>Dhan authentication failed</h2><p>{str(exc)}</p>",
            status_code=400,
        )


@app.post("/api/dhan/consume-token")
async def dhan_consume_token_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        user_id = int(payload.get("user_id", 1))
        token_id = str(payload.get("tokenId") or payload.get("token_id") or "").strip()
        if not token_id:
            return {"error": "DHAN_TOKEN_ID_REQUIRED"}
        creds = await store.load_dhan_api_credentials(user_id)
        api_key = str(creds.get("api_key") or "").strip()
        api_secret = str(creds.get("api_secret") or "").strip()
        if not api_key or not api_secret:
            return {"error": "DHAN_API_CREDENTIALS_MISSING"}
        if _is_test_mode():
            token = {
                "client_id": str(creds.get("client_id") or "test-client"),
                "access_token": "test-generated-access-token",
                "expiry_time": "2099-01-01T00:00:00",
            }
        else:
            token = await _dhan_consume_app_consent(token_id, api_key, api_secret)
        result = await _activate_dhan_token(
            user_id,
            token["client_id"],
            token["access_token"],
            token_expiry=token.get("expiry_time", ""),
            auth_mode="API_KEY",
        )
        result["expiry_time"] = token.get("expiry_time", "")
        return result
    except Exception as exc:
        log.exception("Dhan token consume API failed")
        return {"error": "DHAN_TOKEN_CONSUME_FAILED", "detail": str(exc)}


@app.get("/connect/zerodha")
async def connect_zerodha(user_id: int = 1):
    user_id = int(user_id)
    creds = await store.load_credentials(user_id)
    api_key = (creds.get("api_key") or "").strip()
    api_secret = (creds.get("api_secret") or "").strip()
    if not api_key or not api_secret:
        return RedirectResponse(url=f"/dashboard?user_id={user_id}&error=missing_creds")

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()
    return RedirectResponse(url=login_url)


@app.get("/zerodha/callback")
async def zerodha_callback(request: Request, user_id: Optional[int] = None):
    # 1) Try user_id from query params
    if user_id is None:
        try:
             uid_q = request.query_params.get("user_id")
             if uid_q: user_id = int(uid_q)
        except: pass
        
    # 2) Fallback to session cookie (crucial for preserving context after redirect)
    if user_id is None:
        token = request.cookies.get("session_token")
        if token and auth_service:
            session_data = await auth_service.verify_session(token)
            if session_data:
                user_id = session_data.get("user_id")
                
    # 3) Final fallback
    user_id = int(user_id or 1)

    creds = await store.load_credentials(user_id)
    api_key = (creds.get("api_key") or "").strip()
    api_secret = (creds.get("api_secret") or "").strip()
    if not api_key or not api_secret:
        return RedirectResponse(url=f"/dashboard?user_id={user_id}")

    request_token = request.query_params.get("request_token", "") or ""
    if not request_token.strip():
        return RedirectResponse(url=f"/dashboard?user_id={user_id}")

    kite = KiteConnect(api_key=api_key)
    data = kite.generate_session(request_token.strip(), api_secret=api_secret)
    access_token = str(data.get("access_token") or "").strip()

    await store.save_access_token(user_id, access_token)
    await store.save_broker(user_id, "ZERODHA")
    _SESSION_CACHE.pop(user_id, None)
    await _stop_dhan_feed()
    SYMBOL_TOKEN.clear()
    TOKEN_TO_SYMBOL.clear()
    SUB_TOKENS.clear()

    # Build instruments map
    async with INSTR_LOCK:
        await build_symbol_token_map_from_kite(user_id)

    # Subscribe base universe (for sector ranking)
    base_symbols = list(STOCK_INDEX_MAPPING.keys())
    await subscribe_symbols_for_user(user_id, base_symbols)

    # Subscribe any pending symbols that arrived via webhook earlier
    pending = list(PENDING_SYMBOLS.get(user_id, set()))
    if pending:
        await subscribe_symbols_for_user(user_id, pending)
        PENDING_SYMBOLS[user_id] = set()

    # Start / restart ticker
    await start_kite_ticker(user_id)
    await _poke_market_feed_service(user_id, list(STOCK_INDEX_MAPPING.keys()), "zerodha_callback")
    # Ensure engine has latest access token
    eng = await ensure_engine(user_id)
    await eng.configure_kite()

    return RedirectResponse(url=f"/dashboard?user_id={user_id}")


@app.get("/api/zerodha-status")
async def zerodha_status(user_id: int = 1):
    user_id = int(user_id)

    session_ok = await is_session_valid(user_id)
    kill = await store.is_kill(user_id)
    broker = await store.load_broker(user_id)

    if broker == "DHAN":
        shared_connected, feed_health = await _load_shared_feed_connected(user_id, "DHAN")
        ticker_connected = bool(DHAN_CONNECTED and DHAN_USER_ID == user_id) or shared_connected
        return {
            "connected": bool(session_ok),
            "ticker_connected": ticker_connected,
            "kill_switch": kill,
            "broker": broker,
            "feed_health": feed_health,
        }

    shared_connected, feed_health = await _load_shared_feed_connected(user_id, "ZERODHA")
    ticker_connected = bool(KT_CONNECTED and KT_USER_ID == user_id) or shared_connected

    # A valid auth session means Kite login succeeded, even if the background
    # ticker thread is still reconnecting. When that happens, try to self-heal.
    if session_ok and not ticker_connected and not _is_test_mode():
        try:
            eng = await ensure_engine(user_id)
            await eng.configure_kite()
            await _stop_kite_ticker()
            await start_kite_ticker(user_id)
        except Exception as e:
            print(f"[KT] status self-heal failed for user={user_id}: {e}")

        shared_connected, feed_health = await _load_shared_feed_connected(user_id, "ZERODHA")
        ticker_connected = bool(KT_CONNECTED and KT_USER_ID == user_id) or shared_connected

    return {
        "connected": bool(session_ok),
        "ticker_connected": ticker_connected,
        "kill_switch": kill,
        "feed_health": feed_health,
    }


@app.get("/api/broker-status")
async def broker_status(user_id: int = 1) -> Dict[str, Any]:
    user_id = int(user_id)
    broker = await store.load_broker(user_id)
    session_ok = await is_session_valid(user_id)
    kill = await store.is_kill(user_id)
    broker_key = "DHAN" if broker == "DHAN" else "ZERODHA"
    shared_connected, feed_health = await _load_shared_feed_connected(user_id, broker_key)
    ticker_connected = (
        bool(DHAN_CONNECTED and DHAN_USER_ID == user_id) or shared_connected
        if broker == "DHAN"
        else bool(KT_CONNECTED and KT_USER_ID == user_id) or shared_connected
    )
    return {
        "broker": broker,
        "connected": bool(session_ok),
        "ticker_connected": ticker_connected,
        "kill_switch": kill,
        "feed_health": feed_health,
    }


# -----------------------------
# Alert Config
# -----------------------------
@app.get("/api/alert-config")
async def list_alert_config(user_id: int = 1) -> Dict[str, Any]:
    user_id = int(user_id)
    cfg = await store.list_alert_configs(user_id)
    return {"configs": cfg}


@app.post("/api/alert-config")
async def save_alert_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = int(payload.get("user_id", 1))
    raw_name = payload.get("alert_name", "")
    if not raw_name or not str(raw_name).strip():
        return {"error": "ALERT_NAME_REQUIRED"}

    # Normalize key consistently
    alert_name = normalize_alert_name(raw_name)
    exit_alert_enabled = str(payload.get("exit_alert_enabled", "false")).lower() in {"1", "true", "yes", "on"}
    exit_alert_raw = str(payload.get("exit_alert_name_raw") or payload.get("exit_alert_name") or payload.get("exit_alert") or "").strip()
    exit_alert_name = normalize_alert_name(exit_alert_raw) if exit_alert_raw else ""
    if exit_alert_enabled and not exit_alert_name:
        return {"error": "EXIT_ALERT_NAME_REQUIRED"}
    if exit_alert_enabled and exit_alert_name == alert_name:
        return {"error": "EXIT_ALERT_CANNOT_MATCH_ENTRY_ALERT"}

    try:
        existing_configs = await store.list_alert_configs(user_id)
    except Exception:
        existing_configs = {}
    for existing_key, existing_cfg in (existing_configs or {}).items():
        if not isinstance(existing_cfg, dict):
            continue
        other_entry = normalize_alert_name(
            existing_cfg.get("alert_name") or existing_cfg.get("alert_name_raw") or existing_key
        )
        if other_entry == alert_name:
            continue
        other_exit_enabled = str(existing_cfg.get("exit_alert_enabled", "false")).lower() in {"1", "true", "yes", "on"}
        other_exit_raw = str(
            existing_cfg.get("exit_alert_name_raw")
            or existing_cfg.get("exit_alert_name")
            or existing_cfg.get("exit_alert")
            or ""
        ).strip()
        other_exit = normalize_alert_name(other_exit_raw) if other_exit_raw else ""
        if exit_alert_enabled and exit_alert_name and other_entry == exit_alert_name:
            return {
                "error": "EXIT_ALERT_COLLIDES_WITH_ENTRY_ALERT",
                "message": "Exit alert name matches another strategy entry alert name.",
                "exit_alert_name": exit_alert_raw,
                "existing_entry_alert": existing_cfg.get("alert_name_raw") or other_entry,
            }
        if other_exit_enabled and other_exit and other_exit == alert_name:
            return {
                "error": "ALERT_NAME_COLLIDES_WITH_EXIT_ALERT",
                "message": "Entry alert name matches another strategy exit alert name.",
                "existing_entry_alert": existing_cfg.get("alert_name_raw") or other_entry,
                "existing_exit_alert": other_exit_raw,
            }
        if exit_alert_enabled and other_exit_enabled and exit_alert_name and other_exit == exit_alert_name:
            return {
                "error": "EXIT_ALERT_NAME_COLLISION",
                "message": "This exit alert name is already assigned to another strategy.",
                "exit_alert_name": exit_alert_raw,
                "existing_entry_alert": existing_cfg.get("alert_name_raw") or other_entry,
            }

    strategy_mode = str(payload.get("strategy_mode", "CLASSIC") or "CLASSIC").strip().upper()
    if strategy_mode not in {"CLASSIC", "PRECISION_SNIPER", "GMMA_OBV", "GMMA_GOLD_CROSS", "LIQUIDITY_SWEEP", "PURE_LIQUIDITY_SWEEP", "GVK_TREND"}:
        return {"error": "INVALID_STRATEGY_MODE"}
    try:
        order_timeout = float(payload.get("order_confirm_timeout_sec") or payload.get("execution_confirm_timeout_sec") or 1.5)
        order_retries = int(payload.get("order_pending_retry_count") or payload.get("execution_retry_count") or 1)
        order_buffer = float(payload.get("order_limit_buffer_pct") or payload.get("dhan_limit_buffer_pct") or payload.get("execution_protection_pct") or 0.15)
    except Exception:
        return {"error": "ORDER_EXECUTION_SETTINGS_INVALID"}
    if not (0.2 <= order_timeout <= 10.0 and 0 <= order_retries <= 5 and 0.0 <= order_buffer <= 5.0):
        return {"error": "ORDER_EXECUTION_SETTINGS_INVALID"}
    try:
        pyramid_step = float(payload.get("pyramid_step_pct") or 0.8)
        pyramid_max = int(payload.get("pyramid_max_adds") or 0)
    except Exception:
        return {"error": "PYRAMID_SETTINGS_INVALID"}
    pyramid_enabled = str(payload.get("pyramid_enabled", "false")).lower() in {"1", "true", "yes", "on"}
    if not (0.05 <= pyramid_step <= 20.0 and 0 <= pyramid_max <= 20):
        return {"error": "PYRAMID_SETTINGS_INVALID"}
    if pyramid_enabled and pyramid_max < 1:
        return {"error": "PYRAMID_SETTINGS_INVALID"}
    try:
        target_pct = float(payload.get("target_pct") or 0.0)
        stop_loss_pct = float(payload.get("stop_loss_pct") or 0.0)
        trailing_sl_pct = float(payload.get("trailing_sl_pct") or 0.0)
        cost_sl_rr = float(payload.get("cost_sl_rr") or 2.0)
    except Exception:
        return {"error": "RISK_SETTINGS_INVALID"}
    trailing_sl_enabled = str(payload.get("trailing_sl_enabled", "true")).lower() in {"1", "true", "yes", "on"}
    cost_sl_enabled = str(payload.get("cost_sl_enabled", "false")).lower() in {"1", "true", "yes", "on"}
    if not (0.0 <= target_pct <= 100.0 and 0.0 <= stop_loss_pct <= 100.0 and 0.0 <= trailing_sl_pct <= 100.0):
        return {"error": "RISK_SETTINGS_INVALID"}
    if not (0.1 <= cost_sl_rr <= 20.0):
        return {"error": "RISK_SETTINGS_INVALID"}
    if cost_sl_enabled and stop_loss_pct <= 0:
        return {"error": "COST_SL_REQUIRES_STOP_LOSS"}
    if strategy_mode == "PRECISION_SNIPER":
        custom_error = validate_custom_config(payload)
        if custom_error:
            return {"error": custom_error}
    if strategy_mode == "GMMA_OBV":
        custom_error = validate_gmma_obv_config(payload)
        if custom_error:
            return {"error": custom_error}
    if strategy_mode == "GMMA_GOLD_CROSS":
        custom_error = validate_gmma_gold_cross_config(payload)
        if custom_error:
            return {"error": custom_error}
    if strategy_mode == "LIQUIDITY_SWEEP":
        custom_error = validate_liquidity_sweep_config(payload)
        if custom_error:
            return {"error": custom_error}
    if strategy_mode == "PURE_LIQUIDITY_SWEEP":
        custom_error = validate_pure_liquidity_sweep_config(payload)
        if custom_error:
            return {"error": custom_error}
    if strategy_mode == "GVK_TREND":
        custom_error = validate_gvk_trend_config(payload)
        if custom_error:
            return {"error": custom_error}

    # Guard: normalized-name collisions overwrite configs silently.
    # Example: "My_Strategy" vs "My-Strategy" normalize to the same key.
    try:
        existing = await store.get_alert_config(user_id, alert_name)
    except Exception:
        existing = None
    if existing:
        existing_raw = str(existing.get("alert_name_raw") or existing.get("alert_name") or "").strip()
        incoming_raw = str(raw_name or "").strip()
        if existing_raw and incoming_raw and existing_raw.lower() != incoming_raw.lower():
            return {
                "error": "ALERT_NAME_COLLISION",
                "message": "This strategy name normalizes to an existing saved strategy key; rename it to avoid overwriting.",
                "normalized_key": alert_name,
                "existing_alert_name_raw": existing_raw,
                "incoming_alert_name_raw": incoming_raw,
            }

    payload2 = dict(payload)
    payload2["alert_name"] = alert_name
    payload2["alert_name_raw"] = str(raw_name)
    payload2["exit_alert_enabled"] = exit_alert_enabled
    payload2["exit_alert_name"] = exit_alert_name if exit_alert_enabled else ""
    payload2["exit_alert_name_raw"] = exit_alert_raw if exit_alert_enabled else ""
    payload2["strategy_mode"] = strategy_mode
    payload2["order_limit_buffer_pct"] = order_buffer
    payload2["target_pct"] = target_pct
    payload2["stop_loss_pct"] = stop_loss_pct
    payload2["trailing_sl_pct"] = trailing_sl_pct
    payload2["trailing_sl_enabled"] = trailing_sl_enabled
    payload2["cost_sl_enabled"] = cost_sl_enabled
    payload2["cost_sl_rr"] = cost_sl_rr

    await store.save_alert_config(user_id, payload2)
    
    # Log top sectors if sector filter is enabled
    if str(payload2.get("sector_filter_on", payload2.get("sector_on", "false"))).lower() == "true":
         try:
             eng = await ensure_engine(user_id)
             ranks = eng.get_sector_rank()
             top_n = int(payload2.get("top_n_sector", payload2.get("topn", 3)))
             
             # Get top N sectors
             top_sectors = ranks[:top_n]
             
             # Format for log
             sector_str = ", ".join([f"{s[0]} ({s[1]:+.2f}%)" for s in top_sectors])
             
             print("\n" + "="*60)
             print(f"✅ ALERT CONFIG SAVED: '{alert_name}'")
             print(f"🔍 Sector Filter: TOP {top_n}")
             print(f"📊 Current Top {top_n}: {sector_str}")
             print("="*60 + "\n")
         except Exception as e:
             print(f"⚠️ Failed to log top sectors: {e}")

    return {"status": "saved", "config": payload2}


@app.delete("/api/alert-config")
async def delete_alert_config_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = int(payload.get("user_id", 1))
    alert_name = str(payload.get("alert_name", "")).strip()
    deleted = await store.delete_alert_config(user_id, alert_name)
    if not deleted:
        return {"status": "not_found", "deleted": False}
    return {"status": "deleted", "deleted": True}


@app.post("/api/backtest")
async def run_backtest_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = int(payload.get("user_id", 1))
    symbol = normalize_symbol(payload.get("symbol", ""))
    if not symbol:
        return {"error": "SYMBOL_REQUIRED"}

    strategy_mode = str(payload.get("strategy_mode", "PRECISION_SNIPER") or "PRECISION_SNIPER").strip().upper()
    if strategy_mode not in {"PRECISION_SNIPER", "GMMA_OBV", "GMMA_GOLD_CROSS", "LIQUIDITY_SWEEP", "PURE_LIQUIDITY_SWEEP", "GVK_TREND"}:
        return {"error": "BACKTEST_UNSUPPORTED_STRATEGY"}

    if strategy_mode == "PRECISION_SNIPER":
        custom_error = validate_custom_config(payload)
        interval_minutes = timeframe_minutes(payload, 5)
        interval = timeframe_interval(payload, 5)
        precision_settings = resolve_settings(payload)
        required_candles = max(int(precision_settings["ema_trend"]), 50) + 2
    elif strategy_mode == "GMMA_OBV":
        custom_error = validate_gmma_obv_config(payload)
        interval_minutes = timeframe_minutes(payload, 5)
        interval = timeframe_interval(payload, 5)
        gmma_settings = resolve_gmma_obv_settings(payload)
        required_candles = max(
            60,
            int(gmma_settings["obv_donchian"]),
            int(gmma_settings["adx_len"]),
            int(gmma_settings["atr_len"]),
        ) + 5
    elif strategy_mode == "GMMA_GOLD_CROSS":
        custom_error = validate_gmma_gold_cross_config(payload)
        interval_minutes = timeframe_minutes(payload, 5)
        interval = timeframe_interval(payload, 5)
        _ggc_settings = resolve_gmma_gold_cross_settings(payload)
        required_candles = gmma_gold_cross_required_candles(payload)
    elif strategy_mode == "LIQUIDITY_SWEEP":
        custom_error = validate_liquidity_sweep_config(payload)
        interval_minutes = timeframe_minutes(payload, 5)
        interval = timeframe_interval(payload, 5)
        liq_settings = resolve_liquidity_sweep_settings(payload)
        required_candles = max(
            int(liq_settings["swing_len"]) * 2 + int(liq_settings["lookback_bars"]) // 2,
            int(liq_settings["minor_len"]) * 2 + int(liq_settings["confirm_window"]) + 5,
            max(int(liq_settings["gk_len"]), int(liq_settings["gk_atr_len"]) + 5) if liq_settings["use_gk_filter"] else 0,
            int(liq_settings["vol_len"]) + int(liq_settings["atr_len"]) + 5,
        )
    elif strategy_mode == "PURE_LIQUIDITY_SWEEP":
        custom_error = validate_pure_liquidity_sweep_config(payload)
        interval_minutes = timeframe_minutes(payload, 5)
        interval = timeframe_interval(payload, 5)
        _pure_settings = resolve_pure_liquidity_sweep_settings(payload)
        required_candles = pure_liquidity_required_candles(payload)
    else:
        custom_error = validate_gvk_trend_config(payload)
        interval_minutes = timeframe_minutes(payload, 5)
        interval = timeframe_interval(payload, 5)
        _gvk_settings = resolve_gvk_trend_settings(payload)
        required_candles = gvk_trend_required_candles(payload)
    if custom_error:
        return {"error": custom_error}
    bars_per_day = max(1, int(375 / interval_minutes))
    required_trading_sessions = int((required_candles + bars_per_day - 1) / bars_per_day)
    # Backtests must be warm at the first selected candle (e.g. 09:20).
    # Calendar days need extra room for weekends, market holidays, and Dhan's
    # day-wise intraday responses, otherwise long indicators only become ready
    # late in the selected session.
    required_calendar_days = max(3, required_trading_sessions * 2 + 3)
    max_warmup_days = max(required_calendar_days, min(90, required_trading_sessions * 4 + 14))
    requested_warmup = payload.get("warmup_days")
    if requested_warmup in (None, ""):
        warmup_days = required_calendar_days
    else:
        warmup_days = max(int(requested_warmup or 0), required_calendar_days)

    ist = pytz.timezone("Asia/Kolkata")
    today = datetime.datetime.now(ist).date()
    from_raw = str(payload.get("from_date") or today.isoformat())
    to_raw = str(payload.get("to_date") or today.isoformat())
    try:
        from_dt = datetime.datetime.fromisoformat(from_raw)
        to_dt = datetime.datetime.fromisoformat(to_raw)
    except ValueError:
        return {"error": "BACKTEST_DATE_INVALID"}
    from_has_time = "T" in from_raw or " " in from_raw
    to_has_time = "T" in to_raw or " " in to_raw
    if from_dt.tzinfo is None:
        if not from_has_time:
            from_dt = from_dt.replace(hour=9, minute=15, second=0, microsecond=0)
        from_dt = ist.localize(from_dt)
    else:
        from_dt = from_dt.astimezone(ist)
    if to_dt.tzinfo is None:
        if not to_has_time:
            to_dt = to_dt.replace(hour=15, minute=30, second=0, microsecond=0)
        to_dt = ist.localize(to_dt)
    else:
        to_dt = to_dt.astimezone(ist)
    if to_dt <= from_dt:
        return {"error": "BACKTEST_DATE_RANGE_INVALID"}

    candles = payload.get("candles")
    broker_fetched = not isinstance(candles, list)
    if broker_fetched:
        try:
            eng = await ensure_engine(user_id)
            candles = await eng._fetch_backtest_candles(
                symbol,
                interval,
                from_dt,
                to_dt,
                warmup_days=warmup_days,
            )
        except Exception as e:
            return {"error": f"BACKTEST_DATA_FAIL:{e}"}
        def _bt_candle_time(candle: Dict[str, Any]) -> datetime.datetime:
            raw = candle.get("date") or candle.get("time") or candle.get("timestamp")
            if isinstance(raw, datetime.datetime):
                dt = raw
            elif isinstance(raw, (int, float)):
                dt = datetime.datetime.fromtimestamp(float(raw), tz=ist)
            else:
                text = str(raw or "")
                try:
                    dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
                except ValueError:
                    dt = from_dt
            if dt.tzinfo is None:
                return ist.localize(dt)
            return dt.astimezone(ist)

        def _warmup_count(rows: Any) -> int:
            if not isinstance(rows, list):
                return 0
            return len([c for c in rows if isinstance(c, dict) and _bt_candle_time(c) < from_dt])

        # Some brokers, especially Dhan intraday, can return fewer candles than
        # the requested calendar span suggests. Extend warmup until the first
        # selected candle is actually indicator-ready, capped to Dhan's common
        # 90-day chart range guidance.
        while candles and _warmup_count(candles) < required_candles and warmup_days < max_warmup_days:
            next_warmup_days = min(max_warmup_days, max(warmup_days + 7, warmup_days * 2))
            if next_warmup_days <= warmup_days:
                break
            warmup_days = next_warmup_days
            candles = await eng._fetch_backtest_candles(
                symbol,
                interval,
                from_dt,
                to_dt,
                warmup_days=warmup_days,
            )
    if not candles:
        return {
            "error": "BACKTEST_NO_CANDLES_RETURNED",
            "message": "Broker returned zero candles for this symbol/date range. Check broker connection, symbol mapping, date range, and historical-data availability.",
            "symbol": symbol,
            "strategy_mode": strategy_mode,
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "interval": interval,
        }

    result = run_custom_strategy_backtest(
        candles,
        strategy_mode,
        {**payload, "_required_candles": required_candles, "_warmup_days": warmup_days},
        symbol=symbol,
        from_dt=from_dt,
        to_dt=to_dt,
        qty=int(payload.get("qty", 1) or 1),
        capital=float(payload.get("capital", 0) or 0),
    )
    return {"status": "ok", "result": result}


# -----------------------------
# MTM P&L Exit Config (Daily)
# -----------------------------
@app.get("/api/pnl-exit-config")
async def get_pnl_exit_config_api(user_id: int = 1) -> Dict[str, Any]:
    user_id = int(user_id)
    cfg = await store.get_pnl_exit_config(user_id)
    return {"config": cfg}


@app.post("/api/pnl-exit-config")
async def set_pnl_exit_config_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = int(payload.get("user_id", 1))
    cfg = await store.set_pnl_exit_config(user_id, payload or {})
    return {"status": "saved", "config": cfg}


# -----------------------------
# Position Management
# -----------------------------
@app.post("/api/position/exit-all")
async def exit_all_positions_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Exit all open positions"""
    user_id = int(payload.get("user_id", 1))
    
    eng = await ensure_engine(user_id)
    try:
        count = await eng.exit_all_open_positions(reason="MANUAL_EXIT_ALL")
        return {"status": "ok", "count": count, "message": f"Exit orders sent for {count} positions"}
    except Exception as e:
        return {"error": str(e)}


# -----------------------------
# Chartink webhook
# -----------------------------
@app.api_route("/webhook/chartink", methods=["POST", "GET"])
async def chartink_webhook(request: Request, user_id: int = 1) -> Dict[str, Any]:
    if not _webhook_secret_valid(request):
        raise HTTPException(status_code=401, detail="WEBHOOK_SECRET_INVALID")
    runtime = await ensure_service_runtime()
    result = await runtime.api_gateway.receive_chartink_webhook(request, int(user_id))
    logging.getLogger("trade_engine").info(
        "LATENCY | path=chartink_webhook user=%s alert=%s symbols=%s total_ms=%.2f queue_depth=%s",
        int(user_id),
        result.get("alert"),
        len(result.get("symbols") or []),
        float(result.get("latency_ms") or 0.0),
        runtime.signal_intake.queue.queue.qsize(),
    )
    return result


# -----------------------------
# Sectors
# -----------------------------
@app.post("/api/subscribe-symbols")
async def api_subscribe_symbols(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Force subscription for a batch of symbols (used by UI)"""
    user_id = int(payload.get("user_id", 1))
    symbols = payload.get("symbols", [])
    if not symbols:
        return {"ok": False, "error": "NO_SYMBOLS"}
    await subscribe_symbols_for_user(user_id, symbols)
    return {"ok": True, "count": len(symbols), "subscribed": symbols}


@app.get("/api/sectors/top")
async def get_top_sectors(user_id: int = Query(..., alias="user_id"), limit: int = 10):
    """
    Get current top N performing sectors.
    """
    eng = await ensure_engine(user_id)
    await load_sector_cache_for_user(user_id)
    ranks = eng.get_sector_rank()
    
    # Format for display: [{"name": "NIFTY AUTO", "pct": 1.23}, ...]
    top = [{"name": r[0], "pct": r[1]} for r in ranks[:limit]]
    return {
        "ready": bool(top),
        "reason": "" if top else "SECTOR_RANK_NOT_READY",
        "sectors": top,
    }


@app.get("/api/sectors/cache")
async def get_sector_cache_api(user_id: int = 1) -> Dict[str, Any]:
    user_id = int(user_id)
    cache = await load_sector_cache_for_user(user_id)
    eng = await ensure_engine(user_id)
    ranks = eng.get_sector_rank()
    ranked = [{"name": sec, "pct": pct} for sec, pct in ranks]
    return {
        "ok": bool(cache),
        "cache": cache,
        "ready": bool(ranked),
        "reason": "" if ranked else "SECTOR_RANK_NOT_READY",
        "sectors": ranked,
    }


@app.post("/api/sectors/cache")
async def post_sector_cache_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = int(payload.get("user_id", 1))
    return await get_sector_cache_api(user_id=user_id)


@app.post("/api/sectors/cache-dhan")
async def cache_dhan_sector_data_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        user_id = int(payload.get("user_id", 1))
        result = await fetch_and_cache_dhan_sector_data(user_id)
        if result.get("ok"):
            ws_mgr.broadcast_nowait(
                user_id,
                {
                    "type": "sector_cache",
                    "ok_count": result.get("ok_count", 0),
                    "total": result.get("total", 0),
                    "cached_at": result.get("cached_at", ""),
                },
            )
        return result
    except Exception as exc:
        log.exception("Dhan sector cache API failed")
        return {"ok": False, "error": "DHAN_SECTOR_CACHE_FAILED", "detail": str(exc)}


# -----------------------------
# Alerts
# -----------------------------
@app.get("/api/alerts")
async def api_alerts(user_id: int = 1, limit: int = 100) -> Dict[str, Any]:
    user_id = int(user_id)
    alerts = await store.get_recent_alerts(user_id, int(limit))
    return {"alerts": alerts}


@app.delete("/api/alerts")
async def api_clear_alerts(user_id: int = 1) -> Dict[str, Any]:
    user_id = int(user_id)
    await store.delete_alerts(user_id)
    return {"ok": True, "message": "All alerts cleared"}


# -----------------------------
# Positions
# -----------------------------
@app.get("/api/positions")
async def api_positions(user_id: int = 1) -> Dict[str, Any]:
    user_id = int(user_id)
    rows = await store.list_positions(user_id)
    return {"positions": rows}


#-----------------------------
# Square Off positions
# -----------------------------
@app.post("/api/position/squareoff")
async def api_squareoff(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = int(payload.get("user_id", 1))
    raw_symbol = payload.get("symbol", "")
    symbol = _sym_safe(raw_symbol)
    reason = str(payload.get("reason", "MANUAL") or "MANUAL").strip().upper()

    if not symbol:
        return {"error": f"Invalid symbol: {raw_symbol}"}

    eng = await ensure_engine(user_id)

    print(f"🖱️ [SQUAREOFF_CLICK] user={user_id} raw='{raw_symbol}' sym='{symbol}' reason={reason}")
    ok = await is_session_valid(user_id)
    if not ok:
        return {"error": "Selected broker is not connected. Please connect first."}

    # ✅ Works even after restart (memory -> Zerodha fallback)
    r = await eng.manual_squareoff_zerodha(symbol, reason=reason)

    print(f"🧾 [SQUAREOFF_RESULT] user={user_id} sym={symbol} -> {r}")
    
    # Convert response format to match frontend expectations
    if r.get("status") == "ERROR":
        return {"error": r.get("reason", "Square off failed")}
    elif r.get("status") == "NOT_FOUND":
        return {"error": f"No open position found for {symbol}"}
    
    ws_mgr.broadcast_nowait(user_id, {"type": "pos_refresh"})
    return {"ok": True, "message": f"Exit order sent for {symbol}"}


# -----------------------------
# Kill switch
# -----------------------------
@app.post("/api/kill-switch")
async def api_kill(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = int(payload.get("user_id", 1))
    enabled = bool(payload.get("enabled", True))
    if not enabled:
        await store.set_kill(user_id, False)
        return {"ok": True, "enabled": False}

    # Enabling kill switch: square-off first, then activate kill switch.
    eng = await ensure_engine(user_id)
    try:
        sq = await eng.squareoff_all_positions(reason="KILL_SWITCH_MANUAL")
    except Exception as e:
        sq = {"ok": False, "error": str(e), "count": 0, "results": []}

    await store.set_kill(user_id, True)
    try:
        ws_mgr.broadcast_nowait(user_id, {"type": "kill_switch", "enabled": True})
    except Exception:
        pass
    return {"ok": True, "enabled": True, "squareoff": sq}


# -----------------------------
# Admin: service restart
# -----------------------------
@app.post("/api/service/restart")
async def api_restart_service(
    request: Request,
    payload: Dict[str, Any],
    x_restart_token: Optional[str] = Header(None, alias="X-Restart-Token"),
) -> Dict[str, Any]:
    """
    Trigger a server-side restart command (typically `systemctl restart trading`).

    Safety:
      - Disabled by default; set ENABLE_SERVICE_RESTART=1 to enable.
      - If SERVICE_RESTART_TOKEN is set, clients must provide X-Restart-Token header.
    """
    if not ENABLE_SERVICE_RESTART:
        # Fallback: soft restart ticker to pick up new tokens without full service restart.
        user_id = int(payload.get("user_id", 1))
        await restart_selected_feed(user_id)
        return {"ok": True, "message": "Ticker restarted (service restart disabled)"}

    if SERVICE_RESTART_TOKEN and (x_restart_token or "") != SERVICE_RESTART_TOKEN:
        raise HTTPException(status_code=403, detail="INVALID_RESTART_TOKEN")

    if sys.platform.startswith("win"):
        # Allow Windows only if TRADING_RESTART_CMD is explicitly set to a non-systemctl command.
        if not TRADING_RESTART_CMD or "systemctl" in TRADING_RESTART_CMD.lower():
            user_id = int(payload.get("user_id", 1))
            await restart_selected_feed(user_id)
            return {"ok": True, "message": "Ticker restarted (Windows fallback)"}

    cmd = TRADING_RESTART_CMD

    result = _run_restart_command(cmd)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error"), "detail": result.get("detail", "")}

    return {"ok": True, "message": "Restart requested", "detail": result.get("detail", "")}


@app.get("/api/services/status")
async def api_services_status() -> Dict[str, Any]:
    runtime = await ensure_service_runtime()
    return {"ok": True, "services": runtime.status()}


# -----------------------------
# Auto Square Off Config
# -----------------------------
@app.get("/api/auto-sq-off/status")
async def get_auto_sq_off(user_id: int = 1) -> Dict[str, Any]:
    enabled = await store.is_auto_sq_off_enabled(int(user_id))
    return {"enabled": enabled}

@app.post("/api/auto-sq-off/toggle")
async def toggle_auto_sq_off(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = int(payload.get("user_id", 1))
    enabled = bool(payload.get("enabled", False))
    await store.set_auto_sq_off_enabled(user_id, enabled)
    return {"enabled": enabled}


# -----------------------------
# WebSocket feed
# -----------------------------
@app.get("/ws/feed")
async def ws_feed_http(user_id: int = 1) -> Dict[str, Any]:
    # If someone hits this endpoint via normal HTTP, FastAPI will not match the WebSocket route
    # and you'll see 404s in logs. Return a clear response instead.
    raise HTTPException(
        status_code=426,
        detail="Upgrade Required: connect using WebSocket (ws:// or wss://) to /ws/feed",
    )

@app.websocket("/ws/feed")
async def ws_feed(ws: WebSocket, user_id: int = 1):
    user_id = int(user_id)
    await ensure_store_ready()
    if _admin_auth_enabled() and not await _admin_session_from_token(ws.cookies.get(ADMIN_SESSION_COOKIE, "")):
        await ws.close(code=4401, reason="ADMIN_AUTH_REQUIRED")
        return
    await ws_mgr.connect(user_id, ws)
    try:
        while True:
            # Keep-alive from client (dashboard sends ping)
            await ws.receive_text()
    except WebSocketDisconnect:
        await ws_mgr.disconnect(user_id, ws)
    except Exception:
        await ws_mgr.disconnect(user_id, ws)
