import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

async def run_verification():
    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)
    screenshot_path = docs_dir / "crew_inspector_unified_prompt_verified.png"
    artifact_screenshot_path = Path(r"C:\Users\User\.gemini\antigravity\brain\13c43420-a759-475b-bd37-501d89910b88\crew_inspector_unified_prompt_verified.png")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1600, "height": 1000})
        page = await context.new_page()
        page.on("console", lambda msg: print(f"    BROWSER LOG: {msg.text}"))
        page.on("pageerror", lambda err: print(f"    BROWSER ERROR: {err}"))

        print("[1] Navigating to http://127.0.0.1:8000/ ...")
        await page.goto("http://127.0.0.1:8000/", wait_until="networkidle")
        await asyncio.sleep(2)

        print("[2] Opening Crew Inspector...")
        btn = page.locator("#btnOpenCrewInspector")
        await btn.click()
        await asyncio.sleep(1.5)

        modal = page.locator("#crewInspectorModal")
        assert await modal.is_visible(), "Crew Inspector modal should be open"

        print("[3] Selecting Developer tab...")
        dev_tab = page.locator("#crewInspectorTabs button:has-text('Developer')")
        await dev_tab.click()
        await asyncio.sleep(1)

        print("[4] Verifying '+ Add Custom Skill for Role' button is removed from Card 2...")
        bottom_add_btn = page.locator("#btnAddSkillForRole")
        count_bottom = await bottom_add_btn.count()
        assert count_bottom == 0, "Duplicate bottom button in Card 2 must be removed"

        print("[5] Verifying Unified System Prompt preview on Card 1...")
        preview_el = page.locator("#cardSystemPromptPreview")
        preview_text = await preview_el.text_content()
        assert "=== DEVELOPER AGENT SYSTEM PROMPT ===" in preview_text, "Preview must show full unified system prompt"
        assert "Operational Rules:" in preview_text, "Preview must include operational rules"

        badge_el = page.locator("#promptStatusBadge")
        badge_text = await badge_el.text_content()
        assert "Canonical" in badge_text, f"Badge should initially be Canonical, got {badge_text}"

        print("[6] Opening Unified System Prompt Editor Drawer...")
        btn_prompt = page.locator("#btnViewPromptDetail")
        await btn_prompt.click()
        await asyncio.sleep(1)

        drawer = page.locator("#inspectorDetailDrawer")
        assert await drawer.is_visible(), "Slide-out detail drawer should be open"

        edit_container = page.locator("#promptEditContainer")
        assert await edit_container.is_visible(), "Prompt editor container should be visible"

        textarea = page.locator("#promptEditTextarea")
        textarea_val = await textarea.input_value()
        assert "=== DEVELOPER AGENT SYSTEM PROMPT ===" in textarea_val, "Textarea must contain full prompt"

        token_counter = page.locator("#promptEditTokens")
        token_text = await token_counter.text_content()
        print(f"    Live prompt tokens: {token_text}")

        print("[7] Modifying system prompt and saving override...")
        custom_directive = "\n\n=== USER OVERRIDE ===\nAlways prioritize asynchronous I/O and Pydantic v2 strict schemas."
        await textarea.fill(textarea_val + custom_directive)
        await asyncio.sleep(0.5)

        save_btn = page.locator("#btnSavePrompt")
        await save_btn.click()
        await asyncio.sleep(2)

        # Check badge on Card 1
        badge_after_save = await badge_el.text_content()
        print(f"    Badge after save: {badge_after_save}")
        assert "Custom Override" in badge_after_save, "Badge should reflect Custom Override after saving"

        print("[8] Resetting system prompt back to Canonical Default...")
        # Playwright dialog handler for confirm()
        page.on("dialog", lambda dialog: dialog.accept())
        reset_btn = page.locator("#btnResetPrompt")
        await reset_btn.click()
        await asyncio.sleep(2)

        badge_after_reset = await badge_el.text_content()
        print(f"    Badge after reset: {badge_after_reset}")
        assert "Canonical" in badge_after_reset, "Badge should revert to Canonical after reset"

        print("[9] Re-opening prompt editor for verified screenshot...")
        await btn_prompt.click()
        await asyncio.sleep(1)

        print("[10] Taking verified high-resolution screenshot...")
        await page.screenshot(path=str(screenshot_path), full_page=False)
        await page.screenshot(path=str(artifact_screenshot_path), full_page=False)
        print(f"    Saved screenshots to {screenshot_path} and {artifact_screenshot_path}")

        await browser.close()
        print("[SUCCESS] All Playwright E2E unified prompt & inspector checks passed flawlessly!")

if __name__ == "__main__":
    asyncio.run(run_verification())
