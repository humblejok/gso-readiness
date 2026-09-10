from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path

from hubapp import api, request_views, tech_lead_api, user_token_views, views
from hubapp.account_views import HubLoginView, HubPasswordResetView

urlpatterns = [
    path("", views.home, name="home"),
    path("legal/", views.legal),
    path("accounts/signup/", views.signup),
    path("accounts/verify/<str:token>/", views.verify),
    path("accounts/resend-verification/", views.resend_verification),
    path(
        "accounts/login/",
        HubLoginView.as_view(),
        name="login",
    ),
    path("accounts/logout/", auth.LogoutView.as_view(), name="logout"),
    path(
        "accounts/password-reset/",
        HubPasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "accounts/password-reset/done/",
        auth.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        auth.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "accounts/reset/done/",
        auth.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("workspaces/new/", views.new_workspace),
    path("accounts/tokens/", user_token_views.personal_tokens, name="personal-tokens"),
    path("invitations/<str:token>/", views.accept_invitation),
    path("w/<uuid:organization_id>/", views.dashboard),
    path("w/<uuid:organization_id>/requests/", request_views.index),
    path("w/<uuid:organization_id>/requests/new/", request_views.new),
    path("w/<uuid:organization_id>/requests/<uuid:pk>/", request_views.detail),
    path("w/<uuid:organization_id>/findings/<uuid:pk>/", views.finding_detail),
    path(
        "w/<uuid:organization_id>/findings/<uuid:pk>/implementation/", views.finding_implementation
    ),
    path("w/<uuid:organization_id>/upload/", views.upload),
    path("w/<uuid:organization_id>/tokens/", views.tokens),
    path("w/<uuid:organization_id>/members/", views.members),
    path("w/<uuid:organization_id>/branding/", views.branding),
    path("w/<uuid:organization_id>/billing/", views.billing),
    path("w/<uuid:organization_id>/integrations/", views.integrations),
    path("w/<uuid:organization_id>/deliveries/", views.deliveries),
    path("w/<uuid:organization_id>/audit/", views.audit_history),
    path("w/<uuid:organization_id>/export/", views.export),
    path("w/<uuid:organization_id>/erasure/", views.deletion_request),
    path("api/v1/review-imports", api.ImportsAPI.as_view()),
    path("api/v1/workspaces", tech_lead_api.WorkspacesAPI.as_view()),
    path("api/v1/workspaces/<uuid:workspace_id>", tech_lead_api.WorkspaceDetailAPI.as_view()),
    path("api/v1/findings/<uuid:pk>/management", tech_lead_api.FindingManageAPI.as_view()),
    path("api/v1/review-imports/<uuid:pk>", api.ImportDetailAPI.as_view()),
    path("api/v1/repositories", api.RepositoriesAPI.as_view()),
    path("api/v1/repositories/<uuid:pk>", api.RepositoryDetailAPI.as_view()),
    path("api/v1/repositories/<uuid:repository_id>/findings", api.FindingsAPI.as_view()),
    path("api/v1/findings", api.FindingsAPI.as_view()),
    path("api/v1/findings/<uuid:pk>", api.FindingDetailAPI.as_view()),
    path("api/v1/findings/<uuid:pk>/implementation", api.FindingWorkAPI.as_view()),
    path("api/v1/findings/<uuid:pk>/revalidation", api.FindingRevalidationAPI.as_view()),
    path("api/v1/findings/<uuid:pk>/<str:part>", api.FindingDetailAPI.as_view()),
    path("billing/webhook/", views.billing_webhook),
    path("health/live", api.live),
    path("health/ready", api.ready),
    path("operator/", admin.site.urls),
]
