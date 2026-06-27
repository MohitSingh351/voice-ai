import pytest
from rest_framework.test import APIClient

from apps.calls.models import Call
from apps.campaigns.models import Campaign, CampaignLead
from apps.leads.models import Lead
from apps.organizations.models import Organization

SECRET = "test-webhook-secret"
URL = "/webhooks/vapi/"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.VAPI_WEBHOOK_SECRET = SECRET
    settings.CELERY_TASK_ALWAYS_EAGER = True


@pytest.fixture
def call(db):
    org = Organization.get_default()
    org.vapi_assistant_id = "as_1"
    org.vapi_phone_number_id = "pn_1"
    org.save()
    campaign = Campaign.objects.create(
        organization=org, name="C", status=Campaign.Status.RUNNING, max_attempts=2
    )
    lead = Lead.objects.create(organization=org, name="Ada", phone_e164="+14155552671")
    cl = CampaignLead.objects.create(
        campaign=campaign, lead=lead, status=CampaignLead.Status.IN_FLIGHT, attempts=1
    )
    return Call.objects.create(campaign_lead=cl, vapi_call_id="call_x", status=Call.Status.QUEUED)


def _post(payload, secret=SECRET):
    client = APIClient()
    headers = {"HTTP_X_VAPI_SECRET": secret} if secret is not None else {}
    return client.post(URL, payload, format="json", **headers)


def test_rejects_missing_secret(call):
    resp = _post({"message": {"type": "status-update", "status": "ringing", "call": {"id": "call_x"}}}, secret=None)
    assert resp.status_code == 401


def test_rejects_wrong_secret(call):
    resp = _post({"message": {"type": "status-update"}}, secret="nope")
    assert resp.status_code == 401


def test_status_update_advances_call(call):
    resp = _post({"message": {"type": "status-update", "status": "in-progress", "call": {"id": "call_x"}}})
    assert resp.status_code == 200
    call.refresh_from_db()
    assert call.status == Call.Status.IN_PROGRESS
    assert call.started_at is not None


def test_end_of_call_report_persists_and_completes_lead(call):
    payload = {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "customer-ended-call",
            "call": {"id": "call_x"},
            "transcript": "Agent: Hi\nAda: Hello",
            "summary": "Lead is interested.",
            "recordingUrl": "https://rec.example.com/x.wav",
            "analysis": {"successEvaluation": "true", "structuredData": {"interested": True}},
            "cost": 0.42,
        }
    }
    resp = _post(payload)
    assert resp.status_code == 200

    call.refresh_from_db()
    assert call.status == Call.Status.ENDED
    assert "Hello" in call.transcript
    assert call.summary == "Lead is interested."
    assert call.recording_url.endswith("x.wav")
    assert call.structured_outcome["structuredData"] == {"interested": True}
    assert float(call.cost) == 0.42

    call.campaign_lead.refresh_from_db()
    assert call.campaign_lead.status == CampaignLead.Status.DONE


def test_no_answer_schedules_retry(call):
    payload = {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "customer-did-not-answer",
            "call": {"id": "call_x"},
        }
    }
    resp = _post(payload)
    assert resp.status_code == 200
    cl = call.campaign_lead
    cl.refresh_from_db()
    # attempts=1 < max_attempts=2 -> retry scheduled
    assert cl.status == CampaignLead.Status.FAILED
    assert cl.next_attempt_at is not None


def test_unknown_event_type_is_ignored(call):
    resp = _post({"message": {"type": "speech-update", "call": {"id": "call_x"}}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_end_of_call_report_is_idempotent(call):
    payload = {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "customer-did-not-answer",
            "call": {"id": "call_x"},
        }
    }
    _post(payload)
    cl = call.campaign_lead
    cl.refresh_from_db()
    assert cl.status == CampaignLead.Status.FAILED
    first_next = cl.next_attempt_at
    # Re-deliver: must not double-resolve (no further attempt change).
    _post(payload)
    cl.refresh_from_db()
    assert cl.next_attempt_at == first_next
    assert cl.attempts == 1
