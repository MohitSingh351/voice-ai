import pytest

from apps.calls.models import Call
from apps.campaigns.models import Campaign, CampaignLead
from apps.campaigns.services.dispatch import (
    available_slots,
    count_in_flight,
    maybe_complete,
    plan_and_reserve,
)
from apps.campaigns.services.throttle import reserve_call_budget
from apps.leads.models import Lead
from apps.organizations.models import Organization


def _campaign(**kwargs):
    org = Organization.get_default()
    defaults = dict(
        organization=org,
        name="Test",
        status=Campaign.Status.RUNNING,
        max_concurrent_calls=2,
        calls_per_minute=100,
    )
    defaults.update(kwargs)
    return Campaign.objects.create(**defaults)


def _add_leads(campaign, n):
    org = campaign.organization
    cls = []
    for i in range(n):
        lead = Lead.objects.create(
            organization=org, name=f"L{i}", phone_e164=f"+1415555{i:04d}"
        )
        cls.append(CampaignLead(campaign=campaign, lead=lead))
    CampaignLead.objects.bulk_create(cls)


# -- throttle ---------------------------------------------------------------
def test_reserve_call_budget_caps_per_minute():
    cid = 999001
    assert reserve_call_budget(cid, calls_per_minute=3, want=2) == 2
    assert reserve_call_budget(cid, calls_per_minute=3, want=5) == 1  # only 1 left
    assert reserve_call_budget(cid, calls_per_minute=3, want=5) == 0


def test_reserve_call_budget_unthrottled_when_zero():
    assert reserve_call_budget(999002, calls_per_minute=0, want=7) == 7


# -- slot math + reservation -----------------------------------------------
@pytest.mark.django_db
def test_available_slots_reflects_in_flight():
    c = _campaign(max_concurrent_calls=3)
    _add_leads(c, 5)
    assert available_slots(c) == 3
    CampaignLead.objects.filter(campaign=c).order_by("id")[:2]  # noqa
    ids = list(c.campaign_leads.values_list("id", flat=True)[:2])
    CampaignLead.objects.filter(id__in=ids).update(status=CampaignLead.Status.IN_FLIGHT)
    assert count_in_flight(c) == 2
    assert available_slots(c) == 1


@pytest.mark.django_db
def test_plan_and_reserve_respects_concurrency_cap():
    c = _campaign(max_concurrent_calls=2, calls_per_minute=100)
    _add_leads(c, 5)

    first = plan_and_reserve(c)
    assert len(first) == 2
    for cl in CampaignLead.objects.filter(id__in=first):
        assert cl.status == CampaignLead.Status.IN_FLIGHT
        assert cl.attempts == 1

    # Slots are full -> nothing more reserved.
    assert plan_and_reserve(c) == []

    # Free one slot (simulate a finished call).
    CampaignLead.objects.filter(id=first[0]).update(status=CampaignLead.Status.DONE)
    second = plan_and_reserve(c)
    assert len(second) == 1


@pytest.mark.django_db
def test_plan_and_reserve_respects_rate_budget():
    c = _campaign(max_concurrent_calls=100, calls_per_minute=3)
    _add_leads(c, 10)
    reserved = plan_and_reserve(c)
    assert len(reserved) == 3  # capped by per-minute budget, not slots


@pytest.mark.django_db
def test_invariant_never_exceeds_concurrency_over_many_ticks():
    """Property check: across repeated ticks the in-flight count never exceeds
    max_concurrent_calls (the core throttling guarantee)."""
    c = _campaign(max_concurrent_calls=2, calls_per_minute=1000)
    _add_leads(c, 20)
    for _ in range(20):
        plan_and_reserve(c)
        assert count_in_flight(c) <= 2
        # simulate half of the in-flight calls completing each tick
        in_flight = list(
            c.campaign_leads.filter(status=CampaignLead.Status.IN_FLIGHT)
            .values_list("id", flat=True)[:1]
        )
        CampaignLead.objects.filter(id__in=in_flight).update(
            status=CampaignLead.Status.DONE
        )


@pytest.mark.django_db
def test_maybe_complete_marks_done_when_no_work_left():
    c = _campaign()
    _add_leads(c, 1)
    c.campaign_leads.update(status=CampaignLead.Status.DONE)
    assert maybe_complete(c) is True
    c.refresh_from_db()
    assert c.status == Campaign.Status.COMPLETED
