"""Screenshot the Vigil console views for design review."""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent / "_shots"
OUT.mkdir(exist_ok=True)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1360, "height": 900}, device_scale_factor=2)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: (m.type == "error") and errs.append(m.text))
        await pg.goto("http://127.0.0.1:8800/", wait_until="networkidle")
        await pg.wait_for_timeout(700)
        await pg.screenshot(path=str(OUT / "v_console.png"))
        await pg.evaluate("openAlert('ALR-1042')")
        await pg.wait_for_timeout(300)
        await pg.screenshot(path=str(OUT / "v_alerts.png"))
        await pg.evaluate("showView('incidents')")
        await pg.wait_for_timeout(300)
        await pg.screenshot(path=str(OUT / "v_incidents.png"))
        await pg.evaluate("showView('agents')")
        await pg.wait_for_timeout(300)
        await pg.screenshot(path=str(OUT / "v_agents.png"))
        print("JS errors:", errs or "none")
        await b.close()
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
