# Personal Assistant Context Seed

This is the minimum upfront context Memocore needs to become a useful secretary instead of a flat
task collector. V2 can use this lightly for routing and project hints. V3/V4 should turn it into
first-class work-group, project, people, and memory links.

## User Context

- Name and preferred pronouns.
- Primary language and fallback language.
- Timezone, usual work location, and normal work rhythm.
- Current roles, such as founder, teacher, mentor, reviewer, or project owner.

## Work Groups

For each group, provide:

- Group name, aliases, and common shorthand.
- What the user is responsible for.
- Typical task types.
- Important recurring reviews.
- Common words that should map to the group.

Example:

- `MindX`
- Aliases: `mindX`, `MindX học viên`, `lớp`, `TF`
- Responsibilities: project progress, student assignments, CV review, staffing/TF coordination.
- Typical tasks: check project completion, review submissions, review CVs, arrange personnel.

## Projects

For each active project, provide:

- Project name and aliases.
- Parent work group.
- Current status.
- Definition of done.
- Open loops.
- Key people or roles.

## People And Roles

For important people or categories:

- Name or role.
- Aliases.
- Relationship to the user.
- Work group or project.
- What they usually owe the user or what the user owes them.

Examples:

- `TF`: teaching/support staff for MindX.
- `học viên`: MindX students.
- `Sơn`: person linked to PC purchase, if relevant.

## Vocabulary

Provide domain shorthand that would otherwise be ambiguous:

- `TF`: teaching/support staff.
- `PC mới`: hardware purchase/setup task.
- `bài tập`: student submissions.
- `CV`: student or candidate CV review.
- `TPAJ` or similar course/project terms.

## Task Routing Rules

Memocore should learn rules like:

- `cần check X đã xong chưa` means create/check work, not mark a task done.
- `tôi đã làm xong X`, `đã X xong`, or `finished X` means mark a matching task done.
- `task mới` after a wrong interpretation means capture the previous message as a new task note.
- `đừng lưu cái này` means reject/delete the relevant recent object only when context is clear.
- If multiple objects match, ask a concise clarification question.

## Version Boundary

- V2: deterministic intent routing, simple project/task queries, task completion, corrections, and safer follow-ups.
- V3: daily/weekly planning, recurring review surfaces, and proactive briefings by work group.
- V4: first-class people, roles, work groups, project memory, and linked retrieval.
