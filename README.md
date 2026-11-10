# 知识库平台

把文档解析、切分、向量化后存入 Milvus，并提供带权限校验的基础召回接口。

- 设计文档：[docs/design.md](docs/design.md)
- 给接入应用的开发者：[docs/retrieval-api.md](docs/retrieval-api.md)

## 一键部署

需要 Docker 和 Docker Compose。

```bash
cd deploy
cp .env.example .env      # 按文件内注释修改标有【必改】的项
docker compose up -d      # 首次会构建镜像，需要几分钟
```

启动后访问 `http://服务器地址:8200`。

**首次登录**：登录名 `admin`，初始密码在 `deploy/volumes/secrets/initial_admin_password`，登录后必须修改。改完请删除这个文件。

**数据位置**：数据库、原始文件、向量数据、日志、密钥都在 `deploy/volumes/` 下。备份和迁移时整个目录一起处理。

### MinIO 镜像需要手工导入

MinIO 的镜像已从公共镜像仓库下架，新服务器上 `docker compose up` 拉不到它。部署前先从已有这个镜像的机器导出，再到新服务器导入：

```bash
# 在已有镜像的机器上
docker save minio/minio:RELEASE.2025-09-07T16-13-09Z | gzip > minio.tar.gz

# 把 minio.tar.gz 拷到新服务器，然后
gunzip -c minio.tar.gz | docker load
```

公司有内部镜像仓库时，也可以把镜像推上去，然后在 `deploy/.env` 里设置 `MINIO_IMAGE`。

### 常用操作

```bash
docker compose ps                    # 查看各组件状态
docker compose logs -f api worker    # 查看接口和任务进程的输出
docker compose up -d --build         # 更新代码后重新构建并启动
docker compose down                  # 停止，数据保留
curl http://127.0.0.1:8200/api/v1/health   # 健康检查
```

运行日志在 `deploy/volumes/logs/api/app.log` 和 `deploy/volumes/logs/worker/app.log`，每天午夜归档，保留 30 天。

### 使用已有的 PostgreSQL、Redis、MinIO 或 Milvus

从 `deploy/.env` 的 `COMPOSE_PROFILES` 里去掉对应的名字，再取消「外部组件地址」一节的注释并填写。

### 更换向量模型

修改 `deploy/.env` 里的 `EMBEDDING_*` 后重启。已有的知识库会提示「需要重建索引」，在知识库的「设置」页签里点重建；重建之前只能做关键字检索。

## 本机开发

基础组件用 Docker 运行，接口进程、任务进程和界面直接在本机运行。

```bash
# 1. Python 环境（只需一次）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 基础组件
cd deploy && cp .env.example .env          # 填写密码
docker compose up -d postgres redis minio etcd milvus

# 3. 接口进程与任务进程
cd ../backend && cp .env.example .env      # 密码与 deploy/.env 保持一致，填写向量模型的密钥
./manage.sh migrate
./manage.sh start

# 4. 管理界面
cd ../frontend
npm install                                # 只需一次
npm run dev                                # http://127.0.0.1:8220
```

| 命令 | 作用 |
|---|---|
| `./manage.sh start\|stop\|restart\|status [api\|worker]` | 启停进程，不写进程名表示两个都操作 |
| `./manage.sh migrate` | 执行数据库迁移 |
| `KB_WEB_HOST=0.0.0.0 npm run dev` | 让局域网内其他电脑也能访问开发中的界面 |

- 首次启动接口进程时创建 `admin`，初始密码在 `backend/run/initial_admin_password`。
- 界面的开发服务器会把 `/api` 和 `/open` 的请求转给本机 8210 端口的接口进程。
- 接口文档：`http://127.0.0.1:8210/api/v1/docs`
- 日志在 `backend/logs/api/app.log` 和 `backend/logs/worker/app.log`。
- 修改数据表后生成迁移：在 `backend` 目录执行 `../.venv/bin/alembic revision --autogenerate -m "说明"`。表和字段都必须写注释。

注意：本机开发用的进程和一键部署的容器不要同时运行。它们连的是同一套数据库和任务队列，同时运行会互相抢任务。

## 目录

```
backend/     接口进程与任务进程（FastAPI、RQ）
frontend/    管理界面（React、Ant Design）
deploy/      一键部署：docker-compose.yaml、.env.example、nginx 配置
docs/        设计文档、召回接口使用说明
```
