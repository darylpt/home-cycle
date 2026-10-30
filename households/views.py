import calendar as month_calendar
import hashlib
import secrets
from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import CompletionForm, SignUpForm, TaskForm
from .models import (
    Completion,
    CompletionRevision,
    Household,
    Invitation,
    Membership,
    Task,
)


def household_for_user(user):
    return get_object_or_404(Household, members__user=user)


def add_task_summary(task, completion_dates):
    dates = sorted(completion_dates)
    task.completion_date_count = len(dates)
    task.last_completed_on = dates[-1] if dates else None
    task.days_since_last_completed = (
        (timezone.localdate() - task.last_completed_on).days
        if task.last_completed_on
        else None
    )
    task.observed_median_days = (
        median((later - earlier).days for earlier, later in zip(dates, dates[1:]))
        if len(dates) >= 3
        else None
    )
    task.suggested_date = (
        task.last_completed_on + timedelta(days=task.target_interval_days)
        if task.last_completed_on
        and task.target_interval_days
        and task.target_interval_days <= (date.max - task.last_completed_on).days
        else None
    )
    return task


def invitation_is_usable(invitation, now=None):
    now = now or timezone.now()
    return (
        invitation.revoked_at is None
        and invitation.consumed_at is None
        and invitation.expires_at > now
    )


def household_members_context(household, invitation_url=None):
    now = timezone.now()
    invitations = list(household.invitations.all())
    for invitation in invitations:
        if invitation.revoked_at:
            invitation.status = "Revoked"
        elif invitation.consumed_at:
            invitation.status = "Used"
        elif invitation.expires_at <= now:
            invitation.status = "Expired"
        else:
            invitation.status = "Active"
        invitation.can_revoke = invitation.status == "Active"
    return {
        "household": household,
        "members": household.members.select_related("user"),
        "invitations": invitations,
        "invitation_url": invitation_url,
    }


@login_required
def household_members(request):
    household = household_for_user(request.user)
    invitation_url = request.session.pop("new_invitation_url", None)
    return render(
        request,
        "households/household_members.html",
        household_members_context(household, invitation_url),
    )


@login_required
@require_POST
def invitation_create(request):
    household = household_for_user(request.user)
    token = secrets.token_urlsafe(32)
    Invitation.objects.create(
        household=household,
        created_by=request.user,
        token_digest=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(days=7),
    )
    request.session["new_invitation_url"] = request.build_absolute_uri(
        reverse("invitation_accept", kwargs={"token": token})
    )
    messages.success(request, "Invitation link created. It can be used once within seven days.")
    return redirect("household_members")


@login_required
@require_POST
def invitation_revoke(request, invitation_id):
    household = household_for_user(request.user)
    invitation = get_object_or_404(
        Invitation,
        pk=invitation_id,
        household=household,
    )
    if invitation_is_usable(invitation):
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=("revoked_at",))
        messages.success(request, "Invitation revoked.")
    return redirect("household_members")


@login_required
@require_POST
def member_remove(request, membership_id):
    household = household_for_user(request.user)
    membership = get_object_or_404(
        Membership.objects.select_related("user"),
        pk=membership_id,
        household=household,
    )
    if membership.user_id == request.user.pk:
        messages.error(request, "You cannot remove your own household access.")
    else:
        username = membership.user.username
        membership.delete()
        messages.success(request, f"{username} no longer has household access.")
    return redirect("household_members")


def claim_invitation(invitation, user):
    if Membership.objects.filter(user=user).exists():
        return False
    now = timezone.now()
    claimed = Invitation.objects.filter(
        pk=invitation.pk,
        revoked_at__isnull=True,
        consumed_at__isnull=True,
        expires_at__gt=now,
    ).update(consumed_at=now)
    if claimed != 1:
        return False
    Membership.objects.create(household=invitation.household, user=user)
    return True


@require_http_methods(["GET", "POST"])
def invitation_accept(request, token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    invitation = Invitation.objects.filter(token_digest=digest).first()
    if invitation is None or not invitation_is_usable(invitation):
        return render(
            request,
            "households/invitation_unavailable.html",
            status=410,
        )
    if request.user.is_authenticated and Membership.objects.filter(user=request.user).exists():
        return render(
            request,
            "households/invitation_unavailable.html",
            status=410,
        )

    if request.user.is_authenticated:
        if request.method == "POST":
            with transaction.atomic():
                if not claim_invitation(invitation, request.user):
                    return render(
                        request,
                        "households/invitation_unavailable.html",
                        status=410,
                    )
            messages.success(request, "You joined the household.")
            return redirect("schedule")
        return render(
            request,
            "households/invitation_accept.html",
            {"invitation": invitation, "form": None},
        )

    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            if not Invitation.objects.filter(
                pk=invitation.pk,
                revoked_at__isnull=True,
                consumed_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).update(consumed_at=timezone.now()):
                return render(
                    request,
                    "households/invitation_unavailable.html",
                    status=410,
                )
            user = form.save()
            Membership.objects.create(household=invitation.household, user=user)
        login(request, user)
        messages.success(request, "You joined the household.")
        return redirect("schedule")
    return render(
        request,
        "households/invitation_accept.html",
        {"invitation": invitation, "form": form},
    )


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("schedule")

    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            household = Household.objects.create()
            Membership.objects.create(household=household, user=user)
        login(request, user)
        messages.success(request, "Your household is ready.")
        return redirect("schedule")

    return render(request, "households/signup.html", {"form": form})


@login_required
def schedule(request):
    membership = get_object_or_404(Membership.objects.select_related("household"), user=request.user)
    tasks = list(
        Task.objects.filter(
            household=membership.household,
            archived_at__isnull=True,
        )
        .select_related("category__parent")
        .prefetch_related("completions")
    )
    for task in tasks:
        add_task_summary(
            task,
            (completion.completed_on for completion in task.completions.all()),
        )
    tasks.sort(
        key=lambda task: (
            0 if task.last_completed_on is None else 1 if task.suggested_date else 2,
            task.suggested_date or timezone.localdate(),
            task.name.casefold(),
            task.pk,
        )
    )
    return render(
        request,
        "households/schedule.html",
        {"household": membership.household, "tasks": tasks},
    )


def shift_month(month_start, offset):
    month_index = (month_start.year - 1) * 12 + month_start.month - 1 + offset
    if not 0 <= month_index < 9999 * 12:
        return None
    year, month = divmod(month_index, 12)
    return date(year + 1, month + 1, 1)


WEEKDAYS = (
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
)


def month_from_query(value, today):
    try:
        year, month = value.split("-")
        if len(year) != 4 or len(month) != 2:
            raise ValueError
        return date(int(year), int(month), 1)
    except (AttributeError, TypeError, ValueError):
        return today.replace(day=1)


def calendar_weeks(selected_month, today, entries_by_date):
    weeks = []
    for day_numbers in month_calendar.Calendar(
        firstweekday=month_calendar.SUNDAY
    ).monthdayscalendar(selected_month.year, selected_month.month):
        week = []
        for day_number in day_numbers:
            if not day_number:
                week.append({"in_month": False, "entries": []})
                continue
            day = selected_month.replace(day=day_number)
            week.append(
                {
                    "date": day,
                    "in_month": True,
                    "is_today": day == today,
                    "entries": entries_by_date.get(day, []),
                }
            )
        weeks.append(week)
    return weeks


def calendar_query(selected_month, **params):
    values = {"month": selected_month.strftime("%Y-%m")}
    values.update({key: value for key, value in params.items() if value})
    return "?" + urlencode(values)


def filter_pk(value):
    try:
        pk = int(value)
    except (TypeError, ValueError):
        return None
    return pk if pk > 0 else None


def calendar_context(request, household, selected_month, today):
    last_day = selected_month.replace(
        day=month_calendar.monthrange(selected_month.year, selected_month.month)[1]
    )

    tasks = list(
        Task.objects.filter(
            household=household,
        )
        .select_related("category__parent")
        .prefetch_related("completions")
    )
    for task in tasks:
        add_task_summary(
            task,
            (completion.completed_on for completion in task.completions.all()),
        )
    tasks.sort(key=lambda task: (task.name.casefold(), task.pk))

    categories = list(household.task_categories.select_related("parent"))
    task_filter_id = filter_pk(request.GET.get("task"))
    category_filter_id = filter_pk(request.GET.get("category"))
    subcategory_filter_id = filter_pk(request.GET.get("subcategory"))
    selected_task = next((task for task in tasks if task.pk == task_filter_id), None)
    selected_category = next(
        (
            node
            for node in categories
            if node.pk == category_filter_id and node.parent_id is None
        ),
        None,
    )
    selected_subcategory = next(
        (
            node
            for node in categories
            if node.pk == subcategory_filter_id and node.parent_id is not None
        ),
        None,
    )

    active_filters = {}
    completion_filters = {}
    filter_status = ""
    filtered_tasks = tasks
    if selected_task is not None:
        filtered_tasks = [selected_task]
        active_filters["task"] = selected_task.pk
        completion_filters["task"] = selected_task
        filter_status = f"Showing {selected_task.name}"
    elif selected_subcategory is not None:
        filtered_tasks = [
            task for task in tasks if task.category_id == selected_subcategory.pk
        ]
        active_filters = {
            "category": selected_subcategory.parent_id,
            "subcategory": selected_subcategory.pk,
        }
        completion_filters["task__category"] = selected_subcategory
        filter_status = f"Showing {selected_subcategory.display_label}"
    elif selected_category is not None:
        category_ids = {
            node.pk for node in categories if node.parent_id == selected_category.pk
        }
        category_ids.add(selected_category.pk)
        filtered_tasks = [task for task in tasks if task.category_id in category_ids]
        active_filters["category"] = selected_category.pk
        completion_filters["task__category_id__in"] = category_ids
        filter_status = f"Showing {selected_category.label}"

    entries_by_date = defaultdict(list)
    undated_tasks = []
    for task in filtered_tasks:
        if task.archived_at is not None:
            continue
        if task.suggested_date is None:
            undated_tasks.append(task)
        elif selected_month <= task.suggested_date <= last_day:
            entries_by_date[task.suggested_date].append(
                {
                    "task": task,
                    "kind": "suggested",
                    "kind_label": "Suggested",
                    "meta": "",
                }
            )
    completions = Completion.objects.filter(
        task__household=household,
        completed_on__gte=selected_month,
        completed_on__lte=last_day,
    ).select_related("task", "attributed_to")
    if completion_filters:
        completions = completions.filter(**completion_filters)
    for completion in completions.order_by("completed_on", "task__name", "pk"):
        entries_by_date[completion.completed_on].append(
            {
                "task": completion.task,
                "kind": "completed",
                "kind_label": "Completed",
                "archived": completion.task.archived_at is not None,
                "meta": (
                    f"Completed by {completion.attributed_to.username}"
                    if completion.attributed_to_id
                    else ""
                ),
            }
        )
    for entries in entries_by_date.values():
        entries.sort(
            key=lambda entry: (
                entry["kind"] != "completed",
                entry["task"].name.casefold(),
                entry["task"].pk,
            )
        )

    weeks = calendar_weeks(selected_month, today, entries_by_date)
    days = [
        day for week in weeks for day in week if day["in_month"] and day["entries"]
    ]


    used_roots = {}
    used_subcategories = {}
    for task in tasks:
        node = task.category
        if node is None:
            continue
        root = node.parent if node.parent_id else node
        used_roots[root.pk] = root
        if node.parent_id:
            used_subcategories[node.pk] = node
    category_links = [
        {
            "label": root.label,
            "url": calendar_query(selected_month, category=root.pk),
            "selected": root.pk == category_filter_id,
        }
        for root in sorted(
            used_roots.values(), key=lambda node: (node.label.casefold(), node.pk)
        )
    ]
    subcategory_links = [
        {
            "label": node.label,
            "url": calendar_query(
                selected_month,
                category=node.parent_id,
                subcategory=node.pk,
            ),
            "selected": node.pk == subcategory_filter_id,
        }
        for node in sorted(
            (
                node
                for node in used_subcategories.values()
                if selected_category is None or node.parent_id == selected_category.pk
            ),
            key=lambda node: (
                node.parent.label.casefold(),
                node.label.casefold(),
                node.pk,
            ),
        )
    ]

    previous_month = shift_month(selected_month, -1)
    next_month = shift_month(selected_month, 1)
    return {
        "previous_month_url": (
            calendar_query(previous_month, **active_filters)
            if previous_month
            else None
        ),
        "next_month_url": (
            calendar_query(next_month, **active_filters)
            if next_month
            else None
        ),
        "weeks": weeks,
        "days": days,
        "empty_note": f"No completions or suggested dates in {selected_month.strftime('%B %Y')}.",
        "agenda_title": "Activity",
        "undated_tasks": undated_tasks,
        "category_links": category_links,
        "subcategory_links": subcategory_links,
        "filter_status": filter_status,
        "all_tasks_url": calendar_query(selected_month),
        "task_links": [
            {
                "label": task.name,
                "url": calendar_query(selected_month, task=task.pk),
                "selected": selected_task is not None
                and task.pk == selected_task.pk,
            }
            for task in tasks
        ],
    }


@login_required
def calendar(request):
    household = household_for_user(request.user)
    today = timezone.localdate()
    selected_month = month_from_query(request.GET.get("month"), today)
    context = calendar_context(request, household, selected_month, today)
    context.update({"selected_month": selected_month, "weekdays": WEEKDAYS})
    return render(request, "households/calendar.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def task_create(request):
    membership = get_object_or_404(Membership.objects.select_related("household"), user=request.user)
    form = TaskForm(request.POST or None, household=membership.household)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Task added to your household schedule.")
        return redirect("schedule")
    return render(
        request,
        "households/task_form.html",
        {"form": form, "page_title": "Add a recurring task", "submit_label": "Save task"},
    )


@login_required
@require_http_methods(["GET", "POST"])
def task_edit(request, task_id):
    membership = get_object_or_404(Membership.objects.select_related("household"), user=request.user)
    task = get_object_or_404(
        Task,
        pk=task_id,
        household=membership.household,
        archived_at__isnull=True,
    )
    form = TaskForm(
        request.POST or None,
        instance=task,
        household=membership.household,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Task updated.")
        return redirect("schedule")
    return render(
        request,
        "households/task_form.html",
        {"form": form, "page_title": "Edit task", "submit_label": "Save changes"},
    )


@login_required
@require_POST
def task_archive(request, task_id):
    membership = get_object_or_404(Membership.objects.select_related("household"), user=request.user)
    task = get_object_or_404(
        Task,
        pk=task_id,
        household=membership.household,
        archived_at__isnull=True,
    )
    task.archived_at = timezone.now()
    task.save(update_fields=("archived_at",))
    messages.success(request, "Task archived.")
    return redirect("schedule")


@login_required
def archived_tasks(request):
    membership = get_object_or_404(Membership.objects.select_related("household"), user=request.user)
    tasks = Task.objects.filter(
        household=membership.household,
        archived_at__isnull=False,
    ).select_related("category__parent")
    return render(
        request,
        "households/archived_tasks.html",
        {"tasks": tasks},
    )


@login_required
@require_POST
def task_restore(request, task_id):
    membership = get_object_or_404(Membership.objects.select_related("household"), user=request.user)
    task = get_object_or_404(
        Task,
        pk=task_id,
        household=membership.household,
        archived_at__isnull=False,
    )
    task.archived_at = None
    task.save(update_fields=("archived_at",))
    messages.success(request, "Task restored.")
    return redirect("schedule")


@login_required
def task_detail(request, task_id):
    household = household_for_user(request.user)
    task = get_object_or_404(
        Task.objects.select_related("category__parent"),
        pk=task_id,
        household=household,
    )
    completions = list(
        task.completions.select_related("attributed_to").prefetch_related("revisions")
    )
    add_task_summary(task, (completion.completed_on for completion in completions))
    for completion in completions:
        completion.latest_undoable_revision = next(
            (revision for revision in completion.revisions.all() if revision.undone_at is None),
            None,
        )
    return render(
        request,
        "households/task_detail.html",
        {"task": task, "completions": completions},
    )


@login_required
@require_http_methods(["GET", "POST"])
def completion_create(request, task_id):
    household = household_for_user(request.user)
    task = get_object_or_404(
        Task,
        pk=task_id,
        household=household,
        archived_at__isnull=True,
    )
    form = CompletionForm(
        request.POST or None,
        task=task,
        household=household,
        current_user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        completion = form.save(commit=False)
        completion.task = task
        try:
            with transaction.atomic():
                completion.save()
        except IntegrityError:
            if not Completion.objects.filter(
                task=task, completed_on=completion.completed_on
            ).exists():
                raise
            form.add_error("completed_on", "A completion already exists on this date.")
        else:
            messages.success(request, "Completion recorded.")
            return redirect("task_detail", task_id=task.pk)
    return render(
        request,
        "households/completion_form.html",
        {"form": form, "task": task, "page_title": "Log completion", "submit_label": "Save completion"},
    )


@login_required
@require_http_methods(["GET", "POST"])
def completion_edit(request, task_id, completion_id):
    household = household_for_user(request.user)
    task = get_object_or_404(Task, pk=task_id, household=household)
    with transaction.atomic():
        if request.method == "POST":
            Completion.objects.filter(pk=completion_id, task=task).update(
                updated_at=F("updated_at")
            )
        completion = get_object_or_404(
            Completion.objects.select_related("attributed_to"),
            pk=completion_id,
            task=task,
        )
        previous_completed_on = completion.completed_on
        previous_attributed_to = completion.attributed_to
        previous_attributed_to_id = completion.attributed_to_id
        form = CompletionForm(
            request.POST or None,
            instance=completion,
            task=task,
            household=household,
            current_user=request.user,
        )
        if request.method == "POST" and form.is_valid():
            corrected = form.save(commit=False)
            changes = (
                previous_completed_on != corrected.completed_on
                or previous_attributed_to_id != corrected.attributed_to_id
            )
            if changes:
                CompletionRevision.objects.create(
                    completion=completion,
                    previous_completed_on=previous_completed_on,
                    previous_attributed_to=previous_attributed_to,
                    new_completed_on=corrected.completed_on,
                    new_attributed_to=corrected.attributed_to,
                    changed_by=request.user,
                )
                corrected.save()
            messages.success(request, "Completion corrected.")
            return redirect("task_detail", task_id=task.pk)
        return render(
            request,
            "households/completion_form.html",
            {
                "form": form,
                "task": task,
                "page_title": "Correct completion",
                "submit_label": "Save correction",
            },
        )


@login_required
@require_POST
def completion_undo(request, task_id, completion_id, revision_id):
    household = household_for_user(request.user)
    task = get_object_or_404(Task, pk=task_id, household=household)
    with transaction.atomic():
        Completion.objects.filter(pk=completion_id, task=task).update(
            updated_at=F("updated_at")
        )
        completion = get_object_or_404(Completion, pk=completion_id, task=task)
        revision = get_object_or_404(
            CompletionRevision,
            pk=revision_id,
            completion=completion,
            undone_at__isnull=True,
        )
        latest_revision = completion.revisions.filter(undone_at__isnull=True).first()
        if latest_revision != revision:
            messages.error(request, "Undo the most recent correction first.")
            return redirect("task_detail", task_id=task.pk)
        if Completion.objects.filter(
            task=task,
            completed_on=revision.previous_completed_on,
        ).exclude(pk=completion.pk).exists():
            messages.error(request, "That date already has a completion and cannot be restored.")
            return redirect("task_detail", task_id=task.pk)

        completion.completed_on = revision.previous_completed_on
        completion.attributed_to = revision.previous_attributed_to
        completion.save(update_fields=("completed_on", "attributed_to", "updated_at"))
        revision.undone_at = timezone.now()
        revision.save(update_fields=("undone_at",))
        messages.success(request, "Correction undone.")
        return redirect("task_detail", task_id=task.pk)
