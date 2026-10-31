import hashlib
from datetime import timedelta

from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone
from households.models import Invitation
from playwright.async_api import expect, async_playwright


async def accept_confirmation(dialog):
    await dialog.accept()


async def dismiss_confirmation(dialog):
    await dialog.dismiss()


async def sign_up_and_open_household(base_url):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{base_url}/signup/")
            await expect(
                page.get_by_role("heading", name="Create your HomeCycle account")
            ).to_be_visible()

            await page.get_by_label("Username").fill("maki-caregiver")
            await page.get_by_label("Password", exact=True).fill("Sensible-care-pass-42!")
            await page.get_by_label("Confirm password").fill("Sensible-care-pass-42!")
            await page.get_by_role("button", name="Create account").click()

            await expect(page).to_have_url(f"{base_url}/")
            await expect(page.get_by_role("heading", name="Tasks", exact=True)).to_be_visible()
            await expect(
                page.get_by_role("link", name="Tasks", exact=True)
            ).to_have_attribute("aria-current", "page")
            await expect(page.get_by_role("link", name="Calendar")).to_be_visible()
            await expect(
                page.get_by_role("link", name="Household members")
            ).to_be_visible()
            await expect(page.get_by_role("link", name="+ Add task")).to_be_visible()
            await expect(page.get_by_text("Your household", exact=True)).to_be_visible()
            await expect(page.get_by_text("No tasks yet")).to_be_visible()
        finally:
            await browser.close()


async def anonymous_schedule_redirects_to_sign_in(base_url):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{base_url}/")
            await expect(page.get_by_role("heading", name="Sign in to HomeCycle")).to_be_visible()
        finally:
            await browser.close()


async def signup_exposes_installable_pwa(base_url):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{base_url}/signup/")
            manifest_path = await page.locator('link[rel="manifest"]').get_attribute("href")
            assert manifest_path is not None

            response = await page.request.get(f"{base_url}{manifest_path}")
            assert response.ok
            manifest = await response.json()
            assert manifest["name"] == "HomeCycle"
            assert manifest["display"] == "standalone"
            icon_sizes = {
                size
                for icon in manifest["icons"]
                for size in icon["sizes"].split()
            }
            assert {"192x192", "512x512"} <= icon_sizes

            await page.wait_for_function(
                "navigator.serviceWorker && navigator.serviceWorker.getRegistration()"
                ".then((registration) => Boolean(registration))"
            )
        finally:
            await browser.close()


async def create_task_archive_and_restore(base_url):
    today = timezone.localdate()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{base_url}/signup/")
            await page.get_by_label("Username").fill("task-manager")
            await page.get_by_label("Password", exact=True).fill("Sensible-care-pass-42!")
            await page.get_by_label("Confirm password").fill("Sensible-care-pass-42!")
            await page.get_by_role("button", name="Create account").click()

            await page.get_by_role("link", name="Add your first task").click()
            await expect(page.get_by_role("heading", name="Add a recurring task")).to_be_visible()
            await page.get_by_label("Task name").fill("Maki bath")
            await page.get_by_label("Target interval in days").fill("7")
            await page.get_by_role("button", name="Save task").click()

            task = page.get_by_role("article", name="Maki bath")
            await expect(task).to_contain_text("Not logged yet")
            await expect(task).to_contain_text("Target: every 7 days")
            await task.get_by_role("link", name="Edit task").click()
            await page.get_by_label("Target interval in days").fill("10")
            await page.get_by_role("button", name="Save changes").click()
            task = page.get_by_role("article", name="Maki bath")
            await expect(task).to_contain_text("Target: every 10 days")
            await task.get_by_role("link", name="View history").click()
            await page.get_by_role("link", name="Log completion").click()
            await page.get_by_role("button", name="Save completion").click()
            await page.get_by_role("link", name="Back to tasks").click()
            task = page.get_by_role("article", name="Maki bath")

            await task.get_by_role("button", name="Archive task").click()
            await expect(page.get_by_role("heading", name="No tasks yet")).to_be_visible()
            await page.get_by_role("link", name="Archived tasks").click()
            archived_task = page.get_by_role("article", name="Maki bath")
            await expect(archived_task).to_be_visible()
            await archived_task.get_by_role("button", name="Restore task").click()
            await expect(page.get_by_role("article", name="Maki bath")).to_be_visible()
            restored = page.get_by_role("article", name="Maki bath")
            await restored.get_by_role("link", name="View history").click()
            await expect(
                page.get_by_role("article", name=f"Completion {today.isoformat()}")
            ).to_be_visible()
        finally:
            await browser.close()

async def log_correct_and_undo_completion(base_url):
    today = timezone.localdate()
    older = (today - timedelta(days=5)).isoformat()
    corrected = (today - timedelta(days=2)).isoformat()
    future = (today + timedelta(days=1)).isoformat()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            page.set_default_timeout(5000)
            await page.goto(f"{base_url}/signup/")
            await page.get_by_label("Username").fill("history-caregiver")
            await page.get_by_label("Password", exact=True).fill("Sensible-care-pass-42!")
            await page.get_by_label("Confirm password").fill("Sensible-care-pass-42!")
            await page.get_by_role("button", name="Create account").click()

            await page.get_by_role("link", name="Add your first task").click()
            await page.get_by_label("Task name").fill("Maki bath")
            await page.get_by_label("Target interval in days").fill("7")
            await page.get_by_role("button", name="Save task").click()
            await page.get_by_role("link", name="View history").click()

            await page.get_by_role("link", name="Log completion").click()
            date_field = page.get_by_label("Completion date")
            await expect(date_field).to_have_value(today.isoformat())
            await expect(
                page.get_by_label("Completed by").locator("option:checked")
            ).to_have_text("history-caregiver")
            await page.get_by_role("button", name="Save completion").click()

            await page.get_by_role("link", name="Log completion").click()
            await page.get_by_label("Completion date").fill(older)
            await page.get_by_label("Completed by").select_option("")
            await page.get_by_role("button", name="Save completion").click()
            dates = await page.locator(".history-list time").evaluate_all(
                "(nodes) => nodes.map((node) => node.getAttribute('datetime'))"
            )
            assert dates == [today.isoformat(), older]

            completion = page.get_by_role("article", name=f"Completion {today.isoformat()}")
            await completion.get_by_role("link", name="Correct completion").click()
            await page.get_by_label("Completion date").fill(corrected)
            await page.get_by_label("Completed by").select_option("")
            await page.get_by_role("button", name="Save correction").click()
            corrected_entry = page.get_by_role("article", name=f"Completion {corrected}")
            await expect(corrected_entry).to_contain_text("Unattributed")
            await corrected_entry.get_by_role("button", name="Undo correction").click()
            restored_entry = page.get_by_role("article", name=f"Completion {today.isoformat()}")
            await expect(restored_entry).to_contain_text("history-caregiver")

            await page.get_by_role("link", name="Log completion").click()
            await page.get_by_label("Completion date").fill(today.isoformat())
            await page.get_by_role("button", name="Save completion").click()
            await expect(page.get_by_text("A completion already exists on this date.")).to_be_visible()

            await page.get_by_label("Completion date").evaluate(
                "(input) => input.removeAttribute('max')"
            )
            await page.get_by_label("Completion date").fill(future)
            await page.get_by_role("button", name="Save completion").click()
            await expect(page.get_by_text("Completion date cannot be in the future.")).to_be_visible()
        finally:
            await browser.close()


async def cadence_summaries_and_schedule_order(base_url):
    today = timezone.localdate()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            page.set_default_timeout(5000)
            await page.goto(f"{base_url}/signup/")
            await page.get_by_label("Username").fill("cadence-caregiver")
            await page.get_by_label("Password", exact=True).fill("Sensible-care-pass-42!")
            await page.get_by_label("Confirm password").fill("Sensible-care-pass-42!")
            await page.get_by_role("button", name="Create account").click()

            task_count = 0

            async def create_task(name, interval):
                nonlocal task_count
                add_link = "Add your first task" if task_count == 0 else "Add a task"
                await page.get_by_role("link", name=add_link).click()
                await page.get_by_label("Task name").fill(name)
                if interval is not None:
                    await page.get_by_label("Target interval in days").fill(str(interval))
                await page.get_by_role("button", name="Save task").click()
                task_count += 1

            async def add_completion(name, day):
                card = page.get_by_role("article", name=name)
                await card.get_by_role("link", name="View history").click()
                await page.get_by_role("link", name="Log completion").click()
                await page.get_by_label("Completion date").fill(day.isoformat())
                await page.get_by_role("button", name="Save completion").click()
                await page.get_by_role("link", name="Back to tasks").click()

            await create_task("Never routine", 7)
            await expect(
                page.get_by_role("article", name="Never routine")
            ).to_contain_text("Not logged yet")
            await create_task("Cadence routine", 4)
            await add_completion("Cadence routine", today - timedelta(days=16))
            await add_completion("Cadence routine", today - timedelta(days=10))
            cadence = page.get_by_role("article", name="Cadence routine")
            await expect(cadence).to_contain_text("Not enough history (2 dates)")
            await expect(cadence).to_contain_text("Done 10 days ago")
            await expect(cadence).to_contain_text(
                f"Last done {(today - timedelta(days=10)).strftime('%b %-d, %Y')}"
            )
            await add_completion("Cadence routine", today - timedelta(days=6))
            cadence = page.get_by_role("article", name="Cadence routine")
            await expect(cadence).to_contain_text("Done 6 days ago")
            await expect(cadence.locator(".task-cadence")).to_contain_text("5 days")
            await expect(cadence).to_contain_text(
                f"Suggested next date: {(today - timedelta(days=2)).strftime('%b %-d, %Y')}"
            )

            await add_completion("Cadence routine", today)
            await expect(cadence).to_contain_text("Done today")
            cadence = page.get_by_role("article", name="Cadence routine")
            await expect(cadence.locator(".task-cadence")).to_contain_text("6 days")
            await expect(cadence).to_contain_text(
                f"Suggested next date: {(today + timedelta(days=4)).strftime('%b %-d, %Y')}"
            )

            await cadence.get_by_role("link", name="Edit task").click()
            await page.get_by_label("Target interval in days").fill("8")
            await page.get_by_role("button", name="Save changes").click()
            cadence = page.get_by_role("article", name="Cadence routine")
            await expect(cadence.locator(".task-cadence")).to_contain_text("6 days")
            await expect(cadence).to_contain_text(
                f"Suggested next date: {(today + timedelta(days=8)).strftime('%b %-d, %Y')}"
            )

            await create_task("Sooner routine", 1)
            await add_completion("Sooner routine", today)
            await create_task("No target routine", None)
            for day_offset in (12, 8, 3):
                await add_completion("No target routine", today - timedelta(days=day_offset))
            no_target = page.get_by_role("article", name="No target routine")
            await expect(no_target.locator(".task-cadence")).to_contain_text("4.5 days")
            await add_completion("No target routine", today - timedelta(days=1))
            no_target = page.get_by_role("article", name="No target routine")
            await expect(no_target).to_contain_text("Done 1 day ago")
            await expect(no_target.locator(".task-cadence")).to_contain_text("4 days")
            await expect(no_target).not_to_contain_text("Suggested next date")

            task_names = await page.locator(
                'section[aria-label="Active tasks"] article h2'
            ).all_text_contents()
            assert task_names == [
                "Never routine",
                "Sooner routine",
                "Cadence routine",
                "No target routine",
            ]

            await cadence.get_by_role("link", name="View history").click()
            latest = page.get_by_role(
                "article",
                name=f"Completion {today.isoformat()}",
            )
            await latest.get_by_role("link", name="Correct completion").click()
            corrected_date = today - timedelta(days=18)
            await page.get_by_label("Completion date").fill(corrected_date.isoformat())
            await page.get_by_role("button", name="Save correction").click()
            summary = page.get_by_role("region", name="Task summary")
            await expect(summary.locator(".task-cadence")).to_contain_text("4 days")
            await expect(summary).to_contain_text("Done 6 days ago")
            await expect(summary).to_contain_text(
                f"Last done {(today - timedelta(days=6)).strftime('%b %-d, %Y')}"
            )
            await expect(summary).to_contain_text(
                f"Suggested next date: {(today + timedelta(days=2)).strftime('%b %-d, %Y')}"
            )
            corrected_entry = page.get_by_role(
                "article",
                name=f"Completion {corrected_date.isoformat()}",
            )
            await corrected_entry.get_by_role("button", name="Undo correction").click()
            summary = page.get_by_role("region", name="Task summary")
            await expect(summary.locator(".task-cadence")).to_contain_text("6 days")
            await expect(summary).to_contain_text("Done today")
            await expect(summary).to_contain_text(
                f"Last done {today.strftime('%b %-d, %Y')}"
            )
            await expect(summary).to_contain_text(
                f"Suggested next date: {(today + timedelta(days=8)).strftime('%b %-d, %Y')}"
            )
            await page.get_by_role("link", name="Back to tasks").click()
            cadence = page.get_by_role("article", name="Cadence routine")
            await expect(cadence.locator(".task-cadence")).to_contain_text("6 days")
        finally:
            await browser.close()

async def household_invitations_sharing_and_removal(base_url):
    today = timezone.localdate()
    earlier = today - timedelta(days=1)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            owner = await browser.new_page()
            owner.on("dialog", accept_confirmation)
            await owner.goto(f"{base_url}/signup/")
            await owner.get_by_label("Username").fill("member-inviter")
            await owner.get_by_label("Password", exact=True).fill("Sensible-care-pass-42!")
            await owner.get_by_label("Confirm password").fill("Sensible-care-pass-42!")
            await owner.get_by_role("button", name="Create account").click()
            await owner.get_by_role("link", name="Add your first task").click()
            await owner.get_by_label("Task name").fill("Shared bath")
            await owner.get_by_label("Target interval in days").fill("7")
            await owner.get_by_role("button", name="Save task").click()
            await owner.get_by_role("link", name="View history").click()
            await owner.get_by_role("link", name="Log completion").click()
            await owner.get_by_role("button", name="Save completion").click()
            await owner.get_by_role("link", name="Back to tasks").click()

            await owner.get_by_role("link", name="Household members").click()
            await owner.get_by_role("button", name="Create invitation link").click()
            invite_url = await owner.locator(".invite-link a").get_attribute("href")
            assert invite_url is not None
            await expect(owner.get_by_text("expires in seven days")).to_be_visible()

            invitee = await browser.new_page()
            invitee.on("dialog", accept_confirmation)
            invitee.set_default_timeout(5000)
            response = await invitee.goto(invite_url)
            assert response is not None and response.status == 200
            await invitee.get_by_label("Username").fill("member-invitee")
            await invitee.get_by_label("Password", exact=True).fill("Sensible-care-pass-42!")
            await invitee.get_by_label("Confirm password").fill("Sensible-care-pass-42!")
            await invitee.get_by_role("button", name="Create account and join").click()
            shared_task = invitee.get_by_role("article", name="Shared bath")
            await expect(shared_task).to_be_visible()
            await shared_task.get_by_role("link", name="View history").click()
            await expect(
                invitee.get_by_role("article", name=f"Completion {today.isoformat()}")
            ).to_contain_text("Completed by member-inviter")
            await invitee.get_by_role("link", name="Log completion").click()
            await invitee.get_by_label("Completion date").fill(earlier.isoformat())
            await expect(
                invitee.get_by_label("Completed by").locator("option:checked")
            ).to_have_text("member-invitee")
            await invitee.get_by_role("button", name="Save completion").click()
            await expect(
                invitee.get_by_role("article", name=f"Completion {earlier.isoformat()}")
            ).to_contain_text("Completed by member-invitee")

            await invitee.get_by_role("link", name="Household members").click()
            await invitee.get_by_role("button", name="Create invitation link").click()
            await expect(invitee.locator(".invite-link a")).to_be_visible()
            invitee.remove_listener("dialog", accept_confirmation)
            invitee.once("dialog", dismiss_confirmation)
            await invitee.get_by_role("button", name="Revoke invitation").click()
            await expect(invitee.get_by_text("Active", exact=True)).to_be_visible()
            invitee.on("dialog", accept_confirmation)
            await invitee.get_by_role("button", name="Revoke invitation").click()
            await expect(invitee.get_by_text("Revoked", exact=True)).to_be_visible()

            reused_link_page = await browser.new_page()
            reused_response = await reused_link_page.goto(invite_url)
            assert reused_response is not None and reused_response.status == 410
            await expect(
                reused_link_page.get_by_role("heading", name="Invitation unavailable")
            ).to_be_visible()

            await owner.goto(f"{base_url}/members/")
            await owner.get_by_role("button", name="Create invitation link").click()
            revoked_url = await owner.locator(".invite-link a").get_attribute("href")
            assert revoked_url is not None
            await owner.get_by_role("button", name="Revoke invitation").click()
            await expect(owner.get_by_text("Revoked", exact=True)).to_have_count(2)
            revoked_response = await (await browser.new_page()).goto(revoked_url)
            assert revoked_response is not None and revoked_response.status == 410

            await owner.get_by_role("button", name="Create invitation link").click()
            expired_url = await owner.locator(".invite-link a").get_attribute("href")
            assert expired_url is not None
            token = expired_url.rstrip("/").rsplit("/", 1)[-1]
            digest = hashlib.sha256(token.encode()).hexdigest()
            updated = await sync_to_async(
                Invitation.objects.filter(token_digest=digest).update
            )(expires_at=timezone.now() - timedelta(seconds=1))
            assert updated == 1
            expired_response = await (await browser.new_page()).goto(expired_url)
            assert expired_response is not None and expired_response.status == 410
            await owner.reload()
            await expect(
                owner.get_by_text("Expired", exact=True)
            ).to_be_visible()

            await owner.get_by_role("button", name="Remove member-invitee").click()
            await expect(owner.locator(".member-list")).not_to_contain_text("member-invitee")
            await owner.get_by_role("link", name="Back to tasks").click()
            await owner.get_by_role("link", name="View history").click()
            await expect(
                owner.get_by_role("article", name=f"Completion {earlier.isoformat()}")
            ).to_contain_text("Completed by member-invitee")
            removed_response = await invitee.goto(f"{base_url}/")
            assert removed_response is not None and removed_response.status == 404
        finally:
            await browser.close()


async def household_calendar_shows_logged_completions(base_url):
    today = timezone.localdate()
    first_day_of_month = today.replace(day=1)
    previous_month = (first_day_of_month - timedelta(days=1)).replace(day=1)
    first_completion = previous_month + timedelta(days=4)
    second_completion = previous_month + timedelta(days=10)
    suggested_date = second_completion + timedelta(days=3)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            page.set_default_timeout(5000)
            await page.goto(f"{base_url}/signup/")
            await page.get_by_label("Username").fill("calendar-caregiver")
            await page.get_by_label("Password", exact=True).fill("Sensible-care-pass-42!")
            await page.get_by_label("Confirm password").fill("Sensible-care-pass-42!")
            await page.get_by_role("button", name="Create account").click()

            task_count = 0

            async def create_task(name, interval=None):
                nonlocal task_count
                add_link = "Add your first task" if task_count == 0 else "Add a task"
                await page.get_by_role("link", name=add_link).click()
                await page.get_by_label("Task name").fill(name)
                if interval:
                    await page.get_by_label("Target interval in days").fill(str(interval))
                await page.get_by_role("button", name="Save task").click()
                task_count += 1

            async def log_completion(name, completed_on):
                task = page.get_by_role("article", name=name)
                await task.get_by_role("link", name="View history").click()
                await page.get_by_role("link", name="Log completion").click()
                await page.get_by_label("Completion date").fill(completed_on.isoformat())
                await page.get_by_role("button", name="Save completion").click()
                await expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
                await page.get_by_role("link", name="Back to tasks").click()

            await create_task("Plant care", interval=3)
            await create_task("Bedding wash")
            await log_completion("Plant care", first_completion)
            await log_completion("Bedding wash", first_completion)
            await log_completion("Plant care", second_completion)
            await page.get_by_role("article", name="Bedding wash").get_by_role(
                "button", name="Archive task"
            ).click()
            await page.get_by_role("link", name="Calendar").click()
            await expect(page.get_by_role("heading", name="Calendar")).to_be_visible()
            await expect(page.locator(".calendar-modes")).to_have_count(0)
            await expect(page.locator(".calendar-grid")).to_be_visible()
            await expect(page.locator(".calendar-grid .calendar-entry")).to_have_count(0)
            await page.get_by_role("link", name="Previous month").click()
            await expect(
                page.get_by_role("heading", name=previous_month.strftime("%B %Y"))
            ).to_be_visible()

            calendar_grid = page.locator(".calendar-grid")
            await expect(calendar_grid.get_by_role("link", name="Plant care")).to_have_count(3)
            await expect(calendar_grid.get_by_role("link", name="Bedding wash")).to_have_count(1)
            archived_entry = calendar_grid.get_by_role("link", name="Bedding wash").locator("..")
            await expect(archived_entry.get_by_text("Archived task", exact=True)).to_be_visible()
            await expect(calendar_grid.get_by_text("Completed", exact=True)).to_have_count(3)
            await expect(calendar_grid.get_by_text("Suggested", exact=True)).to_have_count(1)
            await expect(
                calendar_grid.locator(f'td[data-date="{suggested_date.isoformat()}"]')
                .get_by_text("Suggested", exact=True)
            ).to_be_visible()
            blank_day = calendar_grid.locator(
                f'td[data-date="{previous_month.replace(day=2).isoformat()}"]'
            )
            await expect(blank_day.get_by_role("link")).to_have_count(0)
            await page.set_viewport_size({"width": 390, "height": 844})

            agenda = page.locator(".calendar-agenda")
            await expect(agenda).to_be_visible()
            await expect(agenda.get_by_role("link", name="Plant care")).to_have_count(3)
            await expect(agenda.get_by_role("link", name="Bedding wash")).to_have_count(1)
            archived_agenda_entry = agenda.get_by_role(
                "link", name="Bedding wash"
            ).locator("..")
            await expect(
                archived_agenda_entry.get_by_text("Archived task", exact=True)
            ).to_be_visible()
            await expect(calendar_grid).not_to_be_visible()
            await expect(agenda).to_be_visible()
            await agenda.get_by_role("link", name="Plant care").first.click()
            await expect(page.get_by_role("heading", name="Plant care", exact=True)).to_be_visible()
            await page.set_viewport_size({"width": 1280, "height": 800})

            await page.get_by_role("link", name="Calendar").click()
            await page.get_by_role("link", name="Next month").click()
            next_month = (first_day_of_month.replace(day=28) + timedelta(days=4)).replace(day=1)
            await expect(
                page.get_by_role("heading", name=next_month.strftime("%B %Y"))
            ).to_be_visible()
            await expect(page.get_by_text("No completions or suggested dates in")).to_be_visible()
            await expect(page.locator(".calendar-grid .calendar-entry")).to_have_count(0)
        finally:
            await browser.close()

async def global_calendar_filters_classified_tasks_and_keeps_undated_tasks(base_url):
    today = timezone.localdate()
    suggested_date = today + timedelta(days=1)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            page.set_default_timeout(5000)
            await page.goto(f"{base_url}/signup/")
            await page.get_by_label("Username").fill("global-calendar-user")
            await page.get_by_label("Password", exact=True).fill("Sensible-care-pass-42!")
            await page.get_by_label("Confirm password").fill("Sensible-care-pass-42!")
            await page.get_by_role("button", name="Create account").click()

            task_count = 0

            async def create_task(name, category="", subcategory="", interval=None):
                nonlocal task_count
                add_link = "Add your first task" if task_count == 0 else "Add a task"
                await page.get_by_role("link", name=add_link).click()
                await page.get_by_label("Task name").fill(name)
                await page.get_by_label("Category", exact=True).fill(category)
                await page.get_by_label("Subcategory", exact=True).fill(subcategory)
                if interval:
                    await page.get_by_label("Target interval in days").fill(str(interval))
                await page.get_by_role("button", name="Save task").click()
                task_count += 1

            await create_task("Maki bath", "Dogs", "Bath", interval=1)
            await create_task("Maki vet", "Dogs", "Veterinary")
            await create_task("Motorcycle gas", "Motorcycle", "Gas")
            await create_task("Unsorted task")

            task = page.get_by_role("article", name="Maki vet")
            await task.get_by_role("link", name="Edit task").click()
            await expect(page.get_by_label("Category", exact=True)).to_have_value("Dogs")
            await expect(page.get_by_label("Subcategory", exact=True)).to_have_value("Veterinary")
            await page.get_by_label("Subcategory", exact=True).fill("Checkup")
            await page.get_by_role("button", name="Save changes").click()

            task = page.get_by_role("article", name="Maki bath")
            await task.get_by_role("link", name="View history").click()
            await page.get_by_role("link", name="Log completion").click()
            await page.get_by_label("Completion date").fill(today.isoformat())
            await page.get_by_role("button", name="Save completion").click()
            await page.get_by_role("link", name="Back to tasks").click()

            await page.get_by_role("link", name="Calendar").click()
            await expect(page.get_by_role("heading", name="Calendar")).to_be_visible()
            await expect(page.locator(".calendar-modes")).to_have_count(0)
            await expect(
                page.get_by_role("heading", name=today.strftime("%B %Y"))
            ).to_be_visible()
            await expect(page.locator(".task-filter-list")).to_be_visible()
            undated = page.locator(".undated-task-list")
            await expect(undated.get_by_role("link", name="Maki vet")).to_be_visible()
            await expect(undated.get_by_role("link", name="Motorcycle gas")).to_be_visible()
            await expect(undated.get_by_role("link", name="Unsorted task")).to_be_visible()

            selected_month = today
            same_month = suggested_date.replace(day=1) == today.replace(day=1)
            expected_bath_entries = 2 if same_month else 1
            calendar_grid = page.locator(".calendar-grid")
            today_cell = calendar_grid.locator(f'td[data-date="{today.isoformat()}"]')
            await expect(today_cell.get_by_role("link", name="Maki bath")).to_have_count(1)
            await expect(today_cell.get_by_text("Completed", exact=True)).to_be_visible()
            if not same_month:
                await page.get_by_role("link", name="Next month").click()
                selected_month = suggested_date
                await expect(
                    page.get_by_role("heading", name=selected_month.strftime("%B %Y"))
                ).to_be_visible()
            await expect(calendar_grid.get_by_role("link", name="Maki bath")).to_have_count(
                expected_bath_entries
            )
            await expect(calendar_grid.get_by_text("Suggested", exact=True)).to_have_count(1)
            await expect(calendar_grid.get_by_role("link", name="Maki vet")).to_have_count(0)

            category_filters = page.get_by_role("navigation", name="Filter by category")
            await category_filters.get_by_role("link", name="Dogs", exact=True).click()
            await expect(
                page.get_by_role("heading", name=selected_month.strftime("%B %Y"))
            ).to_be_visible()
            await expect(page.locator(".calendar-grid").get_by_role("link", name="Maki bath")).to_have_count(expected_bath_entries)
            await expect(undated.get_by_role("link", name="Maki vet")).to_be_visible()
            await expect(undated.get_by_role("link", name="Motorcycle gas")).to_have_count(0)

            subcategory_filters = page.get_by_role("navigation", name="Filter by subcategory")
            await subcategory_filters.get_by_role("link", name="Bath", exact=True).click()
            await expect(page.locator(".calendar-grid").get_by_role("link", name="Maki bath")).to_have_count(expected_bath_entries)
            await expect(page.locator(".undated-task-list").get_by_role("link", name="Maki vet")).to_have_count(0)

            task_filters = page.locator(".task-filter-list")
            await task_filters.get_by_role("link", name="Maki bath", exact=True).click()
            await expect(page.get_by_text("Showing Maki bath", exact=True)).to_be_visible()
            await expect(page.locator(".calendar-grid").get_by_role("link", name="Maki bath")).to_have_count(expected_bath_entries)

            await page.set_viewport_size({"width": 390, "height": 844})
            await page.get_by_role("link", name="Calendar").click()
            filters = page.locator("[data-calendar-filters]")
            assert await filters.evaluate("(node) => node.open") is False
            await page.locator(".calendar-filters > summary").click()
            await expect(page.locator(".task-filter-list")).to_be_visible()
            if not same_month:
                await page.get_by_role("link", name="Next month").click()
                await expect(
                    page.get_by_role("heading", name=suggested_date.strftime("%B %Y"))
                ).to_be_visible()
            await expect(page.locator(".calendar-grid")).not_to_be_visible()
            await expect(page.locator(".calendar-agenda").get_by_role("link", name="Maki bath")).to_have_count(expected_bath_entries)
            calendar_link = page.get_by_role("link", name="Calendar")
            calendar_box = await calendar_link.bounding_box()
            assert (
                calendar_box is not None
                and 0 <= calendar_box["x"]
                and calendar_box["x"] + calendar_box["width"] <= 390
            )
        finally:
            await browser.close()




class HomeCycleBrowserTests(StaticLiveServerTestCase):
    def test_new_member_can_create_account_and_open_empty_household(self):
        async_to_sync(sign_up_and_open_household)(self.live_server_url)
    def test_household_schedule_requires_sign_in(self):
        async_to_sync(anonymous_schedule_redirects_to_sign_in)(self.live_server_url)

    def test_public_pages_expose_installable_pwa_metadata(self):
        async_to_sync(signup_exposes_installable_pwa)(self.live_server_url)
    def test_member_can_manage_and_restore_recurring_tasks(self):
        async_to_sync(create_task_archive_and_restore)(self.live_server_url)
    def test_member_can_log_backdate_correct_and_undo_completions(self):
        async_to_sync(log_correct_and_undo_completion)(self.live_server_url)

    def test_task_cadence_summary_and_schedule_order(self):
        async_to_sync(cadence_summaries_and_schedule_order)(self.live_server_url)

    def test_invitation_sharing_expiry_revoke_and_member_removal(self):
        async_to_sync(household_invitations_sharing_and_removal)(self.live_server_url)
    def test_calendar_shows_logged_completions_and_month_navigation(self):
        async_to_sync(household_calendar_shows_logged_completions)(self.live_server_url)

    def test_global_calendar_filters_classified_tasks_and_keeps_undated_tasks(self):
        async_to_sync(
            global_calendar_filters_classified_tasks_and_keeps_undated_tasks
        )(self.live_server_url)
