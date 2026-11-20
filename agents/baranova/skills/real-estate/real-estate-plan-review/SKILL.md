---
name: real-estate-plan-review
description: Use when сверяешь планировки с ТЗ и готовишь замечания.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [real-estate, plan-review, remarks, developer, product]
    related_skills: [floor-plan-area-extraction, real-estate-product-concept, microsoft-office, pdf]
---

# Real-estate plan review (замечания продукта к планировкам)

## When to use
User (product director at a developer) drops a BUNDLE of documents for one
project stage (ЭП/П/РД) and asks to «проверить планировки» / «дать замечания от
продукта». Deliverable = структурированная таблица (.xlsx) замечаний (Word — для формального письма) от продукта проектировщикам:
formal title («ЗАМЕЧАНИЯ К ЭСКИЗНОМУ ПРОЕКТУ»), numbered remarks, one section per
block (апартаменты / стилобат / паркинг / инженерка), speaker = the product
director herself.

## Step 1 — classify every document in the bundle (each has ONE role)
- **Планы для проверки** (usually 2 PDF: башня/секция + стилобат/паркинг) — what you review.
- **ТЗ** (the compliance baseline): «Требования к планировкам» (docx/text),
  «Приложение N к ТЗ» (xlsx: требования к помещениям — площади/соседство),
  «Объём инженерки» (docx: состав инженерных сетей).
- **Замечания к ПРОШЛОЙ итерации** (often the user's OWN email remarks) — verify
  whether addressed; in the new doc restate as «остаётся в силе», never re-derive.
- **Замечания СМЕЖНЫХ подразделений к ТЕКУЩЕЙ итерации** (Экспертный совет:
  КР/ЭЛ/ЭС/ВК) — «имей в виду»: list under «принять к исполнению» as a separate
  track, do NOT merge into your own numbering.

## Step 2 — read everything; match reader to source type
- DOCX tables = structured remarks (per-row: №/уровень/суть/ответ). XLSX = помещение
  requirements. Iterate `.tables` and `.paragraphs`, not just paragraphs.
- PDF may be VECTOR (pdfplumber extract_text/words) or SCANNED/PRINTOUT (empty
  text layer → render via pypdfium2 at scale ≥2 and vision_analyze the body).
- The «замечания» PDFs are frequently EMAIL PRINTOUTS (Яндекс 360), not marked-up
  plans — the remarks live in the email body, not as red/green bubbles on a drawing.
  Check the source type before assuming it is a markup layer.

## Step 3 — ground the strong remarks in extracted numbers
Run floor-plan-area-extraction on the plan PDFs (поэтажные площади, кол-во лотов по
типам, средние площади, сверка с «номерографией» xlsx). The hard deltas this
produces — дефицит лотов против ТЗ, средняя/максимальная площадь типа выше ТЗ —
become the headline remarks, phrased with the concrete numbers.

## Step 4 — write the замечания as the product director
- Each remark = a requirement to the designer, referencing a ТЗ clause («п.1.1»)
  or a prior remark date; phrasing: «требуется / обеспечить / подтвердить на
  планах / представить».
- The известные замечания (past iterations + Экспертный совет) are included to
  check closure, but the value-add is NEW нелогичности/неудобства/несоответствия.
- Where you did NOT verify a plan pixel-by-pixel, frame as «подтвердить на
  планах», never assert a violation you cannot back.

## Step 5 — find NEW issues at scale
Render every plan page, then delegate parallel review to subagents (delegate_task,
`images` param carries up to ~4 rendered pages each) with context = the ТЗ clauses +
«skip these already-known remarks: <list>». Collect structured findings
({floor, location, problem, category, why}) and dedupe against known remarks before
merging into the doc. Splitting by zone (К1 нижние/верхние этажи, паркинг, стилобат)
keeps the page count per child low and the review complete.

## Step 6 — build and deliver
The readable primary deliverable is a STRUCTURED TABLE (.xlsx), not prose: columns
«№ / Раздел / Категория / Этаж-зона / Замечание / Требуется», auto-filter, frozen
header, and a fill colour per Раздел. With 50–70 remarks the user scans and filters
— long bullet lists are unreadable. A Word letter (right-aligned sender signature,
centred title «ЗАМЕЧАНИЯ К ЭСКИЗНОМУ ПРОЕКТУ», Heading 1 per block, numbered
remarks) is the formal/cover form, not the digest. Save and send via MEDIA; offer
Yandex Disk (see yandex-disk-webdav skill).

Name every deliverable «Логичное_Название_ДД.ММ.ГГГГ_h-.ext» — the trailing «h-»
marks an agent-prepared file; the date is DD.MM.YYYY.

## Pitfalls
- Don't re-derive the user's OWN prior remarks — restate as «остаётся в силе (дата)»
  and confirm the current iteration addressed them; that is the check she wants.
- Смежные (Экспертный совет) remarks are a separate authority, not your voice —
  forward them as «принять к исполнению», keep them out of your own numbering.
- vision_analyze OCR of area numbers is ±0.1–0.4 m² per value — use it for
  STRUCTURE (apartment list, types, room names), then reconcile sums against the
  exact «на этаже» totals from the vector layer (see floor-plan-area-extraction).
- State hard numbers only when extracted; otherwise say «подтвердить», or you will
  overclaim and the designer will push back.
- Cyrillic attachment filenames display percent-encoded («%D0%9E…») in Yandex
  Messenger: the gateway percent-encodes non-ASCII filenames in the multipart upload
  (aiohttp FormData quote_fields) and the app echoes it raw. The on-disk name stays
  correct — only the display is munged, so when a clean display name matters send a
  transliterated ASCII basename; this is a gateway bug, not something the agent can
  fix from inside.
