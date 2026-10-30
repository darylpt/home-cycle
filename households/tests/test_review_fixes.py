from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import close_old_connections
from django.test import RequestFactory, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from households.forms import CompletionForm, TaskForm
from households.models import Completion, Household, Membership, Task, TaskCategory
from households.views import add_task_summary, completion_edit


class ReviewFixTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="member")
        self.household = Household.objects.create()
        Membership.objects.create(user=self.user, household=self.household)
        self.task = Task.objects.create(household=self.household, name="Care", target_interval_days=7)
        self.today = timezone.localdate()
        self.completion = Completion.objects.create(task=self.task, completed_on=self.today)
        self.client.force_login(self.user)

    def test_suggestion_at_date_boundary_and_ordinary_interval(self):
        self.assertEqual(add_task_summary(self.task, [self.today]).suggested_date, self.today + timedelta(days=7))
        self.task.target_interval_days = (date.max - self.today).days
        self.assertEqual(add_task_summary(self.task, [self.today]).suggested_date, date.max)
        self.task.target_interval_days += 1
        self.assertIsNone(add_task_summary(self.task, [self.today]).suggested_date)

    def test_extreme_interval_keeps_all_summary_pages_available(self):
        self.task.target_interval_days = 3650000
        self.task.save()
        for name, kwargs in [("schedule", {}), ("calendar", {}), ("task_detail", {"task_id": self.task.pk})]:
            response = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(response.status_code, 200)
            task = response.context["task"] if name == "task_detail" else (
                response.context["tasks"][0] if name == "schedule" else response.context["undated_tasks"][0]
            )
            self.assertIsNone(task.suggested_date)

    def test_archived_history_remains_filterable_without_suggestions(self):
        category = TaskCategory.resolve(self.household, "Home", "Cleaning")
        self.task.category = category
        self.task.archived_at = timezone.now()
        self.task.save()
        other = Task.objects.create(household=self.household, name="Other")
        Completion.objects.create(task=other, completed_on=self.today)
        for filters in [{"task": self.task.pk}, {"category": category.parent_id}, {"subcategory": category.pk}]:
            response = self.client.get(reverse("calendar"), filters)
            entries = [entry for day in response.context["days"] for entry in day["entries"]]
            self.assertEqual([(entry["task"].pk, entry["kind"], entry["archived"]) for entry in entries], [(self.task.pk, "completed", True)])
            self.assertEqual(response.context["undated_tasks"], [])
            self.assertTrue(any(link["label"] == "Care" for link in response.context["task_links"]))
            self.assertTrue(any(link["label"] == "Home" for link in response.context["category_links"]))
            self.assertTrue(any(link["label"] == "Cleaning" for link in response.context["subcategory_links"]))

    def test_concurrent_corrections_preserve_undo_chain(self):
        first_ready, release_first, second_started = Event(), Event(), Event()
        original = CompletionForm.is_valid
        first_date = self.today - timedelta(days=1)
        second_date = self.today - timedelta(days=2)

        def validate(form):
            if form.data.get("completed_on") == first_date.isoformat():
                first_ready.set()
                if not release_first.wait(5):
                    raise AssertionError("First correction was not released")
            return original(form)

        def correct(day):
            close_old_connections()
            try:
                request = RequestFactory().post("/", {"completed_on": day.isoformat(), "attributed_to": ""})
                request.user = self.user
                request.session = {}
                request._messages = FallbackStorage(request)
                if day == second_date:
                    second_started.set()
                return completion_edit(request, self.task.pk, self.completion.pk).status_code
            finally:
                close_old_connections()

        with patch.object(CompletionForm, "is_valid", validate), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(correct, first_date)
            self.assertTrue(first_ready.wait(5))
            second = pool.submit(correct, second_date)
            self.assertTrue(second_started.wait(5))
            try:
                second.result(timeout=0.2)
            except TimeoutError:
                pass
            finally:
                release_first.set()
            self.assertEqual(first.result(timeout=5), 302)
            self.assertEqual(second.result(timeout=5), 302)
        revisions = list(self.completion.revisions.order_by("created_at", "pk"))
        self.assertEqual([(r.previous_completed_on, r.new_completed_on) for r in revisions], [(self.today, first_date), (first_date, second_date)])
        for revision, expected in [(revisions[1], first_date), (revisions[0], self.today)]:
            response = self.client.post(reverse("completion_undo", args=[self.task.pk, self.completion.pk, revision.pk]))
            self.assertEqual(response.status_code, 302)
            self.completion.refresh_from_db()
            self.assertEqual(self.completion.completed_on, expected)

    def test_creation_conflict_after_validation_returns_feedback(self):
        day = self.today - timedelta(days=1)
        original = CompletionForm.is_valid

        def validate(form):
            valid = original(form)
            Completion.objects.create(task=self.task, completed_on=day)
            return valid

        with patch.object(CompletionForm, "is_valid", validate):
            response = self.client.post(
                reverse("completion_create", args=[self.task.pk]),
                {"completed_on": day.isoformat(), "attributed_to": ""},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].errors["completed_on"],
                         ["A completion already exists on this date."])
        self.assertEqual(self.task.completions.filter(completed_on=day).count(), 1)

    def test_task_edit_preserves_concurrent_archive_and_creation(self):
        form = TaskForm(
            {"name": "Updated", "target_interval_days": 14,
             "category_label": "Home", "subcategory_label": "Care"},
            instance=self.task, household=self.household,
        )
        self.assertTrue(form.is_valid())
        archived_at = timezone.now()
        Task.objects.filter(pk=self.task.pk).update(archived_at=archived_at)
        form.save()
        self.task.refresh_from_db()
        self.assertEqual(self.task.archived_at, archived_at)
        self.assertEqual(self.task.name, "Updated")
        self.assertEqual(self.task.target_interval_days, 14)
        self.assertEqual(self.task.category.display_label, "Home / Care")
        create = TaskForm({"name": "New", "target_interval_days": "",
                           "category_label": "", "subcategory_label": ""},
                          household=self.household)
        self.assertTrue(create.is_valid())
        self.assertIsNotNone(create.save().pk)
