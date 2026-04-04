import requests
import logging
"""
Empower Personal Capital client — Playwright-based authentication.

Login flow (first time / session expired):
  1. login() starts a daemon Playwright thread and returns quickly.
  2. Thread navigates to Empower, fills email + password.
  3. If 2FA is required:
       - login() returns {"status": "2fa_required"}
       - verify_2fa(code) sends the code to the waiting thread.
  4. Thread saves cookies + CSRF + API base URL to disk.
  5. _authenticated is set True.

Subsequent syncs (session still valid):
  - Uses requests.Session() with stored cookies — no browser.
  - Falls back to Playwright re-login if session has expired.

Thread coordination uses two threading.Event objects:
  _2fa_done_event  — set by PW thread to unblock the HTTP handler.
                     Used twice: once for "2fa_required" signal,
                     once for "login complete" signal.
  _2fa_code_event  — set by verify_2fa() to wake the waiting PW thread.
_login_phase:  None | "2fa_required" | "complete" | "failed"
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

COOKIES_FILE = Path(__file__).parent.parent / "empower_cookies.json"

# Selectors — broad multi-pattern lists to survive UI changes
_EMAIL_SEL = (
    'input[type="email"], input[name="username"], input[name="email"], '
    'input[id*="email" i], input[placeholder*="email" i], '
    'input[placeholder*="username" i]'
)
_PASSWORD_SEL = 'input[type="password"]'
_TWOFA_SEL = (
    'input[autocomplete="one-time-code"], input[inputmode="numeric"], '
    'input[name="code"], input[name="smsCode"], input[name="authCode"], '
    'input[name="verificationCode"], input[name="challengeCode"], '
    'input[name*="code" i], input[name*="otp" i], input[name*="token" i], '
    'input[placeholder*="code" i], input[placeholder*="verification" i], '
    'input[type="tel"][maxlength="6"]'
)
_SUBMIT_SEL = (
    'button[type="submit"], input[type="submit"], '
    'button:has-text("Sign In"), button:has-text("Log In"), '
    'button:has-text("Continue"), button:has-text("Next")'
)


def _email() -> str:
    return os.getenv("EMPOWER_EMAIL", "")

def _password() -> str:
    return os.getenv("EMPOWER_PASSWORD", "")


def _map_nature(atg: str, at: str) -> str:
    atg = (atg or "").upper()
    at = (at or "").lower()
    if atg == "BANK":
        return "savings" if ("saving" in at or "money_market" in at) else "checking"
    if atg == "CREDIT_CARD":
        return "credit"
    if atg in ("INVESTMENT", "BROKERAGE"):
        return "investment"
    if atg in ("LOAN", "MORTGAGE", "OTHER_LIABILITIES"):
        return "credit"
    return "account"


def _normalize_accounts(raw: List[Dict]) -> List[Dict]:
    out = []
    for acc in raw:
        bal = acc.get("balance")
        if bal is None:
            bal = acc.get("currentBalance")
        out.append({
            "account_id": str(acc.get("userAccountId", "")),
            "name": acc.get("name", ""),
            "firm_name": acc.get("firmName", ""),
            "nature": _map_nature(
                acc.get("accountTypeGroup", ""),
                acc.get("accountType", ""),
            ),
            "balance": bal,
            "currency_code": acc.get("currency", "USD"),
            "account_number": acc.get("accountNumber", ""),
        })
    return out


def _normalize_transactions(raw: List[Dict]) -> List[Dict]:
    out = []
    for txn in raw:
        out.append({
            "transaction_id": str(txn.get("userTransactionId", "")),
            "account_id": str(txn.get("userAccountId", "")),
            "amount": txn.get("amount", 0),
            "made_on": txn.get("transactionDate", ""),
            "description": txn.get("description") or txn.get("originalDescription", ""),
            "category": txn.get("categoryName", ""),
            "status": "pending" if txn.get("isPending") else "posted",
        })
    return out


class SessionExpiredError(Exception):
    pass


class EmpowerClient:
    def __init__(self):
        self._authenticated = False

        # Thread coordination
        self._login_phase: Optional[str] = None  # None|"2fa_required"|"complete"|"failed"
        self._2fa_done_event = threading.Event()
        self._2fa_code_event = threading.Event()
        self._2fa_code: Optional[str] = None
        self._2fa_error: Optional[str] = None
        self._login_lock = threading.Lock()

        # API state
        self._api_base: str = "https://home.personalcapital.com"
        self._csrf: str = ""
        self._session = None          # requests.Session with cookies
        self._first_sync: bool = False

        self._load_state()

    # ── Persistence ────────────────────────────────────────────────────────

    def _load_state(self):
        if not COOKIES_FILE.exists():
            return
        try:
            data = json.loads(COOKIES_FILE.read_text())
            self._api_base = data.get("api_base", self._api_base)
            self._csrf = data.get("csrf", "")
            self._first_sync = data.get("first_sync", False)
            self._build_session(data.get("storage_state", {}).get("cookies", []))
        except Exception:
            pass

    def _save_state(self, storage_state: Dict, api_base: str, csrf: str, first_sync=True):
        self._api_base = api_base
        self._csrf = csrf
        self._first_sync = first_sync
        self._build_session(storage_state.get("cookies", []))
        COOKIES_FILE.write_text(json.dumps({
            "storage_state": storage_state,
            "api_base": api_base,
            "csrf": csrf,
            "first_sync": first_sync,
        }))

    def _build_session(self, cookies: List[Dict]):
        import requests
        s = requests.Session()
        s.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
        for c in cookies:
            s.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )
        self._session = s

    # ── Session health ─────────────────────────────────────────────────────

    def _is_expired(self, resp) -> bool:
        if resp.status_code == 401:
            return True
        try:
            data = resp.json()
            sp = data.get("spHeader", {})
            if sp.get("success") is False:
                codes = [str(e.get("code", "")) for e in sp.get("errors", [])]
                # 201 is "Session not authenticated", 100 is "Invalid CSRF"
                # We only want to trigger "Expired" (and thus re-login) if it's a hard auth failure.
                if any(c in ("201", "session.timeout", "UNAUTHORIZED", "auth.required") for c in codes):
                    return True
        except Exception:
            pass
        return False

    def _is_session_valid(self) -> bool:
        import logging
        if not self._session:
            logging.error(f"DEBUG: _is_session_valid False (no session)")
            return False
        try:
            # If no CSRF yet, establish it by calling a probe endpoint
            resp = self._api_post("/login/querySession", {})
            sp_header = resp.get("spHeader", {})
            success = sp_header.get("success", False)
            auth_level = sp_header.get("authLevel", "NONE")
            
            # success=True is not enough; authLevel must not be NONE
            is_valid = success and auth_level != "NONE"
            logging.error(f"DEBUG: _is_session_valid {is_valid} (base={self._api_base}, success={success}, auth={auth_level})")
            return is_valid
        except Exception as e:
            logging.error(f"DEBUG: _is_session_valid Exception: {e}")
            return False

    # ── Playwright thread ──────────────────────────────────────────────────

    def _playwright_login_thread(self):
        try:
            from playwright.sync_api import sync_playwright

            api_bases: List[str] = []
            captured_csrf: List[str] = []

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                )
                context = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()

                def on_response(response):
                    if "/api/" in response.url and response.status == 200:
                        try:
                            body = response.json()
                            csrf = body.get("spHeader", {}).get("csrf", "")
                            if csrf:
                                captured_csrf.clear()
                                captured_csrf.append(csrf)
                            parsed = urlparse(response.url)
                            base = f"{parsed.scheme}://{parsed.netloc}"
                            if base not in api_bases:
                                api_bases.append(base)
                        except Exception:
                            pass

                page.on("response", on_response)

                # ── Navigate to login ──
                page.goto(
                    "https://home.personalcapital.com/page/login/goHome",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                page.wait_for_load_state("networkidle", timeout=20_000)
                page.wait_for_timeout(1_500)

                # ── Fill email ──
                try:
                    page.wait_for_selector('input[name="username"]', state="visible", timeout=20_000)
                    page.fill('input[name="username"]', _email())
                    page.wait_for_timeout(500)
                    # Try clicking the submit button; fall back to Enter
                    submitted = False
                    for btn_sel in [
                        'button[type="submit"]',
                        'input[type="submit"]',
                        'button:has-text("Continue")',
                        'button:has-text("Next")',
                        'button:has-text("Sign In")',
                        'button:has-text("Log In")',
                    ]:
                        try:
                            loc = page.locator(btn_sel).first
                            if loc.is_visible():
                                loc.click(timeout=3_000)
                                submitted = True
                                break
                        except Exception:
                            continue
                    if not submitted:
                        page.press('input[name="username"]', "Enter")
                except Exception as exc:
                    self._2fa_error = f"Could not find email input: {exc}"
                    self._login_phase = "failed"
                    self._2fa_done_event.set()
                    browser.close()
                    return

                # ── Fill password ──
                try:
                    # Empower often has multiple hidden fields; find the one that is actually usable
                    pw_input = page.locator('input[name="passwd"]').first
                    pw_input.scroll_into_view_if_needed()
                    pw_input.click(force=True)
                    pw_input.fill(_password())
                    page.wait_for_timeout(500)
                    submitted = False
                    for btn_sel in [
                        'button[type="submit"]',
                        'input[type="submit"]',
                        'button:has-text("Sign In")',
                        'button:has-text("Log In")',
                        'button:has-text("Continue")',
                        'button:has-text("Next")',
                    ]:
                        try:
                            loc = page.locator(btn_sel).first
                            if loc.is_visible():
                                loc.click(timeout=3_000)
                                submitted = True
                                break
                        except Exception:
                            continue
                    if not submitted:
                        page.press('input[name="passwd"]', "Enter")
                    page.wait_for_timeout(3_000)
                except Exception as exc:
                    self._2fa_error = f"Could not find password input: {exc}"
                    self._login_phase = "failed"
                    self._2fa_done_event.set()
                    browser.close()
                    return

                # ── 2FA or success? ──
                # Give the page time to transition after password submit
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
                page.wait_for_timeout(2_000)
                if self._is_2fa_page(page):
                    self._login_phase = "2fa_required"
                    self._2fa_done_event.set()       # unblock login() caller
                    self._2fa_done_event.clear()     # reset for second use

                    if not self._2fa_code_event.wait(timeout=300):
                        self._2fa_error = "2FA timed out waiting for code"
                        self._login_phase = "failed"
                        self._2fa_done_event.set()
                        browser.close()
                        return

                    code = self._2fa_code
                    self._2fa_code_event.clear()

                    try:
                        page.wait_for_selector(_TWOFA_SEL, timeout=5_000)
                        page.fill(_TWOFA_SEL, code)
                        try:
                            page.locator(_SUBMIT_SEL).first.click(timeout=3_000)
                        except Exception:
                            page.press(_TWOFA_SEL, "Enter")
                        page.wait_for_load_state("networkidle", timeout=25_000)
                        page.wait_for_timeout(3_000)
                    except Exception as exc:
                        self._2fa_error = f"2FA entry failed: {exc}"
                        self._login_phase = "failed"
                        self._2fa_done_event.set()
                        browser.close()
                        return

                # ── Confirm logged in ──
                if not self._is_logged_in(page):
                    self._2fa_error = (
                        f"Login did not succeed (landed on: {page.url}). "
                        "Check credentials and try again."
                    )
                    self._login_phase = "failed"
                    self._2fa_done_event.set()
                    browser.close()
                    return

                storage_state = context.storage_state()
                api_base = api_bases[0] if api_bases else self._api_base
                csrf = captured_csrf[0] if captured_csrf else ""
                browser.close()

            self._save_state(storage_state, api_base, csrf, first_sync=True)
            self._authenticated = True
            self._login_phase = "complete"

        except Exception as exc:
            self._2fa_error = str(exc)
            self._login_phase = "failed"
        finally:
            self._2fa_done_event.set()

    def _is_2fa_page(self, page) -> bool:
        # Check for visible 2FA inputs first
        for sel in [s.strip() for s in _TWOFA_SEL.split(",")]:
            try:
                if page.locator(sel).filter(has=page.locator(":visible")).count() > 0:
                    return True
            except Exception:
                pass
        # Also check if any 2FA-like input is visible on the page
        try:
            for sel in ['input[name="code"]', 'input[name="smsCode"]',
                        'input[name="authCode"]', 'input[name="verificationCode"]',
                        'input[name="challengeCode"]']:
                if page.locator(sel).count() > 0:
                    return True
        except Exception:
            pass
        text = page.inner_text("body").lower()
        return any(p in text for p in [
            "verification code", "enter the code", "check your phone",
            "two-factor", "two factor", "authentication code",
            "sent you a code", "security code", "text message",
        ])

    def _is_logged_in(self, page) -> bool:
        url = page.url.lower()
        if any(x in url for x in ["login", "signin", "sign-in", "/auth/"]):
            return False
        if any(x in url for x in ["dashboard", "accounts", "participant.empower",
                                    "wealth", "home.empower", "overview"]):
            return True
        for sel in [
            '[data-testid="dashboard"]', 'nav[aria-label*="main" i]',
            'a[href*="accounts" i]', '[class*="dashboard" i]',
        ]:
            if page.locator(sel).count() > 0:
                return True
        # If we captured any API traffic, auth succeeded
        return bool(self._api_base != "https://home.personalcapital.com")

    # ── Cookie import (browser session) ───────────────────────────────────

    def import_session(self, cookies: List[Dict], api_base: str = None) -> Dict:
        """
        Import cookies exported from a browser (Cookie-Editor JSON array format).
        Probes known API endpoints to establish a working session.
        Returns {"status": "ok", "api_base": ...} on success or raises RuntimeError.
        """
        # Normalize: accept both raw array and {"cookies": [...]} wrapper
        if isinstance(cookies, dict):
            cookies = cookies.get("cookies", [])

        if not cookies:
            raise ValueError("No cookies provided")

        # Infer API base from cookie domains if not supplied
        if not api_base:
            domains = {c.get("domain", "").lstrip(".") for c in cookies}
            if "participant.empower-retirement.com" in domains:
                api_base = "https://participant.empower-retirement.com"
            elif "home.personalcapital.com" in domains or "personalcapital.com" in domains:
                api_base = "https://home.personalcapital.com"
            else:
                api_base = "https://home.personalcapital.com"  # default

        self._build_session(cookies)

        # Probe endpoints to find one that yields a CSRF token and confirms auth
        probe_candidates = [
            ("https://home.personalcapital.com", "/api/login/querySession"),
            (api_base, "/api/login/querySession"),
        ]
        # Deduplicate while preserving order
        seen = set()
        probes = []
        for base, path in probe_candidates:
            key = base + path
            if key not in seen:
                seen.add(key)
                probes.append((base, path))

        # Try to find a CSRF token in the imported cookies if possible
        imported_csrf = ""
        cookie_domains = set()
        jsession_domain = None
        for c in cookies:
            name = c.get("name")
            domain = c.get("domain", "").lstrip(".")
            if domain:
                cookie_domains.add(domain)
            if name == "JSESSIONID":
                jsession_domain = domain
            if name in ("csrf", "CSRF", "X-CSRF-TOKEN"):
                imported_csrf = c.get("value", "")
        
        # Prioritize the domain the cookies actually came from
        probe_candidates = []
        if api_base:
            probe_candidates.append((api_base, "/api/login/querySession"))
        
        if jsession_domain:
            logging.error(f"DEBUG: Prioritizing JSESSIONID domain: {jsession_domain}")
            probe_candidates.append((f"https://{jsession_domain}", "/api/login/querySession"))
        
        for d in cookie_domains:
            if "empower" in d or "personalcapital" in d:
                probe_candidates.append((f"https://{d}", "/api/login/querySession"))

        probe_candidates.append(("https://home.personalcapital.com", "/api/login/querySession"))
        probe_candidates.append(("https://participant.empower-retirement.com", "/api/login/querySession"))
        
        # Deduplicate while preserving order
        seen = set()
        probes = []
        for b, p in probe_candidates:
            # Normalize base URL (strip trailing slash)
            b = b.rstrip("/")
            key = b + p
            if key not in seen:
                seen.add(key)
                probes.append((b, p))

        working_base = None
        csrf = imported_csrf
        probe_errors = []

        logging.error(f"DEBUG: Probing candidates: {probes} with initial CSRF: {bool(csrf)}")

        for base, path in probes:
            try:
                url = f"{base}{path}"
                logging.error(f"DEBUG: Probing {url} ...")
                resp = self._session.post(
                    url,
                    # Use imported CSRF or null to let server give us a new one
                    data={"lastServerChangeId": "-1", "csrf": csrf if csrf else "null", "apiClient": "WEB"},
                    timeout=15,
                )
                logging.error(f"DEBUG: {url} response status: {resp.status_code}")
                
                # If we get a 200, try to parse it
                if resp.status_code == 200:
                    data = resp.json()
                    sp = data.get("spHeader", {})
                    new_csrf = sp.get("csrf", "")
                    success = sp.get("success", False)
                    auth_level = sp.get("authLevel", "NONE")
                    
                    logging.error(f"DEBUG: {url} success={success}, auth={auth_level}, new_csrf={bool(new_csrf)}")
                    
                    if new_csrf:
                        csrf = new_csrf
                    
                    # Must be successful AND have an auth level
                    if success and auth_level != "NONE":
                        logging.error(f"DEBUG: Found working base: {base}")
                        working_base = base
                        break
                    elif new_csrf and not working_base:
                        # Got a CSRF, might be a valid domain but just not authenticated yet
                        logging.error(f"DEBUG: Got CSRF from {base} but not authenticated (auth={auth_level})")
                        working_base = base
                else:
                    probe_errors.append(f"{base} → {resp.status_code}")
            except Exception as e:
                logging.error(f"DEBUG: Error probing {base}: {e}")
                probe_errors.append(f"{base} → {e}")

        # RECOVERY: If we failed all probes but have a working base from CSRF, 
        # let's TRY to use it anyway if the user is adamant.
        if not working_base or (auth_level == "NONE" and not working_base):
            # Try one more: maybe the data endpoints work even if querySession is weird
            if not working_base and api_base:
                working_base = api_base
            elif not working_base:
                # Pick the first domain from cookies
                for d in cookie_domains:
                    if "empower" in d:
                        working_base = f"https://{d}"
                        break
        
        if not working_base:
            error_msg = (
                "Cookies imported but could not find a working Empower domain. "
                "Make sure you are logged into Empower in your browser."
            )
            logging.error(f"DEBUG: import_session failed completely. cookie_domains={cookie_domains}")
            raise RuntimeError(error_msg)

        logging.error(f"DEBUG: import_session finishing. base={working_base}, csrf={bool(csrf)}")
        self._api_base = working_base
        self._csrf = csrf
        self._authenticated = True
        self._save_state({"cookies": cookies}, working_base, csrf, first_sync=True)
        return {"status": "ok", "api_base": working_base, "auth_found": bool(working_base)}

    # ── Public auth interface ──────────────────────────────────────────────

    def login(self) -> Dict:
        with self._login_lock:
            if self._authenticated and self._is_session_valid():
                return {"status": "ok"}
            if not _email() or not _password():
                raise ValueError("EMPOWER_EMAIL and EMPOWER_PASSWORD must be set in .env")

            self._login_phase = None
            self._2fa_error = None
            self._2fa_code = None
            self._2fa_done_event.clear()
            self._2fa_code_event.clear()
            self._authenticated = False

            t = threading.Thread(target=self._playwright_login_thread, daemon=True)
            t.start()

            # Block until 2FA needed or login done (up to 90 s for page loads)
            if not self._2fa_done_event.wait(timeout=90):
                raise RuntimeError("Login timed out — Empower page took too long to load")

            if self._login_phase == "complete":
                return {"status": "ok"}
            elif self._login_phase == "2fa_required":
                return {"status": "2fa_required", "mode": "SMS"}
            else:
                raise RuntimeError(self._2fa_error or "Login failed")

    def verify_2fa(self, code: str) -> Dict:
        if self._login_phase != "2fa_required":
            raise RuntimeError("No 2FA currently in progress")
        self._2fa_code = code
        self._2fa_done_event.clear()
        self._2fa_code_event.set()

        if not self._2fa_done_event.wait(timeout=90):
            raise RuntimeError("Timed out after entering 2FA code")

        if self._login_phase == "complete":
            self._authenticated = True
            return {"status": "ok"}
        raise RuntimeError(self._2fa_error or "2FA verification failed")

    def clear_session(self):
        self._authenticated = False
        self._session = None
        self._csrf = ""
        if COOKIES_FILE.exists():
            COOKIES_FILE.unlink()

    def is_first_sync(self) -> bool:
        return self._first_sync

    def clear_first_sync_flag(self):
        self._first_sync = False
        if COOKIES_FILE.exists():
            try:
                data = json.loads(COOKIES_FILE.read_text())
                data["first_sync"] = False
                COOKIES_FILE.write_text(json.dumps(data))
            except Exception:
                pass

    # ── API calls via requests (no browser) ───────────────────────────────

    def _api_post(self, path: str, extra: Dict = None) -> Dict:
        # Fallback to requests if we already have a session and CSRF
        # But if we get a 403, we should probably switch to Playwright
        try:
            return self._api_post_requests(path, extra)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403 and "Just a moment" in e.response.text:
                logging.error(f"DEBUG: Cloudflare detected on {path}, switching to Playwright fetch")
                return self._api_post_playwright(path, extra)
            raise

    def _api_post_requests(self, path: str, extra: Dict = None) -> Dict:
        import sys
        if not self._session:
            raise SessionExpiredError("No session — sync to log in first")
        
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{self._api_base}/",
            "Origin": self._api_base,
            "X-Client-Type": "WEB",
        }
        
        csrf_val = self._csrf if self._csrf else "null"
        payload = {"lastServerChangeId": "-1", "csrf": csrf_val, "apiClient": "WEB"}
        if extra:
            payload.update(extra)
        
        resp = self._session.post(f"{self._api_base}/api{path}", data=payload, headers=headers, timeout=30)
        
        if self._is_expired(resp):
            self._authenticated = False
            raise SessionExpiredError("Empower session has expired")
        
        resp.raise_for_status()
        result = resp.json()
        new_csrf = result.get("spHeader", {}).get("csrf", "")
        if new_csrf and new_csrf != self._csrf:
            self._csrf = new_csrf
        return result

    def _api_post_playwright(self, path: str, extra: Dict = None) -> Dict:
        """Fetch API data using a real browser context to bypass Cloudflare."""
        from playwright.sync_api import sync_playwright
        import json
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            
            # Map requests cookies to playwright cookies
            pw_cookies = []
            for c in self._session.cookies:
                domain = c.domain
                # BROADCAST: Apply to all relevant variations to satisfy sub-service checks
                target_domains = [domain]
                if "empower-retirement.com" in domain:
                    target_domains.extend([".empower-retirement.com", "participant.empower-retirement.com", ".participant.empower-retirement.com"])
                elif "personalcapital.com" in domain:
                    target_domains.extend([".personalcapital.com", "home.personalcapital.com", ".home.personalcapital.com"])
                
                for d in set(target_domains):
                    pw_cookies.append({
                        "name": c.name,
                        "value": c.value,
                        "domain": d,
                        "path": c.path if c.path else "/",
                    })
            context.add_cookies(pw_cookies)
            
            page = context.new_page()
            page.set_default_timeout(120_000)

            csrf_val = self._csrf if self._csrf else "null"

            # Navigate to base
            logging.error(f"DEBUG: Playwright isolated attempt for {self._api_base} ...")
            try:
                # Add cookies BEFORE anything else
                context.add_cookies(pw_cookies)

                # INJECT TOKENS INTO WINDOW
                page.add_init_script(f"""
                    window.pe_csrf = '{csrf_val}';
                    if (window.context) window.context.csrf = '{csrf_val}';
                    window.CSRF_TOKEN = '{csrf_val}';
                """)

                page.goto(f"{self._api_base}/participant/#/summary", wait_until="commit")
            except Exception as e:
                logging.error(f"DEBUG: Load failed: {e}")

            # Wait for page to load, but fail fast on Cloudflare blocks
            max_attempts = 15
            cloudflare_count = 0
            for i in range(max_attempts):
                try:
                    content = page.content()
                    url = page.url
                    is_blocked = any(x in content for x in ["Just a moment", "challenging-balancer", "Ray ID", "Checking your browser", "Verify you are human"]) or "waiting-room" in url
                    is_error = "generic-error" in url or "generic-error" in content
                    is_in_app = any(x in content.lower() for x in ["total balance", "account name", "my accounts", "plan summary", "pwsjsessionid", "market value"])

                    if is_blocked:
                        cloudflare_count += 1
                        logging.error(f"DEBUG: Blocked by Cloudflare (attempt {i+1})")
                        if cloudflare_count >= 3:
                            browser.close()
                            raise RuntimeError(
                                "Cloudflare is blocking requests from this server. "
                                "Your session cookies are valid but need to be refreshed. "
                                "Please re-export cookies from your browser and import them again."
                            )
                        page.wait_for_timeout(5000)
                    elif is_error:
                        logging.error(f"DEBUG: Generic error! Forcing summary reload...")
                        page.goto(f"{self._api_base}/participant/#/summary", wait_until="commit")
                        context.add_cookies(pw_cookies)
                        page.wait_for_timeout(5000)
                    elif is_in_app:
                        logging.error(f"DEBUG: Success! App reached: {url}")
                        break
                    else:
                        logging.error(f"DEBUG: Neutral ({url}) (attempt {i+1})")
                        page.wait_for_timeout(3000)
                except RuntimeError:
                    raise
                except Exception:
                    page.wait_for_timeout(2000)

            # LATEST TOKEN EXTRACTION
            current_cookies = context.cookies()
            latest_csrf = ""
            for c in current_cookies:
                if c["name"].lower() in ("x-csrf-token", "csrf", "xsrf-token", "xsrftoken"):
                    latest_csrf = c["value"]
                    break
            
            if not latest_csrf:
                try:
                    latest_csrf = page.evaluate("window.pe_csrf || (window.context && window.context.csrf) || window.CSRF_TOKEN || ''")
                except Exception: pass
            
            if latest_csrf:
                self._csrf = latest_csrf

            csrf_val = self._csrf if self._csrf else "null"
            payload = {"lastServerChangeId": "-1", "csrf": csrf_val, "apiClient": "WEB"}
            if extra:
                payload.update(extra)

            api_url = f"{self._api_base.rstrip('/')}/api{path}"
            logging.error(f"DEBUG: Executing browser fetch for {api_url} ...")
            
            # Execute fetch INSIDE the page context to inherit everything
            script = f"""
                async () => {{
                    try {{
                        const resp = await fetch('{api_url}', {{
                            method: 'POST',
                            headers: {{ 
                                'Content-Type': 'application/x-www-form-urlencoded',
                                'X-Client-Type': 'WEB',
                                'X-CSRF-TOKEN': '{csrf_val}',
                                'Accept': 'application/json, text/plain, */*'
                            }},
                            body: new URLSearchParams({json.dumps(payload)})
                        }});
                        const text = await resp.text();
                        return {{ status: resp.status, text: text }};
                    }} catch (e) {{
                        return {{ status: 0, text: e.message }};
                    }}
                }}
            """
            eval_result = page.evaluate(script)
            status = eval_result["status"]
            text = eval_result["text"]
            logging.error(f"DEBUG: Browser fetch status={status}")
            
            if status == 403 and "Just a moment" in text:
                raise RuntimeError("Cloudflare bypass failed.")

            try:
                result = json.loads(text)
            except Exception:
                raise RuntimeError(f"Empower returned non-JSON response ({status}): {text[:100]}")
            
            # Update CSRF from response
            new_csrf = result.get("spHeader", {}).get("csrf", "")
            if new_csrf:
                self._csrf = new_csrf
                
            browser.close()
            return result

    def get_accounts(self) -> List[Dict]:
        import sys
        result = self._api_post("/newaccount/getAccounts")
        print(f"DEBUG: raw getAccounts response: {json.dumps(result)[:500]}...", file=sys.stderr)
        accounts = result.get("spData", {}).get("accounts", [])
        print(f"DEBUG: Found {len(accounts)} raw accounts", file=sys.stderr)
        return _normalize_accounts(accounts)

    def get_transactions(self, start_date: str, end_date: str) -> List[Dict]:
        import sys
        all_txns: List[Dict] = []
        page_num = 0
        rows = 100
        while True:
            result = self._api_post("/transaction/getUserTransactions", {
                "startDate": start_date,
                "endDate": end_date,
                "sort_cols": "transactionTime",
                "sort_rev": "true",
                "page": page_num,
                "rows_per_page": rows,
                "component": "DATAGRID",
            })
            if page_num == 0:
                print(f"DEBUG: raw getUserTransactions response (page 0): {json.dumps(result)[:500]}...", file=sys.stderr)
            batch = result.get("spData", {}).get("transactions", [])
            print(f"DEBUG: Found {len(batch)} transactions on page {page_num}", file=sys.stderr)
            all_txns.extend(batch)
            if len(batch) < rows:
                break
            page_num += 1
        return _normalize_transactions(all_txns)


# Module-level singleton
_client: Optional[EmpowerClient] = None


def get_client() -> EmpowerClient:
    global _client
    if _client is None:
        _client = EmpowerClient()
    return _client
