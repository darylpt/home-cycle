
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.schedule, name="schedule"),
    path("calendar/", views.calendar, name="calendar"),
    path("signup/", views.signup, name="signup"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="households/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("members/", views.household_members, name="household_members"),
    path(
        "members/invitations/new/",
        views.invitation_create,
        name="invitation_create",
    ),
    path(
        "members/invitations/<int:invitation_id>/revoke/",
        views.invitation_revoke,
        name="invitation_revoke",
    ),
    path(
        "members/<int:membership_id>/remove/",
        views.member_remove,
        name="member_remove",
    ),
    path("invitations/<str:token>/", views.invitation_accept, name="invitation_accept"),
    path("tasks/new/", views.task_create, name="task_create"),
    path("tasks/archived/", views.archived_tasks, name="archived_tasks"),
    path("tasks/<int:task_id>/", views.task_detail, name="task_detail"),
    path(
        "tasks/<int:task_id>/completions/new/",
        views.completion_create,
        name="completion_create",
    ),
    path(
        "tasks/<int:task_id>/completions/<int:completion_id>/edit/",
        views.completion_edit,
        name="completion_edit",
    ),
    path(
        "tasks/<int:task_id>/completions/<int:completion_id>/revisions/<int:revision_id>/undo/",
        views.completion_undo,
        name="completion_undo",
    ),
    path("tasks/<int:task_id>/edit/", views.task_edit, name="task_edit"),
    path("tasks/<int:task_id>/archive/", views.task_archive, name="task_archive"),
    path("tasks/<int:task_id>/restore/", views.task_restore, name="task_restore"),
]
