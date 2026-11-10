# 知识库平台

负责把文档解析、切分、向量化后存入 Milvus，并提供带权限校验的基础召回接口。设计见 [docs/design.md](docs/design.md)。

## 一键部署

```bash
cd deploy
cp .env.example .env      # 按文件内注释修改标有【必改】的项
docker compose up -d
```

启动后访问 `http://<服务器>:8200`。数据、日志、密钥都在 `deploy/volumes/` 下。

## 本机开发

基础组件用 Docker 运行，接口进程和任务进程直接在本机运行。

```bash
# 1. 环境（只需一次）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 基础组件
cd deploy && cp .env.example .env          # 填写密码
docker compose up -d postgres redis minio etcd milvus

# 3. 接口进程与任务进程
cd ../backend && cp .env.example .env      # 密码与 deploy/.env 保持一致
./manage.sh migrate
./manage.sh start
```

| 命令 | 作用 |
|---|---|
| `./manage.sh start\|stop\|restart\|status [api\|worker]` | 启停进程，不写进程名表示两个都操作 |
| `./manage.sh migrate` | 执行数据库迁移 |

健康检查：`curl http://127.0.0.1:8210/api/v1/health`　接口文档：`http://127.0.0.1:8210/api/v1/docs`

日志在 `backend/logs/api/app.log` 和 `backend/logs/worker/app.log`。
