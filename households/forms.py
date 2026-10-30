from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Completion, Task, TaskCategory


class SignUpForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password2"].label = "Confirm password"


class TaskForm(forms.ModelForm):
    target_interval_days = forms.IntegerField(
        label="Target interval in days",
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"min": 1, "step": 1}),
    )
    category_label = forms.CharField(
        label="Category",
        required=False,
        max_length=60,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    subcategory_label = forms.CharField(
        label="Subcategory",
        required=False,
        max_length=60,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )

    class Meta:
        model = Task
        fields = ("name", "target_interval_days")
        labels = {"name": "Task name"}

    def __init__(self, *args, household, **kwargs):
        super().__init__(*args, **kwargs)
        self.household = household
        if not self.instance.household_id:
            self.instance.household = household
        self.order_fields(
            ("name", "target_interval_days", "category_label", "subcategory_label")
        )
        if self.instance.pk and self.instance.category_id:
            category = self.instance.category
            if category.parent_id:
                self.initial.setdefault("category_label", category.parent.label)
                self.initial.setdefault("subcategory_label", category.label)
            else:
                self.initial.setdefault("category_label", category.label)

    def clean(self):
        cleaned_data = super().clean()
        if (
            "category_label" in cleaned_data
            and not cleaned_data["category_label"]
            and cleaned_data.get("subcategory_label")
        ):
            self.add_error(
                "subcategory_label",
                "Choose a category before adding a subcategory.",
            )
        return cleaned_data

    def save(self, commit=True):
        task = super().save(commit=False)
        if not commit:
            return task
        with transaction.atomic():
            task.category = TaskCategory.resolve(
                self.household,
                self.cleaned_data["category_label"],
                self.cleaned_data["subcategory_label"],
            )
            task.save(
                update_fields=("name", "target_interval_days", "category")
                if not task._state.adding
                else None
            )
            self.save_m2m()
        return task


class CompletionForm(forms.ModelForm):
    attributed_to = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Completed by",
        empty_label="No attribution",
    )

    class Meta:
        model = Completion
        fields = ("completed_on", "attributed_to")
        labels = {"completed_on": "Completion date"}
        widgets = {"completed_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, task, household, current_user, **kwargs):
        super().__init__(*args, **kwargs)
        self.task = task
        self.fields["attributed_to"].queryset = User.objects.filter(
            household_membership__household=household
        ).distinct()
        if self.instance.pk and self.instance.attributed_to_id:
            self.fields["attributed_to"].queryset |= User.objects.filter(
                pk=self.instance.attributed_to_id
            ).distinct()
        self.fields["attributed_to"].queryset = self.fields[
            "attributed_to"
        ].queryset.order_by("username")
        self.fields["completed_on"].widget.attrs["max"] = timezone.localdate().isoformat()
        if not self.is_bound:
            self.initial.setdefault("completed_on", timezone.localdate())
            if not self.instance.pk:
                self.initial["attributed_to"] = current_user.pk

    def clean_completed_on(self):
        completed_on = self.cleaned_data["completed_on"]
        if completed_on > timezone.localdate():
            raise ValidationError("Completion date cannot be in the future.")
        return completed_on

    def clean(self):
        cleaned_data = super().clean()
        completed_on = cleaned_data.get("completed_on")
        if completed_on:
            existing = Completion.objects.filter(task=self.task, completed_on=completed_on)
            if self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                self.add_error(
                    "completed_on",
                    "A completion already exists on this date.",
                )
        return cleaned_data
