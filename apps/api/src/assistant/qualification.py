"""Lead qualification: deterministic rules (editable in config), not LLM judgement.

The LLM gathers answers conversationally; this module decides the status, so the outcome is
explainable and testable. Rules live in config key `qualification.rules`.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

NeedCategory = Literal[
    "cloud_migration",
    "managed_it",
    "cybersecurity",
    "workplace",
    "data_analytics",
    "custom_software",
    "it_strategy",
    "other",
]
CompanySize = Literal["1-10", "11-50", "51-250", "251-1000", "1000+"]
BudgetRange = Literal["<10k", "10-50k", "50-150k", "150k+", "unknown"]
Timeline = Literal["<1m", "1-3m", "3-6m", "6m+", "exploring"]
Role = Literal["decision_maker", "influencer", "other"]

PROFILE_FIELDS = (
    "need",
    "need_category",
    "company_size",
    "current_systems",
    "budget_range",
    "timeline",
    "role",
)

DEFAULT_RULES: dict[str, Any] = {
    # Fields we try to collect before deciding (contact details are collected separately).
    "required": ["need_category", "company_size", "budget_range", "timeline", "role"],
    "qualifying_needs": [
        "cloud_migration",
        "managed_it",
        "cybersecurity",
        "workplace",
        "data_analytics",
        "custom_software",
        "it_strategy",
    ],
    "min_sizes": ["11-50", "51-250", "251-1000", "1000+"],
    "qualifying_budgets": ["10-50k", "50-150k", "150k+"],
    # Budget unknown still qualifies when the person can make the decision.
    "unknown_budget_ok_for_roles": ["decision_maker"],
    "qualifying_timelines": ["<1m", "1-3m", "3-6m"],
    "enterprise_sizes": ["1000+"],
}


@dataclass
class Assessment:
    status: Literal["new", "qualified", "unqualified", "enterprise"]
    missing: list[str]
    reasons: list[str] = field(default_factory=list)


def assess(profile: dict[str, Any], rules: dict[str, Any] | None = None) -> Assessment:
    r = {**DEFAULT_RULES, **(rules or {})}
    missing = [f for f in r["required"] if not profile.get(f)]

    if profile.get("company_size") in r["enterprise_sizes"]:
        return Assessment("enterprise", missing, ["enterprise-sized organisation"])
    if missing:
        return Assessment("new", missing)

    reasons: list[str] = []
    if profile["need_category"] not in r["qualifying_needs"]:
        reasons.append("need outside our services")
    if profile["company_size"] not in r["min_sizes"]:
        reasons.append("below minimum company size")
    budget, role = profile["budget_range"], profile.get("role")
    budget_ok = budget in r["qualifying_budgets"] or (
        budget == "unknown" and role in r["unknown_budget_ok_for_roles"]
    )
    if not budget_ok:
        reasons.append("budget not confirmed")
    if profile["timeline"] not in r["qualifying_timelines"]:
        reasons.append("timeline beyond six months")
    return Assessment("unqualified" if reasons else "qualified", [], reasons)
