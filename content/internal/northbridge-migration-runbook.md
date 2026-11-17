---
title: Northbridge Legal: migration runbook
type: technical
client: northbridge.example.com
sensitivity: client_confidential
---
# Northbridge Legal: migration runbook

## Cutover
Each batch cuts over on a Friday at 18:00 after the partners' weekly meeting. MX records point to Microsoft 365 from batch one; Exchange 2016 stays as a hybrid server until batch three.

## Matter sites
Each matter gets a SharePoint site named after its NBL matter number, with the retention label "Client matter - 15 years".

## Known issue
Outlook on iOS failed to sync shared calendars for some litigation users; fixed by re-adding the shared mailbox through Outlook's account settings (ticket T-1001).
