"""Phase 1 tools. Later phases add qualification, booking, tickets and handover."""

from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field

from assistant.tools.registry import OutboxSpec, Planned, Tool, ToolContext

ALL_CHANNELS = {"web", "whatsapp", "internal"}


class CurrentTimeArgs(BaseModel):
    timezone: str = Field(default="Europe/London", description="IANA timezone, e.g. Europe/London")


class GetCurrentTime(Tool):
    name = "get_current_time"
    description = "Get the current date and time in a timezone."
    args_model = CurrentTimeArgs
    channels = ALL_CHANNELS

    async def run(self, ctx: ToolContext, args: CurrentTimeArgs) -> dict[str, Any]:
        try:
            tz = ZoneInfo(args.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return {"error": f"unknown timezone '{args.timezone}'"}
        now = datetime.now(tz)
        return {"iso": now.isoformat(timespec="minutes"), "weekday": now.strftime("%A")}


class SendEmailArgs(BaseModel):
    to: EmailStr
    subject: str = Field(max_length=200)
    body: str = Field(max_length=10_000)


class SendEmail(Tool):
    """Staff-only for now (e.g. sending a drafted follow-up). Goes through the outbox."""

    name = "send_email"
    description = "Send a plain-text email. Only use when the user explicitly asks to send it."
    args_model = SendEmailArgs
    channels = {"internal"}
    side_effect = True

    async def plan(self, ctx: ToolContext, args: SendEmailArgs, key: str) -> Planned:
        return Planned(
            [OutboxSpec("email.send", {"to": args.to, "subject": args.subject, "body": args.body})]
        )


class NotifyTeamArgs(BaseModel):
    message: str = Field(max_length=2000)
    urgency: Literal["low", "normal", "high"] = "normal"


class NotifyTeam(Tool):
    name = "notify_team"
    description = "Post a short message to the team's Slack channel."
    args_model = NotifyTeamArgs
    channels = {"internal"}
    side_effect = True

    async def plan(self, ctx: ToolContext, args: NotifyTeamArgs, key: str) -> Planned:
        return Planned(
            [
                OutboxSpec(
                    "slack.notify",
                    {
                        "text": args.message,
                        "urgency": args.urgency,
                        "conversation_id": str(ctx.conversation_id),
                    },
                )
            ]
        )


def default_tools() -> list[Tool]:
    return [GetCurrentTime(), SendEmail(), NotifyTeam()]
