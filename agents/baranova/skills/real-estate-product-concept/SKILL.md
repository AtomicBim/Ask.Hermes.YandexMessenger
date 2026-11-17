---
name: real-estate-product-concept
description: "Use when drafting a real-estate product concept."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [real-estate, develment, product, concept, mfc, apartments, developer]
    related_skills: [microsoft-office]
---

# Real-estate product concept (девелоперская продуктовая концепция)

## When to use
User asks to draft or refresh a «продуктовую концепцию объекта» for a
residential or mixed-use development (ЖК / МФК / апартаменты), acting as
director of product. The user is a product director at a Ural developer — this
is a recurring job, so calibrate accordingly.

## Gates — do these first, every time
- Review ALL source material, THEN flag missing critical inputs immediately,
  before writing (not after): exact ТЭП (areas, floor counts, lot mix, parking)
  and the price anchor (target ₽/m² and/or себестоимость). If absent, proceed
  with assumptions labeled explicitly — the user often supplies scattered facts
  in chats instead of an official brief and expects you to carry on.
- Ask no unnecessary clarifying questions. State honestly and up front what you
  cannot do.

## Core principle — the whole job turns on this
- **ROI filter:** every feature must satisfy «1 вложенный рубль → прирост цены
  продажи м²»; CAPEX < прирост цены. Score each idea on Impact (готов ли
  клиент переплачивать) vs Feasibility/CAPEX vs OPEX.
- **Class calibration:** this is бизнес-класс в региональном центре
  (Екатеринбург) — НЕ московский премиум and НЕ комфорт на окраине. Cut
  premium attributes with high CAPEX/OPEX; keep the business-class hygiene that
  buyers actually pay for. See references/idea-classification.md for the
  keep/archive table.
- Classify every idea with the 3-level framework and Kano roles (must-have /
  performance / delighter); archive obvious jokes («ПВО на крыше») and premium
  excess without mercy.

## Workflow
1. Collect inputs: object brief/ТЭП, competitor analysis, brainstorm
   (stickers/CSV export from the whiteboard), and one example concept doc (the
   format to mirror).
2. Extract object facts + the «решено / не решено» list from the brainstorm —
   the concept must close every open item (концепция, наполнение МОП, что на
   стилобате, инженерка, отстройка от конкурентов).
3. Run each brainstorm idea through classification + ROI/Kano scoring
   (see references/), producing one keep/archive verdict per idea.
4. Write the concept as a .docx mirroring the example template's section order:
   титул → характеристика объекта → коммерция → планировки → инженерные
   решения → умное здание → безопасность → эксплуатация/сервис →
   благоустройство → дополнительные опции, and explicitly closing the open
   items.

## Output preferences
- Word (.docx) primary, Excel (.xlsx) for comparative/ROI tables. Russian.
  Generate with the microsoft-office skill (python-docx via docx_create.py).
- Deliver the document plus a short chat summary of the key decisions and the
  ROI keep/archive line — never just a description of work.

## References
- references/market-and-audience.md — Ekaterinburg price anchors, competitor
  benchmark, the 5 target-audience cohorts.
- references/idea-classification.md — 3-level product framework, Kano/ROI
  scoring, and the business-class keep/archive calibration table.
