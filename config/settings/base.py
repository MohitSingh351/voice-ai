"""Base Django settings, driven by environment variables via django-environ."""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)

# Read .env if present (local dev). In prod, real env vars take precedence.
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# Public base URL where Vapi can reach this server (tunnel in dev).
PUBLIC_WEBHOOK_BASE_URL = env("PUBLIC_WEBHOOK_BASE_URL", default="")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "django_htmx",
    # local
    "apps.organizations",
    "apps.leads",
    "apps.campaigns",
    "apps.calls",
    "apps.vapi",  # no models; registered so its `provision_vapi` command is discoverable
    "apps.webhooks",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://localhost:5432/voice_ai",
    ),
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "campaign_list"
LOGOUT_REDIRECT_URL = "login"

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

# ---------------------------------------------------------------------------
# Celery / Redis
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 10
CELERY_TIMEZONE = TIME_ZONE
# Interval (seconds) at which the dispatcher tick runs.
CAMPAIGN_TICK_SECONDS = env.int("CAMPAIGN_TICK_SECONDS", default=5)

# ---------------------------------------------------------------------------
# Vapi
# ---------------------------------------------------------------------------
VAPI_BASE_URL = env("VAPI_BASE_URL", default="https://api.vapi.ai")
VAPI_API_KEY = env("VAPI_API_KEY", default="")
VAPI_WEBHOOK_SECRET = env("VAPI_WEBHOOK_SECRET", default="")

# Defaults used by `provision_vapi` to build the BYO-SIP credential, phone
# number and assistant. Most are Twilio Elastic SIP Trunk values.
VAPI_PROVISION = {
    "ASSISTANT_NAME": env("VAPI_ASSISTANT_NAME", default="Sales Agent"),
    "ASSISTANT_FIRST_MESSAGE": env(
        "VAPI_ASSISTANT_FIRST_MESSAGE",
        default="Hi {{name}}, this is Mohit calling from FreightSetu. Do you have a quick minute?",
    ),
    "ASSISTANT_SYSTEM_PROMPT": env(
        "VAPI_ASSISTANT_SYSTEM_PROMPT",
        default=(
            "You are Mohit, a friendly and concise B2B sales representative. "
            "Your goal is to qualify the lead and book a follow-up meeting. "
            "Keep responses short and conversational. If the person is not "
            "interested, thank them politely and end the call."
        ),
    ),
    # Vapi LLM for the assistant brain. Vapi supports Anthropic Claude models;
    # confirm the exact model id against Vapi's supported list before going live.
    "MODEL_PROVIDER": env("VAPI_MODEL_PROVIDER", default="anthropic"),
    "MODEL_NAME": env("VAPI_MODEL_NAME", default="claude-sonnet-4-6"),
    "VOICE_PROVIDER": env("VAPI_VOICE_PROVIDER", default="vapi"),
    "VOICE_ID": env("VAPI_VOICE_ID", default="Elliot"),
    "TRANSCRIBER_PROVIDER": env("VAPI_TRANSCRIBER_PROVIDER", default="deepgram"),
    "TRANSCRIBER_MODEL": env("VAPI_TRANSCRIBER_MODEL", default="nova-2"),
    # Twilio Elastic SIP Trunk termination values for the BYO-SIP credential.
    "SIP_TRUNK_GATEWAY": env("TWILIO_SIP_TERMINATION_URI", default=""),
    "SIP_TRUNK_USERNAME": env("TWILIO_SIP_USERNAME", default=""),
    "SIP_TRUNK_PASSWORD": env("TWILIO_SIP_PASSWORD", default=""),
    "CALLER_ID_E164": env("TWILIO_CALLER_ID", default=""),
}
