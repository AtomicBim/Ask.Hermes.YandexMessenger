# Cross-referencing correspondence (find un-forwarded letters)

For "did every letter sender X produced actually get forwarded to recipient Y?" — matching one mailbox's inbound against its outbound.

## Procedure

1. **Resolve roles first.** Map every address to a person/org from `From`/`To` signatures and the domain — internal = the company
   domain, external = the customer / designer / subcontractors. You cannot match topics until you know which address means
   "Заказчик", "Администрация", a given subcontractor, etc. This is the step that decides everything downstream.
2. **Reconstruct the outbound set from two places, not one.** (a) the user's own `Sent` folder (`SEARCH ... SINCE <date>`);
   (b) for a *colleague* who also sends out, their sends appear in the user's INBOX only when the user is CC'd — search
   `FROM <colleague>` in INBOX and note the limit: a colleague's un-CC'd sends are invisible from this mailbox, so say so in the result.
3. **Collect ids server-side** (`SEARCH FROM "fragment" SINCE ...`), persist headers to a JSON checkpoint in scratch, then fetch
   bodies in small chunks (see the EOF pitfall in SKILL.md).
4. **Match by topic, not just subject.** Subjects are often bare ("Прошу рассмотреть", "Fwd: 1284", no-subject) while the real
   letter topic lives in the body or an attached draft. Read bodies on BOTH sides before concluding anything.
5. **Disposition each of the sender's letters** into one of three buckets: forwarded to the target recipient; forwarded elsewhere
   but not there; or not forwarded anywhere. The "not forwarded (to the customer)" bucket, with date + subject, is the deliverable.

## Pitfalls

- A sender's inline reply to the user's own email is a **draft in circulation**, not a sent letter — confirm it ever surfaced as a
  formal outbound (an исходящий number in the subject) before flagging it as a gap. Letters can sit in revision for days.
- "Forwarded to the customer" shows in the union of `To`+`Cc`, not `To` alone — the customer is often CC'd on a letter addressed to
  the designer. Match on both fields.
- Two near-identical subjects (e.g. "Рабочий шов, корпус А" vs "Второй шов, корпус Б") may be different buildings or the same
  thread re-raised. Match on topical keywords and read the body — do not dedupe by subject string.
