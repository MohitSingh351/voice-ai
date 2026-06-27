from django.core.management.base import BaseCommand, CommandError

from apps.organizations.models import Organization
from apps.vapi.provisioning import ensure_org_provisioned, webhook_server_url


class Command(BaseCommand):
    help = "Create the Vapi BYO-SIP credential, phone number and assistant (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Recreate resources even if IDs already exist on the organization.",
        )

    def handle(self, *args, **options):
        org = Organization.get_default()
        if not webhook_server_url():
            self.stdout.write(
                self.style.WARNING(
                    "PUBLIC_WEBHOOK_BASE_URL is empty — the assistant will be created "
                    "without a webhook server URL. Set it (your tunnel) and re-run with "
                    "--force to receive call events."
                )
            )
        try:
            actions = ensure_org_provisioned(org, force=options["force"])
        except (ValueError, Exception) as exc:  # noqa: BLE001 - surface clearly to CLI
            raise CommandError(str(exc)) from exc

        for resource, outcome in actions.items():
            self.stdout.write(f"  {resource:14s} {outcome}")
        self.stdout.write(self.style.SUCCESS("Vapi provisioning complete."))
