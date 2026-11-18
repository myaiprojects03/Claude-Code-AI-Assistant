"""Dev seed: fictional consultants and two fictional client companies. Safe to re-run.

    assistant-seed            consultants + client companies
    assistant-seed --demo     ...plus fictional activity for the dashboard (re-runnable)

Northbridge Legal LLP   mid-sized client      contact priya.shah@northbridge.example.com
Halcyon Manufacturing   enterprise client     contact tom.okafor@halcyon.example.com
                        (account manager: Cora Consultant)
"""

import asyncio
import json
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant.db import dispose_engine, get_sessionmaker
from assistant.models import ClientAccess, Company, Consultant, Contact, Project, Ticket

CONSULTANTS = [
    {
        "name": "Cora Consultant",
        # Same as her Keycloak sign-in, so handovers assigned to her show as hers.
        "email": "consultant.dev@example.test",
        "skills": ["discovery", "cloud", "workplace"],
    },
    {
        "name": "Sam Security",
        "email": "security.dev@example.com",
        "skills": ["discovery", "cybersecurity"],
    },
]

CLIENTS = [
    {
        "company": {
            "name": "Northbridge Legal LLP",
            "domain": "northbridge.example.com",
            "size_band": "51-250",
            "is_enterprise": False,
        },
        "contact": {
            "name": "Priya Shah",
            "email": "priya.shah@northbridge.example.com",
            "phone": "+44 20 7946 0101",
            "timezone": "Europe/London",
        },
        "tickets": [
            ("Outlook not syncing on mobile phones", "In Progress", "normal"),
            ("New starter laptop for 1 December", "Waiting for customer", "low"),
        ],
        "projects": [
            (
                "Microsoft 365 migration",
                "on track",
                "Mailboxes for the litigation team were migrated last week. SharePoint "
                "document libraries are being mapped next.",
                "SharePoint migration",
                21,
            ),
        ],
    },
    {
        "company": {
            "name": "Halcyon Manufacturing Group",
            "domain": "halcyon.example.com",
            "size_band": "1000+",
            "is_enterprise": True,
        },
        "contact": {
            "name": "Tom Okafor",
            "email": "tom.okafor@halcyon.example.com",
            "phone": "+44 161 496 0202",
            "timezone": "Europe/London",
        },
        "tickets": [
            ("VPN drops for the Leeds plant every evening", "Investigating", "high"),
        ],
        "projects": [
            (
                "Azure landing zone",
                "at risk",
                "Network design is approved; firewall rule review with Halcyon's security "
                "team is pending.",
                "Firewall rule sign-off",
                10,
            ),
            (
                "Endpoint security rollout",
                "on track",
                "Agents deployed to 60% of devices across three sites.",
                "Remaining two sites",
                30,
            ),
        ],
    },
    {
        "company": {
            "name": "Brightwater Housing",
            "domain": "brightwater.example.com",
            "size_band": "251-1000",
            "is_enterprise": False,
        },
        "contact": {
            "name": "Grace Adeyemi",
            "email": "grace.adeyemi@brightwater.example.com",
            "phone": "+44 117 496 0303",
            "timezone": "Europe/London",
        },
        "tickets": [],
        "projects": [
            (
                "Managed IT onboarding",
                "on track",
                "Discovery complete; tooling setup starts after contract signature.",
                "Tooling setup",
                14,
            ),
        ],
    },
]

# Internal knowledge assistant: which clients each consultant may see (by Keycloak email).
# Admins and managers see every client; consultant.dev is assigned to Northbridge only.
CLIENT_ACCESS = [("consultant.dev@example.test", "northbridge.example.com")]


async def _upsert_consultants(s: AsyncSession) -> dict[str, uuid.UUID]:
    # Earlier seeds used consultant.dev@example.com; move that row rather than duplicate it.
    old = (
        await s.execute(select(Consultant).where(Consultant.email == "consultant.dev@example.com"))
    ).scalar_one_or_none()
    new = (
        await s.execute(select(Consultant).where(Consultant.email == "consultant.dev@example.test"))
    ).scalar_one_or_none()
    if old and not new:
        old.email = "consultant.dev@example.test"
        await s.flush()
    for c in CONSULTANTS:
        await s.execute(
            insert(Consultant)
            .values(**c)
            .on_conflict_do_update(
                index_elements=["email"], set_={"name": c["name"], "skills": c["skills"]}
            )
        )
    rows = (await s.execute(select(Consultant.email, Consultant.id))).all()
    return dict(rows)


async def seed() -> dict[str, int]:
    async with get_sessionmaker()() as s:
        stats = await seed_session(s)
    await dispose_engine()
    return stats


async def seed_session(s: AsyncSession) -> dict[str, int]:
    """Seed using the given session (also used by the eval runner on the test DB)."""
    stats = {"consultants": 0, "companies": 0, "tickets": 0, "projects": 0}
    ids = await _upsert_consultants(s)
    stats["consultants"] = len(CONSULTANTS)
    cora = ids["consultant.dev@example.test"]
    for spec in CLIENTS:
        c = spec["company"]
        co = (
            await s.execute(select(Company).where(Company.domain == c["domain"]))
        ).scalar_one_or_none()
        if co is None:
            co = Company(id=uuid.uuid4(), **c)
            s.add(co)
            stats["companies"] += 1
        co.account_manager_id = cora if c["is_enterprise"] else None
        await s.flush()

        ct = spec["contact"]
        contact = (
            await s.execute(select(Contact).where(Contact.email == ct["email"]))
        ).scalar_one_or_none()
        if contact is None:
            contact = Contact(id=uuid.uuid4(), **ct)
            s.add(contact)
        contact.company_id, contact.is_client = co.id, True
        await s.flush()

        has_tickets = (await s.execute(select(Ticket.id).where(Ticket.company_id == co.id))).first()
        if not has_tickets:
            for summary, status, priority in spec["tickets"]:
                s.add(
                    Ticket(
                        company_id=co.id,
                        contact_id=contact.id,
                        summary=summary,
                        description=summary,
                        status=status,
                        priority=priority,
                        updates=[
                            {
                                "at": "2026-11-10T10:00:00+00:00",
                                "by": "team",
                                "text": f"Status set to {status}.",
                            }
                        ],
                    )
                )
                stats["tickets"] += 1
        has_projects = (
            await s.execute(select(Project.id).where(Project.company_id == co.id))
        ).first()
        if not has_projects:
            for name, status, summary, milestone, days in spec["projects"]:
                s.add(
                    Project(
                        company_id=co.id,
                        name=name,
                        status=status,
                        summary=summary,
                        next_milestone=milestone,
                        lead_consultant_id=cora,
                        next_milestone_date=date.today() + timedelta(days=days),
                    )
                )
                stats["projects"] += 1
    for email, domain in CLIENT_ACCESS:
        company_id = (
            await s.execute(select(Company.id).where(Company.domain == domain))
        ).scalar_one()
        await s.execute(
            insert(ClientAccess)
            .values(staff_email=email, company_id=company_id, granted_by="seed")
            .on_conflict_do_nothing(constraint="uq_client_access")
        )
    await s.commit()
    return stats


DEMO_PEOPLE = [
    ("Jo Bloggs", "Initech", "cloud_migration", "51-250", "10-50k", "1-3m", "decision_maker"),
    (
        "Sam Patel",
        "Brightwater Housing",
        "managed_it",
        "251-1000",
        "50-150k",
        "<1m",
        "decision_maker",
    ),
    (
        "Alex Morgan",
        "Morgan & Daughters",
        "cybersecurity",
        "11-50",
        "unknown",
        "1-3m",
        "decision_maker",
    ),
    ("Lee Chen", "Riverside Dental", "workplace", "1-10", "<10k", "exploring", "decision_maker"),
    ("Maria Rossi", "Fenwick Logistics", "data_analytics", "1000+", "150k+", "3-6m", "influencer"),
    (
        "Tom Hughes",
        "Oakfield School Trust",
        "it_strategy",
        "251-1000",
        "10-50k",
        "6m+",
        "influencer",
    ),
    (
        "Nadia Karim",
        "Kestrel Studio",
        "custom_software",
        "11-50",
        "10-50k",
        "1-3m",
        "decision_maker",
    ),
]
DEMO_TOPICS = {
    "cloud_migration": "Cloud migration",
    "managed_it": "Managed IT",
    "cybersecurity": "Cybersecurity review",
    "workplace": "Microsoft 365",
    "data_analytics": "Data and analytics",
    "it_strategy": "IT strategy",
    "custom_software": "Custom software",
}
DEMO_OPENERS = {
    "Do you help companies move their servers to Azure?": "Yes, we plan and carry out migrations to Azure, AWS or Google Cloud. What are you "
    "running today?",
    "What does a discovery call involve?": "It's a free 30-minute video call with a consultant about your goals and current "
    "systems. Would you like me to find a time?",
    "We need someone to look after our IT, around 300 staff.": "Our managed IT service covers your users, devices and servers. Do you have an "
    "in-house IT team at the moment?",
    "Can you review our Microsoft 365 security?": "We do configuration reviews of Microsoft 365 tenants with a prioritised remediation "
    "plan. Roughly how many users do you have?",
    "How much would a migration cost?": "I can't confirm that here, but a consultant can go through it with you on a free "
    "discovery call. Would you like me to find a time?",
    "What are your office hours?": "Monday to Friday, 09:00 to 17:30 UK time, excluding public holidays.",
    "Do you work with schools?": "Yes, education is one of the sectors we work with. What would you like help with?",
    "We want a Power BI dashboard for the board.": "We build reporting with Power BI, from data preparation to board dashboards. Where "
    "does your data live today?",
}


DEMO_UNANSWERED = [
    "Do you offer SAP consulting?",
    "Can you help us with SAP S/4HANA?",
    "Do you do SAP implementations?",
    "Do you support Linux servers?",
    "Can you manage our Ubuntu servers?",
    "Do you build mobile apps for iPhone?",
    "Are you ISO 27001 certified?",
    "Do you have ISO 27001 certification?",
    "Can you help with Cyber Essentials Plus?",
]


async def demo_activity(s: AsyncSession) -> dict[str, int]:
    """Fictional activity for the dashboard: conversations over 14 days, leads, calls,
    handovers. Marked with state={"demo": true}; re-running replaces the previous demo."""
    import random
    from datetime import UTC, datetime

    from sqlalchemy import delete, func, update

    from assistant.models import Booking, Conversation, Handover, Lead, Message
    from assistant.qualification import assess

    rng = random.Random(42)
    demo_ids = select(Conversation.id).where(Conversation.state["demo"].astext == "true")
    for model in (Handover, Booking, Lead, Message):
        await s.execute(delete(model).where(model.conversation_id.in_(demo_ids)))
    await s.execute(delete(Conversation).where(Conversation.state["demo"].astext == "true"))

    cons = (await s.execute(select(Consultant).order_by(Consultant.email))).scalars().all()
    now = datetime.now(UTC)
    stats = {"conversations": 0, "leads": 0, "bookings": 0, "handovers": 0}
    booked_slots: set[tuple[uuid.UUID, datetime]] = set()
    for day in range(13, -1, -1):
        for _ in range(rng.randint(1, 5) + (2 if day < 4 else 0)):
            at = now - timedelta(days=day, hours=rng.randint(0, 8), minutes=rng.randint(0, 59))
            if at > now:
                at = now - timedelta(minutes=rng.randint(5, 50))
            conv = Conversation(
                id=uuid.uuid4(),
                channel="web",
                state={"demo": True},
                created_at=at,
                updated_at=at + timedelta(minutes=6),
            )
            s.add(conv)
            stats["conversations"] += 1
            opener = rng.choice(list(DEMO_OPENERS))
            s.add_all(
                [
                    Message(conversation_id=conv.id, role="user", content=opener, created_at=at),
                    Message(
                        conversation_id=conv.id,
                        role="assistant",
                        provider="groq",
                        model="openai/gpt-oss-120b",
                        tokens_in=2100,
                        tokens_out=120,
                        content=DEMO_OPENERS[opener],
                        created_at=at + timedelta(seconds=3),
                    ),
                ]
            )
            if rng.random() < 0.55:
                name, company, need, size, budget, timeline, role = rng.choice(DEMO_PEOPLE)
                email = f"{name.split()[0].lower()}.{uuid.uuid4().hex[:4]}@example.com"
                contact = Contact(id=uuid.uuid4(), name=name, email=email, timezone="Europe/London")
                s.add(contact)
                await s.flush()
                conv.contact_id = contact.id
                profile = {
                    "need_category": need,
                    "company_size": size,
                    "budget_range": budget,
                    "timeline": timeline,
                    "role": role,
                }
                if rng.random() < 0.3:
                    profile.pop("timeline")  # still qualifying
                status = assess(profile).status
                s.add(
                    Lead(
                        conversation_id=conv.id,
                        contact_id=contact.id,
                        status=status,
                        need=opener,
                        created_at=at,
                        summary=f"Lead status: {status}\nCompany: {company}",
                        **profile,
                    )
                )
                stats["leads"] += 1
                if status in ("qualified", "enterprise") and cons and rng.random() < 0.7:
                    c = rng.choice(cons)
                    start = (now + timedelta(days=rng.randint(1, 9))).replace(
                        hour=rng.choice([10, 11, 14, 15]),
                        minute=rng.choice([0, 30]),
                        second=0,
                        microsecond=0,
                    )
                    if start.weekday() < 5 and (c.id, start) not in booked_slots:
                        booked_slots.add((c.id, start))
                        s.add(
                            Booking(
                                consultant_id=c.id,
                                contact_id=contact.id,
                                conversation_id=conv.id,
                                starts_at=start,
                                ends_at=start + timedelta(minutes=30),
                                status="confirmed",
                                topic=DEMO_TOPICS[need],
                                idempotency_key=uuid.uuid4().hex,
                                calendar_event_id=f"local:{uuid.uuid4().hex[:16]}",
                            )
                        )
                        stats["bookings"] += 1
    # Questions the public content has nothing for (the dashboard's "unanswered topics").
    stats["unanswered"] = 0
    no_results = json.dumps(
        {
            "results": [],
            "note": "No approved information on this. Say you don't know and offer a call "
            "with a consultant. Do not guess.",
        }
    )
    for question in DEMO_UNANSWERED:
        at = now - timedelta(days=rng.randint(0, 12), hours=rng.randint(0, 8))
        conv = Conversation(
            id=uuid.uuid4(), channel="web", state={"demo": True}, created_at=at, updated_at=at
        )
        s.add(conv)
        stats["conversations"] += 1
        s.add_all(
            [
                Message(conversation_id=conv.id, role="user", content=question, created_at=at),
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    provider="groq",
                    model="openai/gpt-oss-120b",
                    tool_calls=[
                        {"id": "c1", "name": "search_knowledge", "arguments": {"query": question}}
                    ],
                    created_at=at + timedelta(seconds=2),
                ),
                Message(
                    conversation_id=conv.id,
                    role="tool",
                    tool_name="search_knowledge",
                    tool_call_id="c1",
                    content=no_results,
                    created_at=at + timedelta(seconds=2),
                ),
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    provider="groq",
                    model="openai/gpt-oss-120b",
                    content="I don't have approved information on that. A consultant can answer "
                    "it on a free discovery call. Would you like me to find a time?",
                    created_at=at + timedelta(seconds=4),
                ),
            ]
        )
        stats["unanswered"] += 1
    await s.flush()

    convs = (
        (
            await s.execute(
                select(Conversation)
                .where(Conversation.state["demo"].astext == "true")
                .order_by(Conversation.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    # One for the team queue, one for the dev consultant, one for the manager (so a
    # consultant can see it but not take it).
    for conv, (reason, urgency, summary, assigned) in zip(
        convs[:3],
        [
            (
                "security_incident",
                "p1",
                "Visitor reported: our files are encrypted, maybe ransomware.",
                None,
            ),
            (
                "pricing_negotiation",
                "normal",
                "Wants to discuss a multi-year managed IT deal.",
                "manager.dev@example.test",
            ),
            (
                "complaint_or_legal",
                "high",
                "Unhappy that a laptop rollout slipped by a week.",
                "consultant.dev@example.test",
            ),
        ],
        strict=False,
    ):
        s.add(
            Handover(
                conversation_id=conv.id,
                reason=reason,
                urgency=urgency,
                route_to=f"#handover / {assigned or 'consultant.dev@example.com'}",
                assigned_to=assigned,
                summary=summary,
                created_at=conv.created_at + timedelta(minutes=2),
            )
        )
        if reason != "security_incident":
            conv.status = "handed_over"
        stats["handovers"] += 1
    await s.flush()
    # Later edits bump updated_at; make the demo timeline look like real activity again.
    await s.execute(
        update(Conversation)
        .where(Conversation.state["demo"].astext == "true")
        .values(updated_at=func.least(Conversation.created_at + timedelta(minutes=6), func.now()))
    )
    await s.commit()
    return stats


async def seed_demo() -> dict[str, int]:
    async with get_sessionmaker()() as s:
        await seed_session(s)
        stats = await demo_activity(s)
    await dispose_engine()
    return stats


def main() -> None:
    import sys

    if "--demo" in sys.argv:
        print(f"demo activity: {asyncio.run(seed_demo())}")
    else:
        print(f"seeded: {asyncio.run(seed())}")


if __name__ == "__main__":
    main()
