import httpx
import pytest
import respx

from apps.vapi.client import VapiClient, VapiError
from apps.vapi.provisioning import ensure_org_provisioned
from apps.organizations.models import Organization

BASE = "https://api.test.vapi"


def make_client():
    return VapiClient(api_key="test-key", base_url=BASE)


@respx.mock
def test_place_call_posts_expected_payload():
    route = respx.post(f"{BASE}/call").mock(
        return_value=httpx.Response(201, json={"id": "call_123", "status": "queued"})
    )
    client = make_client()
    out = client.place_call(
        phone_number_id="pn_1",
        assistant_id="as_1",
        customer_number="+14155552671",
        customer_name="Ada",
        variable_values={"name": "Ada", "company": "AE"},
    )
    assert out["id"] == "call_123"
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer test-key"
    import json

    body = json.loads(sent.content)
    assert body["phoneNumberId"] == "pn_1"
    assert body["customer"] == {"number": "+14155552671", "name": "Ada"}
    assert body["assistantOverrides"]["variableValues"]["company"] == "AE"


@respx.mock
def test_place_call_includes_voice_override_when_set():
    route = respx.post(f"{BASE}/call").mock(
        return_value=httpx.Response(201, json={"id": "call_v", "status": "queued"})
    )
    make_client().place_call(
        phone_number_id="pn_1",
        assistant_id="as_1",
        customer_number="+14155552671",
        voice_provider="vapi",
        voice_id="Rohan",
    )
    import json

    body = json.loads(route.calls.last.request.content)
    assert body["assistantOverrides"]["voice"] == {"provider": "vapi", "voiceId": "Rohan"}


@respx.mock
def test_place_call_omits_voice_override_when_unset():
    route = respx.post(f"{BASE}/call").mock(
        return_value=httpx.Response(201, json={"id": "call_n", "status": "queued"})
    )
    make_client().place_call(
        phone_number_id="pn_1", assistant_id="as_1", customer_number="+14155552671"
    )
    import json

    body = json.loads(route.calls.last.request.content)
    assert "voice" not in body.get("assistantOverrides", {})


@respx.mock
def test_client_raises_on_error():
    respx.post(f"{BASE}/call").mock(return_value=httpx.Response(400, text="bad request"))
    with pytest.raises(VapiError) as exc:
        make_client().place_call(
            phone_number_id="pn", assistant_id="as", customer_number="+1"
        )
    assert exc.value.status_code == 400


@pytest.mark.django_db
@respx.mock
def test_provisioning_is_idempotent(settings):
    settings.VAPI_PROVISION = {
        **settings.VAPI_PROVISION,
        "SIP_TRUNK_GATEWAY": "trunk.pstn.twilio.com",
        "SIP_TRUNK_USERNAME": "u",
        "SIP_TRUNK_PASSWORD": "p",
        "CALLER_ID_E164": "+14155550000",
    }
    cred = respx.post(f"{BASE}/credential").mock(
        return_value=httpx.Response(201, json={"id": "cred_1"})
    )
    num = respx.post(f"{BASE}/phone-number").mock(
        return_value=httpx.Response(201, json={"id": "pn_1"})
    )
    asst = respx.post(f"{BASE}/assistant").mock(
        return_value=httpx.Response(201, json={"id": "as_1"})
    )
    org = Organization.get_default()
    client = VapiClient(api_key="k", base_url=BASE)

    actions = ensure_org_provisioned(org, client=client)
    assert "created cred_1" in actions["credential"]
    org.refresh_from_db()
    assert org.vapi_assistant_id == "as_1"
    assert org.is_provisioned

    # Second run creates nothing new.
    actions2 = ensure_org_provisioned(org, client=client)
    assert actions2["assistant"].startswith("exists")
    assert cred.call_count == 1 and num.call_count == 1 and asst.call_count == 1


@pytest.mark.django_db
def test_provisioning_requires_sip_gateway(settings):
    settings.VAPI_PROVISION = {**settings.VAPI_PROVISION, "SIP_TRUNK_GATEWAY": ""}
    with pytest.raises(ValueError):
        ensure_org_provisioned(Organization.get_default(), client=make_client())
