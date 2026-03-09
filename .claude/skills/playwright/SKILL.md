---
name: playwright
description: Browser automation — scrape pages, fill forms, take screenshots, run E2E tests. Use when the user needs web automation, scraping JavaScript-rendered sites, or UI testing.
metadata: {"ccbot":{"emoji":"🎭","requires":{"bins":["uv"]}}}
---

# Playwright Skill

Uses Playwright via Python. No global install needed — `uv run` handles it.

## Setup (first time)

```bash
uv run --with playwright python3 -m playwright install chromium --with-deps
```

## Take Screenshot

```bash
uv run --with playwright python3 - <<'EOF'
from playwright.sync_api import sync_playwright
import sys

url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(url, wait_until="networkidle")
    page.screenshot(path="output/screenshot.png", full_page=True)
    browser.close()

print("Saved: output/screenshot.png")
EOF "https://github.com/trending"
```

## Scrape Page Content

```bash
uv run --with playwright python3 - <<'EOF'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://news.ycombinator.com")

    # Extract structured data
    items = page.evaluate("""
        () => Array.from(document.querySelectorAll('.athing')).slice(0, 10).map(el => ({
            title: el.querySelector('.titleline > a')?.innerText,
            url: el.querySelector('.titleline > a')?.href,
            points: el.nextElementSibling?.querySelector('.score')?.innerText
        }))
    """)

    for item in items:
        print(f"{item['points']} | {item['title']}")
        print(f"  {item['url']}")

    browser.close()
EOF
```

## Fill Form & Submit

```bash
uv run --with playwright python3 - <<'EOF'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)  # headless=False to see it
    page = browser.new_page()
    page.goto("https://example.com/login")

    page.fill("#username", "user@example.com")
    page.fill("#password", "secret")
    page.click("#submit")

    # Wait for navigation
    page.wait_for_url("**/dashboard")
    print("Logged in! URL:", page.url)

    page.screenshot(path="output/dashboard.png")
    browser.close()
EOF
```

## Scrape with Login Session

```bash
uv run --with playwright python3 - <<'EOF'
from playwright.sync_api import sync_playwright
import json, os

COOKIES_FILE = "playwright_cookies.json"

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()

    # Load saved cookies if they exist
    if os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE) as f:
            context.add_cookies(json.load(f))

    page = context.new_page()
    page.goto("https://example.com/dashboard")

    # Save cookies for next time
    with open(COOKIES_FILE, "w") as f:
        json.dump(context.cookies(), f)

    content = page.inner_text("main")
    print(content[:1000])
    browser.close()
EOF
```

## Wait for Dynamic Content

```bash
uv run --with playwright python3 - <<'EOF'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("https://example.com/spa")

    # Wait for specific element
    page.wait_for_selector(".data-table", timeout=10000)

    # Wait for network to be idle
    page.wait_for_load_state("networkidle")

    # Wait for specific text
    page.wait_for_function("document.title.includes('Loaded')")

    rows = page.locator("table tr").count()
    print(f"Found {rows} rows")
    browser.close()
EOF
```

## Run E2E Tests

```bash
# Write test file
cat > /tmp/test_app.py <<'TESTEOF'
from playwright.sync_api import Page, expect

def test_homepage(page: Page):
    page.goto("http://localhost:8080")
    expect(page.get_by_role("heading")).to_contain_text("Welcome")

def test_login(page: Page):
    page.goto("http://localhost:8080/login")
    page.fill("[name=email]", "test@example.com")
    page.fill("[name=password]", "password")
    page.click("[type=submit]")
    expect(page).to_have_url("**/dashboard")
TESTEOF

uv run --with pytest-playwright pytest /tmp/test_app.py -v
```

## Generate PDF from URL

```bash
uv run --with playwright python3 - <<'EOF'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("https://example.com/report", wait_until="networkidle")
    page.pdf(path="output/report.pdf", format="A4", print_background=True)
    browser.close()

print("Saved: output/report.pdf")
EOF
```

## Tips

- Use `headless=True` (default) for automation; `headless=False` to debug visually.
- `wait_until="networkidle"` ensures JS has finished loading.
- Save cookies to file for sessions that need login (avoid re-logging in).
- Screenshots and PDFs go to `output/` for Feishu delivery.
- For heavy scraping, add random delays: `page.wait_for_timeout(1000 + random.randint(0, 2000))`.
