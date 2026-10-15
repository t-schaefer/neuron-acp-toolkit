# Cerebrum write path for TAKE / Undo (design note, 2026-10-15)

Status: **investigation only, nothing changed in the router's write path.**
Probe script: `cerebrum/probe_xml_api.py` (read-only, refuses ACTION).

## Problem

The router writes ACP2 set-property directly to the Neuron (`acp2/writer.py`,
`router/take.py`). The device announces every change to every client that has
enabled ACP2 events. `acp2/listen_events.py` shows a router write and a
Cerebrum GUI write identically (IPG03A, slot 1, obj 167852, pid 8). The
Cerebrum device GUI still keeps its cached value: it showed 19.5 while the
device, ACP2 and REST had 19.0. It shows the new value only after it re-reads
the device. So the device is correct and Cerebrum's view of it is stale. It
looks like Cerebrum's ACP2 driver ignores announces it did not cause, or never
enables them and relies on its own (slow or one-off) reads.

## What we know about Cerebrum

| Fact | Source |
|---|---|
| Cerebrum controls Neuron with its own **ACP/ACP2 driver** ("Synapse/Neuron - Device control and monitoring") | EVS product sheet + protocol list V2.8 |
| Northbound API = **CRBM-XML-CI**, "Native XML web socket interface". It is a **licensed option**: a community report has a Cerebrum where it was not enabled until the northbound/web licence was added | product sheet; DataMiner Dojo |
| Test server `ws://10.12.81.19:40007`: `426 Upgrade Required`, `Server: WebSocket++/0.8.2`, XML text frames, root element needs `MTID`, else `NACK ERROR="MTID_ERROR" (1)`; bad XML -> `INVALID_XML (3)`; anything before login -> `NOT_LOGGED_IN (6)` | own probe 2026-08-30 (session "Cerebrum API für Multicast-Troubleshooting") |
| Login was never found. Only mixed-case `<Login .../>` was tried, but the server's own elements are UPPER CASE (`<NACK>`), so `<LOGIN MTID=".." USERNAME=".." PASSWORD=".."/>` is the next thing to try **with a real API account** | same |
| DataMiner's Cerebrum connector logs in with user/password, then subscribes to topics. It reads and sets crosspoints, salvos and mnemonics | docs.dataminer.services |
| 2026-10-15: `10.12.81.19:40007` and `:80` do not answer from this Mac (timeout); IPG03A `:2072` does. Which Cerebrum server manages IPG03A, and whether it has the XML-CI licence, is **unknown** | own check |

**No public CRBM-XML-CI spec was found.** From memory of how the interface is
usually described, its commands are LOGIN, POLL, SUBSCRIBE/UNSUBSCRIBE,
OBTAIN (read) and ACTION (write), with change types such as ROUTING_CHANGE
and DEVICE_CHANGE (device / sub-device / parameter / value). **This is
unverified.** Get the spec from EVS support or the VIA partner portal, or ask
internally at Qvest, before building anything on it.

## Options

### A. Write through Cerebrum (XML-CI ACTION / DEVICE_CHANGE)

Cerebrum sends the ACP2 set itself, so its cache and GUI follow.

Needs:
- XML-CI licence on the Cerebrum that manages the IPGs, plus an API account
  (service user) and network access from the router PC to port 40007
- the spec: login, the read/write message for a device parameter, the ACK
  semantics (accepted vs. applied)
- a mapping from ACP2 object ids (what `take.py` uses) to Cerebrum's device /
  parameter addressing. This is probably the ACP2 tree path (for example
  `PROCESSING VIDEO/PATH H/CHANNEL 04/EMBEDDER/AUDIO CHANNEL 1/Input Backup`)
  under the Cerebrum device name. Confirm it with OBTAIN on one parameter.
- keep the ACP2 fresh-read before the write and the ACP2 read-back after it.
  Cerebrum's ACK must not count as proof.

Risks:
- a new dependency in the live path: if Cerebrum is down, slow or failing
  over, TAKE stops (or must fall back to ACP2, which brings back the stale GUI)
- a TAKE can be up to 200 writes: unknown throughput and pacing through
  Cerebrum, and unknown behaviour if Cerebrum queues
- the credential has to be stored on the router PC (it is an unattended
  SYSTEM service)
- XML-CI may also allow routing, salvos and so on. Allow-list the messages the
  same way `probe_xml_api.py` does.
- licence cost and lead time

### B. Keep direct ACP2, then trigger a Cerebrum re-read after TAKE / Undo

Needs one of these, in order of preference:
1. **Cerebrum-side setting (no code):** find out why the Neuron ACP2 driver
   does not take over announces from other clients. Look for a driver option
   such as "enable events / announces" or a parameter poll interval in the
   Cerebrum device configuration, or ask EVS whether this is a known
   driver limitation or bug. If Cerebrum honours announces, the problem is
   gone for every client (router, scripts, Neuron web UI).
2. **XML-CI request:** a re-read of the device or of the changed parameters
   (an OBTAIN/POLL that forces a device read, if one exists). This needs the
   same licence, account and spec as A, but no write goes through Cerebrum.
3. **Manual:** the operator re-opens or refreshes the device in Cerebrum
   (or disables and enables the device in the Cerebrum config, which
   reconnects the driver and re-reads the tree). The router UI already says
   "Cerebrum shows the new values only after it re-reads the device".

Risks: B1 none for the router. A driver reconnect (B3) may briefly show the
device as offline in Cerebrum, but does not touch the device. B2 has the
dependency and credential risks of A, but if Cerebrum is down only the GUI
stays stale, and TAKE still works.

### C. Keep direct ACP2 as it is (status quo)

Needs nothing. The router, ACP2 and REST show the truth, and Cerebrum is
stale until it re-reads. Risk: an operator trusts the stale Cerebrum value and
"corrects" it in the Cerebrum GUI, which overwrites the TAKE. The router's
TAKE then refuses its next run, or its Undo, because "someone changed it
since", which is safe but confusing.

## Recommendation

1. **Now: C, plus a clear note.** Keep the direct ACP2 path, which is
   verified, fast and has read-back. Make the "Cerebrum is stale after TAKE"
   hint visible next to the TAKE result (this is a UI change, not done here).
2. **Ask EVS / the Cerebrum admin (B1).** Why does the Neuron ACP2 driver not
   apply announces from other clients, and is there an events or poll setting?
   It is the only fix that needs no new dependency.
3. **In parallel, get XML-CI access**: the licence status of the production
   Cerebrum, an API account and the spec. Then confirm `<LOGIN>`, OBTAIN of
   one IPG03A parameter, and its addressing with
   `cerebrum/probe_xml_api.py --login` (read-only).
4. **Only then decide between B2 and A.** Prefer B2 (direct ACP2 write, then
   ask Cerebrum to re-read) over A, so that TAKE never depends on Cerebrum.
   Choose A only if Cerebrum must be the single writer (audit/log in Cerebrum,
   or locks/permissions enforced there). Even then, keep the ACP2 fresh-read
   and read-back as the proof.

## Open questions for the user / EVS

- Which Cerebrum server manages IPG03A (address)? Is CRBM-XML-CI licensed
  there? Is 10.12.81.19 still the test server, and why is it unreachable today?
- Is there an API service account for port 40007?
- Is the "announce from another client not applied" behaviour known for
  the Cerebrum ACP2 Neuron driver? Is there a poll interval or an events
  option?
