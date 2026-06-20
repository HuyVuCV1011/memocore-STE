# Telegram Memory UX Specification

## Scope

This slice replaces the `/memory` database dump with one compact navigable dashboard:

```text
/memory
-> overview
-> review/stale/topic slice
-> next/previous page
-> back to overview
```

Direct natural-language questions continue through the conversation and knowledge retrieval flow.

## Response Archetypes

- Direct answer: answer first, then at most a few supporting facts.
- Summary: short framing plus 3-7 bullets.
- Memory dashboard: counts first, then map/slices, then navigation.
- Memory slice: paginated cards, four memory items per page.
- Empty state: say what is missing and offer a recovery path.
- Clarification: ask one focused question.
- Confirmation: repeat the destructive action and require an explicit choice.
- Error: preserve the user's context and provide a retry route.

## Memory Wireframes

```text
Ghi nhớ của bạn

42 memory đang dùng. 7 cần duyệt, 3 cần rà lại.

Triage
- Review inbox: 7
- Stale/needs refresh: 3
- Preference/boundary: 12
- Goals: 2

Map
- Profile: 15
- Project: 20
- Interaction: 7
- People: 18
- Active projects: 9

Top slices
- linked to projects: 20
- linked to people: 14
- ste: 9

[Cần xác nhận] [Có thể lỗi thời]
[Bản thân]     [Mục tiêu]
[Con người]    [Dự án]
[MindX]        [STE]
```

```text
Ghi nhớ: STE

9 mục. Đang hiện 4 mục.

1. STE có các hướng công nghệ, dữ liệu, AI, giáo dục và đầu tư.
   project/project_state | tin cậy 90% | xác nhận 11/06/2026
   id:3f80c9a1 source:57ab93df

2. Một số sản phẩm đào tạo/Data/BI còn cần xác nhận trạng thái trước khi xem là active.
   project/fact | tin cậy 72% | chưa xác nhận
   id:4b27a1de source:9bb8f102

[Sau] [Quay lại]
```

## Interaction Rules

- Always answer callback queries before doing longer work.
- Edit the existing message for navigation instead of adding messages.
- Keep callback payloads short and stable: `mem:o`, `mem:t:<topic>:<page>`, `mem:k:<id>`, `mem:r:<id>`, `mem:s:<id>`, `mem:g:<id>`.
- Treat callbacks as stateless so they survive process restarts.
- Reject malformed or unknown callbacks with a compact recovery message.
- Hide correction/import records from normal views.
- Deduplicate normalized display text before summary, facet, or detail views.
- Use plain text in this slice to avoid Markdown escaping failures.
- Keep one message under Telegram's 4096-character text limit.

## Vietnamese Copy

Research basis: use the Google and Microsoft writing-style principles as defaults: clear, warm, concise, respectful, low-jargon, and useful before clever. Vietnamese copy should sound like a capable personal assistant, not a database browser.

### Voice

- Use “mình” for the assistant and “bạn” for Vũ.
- Use “ghi nhớ” in user-facing copy; reserve “memory item”, `status`, `bucket`, and `confidence` for engineering or detail views.
- Prefer direct verbs: “Mình đang nhớ…”, “Mình chưa thấy…”, “Bạn muốn xem phần nào?”.
- Keep answers calm and competent. Avoid hype, jokes, and overly casual slang.
- Do not over-apologize. If data is missing, say what is missing and offer the next useful action.

### Memory Wording

- Write memory as a durable claim with subject and scope: “Vũ muốn…”, “STE có…”, “MindX đang…”.
- Do not write low-confidence inference as fact. Use wording like “có dấu hiệu”, “cần xác nhận”, or “chưa đủ chắc”.
- Separate current, historical, and unconfirmed information in copy.
- Do not present correction/import metadata as a user-facing fact.
- Avoid broad personality claims unless Vũ explicitly confirmed them.

### Response Patterns

Good:

- “Mình đang nhớ STE theo 3 nhóm chính: công nghệ/dữ liệu/AI, giáo dục, và đầu tư.”
- “Phần này mình chưa đủ chắc để coi là fact. Mình nên hỏi lại bạn trước khi đưa vào memory chính.”
- “Mình chưa thấy task đang mở liên quan đến STE; dữ liệu hiện có chủ yếu là định hướng và năng lực.”
- “Thông tin này có vẻ là lịch sử, không nên dùng như trạng thái hiện tại.”
- “Nếu bạn muốn, mình có thể mở phần chi tiết để xem claim gốc, nguồn và độ tin cậy.”

Avoid:

- “Có 17 mục, trang 1/4.”
- “Theo memory_items, status active và confidence 0.78...”
- “Dữ liệu chứng minh chắc chắn...” when the evidence is only inferred or low-confidence.
- “Tôi, với vai trò hệ thống...”

### Uncertainty

- State uncertainty in natural language and expose confidence/source only on demand.
- Say “chưa đủ chắc”, “cần xác nhận”, or “hiện chỉ nên xem là ý tưởng” instead of raw score language.
- When asked for detail, show source, raw claim, confidence, and status clearly.

### Layout

- For overview, show counts and navigation rather than raw facts.
- For topic views, use cards: compact claim, bucket/kind + trust/freshness, short id/source.
- Four items per page is the default Telegram density limit; lower it if cards become longer.
- Do not show empty sections.
- Backend vocabulary is allowed in card metadata because the user is reviewing a large memory set.
- Use detail/drill-down actions instead of long dumps.

## Canonical Memory Model Proposal

The next schema should separate:

- `knowledge_claims`: canonical user-visible claims with subject, predicate/topic, object/content,
  confidence, confirmation state, importance, sensitivity, valid-from/to, and review date.
- `knowledge_evidence`: source note/event, source type, observed date, extractor/model, and evidence
  strength.
- `knowledge_revisions`: supersedes/rejects/corrects links and audit metadata.
- Operational tables: tasks, follow-ups, commitments, meetings, and reminders remain separate.

Migration should be additive:

1. Create the new tables without changing `memory_items`.
2. Backfill candidate claims and evidence links.
3. Produce a duplicate/supersession review report.
4. Switch reads after parity tests.
5. Keep `memory_items` read-only during a rollback window.

No live records should be automatically deleted or merged during migration.

## Telegram API Basis

- Inline keyboards and callback queries provide drill-down controls.
- Callback queries should be acknowledged with `answerCallbackQuery`.
- `editMessageText` supports in-place navigation.
- Message text is limited to 4096 characters after entity parsing.
- Callback data is limited to 1-64 bytes.
- Command menus can be managed with `setMyCommands`.
- Typing indicators use `sendChatAction` and should be reserved for noticeable latency.

Official references:

- https://core.telegram.org/bots/api
- https://core.telegram.org/bots/features
