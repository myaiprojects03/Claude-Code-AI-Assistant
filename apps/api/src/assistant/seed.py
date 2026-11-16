"""Dev seed: fictional consultants who take discovery calls. Safe to run repeatedly.

assistant-seed
"""

import asyncio

from sqlalchemy.dialects.postgresql import insert

from assistant.db import dispose_engine, get_sessionmaker
from assistant.models import Consultant

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


async def seed() -> int:
    async with get_sessionmaker()() as s:
        for c in CONSULTANTS:
            await s.execute(
                insert(Consultant)
                .values(**c)
                .on_conflict_do_update(
                    index_elements=["email"], set_={"name": c["name"], "skills": c["skills"]}
                )
            )
        await s.commit()
    await dispose_engine()
    return len(CONSULTANTS)


def main() -> None:
    print(f"seeded {asyncio.run(seed())} consultants")


if __name__ == "__main__":
    main()
