from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from app.agent_schemas import Chapter, ComposeInput, Connection, NodeEdit
from app.db import Message, NodeRevision, Run, StoryDraft, StoryNode, row
from app.errors import AppError
from app.responses import MessageView, TurnCreated
from app.services.agent_memory import compose_context, edit_node, memory_view
from app.services.workspace import check_revision, fingerprint, get_project


class NodeView(BaseModel):
    id: str
    project_id: str
    run_id: str
    source_message_id: str
    source_reply_id: str
    lane: Literal["main", "auxiliary"]
    category: Literal["protagonist", "world", "plot", "chat"]
    title: str
    summary: str
    source_quote: str
    next_step: str
    status: Literal["draft", "confirmed", "withdrawn", "stale"]
    include_in_story: bool
    version: int
    created_at: str


class DigestItem(BaseModel):
    id: str
    title: str
    summary: str
    status: str
    next_step: str


class MemoryView(BaseModel):
    main: list[DigestItem]
    auxiliary: list[DigestItem]
    main_total: int
    auxiliary_total: int
    archived_main: int
    archived_auxiliary: int


class StoryView(BaseModel):
    id: str
    project_id: str
    run_id: str
    title: str
    synopsis: str
    chapters: list[Chapter]
    connections: list[Connection]
    node_versions: dict[str, int]
    artifact_revision: int
    agent_revision: int
    skill_name: str
    skill_hash: str
    assumptions: list[str]
    status: Literal["draft", "stale"]
    mode: Literal["mock", "real"]
    created_at: str


class AgentView(BaseModel):
    agent_revision: int
    nodes: list[NodeView]
    memory: MemoryView
    stories: list[StoryView]
    current_goal: str
    next_step: str


class SourceView(BaseModel):
    user: MessageView
    assistant: MessageView
    versions: list[NodeView]


class NodeEditResult(BaseModel):
    node_id: str
    version: int
    agent_revision: int


def router(db, runner, cfg):
    routes = APIRouter(prefix="/api/v1/projects/{pid}")

    @routes.get("/guidance")
    def guidance(pid: UUID):
        from app.services.guidance import progress

        with db.transaction() as s:
            get_project(s, str(pid))
            return progress(s, str(pid))

    @routes.get("/agent", response_model=AgentView)
    def get_agent(pid: UUID):
        with db.transaction() as s:
            p = get_project(s, str(pid))
            nodes = list(
                s.scalars(
                    select(StoryNode).where(StoryNode.project_id == p.id).order_by(StoryNode.created_at)
                )
            )
            main = [n for n in nodes if n.lane == "main" and n.status not in ("withdrawn", "stale")]
            protagonist = [n for n in main if n.category == "protagonist"]
            stories = list(
                s.scalars(
                    select(StoryDraft)
                    .where(StoryDraft.project_id == p.id)
                    .order_by(StoryDraft.created_at.desc())
                )
            )
            return {
                "agent_revision": p.agent_revision,
                "nodes": [row(n) for n in nodes],
                "memory": memory_view(s, p.id),
                "stories": [row(d) for d in stories],
                "current_goal": protagonist[-1].summary if protagonist else "先找到主角的目标与阻碍。",
                "next_step": main[-1].next_step if main else "从一个主角、一条世界规则或一个冲突开始。",
            }

    @routes.patch("/nodes/{nid}", response_model=NodeEditResult)
    def patch_node(pid: UUID, nid: UUID, data: NodeEdit):
        with db.transaction() as s:
            return edit_node(s, str(pid), str(nid), data)

    @routes.get("/nodes/{nid}/source", response_model=SourceView)
    def source(pid: UUID, nid: UUID):
        with db.transaction() as s:
            node = s.get(StoryNode, str(nid))
            if not node or node.project_id != str(pid):
                raise AppError("NOT_FOUND", "节点不存在。", 404)
            return {
                "user": row(s.get(Message, node.source_message_id)),
                "assistant": row(s.get(Message, node.source_reply_id)),
                "versions": [
                    v.payload
                    for v in s.scalars(
                        select(NodeRevision)
                        .where(NodeRevision.node_id == node.id)
                        .order_by(NodeRevision.version.desc())
                    )
                ],
            }

    @routes.post("/stories/compose", status_code=202, response_model=TurnCreated)
    async def compose(pid: UUID, data: ComposeInput):
        with db.transaction() as s:
            p = get_project(s, str(pid))
            fp = fingerprint({**data.model_dump(), "task": "compose"})
            prior = s.scalar(select(Run).where(Run.project_id == p.id, Run.request_id == data.request_id))
            if prior:
                if prior.fingerprint != fp:
                    raise AppError("IDEMPOTENCY_CONFLICT", "请求编号已用于其他任务。", 409)
                return {"message_id": prior.message_id, "run_id": prior.id, "status": prior.status}
            check_revision(p, data.base_revision)
            if p.agent_revision != data.agent_revision:
                raise AppError("VERSION_CONFLICT", "故事节点已变化，请刷新后再生成。", 409)
            if s.scalar(select(Run.id).where(Run.status.in_(["queued", "running"])).limit(1)):
                raise AppError("RUN_BUSY", "请等待当前任务完成或先停止。", 409)
            context = compose_context(s, p, cfg.context_token_budget, data.use_summaries)
            m = Message(
                project_id=p.id,
                role="user",
                content="请用已确认主线节点与明确选入的辅助素材，连接成完整故事草稿。",
            )
            s.add(m)
            s.flush()
            run = Run(
                project_id=p.id,
                request_id=data.request_id,
                message_id=m.id,
                input_revision=p.revision,
                fingerprint=fp,
                context=context,
                task_kind="compose",
                prompt_version="compose_story_v1",
                model="mock" if cfg.model_provider == "mock" else cfg.model_name,
            )
            s.add(run)
            s.flush()
            result = {"message_id": m.id, "run_id": run.id, "status": run.status}
        runner.start(run.id)
        return result

    @routes.get("/stories/{sid}/export")
    def export_story(pid: UUID, sid: UUID):
        with db.transaction() as s:
            story = s.get(StoryDraft, str(sid))
            if not story or story.project_id != str(pid):
                raise AppError("NOT_FOUND", "故事草稿不存在。", 404)
            titles = {
                n.id: n.title for n in s.scalars(select(StoryNode).where(StoryNode.project_id == str(pid)))
            }
            lines = [
                f"# {story.title}",
                "",
                f"状态：{'依据已变化' if story.status == 'stale' else '待审阅草稿'} · {'模拟生成' if story.mode == 'mock' else '模型生成'}",
                "",
                story.synopsis,
            ]
            for chapter in story.chapters:
                lines += [
                    "",
                    "## " + chapter["title"],
                    "",
                    chapter["content"],
                    "",
                    "来源节点：" + "、".join(chapter["node_ids"]),
                ]
            lines += ["", "## 节点连接"]
            for edge in story.connections:
                lines += [
                    f"- {titles.get(edge['source_id'], edge['source_id'])} → {titles.get(edge['target_id'], edge['target_id'])}：{edge['reason']}"
                ]
            lines += [
                "",
                "## 补写与待确认",
                *["- " + a for a in story.assumptions],
                "",
                "## 创作依据",
                f"Skill：{story.skill_name}",
                f"Skill 哈希：{story.skill_hash}",
                "节点版本：" + str(story.node_versions),
            ]
            return Response(
                "\n".join(lines),
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="story-draft.md"'},
            )

    return routes
