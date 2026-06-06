# V2 Manual Test Cases

Use these Telegram messages to verify the conversational secretary router. Query and correction
messages should preserve an audit note but must not create new durable task, reminder, project, or
memory objects unless the action explicitly updates an existing object.

## Agenda Queries

1. Send: `hôm nay tôi cần làm gì`
2. Expected: replies with today's agenda.
3. Expected: no extraction call is needed and no task/memory/reminder is created from the question.
4. Send: `mai tôi cần làm gì` or `/tomorrow`
5. Expected: replies with tomorrow's agenda.
6. Send: `/todays`
7. Expected: behaves like `/today`.

## Memory Queries

1. Send: `tôi đã lưu gì về bản thân`
2. Expected: replies with profile memory only.
3. Expected: no new memory candidate is created from the question.

## Project Queries

1. Ensure a `MemoCore` project has open tasks.
2. Send: `project MemoCore còn gì chưa xong`
3. Expected: replies with open MemoCore tasks.
4. Expected: no new project or note-derived task is created from the question.

## Task Completion

1. Ensure an open task exists with a title such as `mua pc`.
2. Send: `tôi đã làm xong việc mua pc`
3. Expected: the matching task is marked done and the reply confirms it.
4. If multiple tasks match, expected: the bot asks which one to mark done.

## Recent Task Correction

1. Create one recent accidental task.
2. Send: `cái này không phải task`
3. Expected: if exactly one recent active task is clear, it is cancelled.
4. If several recent tasks exist, expected: the bot asks which one is wrong.

## Memory Delete

1. Ensure one clear memory exists containing `cơm tấm`.
2. Send: `xoá memory thích ăn cơm tấm`
3. Expected: matching memory is deleted.
4. Send: `đừng lưu cái này` when multiple active memories exist.
5. Expected: the bot asks which memory to delete instead of guessing.

## Casual Messages

1. Send: `chào`
2. Expected: the bot acknowledges briefly.
3. Expected: no extraction call and no durable objects are created.
