"""External system adapters. Each has a free local default and a real implementation."""

from assistant.integrations.calendar import CalendarAdapter, GoogleCalendar, LocalCalendar
from assistant.integrations.crm import CRMAdapter, HubSpotCRM, LocalCRM
from assistant.settings import Settings, get_settings


def build_crm(s: Settings | None = None) -> CRMAdapter:
    s = s or get_settings()
    if s.crm_provider == "hubspot":
        if not s.hubspot_token:
            raise ValueError("CRM_PROVIDER=hubspot needs HUBSPOT_TOKEN")
        return HubSpotCRM(s.hubspot_token)
    return LocalCRM()


def build_calendar(s: Settings | None = None) -> CalendarAdapter:
    s = s or get_settings()
    if s.calendar_provider == "google":
        if not s.google_service_account_file:
            raise ValueError("CALENDAR_PROVIDER=google needs GOOGLE_SERVICE_ACCOUNT_FILE")
        return GoogleCalendar(s.google_service_account_file)
    return LocalCalendar()
