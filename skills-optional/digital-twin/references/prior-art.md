# Prior art — AI offboarding / digital twins of employees

Existing products and open-source skills that already do variants of this. Use to short-circuit a build decision.

- **Offboarding AI Assistant** (SoftwareHut, Microsoft Marketplace) — "virtual offboarded employee": chat as the person, searches their email/chat/files and answers (e.g. "why did we give that discount 4 years ago"). Closest commercial match to the offboarding scenario.
- **Relay** (relay.tanka.ai) — AI digital twins of departing employees; connects Slack/Email/Zoom to capture decision context; lives in the team chat.
- **Sensay Offboarding** — AI "biographer" runs the exit interview as a guided conversation, then produces a living, searchable chatbot.
- **Edamame** — per-person digital twins for org memory: clone chat, onboarding briefs, offboarding handoffs with source citations.
- **eidola / arkiv** (GitHub, open source) — persona packaging convention + Claude Code plugin: `interview → generate → evaluate → refresh`. Closest to "collect a data portfolio → conversable persona"; archive-first (data frozen into `arkiv` format).
- **Digital Twin** (ClawHub/OpenClaw skill, `clawhub install digital-twin`) — builds a `{name}_personality` installable skill from transcripts/email/chat/docs; captures HOW the person thinks/writes, not the facts. Pair with a vector DB for factual memory.
- **colleague-skill** (GitHub; Claude Code/OpenClaw) — two parts: Work Skill (technical abilities) + Persona (5 layers: rules, identity, speech style, decision-making, interpersonal).

Note the shape they share: a **persona** (voice/judgment) is separate from a **factual corpus** (memory) — a good clone needs both, not one or the other.
