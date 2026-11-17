---
title: Technical standard: Azure landing zone
type: technical
client: none
sensitivity: internal
---
# Technical standard: Azure landing zone

## Structure
Management groups: Platform (identity, management, connectivity) and Landing Zones (corp, online). Subscriptions per workload environment.

## Networking
Hub-and-spoke with Azure Firewall or a client-approved third-party firewall in the hub. All spokes route through the hub; no direct internet egress from spokes.

## Governance
Azure Policy initiatives for allowed regions (UK South, UK West), required tags (owner, cost-centre, environment) and diagnostic settings to a central Log Analytics workspace.

## Hand-off
Deliver the landing zone as code (Bicep or Terraform) in the client's repository, with a pipeline and a runbook.
