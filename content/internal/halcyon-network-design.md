---
title: Halcyon Manufacturing: network design
type: technical
client: halcyon.example.com
sensitivity: client_confidential
---
# Halcyon Manufacturing: network design

## Address plan
Hub 10.40.0.0/22 in UK South. Plant networks: Leeds 172.20.0.0/16, Derby 172.21.0.0/16, Swindon 172.22.0.0/16.

## Firewall
FortiGate 600F active-passive cluster; firewall rule review with Halcyon's security team is the current blocker for go-live.
