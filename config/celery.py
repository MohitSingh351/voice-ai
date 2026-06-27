"""Celery application + beat schedule."""
import os

from celery import Celery
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("voice_ai")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    # Safety-net dispatcher tick; webhooks also kick dispatch event-driven.
    sender.add_periodic_task(
        float(settings.CAMPAIGN_TICK_SECONDS),
        tick_all_campaigns.s(),
        name="tick-all-campaigns",
    )
    # Requeue retryable leads every couple of minutes.
    sender.add_periodic_task(
        120.0,
        retry_failed_leads.s(),
        name="retry-failed-leads",
    )


@app.task
def tick_all_campaigns():
    from apps.calls.tasks import tick_campaigns

    return tick_campaigns()


@app.task
def retry_failed_leads():
    from apps.calls.tasks import retry_failed

    return retry_failed()
