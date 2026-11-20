---
name: floor-plan-area-extraction
description: Use when извлекаешь площади/лоты из планов и свери с Excel.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [real-estate, area, floor-plan, pdf, numbered-units, developer, reconciliation]
    related_skills: [real-estate-product-concept, mop-finish-selection, pdf, microsoft-office]
---

# Floor-plan area extraction (поэтажные площади, лоты, средние площади, сверка с Excel)

## When to use
User sends architectural floor-plan PDFs (планы башни/секции/стилобата/паркинга)
plus an Excel «номерография»/ТЗ and asks to собрать: поэтажные площади,
среднюю площадь лотов по типам, общую площадь — and свериться (cross-check)
against the Excel.

## Workflow
1. Set up the documents environment once (`pdfplumber` + `pypdfium2` + `openpyxl`
via `hermes pm environment documents --extra documents --extra documents-pdf`);
copy the PDF and xlsx into a project work dir (e.g. `/opt/data/work/<project>/`).
2. Map the sheets BEFORE parsing: each PDF page typically holds 2 floor plans
side-by-side, each with its own «экспликация» table to the right. Read the plan
titles («План N этажа», «План типового этажа (5–11, 16–24)») to learn which
floor numbers each DISTINCT plan covers — «типовые» plans repeat across many
floor numbers and their areas/counts must be multiplied by the floor count.
3. Pull the authoritative per-floor totals from the vector text: search for
«на этаже: X» / «этаже: X» — this is «Общая площадь квартир на этаже» and it
is EXACT. Assign each to its floor using the page's plan titles and the x
coordinate (left plan x<0 vs right plan x>0).
4. Extract per-apartment (type, total area) from the экспликация using
pdfplumber `extract_words()` (WITH x/y coordinates) — never `extract_text()`,
which emits the vector layer in drawing order and scrambles numbers with room
labels and dimension strings. The экспликация is 3 columns (Тип кв | Наим |
Площадь): apartment number OR room-type in col 1, room name in col 2, area in
col 3; the apartment total is the bare area in col 3 (no room name on that
row). Each plan's schedule is split into 2–3 side-by-side sub-tables — locate
them by the «Площадь» header x-positions, parse each, concatenate left-to-right.
5. Verify every plan: sum of apartment totals ≈ its «на этаже» total (±0.1). A
mismatch means segmentation broke (apartments merged, or a non-apartment line
got counted).
6. Cross-check vs the Excel номерография: per-floor unit counts by type and the
ТЗ average areas. Weight «типовые» floors by count. Reconcile units (PDF count
vs ТЗ vs поэтажная раскладка) and areas (Σ count×ТЗ‑avg vs PDF total).
7. Build the deliverable .xlsx with live formulas (openpyxl: `=B4*C4`,
`=SUM(...)`, deviation `=C-E`), in sheets: floor→area, type-average, cross-check.
Send via MEDIA and state the headline discrepancies in chat.

## Pitfalls
- «на этаже: X» is the anchor of truth. vision_analyze OCR of numbers is
±0.1–0.4 m² per value — use vision only to read STRUCTURE (apartment list,
types, counts), then reconcile the sum against the exact total, not the reverse.
- On some floors the «на этаже» total INCLUDES a МОП (места общего пользования)
line (e.g. a common-terrace row). Subtract it to get saleable apartment area and
flag the caveat in the deliverable rather than reporting the raw total.
- Apartment numbering can be non-contiguous — a floor with a тех. узел (roof
exit, tech room) skips a flat number. The types/counts from the экспликация must
match the Excel номерография's per-floor columns before you trust the list.
- «Уменьшенные»/terrace floors of a tapering tower have legitimately LARGER
units than the typical floor, so the PDF average-by-type runs ABOVE the ТЗ
averages set on typical floors — that is a real finding to surface, not an
extraction error.
- Compute units at all three layers separately and state all three: ТЗ (task)
vs поэтажная раскладка (Excel floor table) vs факт по планам. The gap between
them (e.g. 610 / 565 / 556) is usually the headline the user cares about.

## Output
- Russian. Excel (.xlsx) primary with live formulas; plus a short chat summary
naming the three headline numbers (units, total area, which types run over/under
ТЗ average) and any МОП / terrace caveats.
