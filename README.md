# HomeCycle

HomeCycle is a shared-household progressive web app for seeing how long it has been since a care task was last done, and who optionally logged it.

## MVP behavior

- Create one household with individual accounts and equal member permissions.
- Set a recurring task with an optional positive whole-day target interval; see elapsed time since the last completion.
- Classify a task with an optional household category and subcategory by typing a label or reusing one. Existing and unclassified tasks keep working.
- Record one date-only completion per task per calendar day, with optional member attribution.
- Review logged completions and suggested dates together on the calendar at `/calendar/`. Each date is labeled Completed or Suggested; suggested dates are estimates, not deadlines. The desktop view uses a month grid and activity agenda; mobile uses the agenda. Archived-task completions remain visible and labeled, including when filtering by task, category, or subcategory. Find active tasks without a suggested date in the filtered undated list.
- Correct completions or undo the most recent correction first; summaries use the current valid completion dates.
- Show the last-completed date and elapsed time, the typical gap (median) after at least three dates, and an optional target-based suggested date. The suggestion adds the target interval to the last completion date; it is unavailable without either value or when the result exceeds December 31, 9999. Suggested dates are calculated, not stored; archived tasks have no suggestions on the calendar.
- Archive and restore tasks without losing their history.
- Invite members with private, single-use links that expire after seven days and can be revoked.

Reminders, attachments, time-of-day, offline editing, and multiple households per account are outside the MVP.

## Run locally

Requires Python 3.12 or newer.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Open `http://127.0.0.1:8000/` and create the first account. The app uses SQLite by default and stores its database in the repository root.

## Browser tests

The end-to-end suite uses Chromium through Playwright:

```sh
.venv/bin/playwright install chromium
.venv/bin/python manage.py test households.tests.test_e2e
```

The suite exercises the user-facing PWA with isolated test data, including two-account invitation and shared-history flows.
