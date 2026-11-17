"""Dev seed: fictional consultants and two fictional client companies. Safe to re-run.

    assistant-seed

Northbridge Legal LLP   mid-sized client      contact priya.shah@northbridge.example.com
Halcyon Manufacturing   enterprise client     contact tom.okafor@halcyon.example.com
                        (account manager: Cora Consultant)
"""

import asyncio
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant.db import dispose_engine, get_sessionmaker
from assistant.models import Company, Consultant, Contact, Project, Ticket

CONSULTANTS = [
    {
        "name": "Cora Consultant",
        "email": "consultant.dev@example.com",
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
]


async def _upsert_consultants(s: AsyncSession) -> dict[str, uuid.UUID]:
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
    cora = ids["consultant.dev@example.com"]
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
    await s.commit()
    return stats


def main() -> None:
    print(f"seeded: {asyncio.run(seed())}")


if __name__ == "__main__":
    main()
