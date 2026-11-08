# 6. Privacy and GDPR

You're in Ireland, so GDPR applies and the Data Protection Commission (DPC) is the regulator. This isn't legal advice. Before selling, get a short review from a data protection solicitor or advisor. Many offer fixed-fee startup packages.

## Why it matters here

The cameras were installed for **security**. Using the footage to analyze guest behavior and staff activity is a **new purpose**. That requires its own lawful basis, transparency, and most likely a Data Protection Impact Assessment (DPIA), since it involves systematic monitoring. Monitoring employees is a sensitive area and needs particular care, including telling staff.

## Roles

- **The venue** is the *data controller* (their cameras, their premises).
- **We** are the *data processor*. We need a **Data Processing Agreement (DPA)** with every venue, including pilots.

## Design rules (privacy by design)

1. **Process on-site.** Video never leaves the venue in normal operation.
2. **No faces, no identities.** No face recognition and no re-identification across days. Track IDs are temporary and reset.
3. **Discard frames** right after inference. Only states, counts and timestamps are stored.
4. **No individual staff performance tracking.** Report on tables and shifts, not "waiter X was slow". This is ethical, avoids a lot of legal risk, and makes staff more likely to accept the system.
5. **Retention limits.** Event data: e.g. 12 months, configurable. Pilot footage used for labeling: deleted on an agreed date.
6. **Access control.** Pilot footage is stored encrypted, only on our machines, and never committed to git (`data/` is git-ignored).

## Staff alerts (email / SMS)

- Alert messages contain only a table id and minutes: no images, no descriptions of guests.
- Email and SMS pass through third parties (e.g. Google, Twilio), so list them as sub-processors in the DPA.
- **Served** taps are stored per table, not per staff member. Don't add staff names: this must not become individual performance tracking.
- Staff phone numbers and email addresses are personal data. Keep them only in the venue's `.env` on the edge device, and remove them when someone leaves.

## Pilot footage (the exception)

To build and evaluate the system we *do* need recorded footage. For pilots:
- A written agreement stating the purpose (R&D for this system), what's recorded, where it's stored, who can access it, and a deletion date
- Signage at the venue telling guests that footage is used for service analytics
- Staff informed in advance
- Blur faces in any frame used outside labeling (e.g. screenshots in a pitch deck or on the CV)

## Checklist before the first pilot

- [ ] DPA template (processor ↔ controller)
- [ ] Draft DPIA for the venue (we can supply a template, which also helps sales)
- [ ] Signage text
- [ ] Staff notice text
- [ ] Retention and deletion procedure written down
- [ ] Short review by a data protection professional
