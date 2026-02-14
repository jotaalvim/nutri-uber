#!/usr/bin/env python3
"""
Uber Eats integration: add basket to cart via Playwright.

Uses your existing Chrome (you're already logged in). Requires CHROME_CDP_URL.
Start Chrome with: open -a "Google Chrome" --args --remote-debugging-port=9222
"""

import os
import time
from typing import Any

from playwright.sync_api import sync_playwright

CHROME_CDP_URL = os.environ.get("CHROME_CDP_URL", "").strip()


def _get_browser(playwright):
    """Connect to user's Chrome via CDP."""
    if not CHROME_CDP_URL:
        raise ValueError(
            "CHROME_CDP_URL required. Start Chrome with --remote-debugging-port=9222, "
            "then set CHROME_CDP_URL=http://localhost:9222"
        )
    return playwright.chromium.connect_over_cdp(CHROME_CDP_URL)


def _get_default_context(browser):
    """Get user's default context (their Chrome profile, already logged in)."""
    for _ in range(10):
        if browser.contexts:
            return browser.contexts[0]
        time.sleep(0.2)
    return browser.new_context()


def add_basket_to_cart(
    store_url: str,
    items: list[dict[str, Any]],
    headless: bool = False,
    keep_open: bool = True,
) -> dict[str, Any]:
    """
    Add basket items to Uber Eats cart. Opens new tab in user's Chrome (already logged in).

    Returns { "added": n, "failed": [...], "message": "..." }
    """
    added = 0
    failed: list[str] = []

    with sync_playwright() as p:
        browser = _get_browser(p)
        context = _get_default_context(browser)
        page = context.new_page()

        try:
            page.goto(store_url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)

            try:
                accept_btn = page.get_by_role("button", name="Accept")
                if accept_btn.is_visible(timeout=2000):
                    accept_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            for _ in range(6):
                page.mouse.wheel(0, 600)
                time.sleep(0.5)

            # For shop_feed (grocery), try search first
            is_shop_feed = "shop_feed" in (store_url or "")
            if is_shop_feed:
                try:
                    search_el = page.get_by_placeholder("Search", exact=False).or_(
                        page.locator('input[type="search"], input[aria-label*="search" i]')
                    ).first
                    if search_el.is_visible(timeout=2000):
                        for item in items:
                            name = (item.get("name") or "").strip()
                            if not name:
                                continue
                            try:
                                search_el.fill(name[:40])
                                time.sleep(2)
                                clicked = page.evaluate("""
                                    (itemName) => {
                                        const name = (itemName || '').toLowerCase().trim();
                                        const titles = document.querySelectorAll('[data-testid="menu-item-title"], h3, h4, [class*="title"]');
                                        for (const t of titles) {
                                            const text = (t.innerText || t.textContent || '').toLowerCase();
                                            if (text.includes(name) || name.includes(text.slice(0, 20))) {
                                                const card = t.closest('a') || t.closest('li') || t.closest('[role="listitem"]') || t.parentElement;
                                                if (!card) continue;
                                                const btn = card.querySelector('button[aria-label*="add" i], button[aria-label*="adicionar" i], button:has-text("+")');
                                                if (btn) { btn.click(); return true; }
                                            }
                                        }
                                        return false;
                                    }
                                """, name[:50])
                                if clicked:
                                    added += 1
                                    time.sleep(1)
                                else:
                                    failed.append(name)
                                search_el.fill("")
                                time.sleep(1)
                            except Exception:
                                failed.append(name)
                        if added or failed:
                            return {"added": added, "failed": failed, "message": f"Added {added}/{len(items)} from shop feed."}
                except Exception:
                    pass

            for item in items:
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                try:
                    clicked = page.evaluate("""
                        (itemName) => {
                            const name = (itemName || '').toLowerCase().trim();
                            const titles = document.querySelectorAll('[data-testid="menu-item-title"]');
                            for (const t of titles) {
                                const text = (t.innerText || t.textContent || '').toLowerCase();
                                const firstLine = text.split('\\n')[0];
                                if (!name || text.includes(name) || firstLine.includes(name) || name.includes(firstLine.slice(0, 15))) {
                                    const card = t.closest('li') || t.closest('[role="listitem"]') || t.closest('[class*="item"]') || t.parentElement;
                                    if (!card) continue;
                                    const buttons = card.querySelectorAll('button, [role="button"]');
                                    for (const b of buttons) {
                                        const label = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').toLowerCase();
                                        if (/add|adicionar|\\+/.test(label)) {
                                            b.click();
                                            return true;
                                        }
                                    }
                                    return false;
                                }
                            }
                            return false;
                        }
                    """, name[:50])
                    if clicked:
                        added += 1
                        time.sleep(1)
                    else:
                        failed.append(name)
                except Exception:
                    failed.append(name)

            if keep_open and not headless:
                page.wait_for_timeout(300_000)
        finally:
            pass  # Don't close user's browser

    return {
        "added": added,
        "failed": failed,
        "message": f"Added {added}/{len(items)} items. Complete checkout in the browser." if added else f"Could not add items. {len(failed)} failed.",
    }
