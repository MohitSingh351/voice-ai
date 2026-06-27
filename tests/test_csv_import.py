import io

import pytest

from apps.leads.models import Lead
from apps.leads.services.csv_import import import_leads_csv, normalize_phone
from apps.organizations.models import Organization


def _csv(text: str) -> io.BytesIO:
    return io.BytesIO(text.encode("utf-8"))


def test_normalize_phone_e164_passthrough():
    assert normalize_phone("+14155552671", "US") == "+14155552671"


def test_normalize_phone_national_with_region():
    assert normalize_phone("(415) 555-2671", "US") == "+14155552671"


def test_normalize_phone_invalid_raises():
    with pytest.raises(ValueError):
        normalize_phone("not-a-number", "US")


@pytest.mark.django_db
def test_import_basic_with_extra_columns():
    org = Organization.get_default()
    csv = "name,phone,company\nAda Lovelace,+14155552671,Analytical Engines\n"
    result = import_leads_csv(_csv(csv), org)

    assert result.created_count == 1
    assert result.error_count == 0
    lead = Lead.objects.get()
    assert lead.name == "Ada Lovelace"
    assert lead.phone_e164 == "+14155552671"
    assert lead.variables == {"company": "Analytical Engines"}
    assert lead.call_variables() == {"name": "Ada Lovelace", "company": "Analytical Engines"}


@pytest.mark.django_db
def test_import_header_aliases():
    org = Organization.get_default()
    csv = "Full Name,Mobile\nGrace Hopper,415-555-2671\n"
    result = import_leads_csv(_csv(csv), org)
    assert result.created_count == 1


@pytest.mark.django_db
def test_import_collects_row_errors_and_continues():
    org = Organization.get_default()
    csv = (
        "name,phone\n"
        "Good One,+14155552671\n"
        ",+14155552672\n"          # missing name
        "Bad Phone,xxxxx\n"        # invalid phone
    )
    result = import_leads_csv(_csv(csv), org)
    assert result.created_count == 1
    assert result.error_count == 2
    reasons = {e.reason for e in result.errors}
    assert "missing name" in reasons


@pytest.mark.django_db
def test_import_skips_duplicates_in_file_and_db():
    org = Organization.get_default()
    Lead.objects.create(organization=org, name="Existing", phone_e164="+14155552671")
    csv = (
        "name,phone\n"
        "Dup Of Existing,+1 415 555 2671\n"   # already in DB
        "New Person,+14155559999\n"
        "New Person Again,+14155559999\n"      # dup within file
    )
    result = import_leads_csv(_csv(csv), org)
    assert result.created_count == 1
    assert result.skipped_duplicates == 2


@pytest.mark.django_db
def test_import_missing_required_columns():
    org = Organization.get_default()
    result = import_leads_csv(_csv("foo,bar\n1,2\n"), org)
    assert result.created_count == 0
    assert result.error_count == 1
