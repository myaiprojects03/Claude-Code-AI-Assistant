---
title: Halcyon Manufacturing: Azure landing zone proposal
type: proposal
client: halcyon.example.com
sensitivity: client_confidential
---
# Halcyon Manufacturing: Azure landing zone proposal

## Summary
Build an Azure landing zone for Halcyon's ERP and plant-floor analytics, connected to its three UK plants (Leeds, Derby, Swindon).

## Approach
Landing zone as code in Terraform; hub-and-spoke networking with Halcyon's existing Fortinet FortiGate 600F cluster as the hub firewall; site-to-site VPNs to each plant.
