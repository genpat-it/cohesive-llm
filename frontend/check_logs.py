import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        
        # Capture console messages
        page.on("console", lambda msg: print(f"CONSOLE {msg.type}: {msg.text}"))
        page.on("pageerror", lambda err: print(f"PAGE ERROR: {err}"))
        
        print("Loading page...")
        await page.goto("http://localhost:8085")
        await page.wait_for_timeout(2000)
        
        # Try to click the button
        print("Clicking example button...")
        try:
            await page.click(".example-btn", timeout=2000)
            await page.click("#sendMessageBtn", timeout=2000)
        except Exception as e:
            print(f"Click failed: {e}")
            
        await page.wait_for_timeout(1000)
        await browser.close()

asyncio.run(main())
