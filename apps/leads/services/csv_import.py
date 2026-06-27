"""Parse, normalize and validate a leads CSV into Lead rows.

Expected columns (header names are matched case-insensitively):
  - name   (aliases: full_name, contact, contact_name)
  - phone  (aliases: phone_number, mobile, number, tel)
Any other columns are stored on Lead.variables and forwarded to Vapi as
variableValues for prompt personalization (e.g. company, city, product).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

import phonenumbers

from apps.leads.models import Lead
from apps.organizations.models import Organization

NAME_ALIASES = {"name", "full_name", "fullname", "contact", "contact_name"}
PHONE_ALIASES = {"phone", "phone_number", "phonenumber", "mobile", "number", "tel", "cell"}


@dataclass
class RowError:
    line: int
    reason: str
    raw: dict


@dataclass
class ImportResult:
    created: list[Lead] = field(default_factory=list)
    skipped_duplicates: int = 0
    errors: list[RowError] = field(default_factory=list)

    @property
    def created_count(self) -> int:
        return len(self.created)

    @property
    def error_count(self) -> int:
        return len(self.errors)


def normalize_phone(raw: str, default_region: str) -> str:
    """Return an E.164 string, or raise ValueError if invalid."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("empty phone")
    try:
        parsed = phonenumbers.parse(raw, None if raw.startswith("+") else default_region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError(f"unparseable phone: {exc}") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("invalid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _resolve_columns(fieldnames: list[str]) -> tuple[str | None, str | None]:
    name_col = phone_col = None
    for col in fieldnames:
        key = (col or "").strip().lower().replace(" ", "_").replace("-", "_")
        if name_col is None and key in NAME_ALIASES:
            name_col = col
        elif phone_col is None and key in PHONE_ALIASES:
            phone_col = col
    return name_col, phone_col


def import_leads_csv(
    file_obj,
    organization: Organization,
    *,
    default_region: str = "US",
    skip_duplicates: bool = True,
) -> ImportResult:
    """Import a CSV file-like (text or bytes) into Lead rows."""
    data = file_obj.read()
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(data))

    result = ImportResult()
    if not reader.fieldnames:
        result.errors.append(RowError(0, "empty file / no header row", {}))
        return result

    name_col, phone_col = _resolve_columns(list(reader.fieldnames))
    if not name_col or not phone_col:
        result.errors.append(
            RowError(0, "CSV must contain a name column and a phone column", {})
        )
        return result

    extra_cols = [
        c for c in reader.fieldnames if c not in (name_col, phone_col) and c
    ]
    existing = set(
        organization.leads.values_list("phone_e164", flat=True)
    )
    seen_in_file: set[str] = set()

    for i, row in enumerate(reader, start=2):  # line 1 is the header
        name = (row.get(name_col) or "").strip()
        raw_phone = (row.get(phone_col) or "").strip()
        if not name:
            result.errors.append(RowError(i, "missing name", dict(row)))
            continue
        try:
            phone = normalize_phone(raw_phone, default_region)
        except ValueError as exc:
            result.errors.append(RowError(i, str(exc), dict(row)))
            continue

        if phone in existing or phone in seen_in_file:
            if skip_duplicates:
                result.skipped_duplicates += 1
                continue
            result.errors.append(RowError(i, "duplicate phone", dict(row)))
            continue

        variables = {c: (row.get(c) or "").strip() for c in extra_cols}
        result.created.append(
            Lead(
                organization=organization,
                name=name,
                phone_e164=phone,
                raw_phone=raw_phone,
                variables=variables,
                source=Lead.Source.CSV,
            )
        )
        seen_in_file.add(phone)

    if result.created:
        Lead.objects.bulk_create(result.created)
    return result
