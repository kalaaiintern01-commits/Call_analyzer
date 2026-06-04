"""Static product knowledge baked into the agent's system prompt.

Source: PDF catalogs in ../../catalogs/ (Kirloskar OEM range as of 2026).
Update this when the catalog changes — the agent quotes from this directly.
"""

PRODUCT_KNOWLEDGE = """\
Kala Genset Pvt Ltd is the OEM for Kirloskar Oil Engines gensets. Product range:

== CPCB IV+ compliant (latest Indian emission standard) ==
- 7.5 to 20 kVA — small commercial, retail, residential
- 25 to 58.5 kVA — small factories, hotels, offices
- 82.5 to 160 kVA — medium factories, IT parks, commercial buildings
- 200 to 250 kVA — large factories, malls, hospitals
- 320 to 750 kVA — heavy industrial, manufacturing

== HHP (High Horsepower) range ==
- 1010 to 1500 kVA — very large industrial, data centers, multi-MW sites

== Kirloskar Powergen Optiprime range ==
- 117 to 2000 kVA — designed for prime power (continuous running, not just backup)

== Standard configurations ==
- Fuel: diesel (default). Gas and petrol options available on request.
- Phase: single-phase up to 25 kVA, three-phase standard above that.
- AMF (Auto Mains Failure) panel: optional add-on, recommended for unmanned sites.
- Enclosure: silent canopy / acoustic enclosure available on all models.

== Lead times (typical) ==
- 7-14 days for stock items up to 250 kVA
- 4-6 weeks for custom configs and HHP range

== Coverage ==
We supply across Maharashtra, Madhya Pradesh, Goa, and Karnataka.
Manufacturing in Chakan/Pune and Bangalore. HO in Pimpri-Chinchwad near Auto Cluster.
We make 9000+ gensets every year.

== Pricing ==
DO NOT quote exact prices. Always say: "Our team will share a formal quotation by
email/WhatsApp once we have your exact requirement." Capture the customer's expected
budget but don't volunteer prices.
"""
