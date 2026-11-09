# scripts/nyt_portal_recon.py
# Sunday recon: drive to the upload page in the UPLOADABLE state and capture
# the real DOM/selectors. Stops BEFORE uploading. Read-only.
import os, sys
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright

NYT_USER = os.environ["NYT_PORTAL_USERNAME"]
NYT_PASS = os.environ["NYT_PORTAL_PASSWORD"]

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    page = browser.new_context().new_page()

    page.goto("https://bestsellers.nytimes.com/login", wait_until="domcontentloaded")
    page.get_by_placeholder("Enter username").fill(NYT_USER)
    page.get_by_placeholder("Password").fill(NYT_PASS)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_load_state("domcontentloaded")   # not networkidle — portal keeps connections open
    print("logged in")

    page.get_by_role("link", name="Upload Spreadsheet").click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(3000)   # let the iframe render
    print("on upload page")

    page.screenshot(path="nyt_UPLOADABLE_state.png", full_page=True)
    print("screenshot -> nyt_UPLOADABLE_state.png")

    frame = page.locator("#spreadsheet_frame").content_frame

    # Does the 'already submitted' text appear? (should be ABSENT in uploadable state)
    print("already-submitted text present:",
          frame.get_by_text("already submitted", exact=False).count())

    # The file input we set the CSV on
    print("#filename count:", frame.locator("#filename").count())

    # Enumerate every button in the iframe — this is the key data:
    # the real name/state of the submit control when upload IS possible.
    btns = frame.locator("button, input[type=submit], input[type=button]")
    n = btns.count()
    print(f"buttons in iframe: {n}")
    for i in range(n):
        b = btns.nth(i)
        try:
            print(f"  [{i}] tag=", b.evaluate("el => el.tagName"),
                  "text=", (b.inner_text() or "").strip()[:60],
                  "value=", b.get_attribute("value"),
                  "name=", b.get_attribute("name"),
                  "id=", b.get_attribute("id"),
                  "visible=", b.is_visible())
        except Exception as e:
            print(f"  [{i}] <err {e}>")

    print("---- IFRAME BODY TEXT (first 800) ----")
    print(frame.locator("body").inner_text()[:800])

    print("\nBrowser stays open 5 min for manual inspection. Ctrl-C to exit early.")
    page.wait_for_timeout(300000)
    browser.close()