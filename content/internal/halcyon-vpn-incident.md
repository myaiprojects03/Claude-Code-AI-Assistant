---
title: Halcyon Manufacturing: Leeds VPN drop investigation
type: technical
client: halcyon.example.com
sensitivity: client_confidential
---
# Halcyon Manufacturing: Leeds VPN drop investigation

## Symptom
The Leeds plant VPN drops every evening at about 19:05 for two to four minutes (ticket T-1003).

## Findings so far
The drop coincides with the plant's scheduled backup job saturating the 100 Mbps line. Dead-peer detection then times out.

## Next step
Rate-limit the backup job and raise the DPD timeout to 45 seconds; agree a change window with Tom Okafor.
