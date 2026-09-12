# 剧本生成器

通过对话搭建世界观与时间线，将问答整理为可追溯的故事摘要，再结合创作 Skill 生成小说草稿。

- 大模型根据回答主导追问，支持主题跳转与补问。
- 主线围绕主角、世界规则及事件因果，闲聊可作为辅助灵感影响剧情。
- 摘要节点支持编辑、确认、撤回、来源追溯和版本记录。
- 点击“生成小说”停止当前回复，用已保存摘要生成可导出的故事。
- 支持 DeepSeek 和兼容 Chat Completions 的服务；默认 mock，无真实模型费用。

## 本地运行

需要 Node.js 24、npm、uv 和 Python 3.11。

```bash
uv sync --project backend --python 3.11 --locked
cd frontend
npm ci
npm run build
cd ..
bash scripts/start-local.sh
```

打开 http://127.0.0.1:3001，后端使用 8001。数据库首次启动自动创建于 `data/story.db`。

## DeepSeek

复制 `.env.deepseek.example` 为 `.env`，在本地填写 `DEEPSEEK_API_KEY` 后重启。不要提交真实密钥。

```bash
cp .env.deepseek.example .env
# 在本地编辑 .env 填写密钥，然后重启服务
curl -X POST http://127.0.0.1:8001/api/v1/model/check
```

连接检查只验证认证及模型列表，不代表文学效果验收。问答和小说生成使用持久化调用预算；示例上限为 100 次真实请求，包含重试。

## 验证

```bash
cd backend
uv run pytest
uv run ruff check .
cd ../frontend
npm run lint
npm run typecheck
# 启动应用后运行桌面和手机验收
npx playwright install chromium
npm run test:e2e
```

当前工程流程已做自动化测试；未提供真实密钥时，模型主导提问和文学质量仅能以模拟流程演示。单次生成定位为短篇草稿，上下文容量有界。

## 文档与创作 Skill

- [DeepSeek 接入](docs/DeepSeek接入.md)
- [Agent 开发说明](docs/故事Agent开发说明.md)
- [产品需求](PRD.md)

创作原则来源于 [Wunicheng233/jin-yong-perspective](https://github.com/Wunicheng233/jin-yong-perspective)，保留本地 Skill 文件并记录指纹；不冒充作者本人。部分技术文档保留早期阶段记录，以当前 README 和最新增补为准。
