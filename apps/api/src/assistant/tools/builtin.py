"""Phase 1 tools. Later phases add qualification, booking, tickets and handover."""

import re
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field

from assistant import identity
from assistant.tools.registry import OutboxSpec, Planned, Tool, ToolContext, ToolRejected

# The person's own latest message must ask for the action.
SEND_INTENT = re.compile(
    r"\b(send|email (it|this|them|her|him)|mail it|go ahead and (send|email))\b", re.I
)
POST_INTENT = re.compile(r"\b(post|notify|tell the team|let the team know|slack|ping)\b", re.I)

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
    offer_when = SEND_INTENT

    async def plan(self, ctx: ToolContext, args: SendEmailArgs, key: str) -> Planned:
        # The decision to send, and the recipient, must come from the person, not the model
        # (eval finding: asked to *draft*, a small model sent to an invented address).
        latest = await identity.latest_visitor_message(ctx.session, ctx.conversation_id) or ""
        if not SEND_INTENT.search(latest):
            raise ToolRejected(
                "Not sent: the user didn't ask to send. Show the draft in your "
                "reply instead and ask if they want it sent."
            )
        typed = await identity.emails_typed_by_visitor(ctx.session, ctx.conversation_id, 6)
        if args.to.lower() not in typed:
            raise ToolRejected(
                "Not sent: only send to an address the user has typed. Ask them "
                "for the recipient's email."
            )
        if ctx.channel == "internal" and not await _allowed_recipient(ctx, args.to):
            # Phase 7 finding: drafts are built from confidential documents, so they may only
            # go to the firm or to a client this person can see, never a personal address.
            raise ToolRejected(
                "Not sent: internal documents can only be emailed to our own domain or to "
                "a client you have access to. Show the draft so they can send it themselves."
            )
        return Planned(
            [OutboxSpec("email.send", {"to": args.to, "subject": args.subject, "body": args.body})]
        )


async def _allowed_recipient(ctx: ToolContext, to: str) -> bool:
    from sqlalchemy import select

    from assistant.models import ClientAccess, Company
    from assistant.settings import get_settings

    domain = to.rsplit("@", 1)[-1].lower()
    if domain in get_settings().firm_email_domains_list:
        return True
    q = select(Company.domain).where(Company.domain.is_not(None))
    if not ({"admin", "manager"} & set(ctx.roles)):
        q = q.join(ClientAccess, ClientAccess.company_id == Company.id).where(
            ClientAccess.staff_email == (ctx.actor_email or "").lower()
        )
    client_domains = {d.lower() for d in (await ctx.session.execute(q)).scalars()}
    return domain in client_domains


class NotifyTeamArgs(BaseModel):
    message: str = Field(max_length=2000)
    urgency: Literal["low", "normal", "high"] = "normal"


class NotifyTeam(Tool):
    name = "notify_team"
    description = "Post a short message to the team's Slack channel."
    args_model = NotifyTeamArgs
    channels = {"internal"}
    side_effect = True
    offer_when = POST_INTENT

    async def plan(self, ctx: ToolContext, args: NotifyTeamArgs, key: str) -> Planned:
        latest = await identity.latest_visitor_message(ctx.session, ctx.conversation_id) or ""
        if not POST_INTENT.search(latest):
            raise ToolRejected("Not posted: the user didn't ask to notify the team.")
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
