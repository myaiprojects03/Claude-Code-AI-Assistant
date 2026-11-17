---
title: Technical standard: Microsoft 365 tenant hardening
type: technical
client: none
sensitivity: internal
---
# Technical standard: Microsoft 365 tenant hardening

## Identity
- Enforce phishing-resistant MFA for admins (FIDO2 keys) and MFA for all users through conditional access.
- Block legacy authentication tenant-wide.
- Use privileged identity management for admin roles; no standing global admins except two break-glass accounts.

## Email
- SPF, DKIM and DMARC at p=quarantine as a minimum, moving to p=reject after a monitored month.
- Disable automatic external forwarding.

## Data
- Sensitivity labels for client matters; retention policies agreed with the client's records owner.
- External sharing limited to named guests; review guest access quarterly.
