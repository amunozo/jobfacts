# Three viable document-extraction business ideas

Research of 17 November 2026. Eleven ideas were researched on three questions: what domain
knowledge they need and whether one person can learn it alone, whether there is real paid demand,
and how much competition there is. Each was then checked by a second researcher who tried to
disprove the findings. None works as a standalone software business for one person. These three
are the only ones with a gap that held up, and each still needs customer interviews before any
code is written.

How much to trust this:

- Most evidence comes from search-result snippets: many sites were blocked in the research
  environment, so pages could not be read in full.
- Prices and revenue ceilings marked "estimate" are the researchers' judgement, not published
  figures.
- Company claims (customer numbers, accuracy, funding) are as the companies report them.

The order is the recommendation: the first idea is the best business, the third the easiest to
build.

---

## 1. Client-takeover intake for Swiss insurance and pension brokers

### The idea

When a Swiss corporate broker wins a new company client, they have to understand everything the
client already has: company insurance policies with their general conditions (AVB), the BVG
pension affiliation contract, the pension fund's rules (Reglement) and the pension certificates.
Today someone reads all of it by hand.

The tool takes those documents in German, French or Italian and returns a structured register:

- **Insurance:** sums insured, deductibles, sublimits, exclusions, expiry and cancellation dates.
- **Pension (BVG):** savings credits by age band, coordination deduction, risk benefits for
  disability and death, cost terms.

Each value links to the sentence it came from, or says "not stated". The register flags gaps
against a checklist, exports into broker software such as BrokerStar, and sends renewal and
cancellation reminders. A later step is comparing a pension fund's rules year over year, since
funds publish new versions each January.

It deliberately does **not** do the offer comparison itself. That part already runs through
Sobrado (see Competition).

### Why it fits jobfacts

- The documents are uploaded by the broker, who already holds them, so access is legal.
- Brokers carry liability for their advice, so a quote behind every value and an honest
  "not stated" are what they need.
- Documents come in three languages, which jobfacts already handles and measures.
- Most documents are text PDFs, but key pension figures sit in tables. **PDF table reading is
  required**, and old policies may be scans.

### Domain knowledge

What to learn:

- BVG pension mechanics: coordinated salary, savings credits, conversion rate, mandatory versus
  extra benefits, funding ratio.
- How collective pension foundations work: contracts, cost rules, surplus sharing, what happens
  when a client leaves.
- Swiss insurance contract law and how policy conditions are built for each commercial line:
  property, liability, daily sickness, accident, D&O, cyber.
- How brokers work and earn: commission versus fees, how tenders run, what they check first.
  89% of brokers rate a fund's funding ratio "very important"
  ([IFZ study 2025](https://www.ubs.com/microsites/focus/de/trends/2025/ifz-studie-2025.html)).

Can you learn it alone? **Partly.** Laws, published fund rules and policy conditions are free to
read:

- About 2–3 months to design a good pension schema.
- 1–2 more months for two or three insurance lines.
- Knowing which differences actually matter comes from practice. The professional diploma in
  occupational pensions is a 4-semester part-time course, which shows how deep the expertise goes.
  You don't need it.
- **Realistically 3–6 months with a practising broker as a design partner.**

What else it needs:

- No licence to sell software to brokers. Recommending insurers directly to employers would move
  toward regulated intermediation, so stay a broker tool.
- Business German is essential, and French for Romandie.
- The broker scene is small, conservative and runs on relationships. An insider partner, such as a
  former broker or pension consultant, is close to mandatory.

### Demand

**Moderate.** Evidence:

- Large brokers pay people to do this by hand. Qualibroker advertises pension roles that include
  evaluating offers
  ([job ad](https://jobup.ch/en/jobs/detail/98debec7-964e-457f-a439-90740271a55c)), and Kessler
  hires occupational-pension specialists
  ([job ad](https://www.jobup.ch/en/jobs/detail/a48e1914-5b5b-4295-834e-07226e7775be)).
- UBS priced a basic pension offer comparison by advisers at about CHF 15,000
  ([UBS, 2021](https://www.ubs.com/microsites/focus/de/services/2021/advice-occupational-pensions.html)).
- German trade press says a broker can hardly compare the conditions of 20+ insurers across
  several lines in detail by hand
  ([cash-online](https://www.cash-online.de/a/gewerbeversicherungen-luecken-und-chancen-725288/2/)).

Who pays and how much:

- Small and mid-size corporate broker firms (5–50 staff). Brokers earn commission from insurers,
  so software comes out of their own margin.
- Price anchors are low: German broker comparison tools cost EUR 35–199 per user per month
  ([softguide](https://www.softguide.de/programm/smart-check-transparenz-und-glaubwuerdigkeit-fuer-den-versicherungsvergleich)).
- **Estimate:** CHF 3–15k a year per small or mid-size broker. There are a few hundred relevant
  firms in Switzerland, so the ceiling is roughly CHF 0.5–2M a year in revenue: a decent one-person
  business, not a venture-scale one.

### Competition

**Moderate:** the only idea of the eleven not rated high or saturated.

- **Sobrado:** owned by insurers (Swiss Life majority). The platform through which brokers request
  pension offers, and it returns offer data in a standard format. It claims about 90% of offer
  requests arrive automatically ([Sobrado](https://landing.sobrado.ch/en/?p=149)). It covers the
  offer comparison, not the client's existing policies or fund rules.
- **Humbrela:** Swiss startup unifying insurance data for brokers. CHF 150,000 from Venture Kick
  in 2025
  ([Venture Kick](https://www.venturekick.ch/Humbrela-receives-CHF-150000-from-Venture-Kick-to-build-the-first-unified-insurance-intelligence-layer)).
- **ScyAI:** Zurich startup analysing companies' property insurance programmes. EUR 2M pre-seed in
  2026 ([report](https://parsers.vc/news/260216-scyai-secures-2m--revolutionizes-enterprise/)).
- **DGTAL:** Swiss AI search and analysis over insurance documents
  ([report](https://www.cash-online.de/a/europaeisches-saas-insurtech-dgtal-launcht-ki-gestuetztes-analyse-tool-662121/)).
- **Credura:** AI insurance adviser that reads policies, but for private customers
  ([report](https://fintech.global/2026/01/19/credura-launches-ai-based-insurance-adviser-for-private-customers/)).
- **US policy-analysis startups** Qumis ($4.3M), Outmarket ($4.7M) and Advocate ($18M) are
  English-only and US-focused.

The gap: no Swiss product was found that turns a new client's existing pension and insurance
documents into a quote-backed register in three languages. The gap is narrow, though. Sobrado,
a broker-software vendor or a general AI tool could close it.

### Risks

- **Sobrado dependence.** If Sobrado adds AI extraction, part of the value disappears.
- **Liability.** A wrong conversion rate or missed exclusion can feed a multi-year decision.
  Contracts need liability caps and a human review step.
- **Sensitive personal data.** Pension certificates contain salaries, social security numbers and
  sometimes disability status. Brokers will expect Swiss or EU hosting and a data processing
  agreement.
- **Episodic work.** A client changes pension fund only every few years. Takeovers are more
  frequent, but volume per broker is modest.
- **"Good enough" substitute.** Brokers can paste PDFs into ChatGPT.

### First step and when to stop

1. Interview 8–10 small and mid-size corporate brokers.
2. Ask each for one past client-takeover file and how many hours it took.
3. Sign one or two as design partners who help label documents.
4. Consider broker-software vendors (BrokerStar, Smart InsurTech) as distribution partners rather
   than competitors.

**Stop if** fewer than three brokers would pay at least about CHF 3–5k a year.

---

## 2. Supplier spec intake and change detection for Swiss food producers

### The idea

Food producers receive specification sheets from every raw-material supplier: ingredients with
sub-ingredients, an allergen matrix (contains / may contain / free from), nutrition values, origin
and certificates with expiry dates. Their quality team checks each one against the producer's own
requirements and copies the data into their systems or into retailer portals.

The tool reads the spec PDFs in German, French or Italian and backs every value with a quote. It
then flags:

- what changed against the previous version of the same spec;
- what doesn't match the producer's raw-material requirements;
- certificates that are missing or expired.

It exports a pre-filled file for a person to check and submit. Target: mid-size Swiss producers and
co-packers that supply Migros or Coop.

It is sold as decision support for the quality team. It makes **no** claim that a label is legally
compliant: legal responsibility stays with the food company, and Swiss food law is being revised.

### Why it fits jobfacts

- Measured accuracy per field is the real differentiator here. No vendor publishes accuracy for
  allergen extraction, and a reviewer said one cheap tool's AI transfer rarely works
  ([review](https://www.softwareadvice.com/nutrition-analysis/food-label-maker-profile)).
- "Not stated" matters: a missing allergen statement does not mean the allergen is absent.
- **Needs PDF and table reading, and probably scans,** from day one. Losing which column a tick
  belongs to is the main technical risk.

### Domain knowledge

What to learn:

- The Swiss food information ordinance
  ([LIV](https://fedlex.admin.ch/filestore/fedlex.data.admin.ch/eli/oc/2017/152/de/pdf-a/fedlex-data-admin-ch-eli-oc-2017-152-de-pdf-a-1.pdf))
  and the EU equivalent (Regulation 1169/2011): the 14 allergens, mandatory particulars, nutrition
  rounding, origin rules.
- How a spec sheet is laid out, and related documents: certificates of analysis, origin
  declarations, allergen statements.
- Audit standards (IFS, BRCGS, FSSC 22000) and the supplier-approval workflow they require.
- How retailer portals work. Migros Industrie launched its MIND-Spec portal in February 2026, where
  suppliers type every mandatory field by hand
  ([Migros Industrie](https://www.migrosindustrie.ch/supplier/en/?p=1218)).

Can you learn it alone? **Partly.**

- About 2–3 months of reading to design the schema and checks. Free starting points: the
  [ZHAW paper on allergen law](https://digitalcollection.zhaw.ch/bitstream/11475/27153/3/2023_KirchsteigerMeier_Rechtsgrundlagen-Allergene_SZE.pdf)
  and the
  [Migros supplier training material](https://prod-migrosindustrie-data.storage.googleapis.com/files/sites/6/20251106105702/Training_Material_Suppliers_EN.pdf).
- 6–12 more months of working with real customers before you can talk credibly with quality
  managers.
- Job ads for this work ask for a food technology or chemistry degree plus 3–5 years in production.
  **A part-time food technologist partner, or a paid ZHAW adviser, is strongly advised.**

What else it needs:

- German and French to sell in Switzerland.
- No licence was found to be required.
- Supplier specs are confidential and not on the web. **Training and test data can only come from
  design partners under NDA,** so the accuracy work can't start before you sign one.

### Demand

**Moderate.** Evidence:

- Swiss producers employ quality staff whose duties include approving raw materials and writing
  specifications, for example
  [Gautschi](https://jobmaps.ch/company/242807/profile/88521?lang=en) and
  [Agrano](https://www.medi-karriere.ch/job/stellenanzeige-qualitätsmanager-in-allschwil_bl-hygiene_und_umweltmedizin-79eec2e5aa85278e00c660553673c1f5/).
  UK manufacturers employ dedicated "specification technologists" at GBP 28–42k a year
  ([job ad](https://www.drnewitt.com/job/senior-specification-technologist-238659)).
- In a survey of 483 food suppliers, 48% still used spreadsheets for document exchange
  ([Food Safety Tech](https://foodsafetytech.com/news_article/48-of-fb-suppliers-rely-on-spreadsheets/)).
- Mistakes are costly: Swiss cantonal chemists tested about 500 foods and pulled products with
  undeclared milk
  ([ZHAW](https://www.zhaw.ch/de/lsfm/institute-zentren/ilgi/lebensmittelrecht-und-qm/news/detailansicht-news/event-news/kontrollkampagne-der-kantonschemiker-der-schweiz-korrekte-deklaration-der-allergene-in-vorverpackten-lebensmitteln-trotzdem-ist-wachsamkeit-geboten)).
- Investors are funding it: Backbone (Brussels) raised EUR 4M in September 2026 for AI that checks
  supplier documents against specifications
  ([tech.eu](https://tech.eu/2026/09/03/backbone-raises-eur4m-for-automated-food-quality-and-compliance/)).

Who pays and how much:

- The head of quality or technical director at mid-size producers.
- Price anchors: spec management engagements of USD 10–50k (third-party estimate for Foods
  Connected), and a human label check at CHF 295 ([Bio-Inspecta](https://www.bio-inspecta.ch/en/label-check)).
- **Estimate:** low tens of thousands of CHF a year per mid-size producer, if the tool clearly
  saves quality-team hours. Not verified.

### Competition

**High,** and close to saturated for generic extraction.

- **Large vendors already ship AI extraction:**
  - TraceGains, since March 2025
    ([report](https://www.qualityassurancemag.com/tracegains-launches-ai-powered-intelligent-document-processing-to-improve-ingredient-safety-compliance)).
    It has sold in German-speaking countries, Switzerland included, since 2025
    ([report](https://industrialnews.co.uk/?p=413309)).
  - Trace One Copilot
    ([press release](https://www.traceone.com/en/plm-blog/press-release-trace-one-advances-its-ai-plm-for-food-and-beverage-with-intelligent-formulation-compliance-automation-and-supplier-onboarding)).
  - Specright
    ([press release](https://www.businesswire.com/news/home/20250918268323/en/Specright-Announces-New-AI-and-Specification-First-PLM-Capabilities-for-the-Food-Beverage-Industry)).
- **SpecPage** (Küssnacht SZ, part of Revalize) is the established Swiss vendor. It has no visible
  AI feature
  ([Revalize](https://revalizesoftware.com/new-specpage)), which makes it either a threat or a
  partner or buyer.
- **AI-native startups:** Backbone (EUR 4M), Tacto (Munich), Loamy ($6M) and Agriplace's document
  scanner.
- **Label checking** is covered by IMERO (AI plus state-certified food chemists, lists Switzerland
  as supported) ([IMERO](https://www.imero.io/en/länder)) and by testing labs.
- **Cheap tools:** Food Label Maker includes AI spec parsing at about USD 49–99 a month.

The gap: no AI-native startup focused on food-spec checking turned up in German-speaking countries,
and nobody publishes measured accuracy. The opening is narrow: intake and change detection for
Swiss specs in three languages, or an AI component sold to SpecPage.

### Risks

- **Liability for missed allergens.** A wrong "free from" can harm consumers and trigger recalls.
  Contracts need a human sign-off step and liability caps; get Swiss legal advice before calling
  anything a "compliance check".
- **Rules are changing.** The federal food office opened a consultation on 16 food ordinances in
  October 2026
  ([ZHAW](https://www.zhaw.ch/de/lsfm/institute-zentren/ilgi/lebensmittelrecht-und-qm/news/detailansicht-news/event-news/blv-revision-von-verordnungen-im-lebensmittelrecht-vernehmlassung-eroeffnet)).
- **Retailer portals.** They require personal logins, so automatically filling them for suppliers
  likely breaks their terms. Export a file for a person to submit instead.
- **Small market.** No count of Swiss food producers was found. Growing beyond Switzerland means
  competing with TraceGains, Trace One and Tacto in Germany and Austria.
- **SpecPage** could add AI to its existing customer base at any time.

### First step and when to stop

1. Find a food technologist adviser.
2. Interview 5–8 quality managers at mid-size Swiss producers.
3. Ask one or two for a set of past specs under NDA and measure how many hours their team spends
   per spec.
4. In parallel, ask SpecPage whether they would license an extraction component.

**Stop if** no producer will share real specs, or none would pay for the hours saved.

---

## 3. Swiss court-damages comparables (Genugtuung)

### The idea

Switzerland has no fixed table for moral damages (Genugtuung) after injury or death. Lawyers and
insurers set amounts by looking for earlier court decisions with similar facts
([Mobiliar guide](https://www.mobiliar.ch/ratgeber/genugtuung-schmerzensgeld-schadenersatz)).

The tool builds a structured, searchable database of these awards from published federal and
cantonal decisions on entscheidsuche.ch, in German, French and Italian. Fields include:

- legal basis and case type;
- injury and body part, and permanent impairment %;
- victim age band, and relationship to the deceased;
- reduction for the victim's own fault;
- amount claimed and amount awarded, year, court and canton.

Every value carries a quote from the decision, or "not stated".

### Why it fits jobfacts

This is the **best technical fit** of all eleven ideas:

- The source texts are free.
- Swiss court decisions are generally not protected by copyright (Art. 5 of the copyright act;
  not verified in this research).
- Fixed values, quotes and "not stated" match the existing design exactly.
- Needs only basic PDF text extraction (most decisions have a text layer) and no table parsing.

It could be built in about 2–3 months.

### Domain knowledge

What to learn:

- How Swiss courts set Genugtuung: the Federal Supreme Court's two-step method (base amount, then
  individual adjustments) and reductions for the victim's own fault.
- How victim-support awards differ from civil awards. They follow capped official tables
  ([Federal Office of Justice guideline 2024](https://www.bj.admin.ch/dam/bj/de/data/gesellschaft/opferhilfe/hilfsmittel/leitf-genugtuung-ohg-2024.pdf.download.pdf/leitf-genugtuung-ohg-2024-d.pdf)).
- Mapping injuries to accident-insurance impairment categories.
- Procedure details: claimed versus awarded amounts, and removing duplicates when the same case
  appears at several court levels.

Can you learn it alone? **Partly.**

- About 1–3 months, from the standard Swiss reference work (Landolt/Hütte, "Genugtuungsrecht"), the
  free official guideline, and the few hundred decisions you read while labelling.
- Field definitions and test labels need review by a practitioner: a specialist liability lawyer
  or a former claims manager.
- Selling runs through a closed network, so expect 6–12 months to credible sales without an
  insider.

What else it needs:

- No licence, as long as it is sold as data and not legal advice.
- German essential, French for Romandie.

### Demand

**Weak.** Evidence:

- People pay for this in Germany: the German damages tables are in their 43rd edition, a book with
  online access at about EUR 124 a year ([listing](https://www.ebay.de/itm/276884658752)).
- The Swiss equivalent is compiled by hand and shipped on a CD, and covers German only
  ([table of contents](https://ub.unibas.ch/tox/IDSLUZ/001114834/PDF)).
- **But:**
  - Swiss awards are low. More than CHF 150,000 is rare even for paraplegia
    ([Beobachter](https://www.beobachter.ch/geld/versicherungen/was-erhalten-geschadigte-als-genugtuung-166062)),
    and most money in injury cases is in other damages this tool doesn't cover.
  - The largest Swiss firm specialising in this area has about 20 lawyers
    ([job ad](https://jobs.ch/de/stellenangebote/detail/cd843c6d-d814-444c-8387-bc68e3a66fc5)).
  - Victim-support authorities already have free official tables.
  - Most cases settle out of court, so published decisions are a small and skewed sample.

Who pays and how much:

- **Insurers:** the claims and recourse departments of about 10–20 Swiss liability, motor and
  accident insurers. **Estimate:** CHF 5–20k a year each.
- **Specialist lawyers:** CHF 50–150 a month at most.
- **Estimated ceiling:** CHF 150–400k a year in total.

### Competition

**Moderate.**

- **Existing references:** the Landolt/Hütte reference work (hand-compiled, German only, on CD) and
  the free official guideline.
- **Funded Swiss legal-AI platforms** search the same open court decisions and could add
  structured award fields quickly:
  - Omnilex, about USD 4.5M raised
    ([Artificial Lawyer](https://www.artificiallawyer.com/2025/11/11/omnilex-bags-4-5m-for-legal-research-ai-commentary/)).
  - Pandektes, claiming 500k+ Swiss decisions
    ([Pandektes](https://pandektes.com/blog/pandektes-enters-the-swiss-legal-market)).
  - Silex.
- **The corpus itself is open:** a free mirror and a connector let any AI tool query it
  ([OpenCaseLaw](https://docs.libratech.ai/en/integrations/switzerland/opencaselaw.md)), so there
  is no data moat.
- **Not yet checked:** a Bern-published Genugtuung book is listed with an "online database". It
  could not be checked (the site was blocked) and may be a direct competitor.

The gap: no structured, quote-backed, regularly updated database of Swiss awards in German, French
and Italian, with filters by injury, impairment, age and reduction, was found.

### Risks

- **Too few usable decisions.** Count them before building anything.
- **Mixed award types.** Civil, criminal and victim-support awards follow different logic and must
  be kept apart, or the comparables will mislead.
- **Health data.** Decisions contain health details about pseudonymised people. Store fields and
  short quotes, not narratives, and avoid making re-identification possible.
- **Single source.** Everything depends on one non-profit site, entscheidsuche.ch.
- **Easy to copy.** The legal-AI platforms could add the feature in a sprint.

### First step and when to stop

1. Count usable Genugtuung awards on entscheidsuche.ch, by type and language. This is an afternoon
   of work.
2. If there are at least about 1,500, build the dataset in 2–3 months, with a specialist lawyer or
   former claims manager checking the labels.
3. Lead with the French and Italian coverage the existing reference work lacks.
4. Sell first to one or two insurers as a data feed (CHF 10–20k a year each).
5. In parallel, offer a data licence to a legal publisher (Schulthess, Weblaw, Swisslex) or to
   Omnilex or Pandektes, so the likely copiers become customers.
6. Later, reuse the pipeline for other numeric legal outcomes with a wider audience: wrongful
   dismissal compensation, rent reductions, disability degrees.

**Stop if** the count is low, or two or three insurers won't commit to about CHF 10k a year.

Even if it fails as a business, it is a cheap credibility project: a public, measured dataset built
with the jobfacts method.

---

## What the three have in common

- **Each needs an insider:** a broker, a food technologist, or a liability lawyer or claims
  manager. Finding that person matters more than which idea you pick.
- **Each needs sales conversations in German,** and French for Romandie.
- **Ideas 1 and 2 need PDF table reading,** and idea 3 needs basic PDF text extraction. Adding
  this to jobfacts is the first engineering step whichever idea you choose.
- **Your edge in all three is measured accuracy with a quote behind every value.** Showing sources
  alone is no longer rare; publishing honest accuracy numbers still is.

## Ideas researched and rejected

The other eight were rejected because established players or funded startups already serve them:

- **Swiss building permit leads:** Infopro and Houzy already serve it, and digital filing becomes
  mandatory in Zurich and Basel in 2027.
- **Customs tariff classification:** AEB and MIC already ship AI document reading in Swiss import
  declarations.
- **Public tenders outside construction:** at least six funded startups, two already covering
  simap.ch.
- **Industrial parts and quote-request matching:** Mercura is profitable in this space, and Hoshii
  operates in Zurich.
- **Supplier substance compliance (REACH, RoHS):** IntegrityNext, Assent/iPoint and funded AI
  startups.
- **Real estate fund energy reporting:** Wincasa already automates it with Novalytica.
- **Letter-of-credit checking for commodity traders:** komgo and Covantis, both in Geneva, already
  serve traders.
- **Insurance claim quote checking:** Solera's sachcontrol reportedly serves Swiss insurers.
