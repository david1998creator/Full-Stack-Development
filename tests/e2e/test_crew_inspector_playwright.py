import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

async def run_verification():
    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)
    screenshot_path = docs_dir / "crew_inspector_verified.png"
    artifact_screenshot_path = Path(r"C:\Users\User\.gemini\antigravity\brain\13c43420-a759-475b-bd37-501d89910b88\crew_inspector_verified.png")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1600, "height": 1000})
        page = await context.new_page()

        print("[1] Navigating to http://127.0.0.1:8000/ ...")
        await page.goto("http://127.0.0.1:8000/", wait_until="networkidle")
        await asyncio.sleep(2)

        print("[2] Checking #btnOpenCrewInspector in sidebar...")
        btn = page.locator("#btnOpenCrewInspector")
        assert await btn.is_visible(), "Inspect Crew button should be visible in left execution roster"

        print("[3] Clicking Inspect Crew button...")
        await btn.click()
        await asyncio.sleep(1.5)

        modal = page.locator("#crewInspectorModal")
        assert await modal.is_visible(), "Crew Inspector modal should be open"

        print("[4] Checking tabs and selecting Developer role...")
        tabs = page.locator("#crewInspectorTabs button")
        tab_count = await tabs.count()
        print(f"    Found {tab_count} role tabs in inspector.")
        assert tab_count == 10, f"Expected 10 role tabs, got {tab_count}"

        # Click Developer tab
        dev_tab = page.locator("#crewInspectorTabs button:has-text('Developer')")
        await dev_tab.click()
        await asyncio.sleep(1)

        print("[5] Verifying 3 Cards are rendered...")
        card1 = page.locator("#cardGoal")
        card2 = page.locator("#cardSkillsList")
        card3 = page.locator("#cardPluginsList")

        assert await card1.is_visible(), "Card 1 (Goal/System Prompt) should be visible"
        assert await card2.is_visible(), "Card 2 (Skills List) should be visible"
        assert await card3.is_visible(), "Card 3 (Plugins List) should be visible"

        print("[6] Toggling skill switch...")
        label = page.locator("#cardSkillsList label").first
        checkbox = page.locator("#cardSkillsList input[type='checkbox']").first
        initial_checked = await checkbox.is_checked()
        await label.click(force=True)
        await asyncio.sleep(1)
        after_checked = await checkbox.is_checked()
        print(f"    Toggled skill: {initial_checked} -> {after_checked}")
        # Toggle back on
        await label.click(force=True)
        await asyncio.sleep(1)

        print("[7] Opening skill detail drawer for FastAPI Production...")
        # Click view instructions on custom skill
        view_buttons = page.locator("#cardSkillsList button:has-text('View Instructions')")
        count_btns = await view_buttons.count()
        print(f"    Found {count_btns} skills with instruction viewers.")
        # Click the custom skill (the last one or first one)
        await view_buttons.last.click(force=True)
        await asyncio.sleep(1)

        drawer = page.locator("#inspectorDetailDrawer")
        assert await drawer.is_visible(), "Detail drawer should be visible"

        drawer_content = page.locator("#inspectorDetailContent")
        content_text = await drawer_content.text_content()
        print(f"    Drawer content length: {len(content_text)} chars")
        assert len(content_text) > 20, "Drawer content should have valid instructions"

        print("[8] Taking verified screenshot...")
        await page.screenshot(path=str(screenshot_path), full_page=False)
        await page.screenshot(path=str(artifact_screenshot_path), full_page=False)
        print(f"    Saved screenshots to {screenshot_path} and {artifact_screenshot_path}")

        await browser.close()
        print("[SUCCESS] All Playwright E2E checks passed flawlessly!")

if __name__ == "__main__":
    asyncio.run(run_verification())
