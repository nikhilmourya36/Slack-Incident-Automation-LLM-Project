"""
One-off diagnostic: what page is Playwright actually receiving?
Run this in Colab to see whether it's a real page, an Akamai block/
challenge page, or something else entirely.
"""
import asyncio
from playwright.async_api import async_playwright

URL = "https://www.realcanadiansuperstore.ca/en/search?search-bar=20188873_EA&storeId=1077"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            locale="en-CA",
            viewport={"width": 1280, "height": 800},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await context.new_page()
        response = await page.goto(URL, timeout=20000, wait_until="networkidle")
        await page.wait_for_timeout(1500)

        print("HTTP status:", response.status if response else "no response object")
        print("Final URL:", page.url)
        print("Page title:", await page.title())

        html = await page.content()
        print("HTML length:", len(html))
        print("Has __NEXT_DATA__:", "__NEXT_DATA__" in html)
        print()
        print("--- First 1500 chars of body ---")
        print(html[:1500])

        await page.screenshot(path="/content/debug_screenshot.png", full_page=True)
        print()
        print("Screenshot saved to /content/debug_screenshot.png")

        await browser.close()

asyncio.run(main())