# 8. Risks

Ordered by how likely each one is to kill the project.

| # | Risk | Why it's serious | How we'll find out early | Mitigation |
|---|------|------------------|--------------------------|------------|
| 1 | **No footage / no pilot venue** | No public dataset matches real CCTV in restaurants | Phase 0, week 1–2 | Use personal network, offer a free report, try many cafes |
| 2 | **Owners won't pay** (POS is "good enough") | No business | Interviews (Q11–12) | Focus on what POS can't see; sell the report first |
| 3 | **Camera access blocked** (locked NVR, cloud-only, analog) | Product can't be installed | Ask Q9 at every venue | Support common brands first; small IP camera as a fallback |
| 4 | **Accuracy on real angles** (fisheye, low light, occlusion) | Wrong states → no trust | Phase 1 eval on test split | Fine-tune the detector, use multiple cameras, smoothing |
| 5 | **False alerts** | Staff mute the app permanently | Phase 4 shadow mode | Alerts last, and only behind a measured gate |
| 6 | **GDPR / staff pushback** | Legal blocker or refusal | DPA/DPIA review, staff question in interviews | Edge processing, no faces, no per-staff tracking |
| 7 | **Setup cost per venue** | Doesn't scale | Measure setup time from pilot 1 | Zone-drawing tool, default thresholds by venue type |
| 8 | **Competition** | Someone already does this well | Phase 0 competitor research | Find the gap (price, small venues, privacy) or pivot |
| 9 | **Scope creep** | Never ships | Roadmap exit gates | Don't start a phase before its gate is met |
