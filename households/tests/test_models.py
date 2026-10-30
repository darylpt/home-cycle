from django.core.exceptions import ValidationError
from django.test import TestCase

from households.forms import TaskForm
from households.models import Household, Task, TaskCategory


class TaskCategoryTests(TestCase):
    def setUp(self):
        self.household = Household.objects.create()
        self.other_household = Household.objects.create()

    def test_resolve_reuses_labels_within_a_household(self):
        first = TaskCategory.resolve(self.household, "Dogs", "Bath")
        second = TaskCategory.resolve(self.household, "dogs", "bath")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.parent.label, "Dogs")
        self.assertEqual(
            TaskCategory.objects.filter(household=self.household).count(), 2
        )

    def test_resolve_keeps_categories_household_scoped(self):
        dogs = TaskCategory.resolve(self.household, "Dogs", None)
        other_dogs = TaskCategory.resolve(self.other_household, "Dogs", None)

        self.assertNotEqual(dogs.pk, other_dogs.pk)
        self.assertEqual(dogs.label, other_dogs.label)

    def test_resolve_requires_a_category_for_a_subcategory(self):
        with self.assertRaises(ValidationError):
            TaskCategory.resolve(self.household, "", "Bath")

        self.assertFalse(TaskCategory.objects.exists())

    def test_subcategory_cannot_have_a_subcategory(self):
        bath = TaskCategory.resolve(self.household, "Dogs", "Bath")
        nested = TaskCategory(household=self.household, parent=bath, label="Deep")

        with self.assertRaises(ValidationError):
            nested.save()

    def test_subcategory_must_share_its_category_household(self):
        dogs = TaskCategory.resolve(self.household, "Dogs", None)
        mismatched = TaskCategory(
            household=self.other_household,
            parent=dogs,
            label="Bath",
        )
        with self.assertRaises(ValidationError):
            mismatched.save()

    def test_category_with_children_cannot_become_a_subcategory(self):
        dogs = TaskCategory.resolve(self.household, "Dogs", None)
        TaskCategory.resolve(self.household, "Dogs", "Bath")
        animals = TaskCategory.resolve(self.household, "Animals", None)
        dogs.parent = animals

        with self.assertRaises(ValidationError):
            dogs.save()

    def test_category_with_tasks_cannot_change_household(self):
        dogs = TaskCategory.resolve(self.household, "Dogs", None)
        Task.objects.create(
            household=self.household,
            name="Feed dogs",
            category=dogs,
        )
        dogs.household = self.other_household

        with self.assertRaises(ValidationError):
            dogs.save()

    def test_category_with_children_cannot_change_household(self):
        dogs = TaskCategory.resolve(self.household, "Dogs", None)
        TaskCategory.resolve(self.household, "Dogs", "Bath")
        dogs.household = self.other_household

        with self.assertRaises(ValidationError):
            dogs.save()

    def test_task_category_must_match_household(self):
        category = TaskCategory.resolve(self.other_household, "Dogs", None)

        with self.assertRaises(ValidationError):
            Task.objects.create(
                household=self.household,
                name="Unsorted task",
                category=category,
            )

    def test_task_can_remain_unclassified(self):
        task = Task.objects.create(household=self.household, name="Unsorted task")

        self.assertIsNone(task.category)


class TaskFormClassificationTests(TestCase):
    def setUp(self):
        self.household = Household.objects.create()

    def test_form_creates_and_reuses_category_nodes(self):
        first = TaskForm(
            {
                "name": "Maki bath",
                "category_label": "Dogs",
                "subcategory_label": "Bath",
            },
            household=self.household,
        )
        self.assertTrue(first.is_valid(), first.errors)
        task = first.save()

        second = TaskForm(
            {
                "name": "Maki vet",
                "category_label": "Dogs",
                "subcategory_label": "Veterinary",
            },
            household=self.household,
        )
        other_task = second.save()

        self.assertEqual(task.category.parent, other_task.category.parent)
        self.assertEqual(task.category.display_label, "Dogs / Bath")
        self.assertEqual(other_task.category.display_label, "Dogs / Veterinary")

    def test_form_prefills_classification_for_an_existing_task(self):
        task = Task.objects.create(
            household=self.household,
            name="Maki bath",
            category=TaskCategory.resolve(self.household, "Dogs", "Bath"),
        )

        form = TaskForm(instance=task, household=self.household)

        self.assertEqual(form.initial["category_label"], "Dogs")
        self.assertEqual(form.initial["subcategory_label"], "Bath")

    def test_form_rejects_a_subcategory_without_a_category(self):
        form = TaskForm(
            {
                "name": "Maki bath",
                "category_label": "",
                "subcategory_label": "Bath",
            },
            household=self.household,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("subcategory_label", form.errors)

    def test_clearing_both_labels_removes_the_classification(self):
        task = Task.objects.create(
            household=self.household,
            name="Maki bath",
            category=TaskCategory.resolve(self.household, "Dogs", "Bath"),
        )
        form = TaskForm(
            {"name": "Maki bath", "category_label": "", "subcategory_label": ""},
            instance=task,
            household=self.household,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()

        self.assertIsNone(saved.category)
