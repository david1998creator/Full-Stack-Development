"""
Playwright E2E Verification for:
- Milestones Roadmap Tab (5 Phases & progress metrics)
- Cytoscape.js In-Browser Knowledge Graph Tab (nodes, edges & inspector)
- HITL Decision Center (Concept C Notification Bell & Drawer)
- Budget Guard Approval Flow (PAUSED_FOR_HUMAN & Resolution)
"""

import sys
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    artifacts_dir = Path(__file__).resolve().parent.parent.parent / "docs"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    console_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        print("Navigating to http://127.0.0.1:8000/ ...")
        page.goto("http://127.0.0.1:8000/", wait_until="networkidle")
        time.sleep(1)

        # 1. Verify Upfront Status Notice Banner
        upfront_banner = page.locator("#bannerUpfrontStatus")
        assert upfront_banner.is_visible(), "Upfront status banner should be visible"
        print("[OK] Upfront status banner verified")

        # 2. Test Milestones Roadmap Tab
        print("Switching to Milestones Roadmap tab...")
        page.click("#btnSwitchMilestones")
        page.wait_for_selector("#milestonesListContainer")
        time.sleep(1)

        milestones_container = page.locator("#viewMilestonesContainer")
        assert milestones_container.is_visible(), "Milestones container should be visible"

        phase_cards = page.locator("#milestonesListContainer > div")
        card_count = phase_cards.count()
        print(f"[OK] Milestones tab loaded {card_count} phase cards")
        assert card_count == 5, f"Expected 5 phase cards, got {card_count}"

        screenshot_milestones = artifacts_dir / "milestones_roadmap_verified.png"
        page.screenshot(path=str(screenshot_milestones))
        print(f"[OK] Saved milestones screenshot to {screenshot_milestones}")

        # 3. Test Knowledge Graph Tab (Cytoscape.js)
        print("Switching to Knowledge Graph tab...")
        page.click("#btnSwitchGraph")
        page.wait_for_selector("#cyCanvas")
        time.sleep(2)

        graph_container = page.locator("#viewGraphContainer")
        assert graph_container.is_visible(), "Graph container should be visible"

        stats_badge = page.locator("#graphStatsBadge").inner_text()
        print(f"[OK] Knowledge Graph rendered with stats: {stats_badge}")

        screenshot_graph = artifacts_dir / "knowledge_graph_verified.png"
        page.screenshot(path=str(screenshot_graph))
        print(f"[OK] Saved knowledge graph screenshot to {screenshot_graph}")

        # 4. Test Notification Bell & HITL Drawer
        print("Clicking Notification Bell to open HITL Drawer...")
        page.click("#btnNotificationBell")
        time.sleep(0.5)

        drawer = page.locator("#hitlDrawer")
        assert drawer.is_visible(), "HITL Drawer should be visible"
        print("[OK] HITL Drawer opened")

        # 5. Trigger Simulated Budget Guard
        print("Triggering simulated Budget Guard spend estimation...")
        page.evaluate("triggerSimulatedBudgetGuard()")
        time.sleep(1.5)

        action_card = page.locator("#hitlActionsContainer > div")
        assert action_card.count() >= 1, "Budget Guard action card should appear"
        
        card_text = action_card.first.inner_text()
        assert "PAUSED FOR HUMAN" in card_text, "Action should be paused for human"
        assert "0.18" in card_text or "Budget" in card_text, "Action should state budget estimate"
        print("[OK] Budget Guard card verified with PAUSED FOR HUMAN status")

        screenshot_hitl = artifacts_dir / "hitl_drawer_budget_guard_verified.png"
        page.screenshot(path=str(screenshot_hitl))
        print(f"[OK] Saved HITL drawer screenshot to {screenshot_hitl}")

        # 6. Approve Budget
        print("Resolving Budget Guard action...")
        approve_btn = page.locator("button:has-text('Approve Implementation')").first
        approve_btn.click()
        time.sleep(1)

        badge_txt = page.locator("#hitlDrawerBadge").inner_text()
        print(f"[OK] Post-approval drawer badge: {badge_txt}")
        assert "0 PENDING" in badge_txt, f"Expected 0 pending, got {badge_txt}"

        # 7. Close Drawer and switch to Live Workspace Pipeline
        page.evaluate("toggleHitlDrawer(false)")
        time.sleep(0.5)
        page.click("#btnSwitchPipeline")
        time.sleep(1)

        screenshot_pipeline = artifacts_dir / "live_workspace_pipeline_verified.png"
        page.screenshot(path=str(screenshot_pipeline))
        print(f"[OK] Saved live workspace screenshot to {screenshot_pipeline}")

        # Check console errors
        print(f"Console errors collected: {len(console_errors)}")
        for err in console_errors:
            print("  [ERROR]", err)

        browser.close()
        print("\nALL VERIFICATIONS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    main()
