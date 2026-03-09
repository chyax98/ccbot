---
name: playwright
description: Browser automation via Playwright MCP — navigate, click, fill forms, take screenshots, scrape pages, run E2E tests. Use when the user needs web automation, testing, or scraping JavaScript-rendered sites.
metadata: {"ccbot":{"emoji":"🎭"}}
---

# Playwright Skill

Two modes available — prefer MCP tools (faster, no subprocess), fall back to Python scripts for complex flows.

## Mode 1: Playwright MCP Tools (preferred)

MCP server `playwright` is pre-configured. Tools are available directly — no setup needed.

### Navigate & Screenshot

```
browser_navigate(url="https://example.com")
browser_take_screenshot(filename="output/screenshot.png")
```

### Interact with Page

```
browser_snapshot()                          # get accessibility tree (use before clicking)
browser_click(element="Submit button", ref="<ref from snapshot>")
browser_type(element="Search input", ref="<ref>", text="hello world")
browser_fill(element="Email field", ref="<ref>", value="user@example.com")
browser_select_option(element="Dropdown", ref="<ref>", values=["option1"])
```

### Navigation & State

```
browser_navigate_back()
browser_navigate_forward()
browser_reload()
browser_wait_for_timeout(time=2000)        # wait 2 seconds
browser_wait_for(selector=".data-loaded")  # wait for element
```

### Extract Data

```
browser_snapshot()      # full accessibility tree — use this to find elements & extract text
browser_network_requests(includeStatic=false)   # inspect API calls
```

### Tabs & Dialogs

```
browser_tab_new(url="https://example.com")
browser_tab_list()
browser_tab_select(index=0)
browser_tab_close()
browser_handle_dialog(accept=true, promptText="confirm")
```

### Scroll & Hover

```
browser_scroll(coordinate=[0, 500], delta=[0, 300])
browser_hover(element="Menu item", ref="<ref>")
browser_drag(startElement="Handle", startRef="<ref>", endElement="Target", endRef="<ref2>")
```

### Keyboard & Mouse

```
browser_press_key(key="Enter")
browser_press_key(key="Control+A")
browser_mouse_move(coordinate=[100, 200])
```

### PDF from Page

```
browser_navigate(url="https://example.com/report")
browser_wait_for(selector=".report-loaded")
browser_pdf_save(filename="output/report.pdf")
```

## Typical Workflow

```
# 1. Open page
browser_navigate(url="https://app.example.com/login")

# 2. Inspect page structure
browser_snapshot()   # → get refs for elements

# 3. Fill & submit
browser_fill(element="Email", ref="ref123", value="user@example.com")
browser_fill(element="Password", ref="ref124", value="...")
browser_click(element="Login button", ref="ref125")

# 4. Wait & verify
browser_wait_for(selector=".dashboard")
browser_take_screenshot(filename="output/dashboard.png")

# 5. Extract data
browser_snapshot()   # read the accessibility tree for text/values
```

## Mode 2: Python Scripts (for complex multi-step flows)

Use when MCP tools are insufficient (e.g. persistent sessions, parallel tabs, custom waits).

### Install browsers (first time only)

```bash
uv run --with playwright python3 -m playwright install chromium --with-deps
```

### Scrape JavaScript-rendered page

```bash
uv run --with playwright python3 - <<'EOF'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://example.com", wait_until="networkidle")

    items = page.evaluate("""
        () => Array.from(document.querySelectorAll('.item')).map(el => ({
            title: el.querySelector('h2')?.innerText,
            url: el.querySelector('a')?.href
        }))
    """)

    for item in items:
        print(f"{item['title']} — {item['url']}")

    browser.close()
EOF
```

### Authenticated session with saved cookies

```bash
uv run --with playwright python3 - <<'EOF'
from playwright.sync_api import sync_playwright
import json, os

COOKIES_FILE = ".playwright_session.json"

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context()

    if os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE) as f:
            ctx.add_cookies(json.load(f))

    page = ctx.new_page()
    page.goto("https://app.example.com/dashboard")

    # Persist session
    with open(COOKIES_FILE, "w") as f:
        json.dump(ctx.cookies(), f)

    content = page.inner_text("main")
    print(content[:2000])
    browser.close()
EOF
```

### E2E Test

```bash
uv run --with pytest-playwright pytest tests/e2e/ -v --headed
```

## Tips

- Always call `browser_snapshot()` before clicking — it gives you element refs.
- Screenshots and PDFs go to `output/` for automatic Feishu delivery.
- MCP mode is stateful per session — browser stays open between tool calls.
- For scraping behind login, save cookies with Mode 2 then use Mode 1 for subsequent visits.
- Use `browser_network_requests()` to inspect what API calls a page makes — often easier than scraping the DOM.
