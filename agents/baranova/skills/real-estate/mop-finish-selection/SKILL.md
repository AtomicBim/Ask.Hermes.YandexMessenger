---
name: mop-finish-selection
description: Use when подбор отделки/оборудования МОП под дизайн-проект.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [real-estate, mop, design, finishes, lifts, developer, visualization]
    related_skills: [real-estate-product-concept, microsoft-office, yandex-disk-webdav]
---

# МОП finish & equipment selection (подбор отделки/оборудования МОП под дизайн-проект)

## When to use
User (product director at a Ural developer) sends a vendor catalog + approved
design-project renders/moodboard and asks to «подобрать» an element — lifts,
doors, lighting, materials, furniture — for the МОП (места общего пользования).
Deliverable: N visual variants, each with визуализация + таблица артикулов,
saved to the project folder on Yandex Disk.

## Workflow
1. Read the vendor catalog FIRST and decode its артикул scheme (even a
text-garbled PDF still yields the code pattern). For EPSS lifts the code key is
in references/epss-lift-codes.md. Never ask the user to re-send or paste —
extract it yourself.
2. Extract the design DNA from the renders + moodboard with vision_analyze:
explicit RAL codes, tile/material names, and the brand theme (logo text, art
motifs). These drive the palette AND the «пасхалка» (easter egg). If the
project has a separate brand-concept document (a `7. Бренд-концепция` folder or
a branded PDF), read it ALL the way through — OCR the image-only pages — and
pull the firm's explicitly named «образы и смыслы» (it usually names 2–3 key
symbols plus a local art/naming anchor). Those named symbols are the most
organic motif source: proposals grounded in them read as the client's own
concept, not something invented on the spot.
3. Design the variants (3 is the usual starting ask, but the user iterates and
will ask for 2 more at a time). Each is a coherent palette + the user's stated
must-haves (e.g. mirror, handrail, brand logo) + ONE clever thematic easter egg
tied to the project theme. The easter egg is explicitly valued — don't skip it.
Any material/color the user names ('в шлифованной нержавейке', 'с терракотовым
цветом') is ADDITIVE to the must-have list — every variant must include it.
Keep easter eggs DISTINCT across variants: map each to a different project
motif (atomic orbits, armillary sphere, mosaic hand, periodic table, star map,
Michelangelo's hands) instead of recycling one image in two variants.
4. Generate one visualization per variant with image_generate, passing the
moodboard (palette) + a key render (style/logo) as reference_image_urls. Use
portrait aspect for vertical elements (cab interiors, doors).
5. Spell the exact Cyrillic brand text word-for-word into the generate prompt
(e.g. the 3-line wordmark «АТОМ / НАУКА И / ИСКУССТВО») — otherwise the model
plants an English placeholder ('LEVEL 5', 'GROUND FLOOR') or garbles it. VERIFY
each image with vision_analyze before delivering: confirm the Cyrillic logo
reads correctly AND sits where the user asked (position is part of the spec —
'справа вверху' must not stretch across the whole wall). If the text is wrong
but the layout is good, fix it with an image-to-image edit (image_generate
image_url=<the good image>, prompt='Replace the … text with <brand text>') —
cheaper than a full regen and preserves the approved composition.
6. Write the артикулы as one table per variant: Элемент | Артикул/материал |
Комментарий. Mark custom items (backlit logos, engravings, etched maps) as
«индивидуальное решение» needing vendor confirmation vs standard catalog codes.
7. Build a summary .docx with python-docx (images + tables), save everything to
Yandex Disk, and send the images in chat via MEDIA so the user sees them
immediately.

## User preferences
- Russian throughout; deliver a .docx summary + separate JPEG/PNG visualizations.
- A starting batch of 3 variants, each visual + артикулы, plus a closing
«рекомендация» naming the main pick and noting combinable elements. The user
frequently asks for 2 more at a time, often adding a new material/color
constraint. Append to the same variant list and REGENERATE one consolidated
.docx covering all variants — delete the previous docx from Yandex Disk and
name the file by total count (e.g. ..._7_вариантов...), never per-batch docs.
- Re-confirm the must-have element list from the request every time (mirror,
handrail, logo, easter egg) and make sure each variant hits all of them.

## Delivery to Yandex Disk
See the yandex-disk-webdav skill for the helper (`/opt/data/.secrets/yd.sh`),
the re-list/verify discipline, and the `/ПРОЕКТЫ/<проект>/` layout. Put the
new material in the matching numbered category folder (e.g. `2. Дизайн МОП`),
creating a named subfolder for the element (e.g. `Лифты`).

## Pitfalls
- Decode the catalog code system before writing anything — a garbled PDF text
layer still reveals the family pattern (e.g. EX-HS vs EX-MS vs EX-RS).
- Always vision-check AI-generated Cyrillic brand text before shipping; the
logo is a must-have and garbled text kills the deliverable.
- The generation backend may silently ignore the aspect_ratio enum — confirm
returned image dimensions (e.g. PIL) before embedding and scale in the docx;
don't assume 'portrait' was honored. When a portrait base comes back landscape,
re-run the SAME edit with an explicit orientation clause in the prompt text
('keep the vertical portrait orientation, framing and aspect ratio unchanged;
output a portrait image') — the model obeys the text even when it drops the
enum, and the retry lands portrait.
- A batch of several image PUTs plus the .docx can exceed the default terminal
timeout (180s): set a generous timeout (≥400s), and if the .docx PUT still
hangs, retry it alone then re-list the folder — a timed-out PUT may not have
landed.
- Fix a wrong or mis-positioned logo with an image-to-image edit (image_url base)
rather than regenerating from scratch — it keeps the approved layout and only
re-paints the text. Iterate position in the edit prompt the same as in the
original.
- A mid-project edit («поменяй материал/логотип на этом рендере») is an
image-to-image edit: `image_url` = the exact render the user just attached (NOT
a previously-saved variant for the same element), `reference_image_urls` = any
newly-attached logo/reference. The attachment may be the user's own designer
render that differs from the AI-generated variants (different motif, ceiling
light, ratio), so vision_analyze the attachment first and write the edit
prompt against its actual state, not the saved file's.
- In an edit prompt, enumerate every element to keep unchanged explicitly
('Do not change the terracotta niche, the hands art, the mirror, the floor
tiles…') — that list is what stops the model from rewriting untouched parts.
One material/logo change is a quick iteration, not a new variant: save the
edited image under a descriptive filename, send via MEDIA, and offer to update
Yandex Disk/docx rather than regenerating the whole .docx.
- Designing an art motif (easter egg) that sits INSIDE an existing МОП design:
prefer «связь/развитие» — continue/develop an artwork already in the space —
over a literal brand symbol (an eye, an atom, a lightbulb read as «слишком в
лоб») or a competing import (a second, different 'hands' or 'figure' motif in
the lift clashes with the lobby's mosaic — the user's words: motifs 'спорят'
with each other). The lift climbs, so a lobby mosaic about liberation breaks
naturally into a vertical 'ascent'; solid tesserae dissolving into glowing
light/pixels is the brand's own «традиция → цифра» move, not an invention. When
you don't have the source artwork (old renders pruned, not on Disk), say so
and offer to make the echo literal if the user re-shares it — don't fake iconography
from memory.
- Keep image_generate prompts in clean prose: parentheses, apostrophes,
em-dashes and hyphenated compounds ('S-shaped', 'top-right') have broken the
tool_call JSON parse ('Expecting delimiter') in practice. When a call fails as
'not valid JSON', strip those characters from the prompt and retry — Cyrillic
text itself is fine, it is the punctuation/hyphenation that trips the parser.

## References
- references/epss-lift-codes.md — EPSS lift catalog артикул key (materials,
floor/ceiling/handrail/mirror/control).