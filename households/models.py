from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


class Household(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)


class Membership(models.Model):
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="members")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="household_membership",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("joined_at", "pk")


class TaskCategory(models.Model):
    household = models.ForeignKey(
        Household,
        on_delete=models.CASCADE,
        related_name="task_categories",
    )
    label = models.CharField(max_length=60)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("label", "pk")
        constraints = [
            models.UniqueConstraint(
                Lower("label"),
                "household",
                condition=Q(parent__isnull=True),
                name="unique_task_category_label_per_household",
            ),
            models.UniqueConstraint(
                Lower("label"),
                "parent",
                condition=Q(parent__isnull=False),
                name="unique_task_subcategory_label_per_parent",
            ),
        ]

    def __str__(self):
        return self.label

    @property
    def display_label(self):
        if self.parent_id:
            return f"{self.parent.label} / {self.label}"
        return self.label

    def clean(self):
        if self.parent_id is not None:
            if self.parent.household_id != self.household_id:
                raise ValidationError(
                    {"parent": "Subcategories must belong to the same household."}
                )
            if self.parent.parent_id is not None:
                raise ValidationError(
                    {"parent": "Categories support only one level of subcategories."}
                )
        if not self.pk:
            return
        previous_household_id = (
            type(self)
            .objects.filter(pk=self.pk)
            .values_list("household_id", flat=True)
            .first()
        )
        if (
            previous_household_id is not None
            and previous_household_id != self.household_id
            and (self.tasks.exists() or self.children.exists())
        ):
            raise ValidationError(
                {"household": "Categories in use cannot change household."}
            )
        if self.parent_id is not None and self.children.exists():
            raise ValidationError(
                {"parent": "Categories with subcategories cannot become subcategories."}
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    @classmethod
    def resolve(cls, household, category_label, subcategory_label):
        category_label = (category_label or "").strip()
        subcategory_label = (subcategory_label or "").strip()
        if not category_label:
            if subcategory_label:
                raise ValidationError(
                    {"subcategory": "Choose a category before adding a subcategory."}
                )
            return None
        category = cls._label_node(household, category_label, parent=None)
        if not subcategory_label:
            return category
        return cls._label_node(household, subcategory_label, parent=category)

    @classmethod
    def _label_node(cls, household, label, parent):
        return cls.objects.get_or_create(
            household=household,
            parent=parent,
            label__iexact=label,
            defaults={"label": label},
        )[0]


class Task(models.Model):
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="tasks")
    name = models.CharField(max_length=120)
    target_interval_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )
    category = models.ForeignKey(
        TaskCategory,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name", "pk")
        constraints = [
            models.CheckConstraint(
                condition=Q(target_interval_days__isnull=True) | Q(target_interval_days__gte=1),
                name="task_interval_positive_or_null",
            )
        ]

    def clean(self):
        if self.category_id and self.household_id:
            if self.category.household_id != self.household_id:
                raise ValidationError(
                    {"category": "Task and category must share a household."}
                )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class Completion(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="completions")
    completed_on = models.DateField()
    attributed_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attributed_completions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-completed_on", "-pk")
        constraints = [
            models.UniqueConstraint(
                fields=("task", "completed_on"),
                name="one_completion_per_task_date",
            )
        ]


class CompletionRevision(models.Model):
    completion = models.ForeignKey(
        Completion,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    previous_completed_on = models.DateField()
    previous_attributed_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    new_completed_on = models.DateField()
    new_attributed_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")


class Invitation(models.Model):
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="invitations")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_household_invitations",
    )
    token_digest = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
