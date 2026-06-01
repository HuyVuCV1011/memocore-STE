from __future__ import annotations

from datetime import UTC, datetime

from memocore.adapters.storage.repositories import (
    FollowUpRepository,
    MemoryItemRepository,
    ProjectRepository,
    TaskRepository,
)


class SecretaryService:
    def __init__(
        self,
        task_repo: TaskRepository,
        followup_repo: FollowUpRepository,
        project_repo: ProjectRepository,
        memory_repo: MemoryItemRepository,
    ):
        self.task_repo = task_repo
        self.followup_repo = followup_repo
        self.project_repo = project_repo
        self.memory_repo = memory_repo

    async def today(self) -> str:
        now = datetime.now(UTC)
        tasks = await self.task_repo.list_active()
        due = [task for task in tasks if task.due_at and task.due_at <= now]
        waiting = [task for task in tasks if task.status in {"waiting", "blocked"}]
        lines = ["Today"]
        lines.extend(_task_lines(due, empty="No due or overdue tasks."))
        if waiting:
            lines.append("")
            lines.append(f"Waiting or blocked: {len(waiting)}")
        return "\n".join(lines)

    async def waiting(self) -> str:
        tasks = await self.task_repo.list_active()
        waiting = [task for task in tasks if task.status in {"waiting", "blocked"}]
        followups = await self.followup_repo.list_open()
        lines = ["Waiting and follow-ups"]
        lines.extend(_task_lines(waiting, empty="No waiting tasks."))
        lines.extend(f"- Follow up: {item.title}" for item in followups)
        return "\n".join(lines)

    async def projects(self) -> str:
        projects = await self.project_repo.list_all()
        if not projects:
            return "Projects\nNo projects captured yet."
        return "Projects\n" + "\n".join(f"- {project.name}" for project in projects)

    async def memories(self) -> str:
        memories = await self.memory_repo.list_active()
        if not memories:
            return "Memory\nNo memory candidates yet."
        return "Memory\n" + "\n".join(
            f"- [{item.bucket}] {item.content}" for item in memories[:20]
        )


def _task_lines(tasks, empty: str) -> list[str]:
    if not tasks:
        return [empty]
    return [f"- [{task.status}] {task.title}" for task in tasks]
