import sys
import time

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765"
SCREENSHOT_DIR = "/tmp"

console_errors = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: console_errors.append(f"pageerror: {exc}"))

    page.goto(URL)
    page.wait_for_selector("#composer-input", timeout=15000)
    time.sleep(1)  # let WS connect + settle

    libs_ok = page.evaluate(
        "() => typeof window.marked !== 'undefined' && typeof window.hljs !== 'undefined'"
    )
    print("marked+hljs loaded:", libs_ok)

    page.screenshot(path=f"{SCREENSHOT_DIR}/step1_initial.png")

    # Send a markdown message and check rendering
    page.fill(
        "#composer-input",
        "Please reply with **exactly** this and nothing else:\n\nHere is code:\n```python\ndef f(x):\n    return x+1\n```",
    )
    page.click("#composer-send")
    page.wait_for_selector(".msg-assistant .status-badge.status-done", timeout=45000)
    time.sleep(0.5)
    page.screenshot(path=f"{SCREENSHOT_DIR}/step2_markdown_response.png")

    bold_present = page.evaluate("() => !!document.querySelector('.msg-assistant strong')")
    code_present = page.evaluate("() => !!document.querySelector('.msg-assistant pre code')")
    hljs_applied = page.evaluate("() => !!document.querySelector('.msg-assistant pre code.hljs')")
    text_block_present = page.evaluate("() => !!document.querySelector('.msg-assistant .text-block')")
    print(
        "bold rendered:",
        bold_present,
        "| code block rendered:",
        code_present,
        "| hljs class applied:",
        hljs_applied,
        "| text-block wrapper present:",
        text_block_present,
    )

    # XSS check: literal script tag in a user message must render as visible text, not execute
    page.fill("#composer-input", "STOPFIRST")
    page.click("#composer-stop") if page.is_visible("#composer-stop") else None
    time.sleep(0.3)

    xss_probe = (
        '<script>window.__xss_fired = true;</script> and <img src=x onerror="window.__xss_fired2=true">'
    )
    page.fill("#composer-input", xss_probe)
    page.click("#composer-send")
    time.sleep(1.5)  # user message renders immediately, don't need to wait for assistant
    xss_fired = page.evaluate("() => window.__xss_fired === true || window.__xss_fired2 === true")
    user_bubble_text = page.evaluate(
        "() => { const els = document.querySelectorAll('.msg-user .msg-bubble'); return els[els.length-1].textContent; }"
    )
    print("xss_fired:", xss_fired)
    print("user_bubble_text contains literal tags:", "<script>" in user_bubble_text)
    page.screenshot(path=f"{SCREENSHOT_DIR}/step3_xss_probe.png")

    # stop the still-running assistant turn from the xss probe message so shutdown is clean
    if page.is_visible("#composer-stop"):
        page.click("#composer-stop")
        time.sleep(0.5)

    print("console_errors:", console_errors)
    browser.close()

if console_errors:
    print("HAD CONSOLE ERRORS")
    sys.exit(1)
