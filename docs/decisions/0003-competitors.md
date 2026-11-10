# 0003: Competitive landscape, and where TableSense can (and can't) win

**Date:** 2026-11-10
**Status:** Accepted (research only; revisit after the 5 owner interviews)

## Context
Phase 0 of the [roadmap](../02-roadmap.md) asks who already sells computer vision to restaurants, what they charge, and why they have or haven't taken off. Risk 8 in [08-risks.md](../08-risks.md) says "someone already does this well". This file answers that.

**How to read it.** **[F]** marks a fact from a cited source, usually the vendor's own marketing, so "fact" means "they say so", not "independently verified". **[G]** marks my guess or inference. All research was done on 2026-11-10 by web search. No vendor was contacted, and no demo was seen.

---

## 1. Direct competitors: computer vision on restaurant tables

### FlowLinks (a Greymatterz product, US) ⚠️ closest match
- **What it does [F]:** detects when guests leave and starts a turnover clock. Flags tables not cleared within a target time and confirms when a table is wiped. Alerts floor staff ("60s from table vacancy to staff alert"). Shift and daily turnover reports, multi-outlet comparison. A separate queue and wait-time page exists. No greet-time or staffing feature is described on the table page.
- **Hardware [F]:** existing CCTV, "0 new cameras required". Setup "in days, not months".
- **Pricing [F]:** **from $35 per camera per month**, published.
- **Markets:** not stated. US phone number, member of a New Jersey chamber of commerce [F]. No Irish, UK or EU presence seen [G].
- **Traction:** no customer names, case studies or testimonials on the page [F]. Looks early or thin [G].
- Source: <https://www.flowlinks.ai/restaurants-cafes/table-turnover>, <https://www.flowlinks.ai/restaurants-cafes>

### SciForce (custom build for a ~1,200-location chain)
- **What it does [F]:** table zones per camera and a rule engine with states Available → Waiting for Greeting → Choosing Meals → Waiting for Waiter → Waiting for Food → Needs Cleaning. Measures **seat-to-first-waiter time**. Alerts if no waiter in 3 min, or a dirty table isn't cleared in 10 min. Uses POS events for some transitions. Zone and traffic data was used for shifts and layouts.
- **Results claimed [F]:** waiter response time fell from over 5 min to under 2, cleanup from ~15 min to under 5, Google rating 4.5 → 4.7.
- **Hardware [F]:** existing dining cameras, on-site processing preferred.
- **Pricing:** custom project, not public.
- **Why it matters [G]:** this is almost exactly TableSense's full roadmap, including the "time to first contact" feature the [product brief](../01-product-brief.md) treats as our moat. It already exists as a consultancy build for chains.
- Source: <https://www.sciforce.solutions/case-studies/computer-vision-restaurant-service-optimization>, <https://medium.com/sciforce/optimizing-multi-zone-restaurant-service-with-computer-vision-for-hospitality-3316f009ec5b>

### FootfallCam (UK-headquartered people-counting company, global)
- **What it does [F]:** for QSR dine-in, seating occupancy, time between customers, delays in clearing, cleaning alerts, turnover rate, plus queue length and wait times at ordering and pickup.
- **Hardware [F]:** mainly **its own devices** (Pro1 in-store IP camera, Pro2 3D entrance counter) and a "Centroid" local hub that can also take existing CCTV feeds. It's unclear whether table metrics work on CCTV alone.
- **Pricing:** not public. Device calculator and resellers [F].
- **Markets [F]:** offices in ~40 countries including the UK, Germany and France. QSR case studies in the UK, Netherlands, Norway, UAE, US and others.
- **Traction [G]:** strong in retail footfall. Restaurant table occupancy looks like a side module, not a focus. A real distribution threat if they push it, because they already sell into UK chains.
- Source: <https://www.footfallcam.com/Industries/QSR>, <https://www.footfallcam.com/Industries/QSRTechnology>

### Wobot.ai (India / US, "video intelligence" on existing CCTV)
- **What it does [F]:** process-compliance checks on existing cameras (hygiene, SOPs, equipment), raising tickets on deviations. A third-party review describes a "table cleaned within 2 minutes" style rule. Its own dine-in page is generic, not a table product.
- **Hardware [F]:** existing CCTV, onboarding "in less than 10 minutes".
- **Pricing:** not public. Demo and free trial [F].
- **Markets and traction [F]:** customers include Rebel Foods, CureFit, Kitopi and Mezeh. Funding reports conflict ($2.5M–$5.4M). Strong in India, the Middle East and US chains. No Irish presence seen [G].
- **Threat [G]:** a horizontal platform where "table states" is one more rule template. Sells to chains, not to 15-table independents.
- Source: <https://wobot.ai/industry/dine-in>, <https://tabletalkai.beehiiv.com/p/wobot-ai-big-brother-in-a-good-way-for-your-restaurant>, <https://craft.co/wobot-intelligence>

### EVERYANGLE (Dublin) ⚠️ local warning sign
- **What it does [F]:** computer vision on existing CCTV for retailers, **hospitality venues** and service stations. Founded 2019. Raised €2.7M in Oct 2022 (Sure Valley, ACT, Furthr VC, Enterprise Ireland), 13 staff at the time, for US and UK expansion.
- **Now [F]:** the homepage reads "Agentic intelligence for physical retail", with a 14-day pilot on existing CCTV, GDPR-safe. No hospitality or table features found.
- **Interpretation [G]:** a funded Dublin team with the same "existing CCTV, privacy-safe" pitch, aimed at the same Irish hospitality market, appears to have narrowed to retail. The most likely reason is that hospitality buyers didn't pay enough, or setup per venue cost too much. **Talk to someone who has worked there, or to their investors, before going further.** This is the cheapest lesson available.
- Source: <https://www.siliconrepublic.com/start-ups/everyangle-computer-vision-cctv-funding>, <https://tech.eu/2022/10/31/reviewing-the-footage-from-the-eye-in-the-sky-everyangle-raises-eur27-million/>, <https://www.everyangle.ai/>

### Tenyks (Cambridge, UK)
- **What it does [F]:** existing CCTV analysed for service speed, order accuracy, layout, and customer demographics and "mood". Burger King is an early adopter.
- **Pricing [F]:** **from £100/month**. Raised ~£3M and is preparing a new round for the US, Canada, Brazil and Dubai (Oct 2025).
- **Threat [G]:** QSR-focused, not table-service. Proves a UK team can sell CCTV analytics at ~£100/month. Their "mood and demographics" tracking is exactly the GDPR exposure we avoid.
- Source: <https://ukai.co/resource/tenyks-leverages-cctv-data-to-boost-retail-and-hospitality-efficiency-through-ai-innovation.html>

### Eyezilla (Loughborough, UK)
- **[F]** Directory listings only: "AI assistant for restaurant operations integrating with existing CCTV and POS", founded 2018, ~$200K pre-seed (2022). Profiles unclaimed, no reviews.
- **[G]** Probably tiny or dormant. One more sign that UK CCTV-for-restaurants startups stay small.
- Source: <https://serchen.com/company/eyezilla>, <https://www.cbinsights.com/company/eyezilla/alternatives-competitors>

### Presto Vision (US), the cautionary tale
- **[F]** A computer vision product for front-of-house ops, piloted at Outback Steakhouse franchises from 2019. The parent company Presto later wound down its tablet business, bet on drive-thru voice AI, and was sold to its lender and investors.
- **[G]** Even with a large chain pilot, front-of-house CV didn't become the business. No public evidence it scaled.
- Source: <https://www.fsrmagazine.com/industry-news/outback-testing-computer-vision-platform/>, <https://thespoon.tech/presto-launches-computer-vision-system-for-front-of-house-restaurant-operations>, <https://restaurantbusinessonline.com/technology/ai-supplier-presto-sold-group-including-lender-former-investor>

### Custom builds and long-tail vendors (the "Melonleaf-style" category)
| Who | What | Notes |
|-----|------|-------|
| **Melonleaf** (Austin / Gurugram IT consultancy) | YOLO seat occupancy, heatmaps and turnover dashboard for a multi-branch restaurant in **Malta** | Claims +45% seating utilisation with no baseline [F]. Proves EU restaurants buy this as a one-off project [G]. <https://melonleaf.com/case-studies/customer-flow-restaurant-seat-occupancy-detection-via-ai-system-built-with-computer-vision> |
| **Web Design Studio London** | 4 overhead cameras and an on-site edge box for a 68-cover London restaurant. Live occupancy, walk-in queue with SMS wait estimates, cover-duration analytics. 14-week build | Same edge, no-video-leaves architecture as [0001](0001-edge-processing-no-video-upload.md) [F]. A London agency can build our product as a project [G]. <https://webdesignstudio.london/case-studies/real-time-table-intelligence> |
| **NWarch AI** (Daten & Wissen, India) | Table occupied or vacant, duration, host dashboard, peak-hour reports, on existing CCTV | Claims −20% waits and +15% turnover, customer unnamed [F]. <https://datenwissen.com/nwarch-ai/occupancy-monitoring/> |
| **HeadRandom / PolyXGO** | Occupied / Vacant-Ready / Vacant-Dirty with a 5–10 min cooldown, edge PC and cloud dashboard | A blog "idea" page, no product or pricing [F]. Same three states as TableSense. <https://headrandom.com/edge-ai-camera-restaurant-table-tracker> |
| **Open-source on GitHub** | TableWatch, ChronoDine, DineSight-AI (with **5-minute waiter dispatch timers**), ai-restaurant-cctv-monitoring (YOLO + ByteTrack + waiter visits) | Several free repos implement our core pipeline [F]. The CV itself is a commodity [G]. <https://github.com/gpradeep7898/Table-Watch->, <https://github.com/Mashood-Basharat/chrono-dine>, <https://github.com/CodewithShahriar/DineSight-Ai>, <https://github.com/anuj1611/ai-restaurant-cctv-monitoring> |

### General video-security platforms that could add this
- **Solink [F]:** cloud video on existing ONVIF and RTSP cameras, synced to POS transactions (Toast, Lightspeed). About **$175/month per location** including hardware (third-party figure). Pitches "see what's slowing service" to full-service restaurants. <https://www.spot.ai/blog/solink-pricing-2026>, <https://www.lightspeedhq.com/uk/integrations/solink/>
- **Spot AI [F]:** camera-agnostic people counting, dwell and heatmaps. Talks about underused restaurant sections. <https://spot.ai/blog/people-counting>
- **Verkada [F]:** Occupancy Trends on its own cameras. Verkada says not to use it as an exact counter. <https://help.verkada.com/verkada-cameras/analytics-and-detection/occupancy-trends>
- **[G]** These vendors already have the camera relationship and a monthly bill. A "table status" template is a small feature for them. Mostly US-focused today. Archangel AI (UK) sells behaviour detection on existing pub CCTV but not table ops: <https://archangelprotect.ai/ai-cctv-london>

---

## 2. Substitutes venues already use

| Substitute | What it already gives the owner | Cost | Gap vs TableSense |
|-----------|----------------------------------|------|-------------------|
| **Lightspeed Restaurant (Tempo)**, common in Irish/UK independents [G] | Floor plan shows how long each table has been open vs its average. **Tempo alerts** on the floor plan when a service step runs longer than expected. Colour-coded table status [F] | Part of POS plan, tier-dependent [F] | Clock starts at order or bill open, not at sitting down. Can't see a dirty, uncleared table [G] |
| **Toast POS / Toast Tables** | Table colours by order status, table timer on the POS, seated turn-time countdown in the host app, waitlist timers that go yellow and red [F] | Bundled | Toast has a large Dublin engineering office since 2017 [F]. I found no evidence it sells POS in Ireland [G, unverified] |
| **Square for Restaurants** | Table management settings, coursing [F] | Bundled | Same POS blind spots |
| **ResDiary** (UK, widely used in Ireland [G]) | Table management, floor plan, POS integration [F] | Plans quoted by booking volume. Add-ons €10–40/month (POS integration €10) [F] | Host marks tables seated and free by hand. The data quality depends on staff tapping [G] |
| **OpenTable / SevenRooms / Eat App** | Floor plan, turn times, waitlist. Eat App has a free tier [F] | Free to hundreds per month | Same manual-tap problem |
| **Coaster pagers** (LRS UK and generic) | Guest buzzed when the table is ready. Some systems include table call buttons [F] | **€90–€180 one-off** for 10–20 pagers on Irish marketplaces [F] | Solves the queue, not unserved or dirty tables. Extremely cheap and staff already understand it |
| **A good floor manager** | Eyes. In a 10–30 table cafe, one person can see the whole room [G] | Already paid | Doesn't produce data, a weekly report or evidence for rota decisions |

Sources: <https://www.lightspeedhq.com/uk/blog/table-service-epos-system/>, <https://www.lightspeedhq.com/pos/restaurant/tempo/>, <https://support.toasttab.com/article/Using-Toast-Tables-Waitlist>, <https://pos.toasttab.com/news/media-alert-toast-opens-office-in-dublin-to-expand-talent-pool>, <https://resdiary.com/pricing?region=IE>, <https://www.capterra.co.uk/software/23375/resdiary>, <https://www.fruugo.ie/restaurant-pager-system-20-pagers-wireless-coaster-style-for-hotels-bars-restaurants-efficient-communication-and-order-management/p-425064923>, <https://www.onbuy.com/ie/p/wireless-restaurant-pager10-coasters-buzzer-pager-receiver-eu-plug~p189220096/>

---

## 3. Where TableSense could be different, and where it has no edge

### No edge (be honest)
1. **The technology.** YOLO + ByteTrack + table polygons + a state machine is in at least four free GitHub repos and several vendor pages. Our three states (empty / seated / needs clearing) match HeadRandom's and FlowLinks' exactly. The tabletop check and AWAY hold are good engineering. A buyer can't see them and won't pay for them [G].
2. **"Time to first contact".** The product brief treats this as the thing POS can't see. SciForce already measures seat-to-first-waiter and alerts at 3 minutes for a 1,200-site chain. It needs staff-vs-guest detection, which we haven't built [F/G].
3. **Price.** FlowLinks publishes $35/camera/month and Tenyks £100/month. We can't undercut much and still pay for setup visits [G].
4. **Edge and privacy.** WDSL, SciForce and EVERYANGLE all sell "video never leaves the building". It's table stakes in the EU, not a differentiator [F].
5. **Live alerts.** FlowLinks, SciForce and HeadRandom already ship staff alerts. Ours are the last item in our own build order ([0002](0002-report-before-alerts.md)).

### Possible edge (all unproven, test in interviews)
1. **Local and hands-on in Ireland.** No competitor found has an Irish sales or install presence for table monitoring. The one Irish player (EVERYANGLE) seems to have left hospitality [G]. Setup on messy Hikvision or Dahua NVRs needs someone on-site, and a Dublin founder can do that and a US SaaS can't. This doesn't scale. It does win the first 10 customers.
2. **Independent 10–30 table venues.** Every funded player sells to chains (Burger King, Rebel Foods, Outback, 1,200-site chains). Nobody is packaging this for a single-site owner [G]. Risk: there may be a reason, namely that independents won't pay.
3. **The staffing report as the product, not the alerts.** Competitors lead with alerts and turnover. A weekly "you were overstaffed Tue 3–5pm, understaffed Sat 1–2pm" ties to labour cost, the owner's biggest controllable cost. It needs no staff buy-in. Only Melonleaf and NWarch mention peak-hour reports, and only as a side feature [G].
4. **Plays alongside the POS rather than against it.** Pair the camera "sat down" and "left" events with the POS "bill opened" and "bill paid" events. This shows the gaps either source misses alone: sat but not ordered, paid but not cleared. Lightspeed and Toast both have integration marketplaces [F]. That's a distribution channel and a defensible angle [G].

### Signs from the market (the uncomfortable part)
- No company found has visible, independently reported traction selling table-state CV to restaurants. Presto Vision faded, EVERYANGLE seems to have moved to retail, Eyezilla looks dormant, and FlowLinks has no named customers [F/G]. Either the market is open, or it **doesn't pay**. The evidence leans toward "doesn't pay at independent scale" [G].
- The claimed results (+45%, +15%, −20%) are all vendor-reported with no baselines [F].

---

## Decision
1. Don't position on technology, privacy or alerts. They aren't differentiators.
2. Position (to be tested) as: **"a weekly staffing and table-turn report for independent Irish venues, set up in person on your existing cameras, for less than a pager system costs per year"**. Combine with POS data where the venue uses Lightspeed.
3. Add to the [customer discovery](../07-customer-discovery.md) interviews: "Does your POS floor-plan timer already tell you this?" and "Would you rather pay €X/month for this or a pager system?"
4. Before more engineering, try to speak to someone from EVERYANGLE about why hospitality didn't work for them.

## Consequences
- The engineering moat is admitted to be small. The bet moves to local service, the small-venue segment and the report.
- If interviews show owners rely on their POS timer or their own eyes, that confirms the "doesn't pay" reading. Then stop, or pivot to selling to chains via a POS integration, where SciForce, Wobot and FootfallCam are already ahead.
- Pricing needs to land near the FlowLinks and Tenyks reference points (roughly €30–€100/month per venue) [G].
