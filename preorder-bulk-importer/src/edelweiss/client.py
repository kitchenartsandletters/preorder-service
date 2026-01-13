from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from ..config import EdelweissConfig, PlaywrightConfig, Paths
from ..reporting.run_report import RunContext
from . import selectors as S


@dataclass
class EdelweissSession:
    browser: Browser
    context: BrowserContext
    page: Page


class EdelweissClient:
    def __init__(
        self,
        cfg: EdelweissConfig,
        pw_cfg: PlaywrightConfig,
        paths: Paths,
        run_ctx: RunContext,
    ) -> None:
        self.cfg = cfg
        self.pw_cfg = pw_cfg
        self.paths = paths
        self.run_ctx = run_ctx
        self._pw = None
        self._session: Optional[EdelweissSession] = None

    # ----------------------------
    # Lifecycle
    # ----------------------------
    def __enter__(self) -> "EdelweissClient":
        self._pw = sync_playwright().start()
        browser = self._pw.chromium.launch(
            headless=not self.pw_cfg.headful,
            slow_mo=self.pw_cfg.slowmo_ms if self.pw_cfg.slowmo_ms > 0 else None,
        )
        context = browser.new_context()
        context.set_default_timeout(self.pw_cfg.timeout_ms)

        if self.pw_cfg.trace:
            context.tracing.start(screenshots=True, snapshots=True, sources=False)

        page = context.new_page()
        self._session = EdelweissSession(browser=browser, context=context, page=page)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._session and self.pw_cfg.trace:
            trace_path = self.paths.playwright_dir / f"trace_{self.run_ctx.run_id}.zip"
            try:
                self._session.context.tracing.stop(path=str(trace_path))
            except Exception:
                pass

        if self._session:
            try:
                self._session.context.close()
            except Exception:
                pass
            try:
                self._session.browser.close()
            except Exception:
                pass

        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass

    @property
    def page(self) -> Page:
        if not self._session:
            raise RuntimeError("EdelweissClient session not started.")
        return self._session.page

    # ----------------------------
    # Reliability helpers
    # ----------------------------
    def wait_visible(self, selector: str, timeout_ms: Optional[int] = None) -> None:
        self.page.wait_for_selector(selector, state="visible", timeout=timeout_ms or self.pw_cfg.timeout_ms)

    def safe_click(self, selector: str, timeout_ms: Optional[int] = None, retries: int = 2) -> None:
        last_exc = None
        for _ in range(retries + 1):
            try:
                self.wait_visible(selector, timeout_ms=timeout_ms)
                self.page.locator(selector).click()
                return
            except Exception as e:
                last_exc = e
                time.sleep(0.5)
        raise last_exc  # type: ignore

    def screenshot(self, isbn13: str, stage: str) -> str:
        out_dir = self.paths.logs_dir / "runs" / self.run_ctx.run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"{isbn13}_{stage}.png"
        try:
            self.page.screenshot(path=str(p), full_page=True)
        except Exception:
            # best effort
            pass
        return str(p)

    # ----------------------------
    # Core flows
    # ----------------------------
    def goto_home(self) -> None:
        self.page.goto(self.cfg.base_url, wait_until="domcontentloaded")

    def login(self) -> None:
        """
        Log into Edelweiss using explicit, verified selectors.

        If the login form is not visible, assume the session is already authenticated.
        """
        self.goto_home()
        page = self.page

        email_sel = 'input[name="email"]'
        password_sel = 'input[name="pword"]'
        submit_sel_candidates = [
            'button[type="submit"]',
            'button:has-text("Sign In")',
            'button:has-text("Log In")',
        ]

        # Check whether login form is visible
        try:
            page.wait_for_selector(email_sel, state="visible", timeout=5000)
        except Exception:
            # Login form not present → already logged in
            return

        # Ensure password field is visible
        try:
            page.wait_for_selector(password_sel, state="visible", timeout=5000)
        except Exception:
            self.screenshot("login", "password_not_visible")
            raise RuntimeError("Password field not visible on Edelweiss login page.")

        # Fill credentials
        try:
            page.fill(email_sel, self.cfg.user)
            page.fill(password_sel, self.cfg.password)
        except Exception:
            self.screenshot("login", "fill_failed")
            raise

        # Submit form
        for sel in submit_sel_candidates:
            if page.locator(sel).count() > 0:
                try:
                    page.locator(sel).first.click()
                    page.wait_for_load_state("networkidle")
                    return
                except Exception:
                    continue

        # If we get here, no submit button worked
        self.screenshot("login", "submit_not_found")
        raise RuntimeError("Could not find login submit button on Edelweiss page.")

    def search_isbn_and_open_title(self, isbn13: str) -> Tuple[Optional[str], bool]:
        """
        Returns: (url, opened)
        opened means we've reached a state where the title card is present.
        """
        self.goto_home()

        # Search field selector is not provided; we use heuristic.
        # If it fails, anomaly will capture screenshot for selector refinement.
        search_candidates = [
            "input[placeholder*='Search']",
            "input[type='search']",
            "input#search",
            "input[name='search']",
        ]
        search_sel = next((s for s in search_candidates if self.page.locator(s).count() > 0), None)
        if not search_sel:
            return (self.page.url, False)

        self.page.locator(search_sel).fill(isbn13)
        self.page.keyboard.press("Enter")

        # Wait for results to render; then click into the title modal/card.
        # Your snippet shows #title-actions-button.
        try:
            self.wait_visible(S.TITLE_LINK_ID)
            self.safe_click(S.TITLE_LINK_ID)
            # Wait for core biblio to appear
            self.wait_visible(S.BIBLIO_BLOCK)
            return (self.page.url, True)
        except Exception:
            return (self.page.url, False)