# domavpn - 内网穿透代理程序

基于 Python Flask 框架实现的轻量级内网穿透代理程序。

## 功能特点

- **Flask框架**：稳定可靠的HTTP服务
- **自动连接**：内网服务自动连接远程服务器
- **随机端口分配**：动态分配代理端口（50000-60000）
- **Token访问机制**：内网服务连接时分配Token，用户可通过Token访问对应内网服务
- **安全认证**：用户名密码认证（SHA256加密）
- **IP黑名单**：连续N次认证失败自动拉黑（默认3次，5分钟）
- **跨平台**：支持Windows/Linux/macOS
- **连接统计**：实时统计连接数、认证次数等
- **配置热更新**：无需重启即可更新配置
- **自动重连**：指数退避策略自动重新连接
- **Token持久化**：Token自动保存到本地配置文件

## 项目结构

```
domavpn/
├── README.md
├── requirements.txt
├── .gitignore
├── local_server/
│   ├── local_server.py       # 内网HTTP服务
│   ├── config.json          # 内网服务配置
│   ├── shared_dirs.json      # 共享目录配置
│   ├── templates/            # 前端模板（独立）
│   │   └── index.html        # 文件列表页面
│   └── server.log           # 日志文件（自动生成）
├── remote_server/
│   ├── remote_server.py      # 远程代理服务器
│   ├── config.json          # 远程服务器配置
│   ├── templates/           # 前端模板（独立）
│   │   ├── index.html        # Token输入页面
│   │   └── access.html       # 访问结果页面
│   └── server.log           # 日志文件（自动生成）
└── tool/
    ├── config_tool.py        # 配置管理工具
    ├── start_local.py        # 内网服务启动脚本
    └── start_remote.py       # 远程服务器启动脚本
```

## 整体架构

### 双端口设计

```
┌─────────────────────────────────────────────────────────────────┐
│                      远程服务器 (Remote Server)                  │
│                                                                 │
│   ┌─────────────────┐              ┌─────────────────┐        │
│   │  8871 端口       │              │  8872 端口       │        │
│   │  (管理API)       │              │  (用户访问)       │        │
│   │                  │              │                  │        │
│   │  /api/auth       │              │  GET /           │        │
│   │  /api/connect    │              │  POST /access    │        │
│   │  /api/token/xxx  │              │                  │        │
│   └────────┬─────────┘              └────────┬─────────┘        │
│            │                                   │                  │
│            │         ┌─────────────┐          │                  │
│            └────────►│  Token验证  ├──────────┘                  │
│                      └─────────────┘                             │
│                            │                                     │
│                      ┌─────┴─────┐                              │
│                      │ 代理转发   │                              │
│                      └─────┬─────┘                              │
└────────────────────────────┼────────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │     代理端口 (50000-60000)     │
              └──────────────┬──────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      内网服务器 (Local Server)                     │
│                                                                 │
│   ┌─────────────────┐              ┌─────────────────┐        │
│   │  5000 端口       │              │  TCP长连接       │        │
│   │  (文件服务)       │◄─────────────│  (代理转发)      │        │
│   │                  │              │                  │        │
│   │  GET /api/list   │              │  接收HTTP请求    │        │
│   │  GET /api/dirs   │              │  转发给Flask     │        │
│   └─────────────────┘              └─────────────────┘        │
└─────────────────────────────────────────────────────────────────┘
```

## 实现原理

### 1. 服务启动流程

#### 远程服务器 (remote_server.py)

```
1. 初始化Flask应用，监听8871和8872端口
2. 初始化代理服务器：
   - 创建代理端口池（50000-60000）
   - 创建Token映射表（token -> proxy_port）
   - 创建代理客户端字典（proxy_port -> local_conn）
3. 启动Socket服务器（用于代理端口监听）
4. 启动用户访问服务线程（run_user_proxy_server）
5. 启动心跳检测线程
```

#### 内网服务器 (local_server.py)

```
1. 从配置文件加载Token（如果存在）
2. 连接远程服务器认证：
   - POST /api/auth (用户名密码)
   - POST /api/connect (获取Token和代理端口)
3. 建立TCP长连接到代理端口
4. 启动代理数据转发线程
5. 启动心跳线程
6. 启动Flask文件服务（5000端口）
```

### 2. 认证与Token机制

```
┌──────────┐                      ┌──────────┐
│ 内网服务  │                      │ 远程服务  │
└────┬─────┘                      └────┬─────┘
     │                                 │
     │  POST /api/auth                 │
     │  {"username": "xxx",            │
     │   "password": "xxx"}           │
     │────────────────────────────────►│
     │                                 │ 验证用户名密码
     │  200 OK                        │
     │◄────────────────────────────────│
     │                                 │
     │  POST /api/connect              │
     │────────────────────────────────►│
     │                                 │ 生成Token和分配代理端口
     │  {"token": "abc...",           │
     │   "port": 50000}                │
     │◄────────────────────────────────│
     │                                 │
     │  保存Token到配置文件             │
     │                                 │
     │  TCP连接到代理端口50000           │
     │────────────────────────────────►│
     │                                 │ 记录local_conn
     │  连接成功                        │
     │◄────────────────────────────────│
```

### 3. HTTP代理转发原理

当用户通过Token访问内网服务时，数据转发流程如下：

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ 用户浏览器 │    │8872端口  │    │代理端口   │    │local_server│
└────┬─────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘
     │               │               │               │
     │  GET / HTTP   │               │               │
     │──────────────►│               │               │
     │               │               │               │
     │               │ Token验证通过  │               │
     │               │──────────────►│               │
     │               │               │               │
     │               │               │ 转发HTTP请求  │
     │               │               │──────────────►│
     │               │               │               │
     │               │               │  HTTP/1.1 200 │
     │               │               │◄──────────────│
     │               │               │               │
     │               │ 返回响应数据   │               │
     │               │◄──────────────│               │
     │               │               │               │
     │  HTTP/1.1 200 │               │               │
     │◄──────────────│               │               │
```

### 4. local_server代理转发实现

local_server的`_start_proxy_forwarding`函数实现了HTTP代理核心逻辑：

```python
def _start_proxy_forwarding(self):
    # 使用select监听长连接
    readable, _, exceptional = select.select([long_conn], [], [long_conn], 1)

    for src in readable:
        data = src.recv(8192)

        # 1. 解析HTTP请求
        header_end = data.find(b'\r\n\r\n')
        if header_end != -1:
            # 提取请求方法和路径
            path = extract_path_from_headers(data)

            # 2. 转发到本地Flask服务
            conn = http.client.HTTPConnection('127.0.0.1', LOCAL_PORT)
            conn.request(method='GET', url=path)
            response = conn.getresponse()

            # 3. 构建HTTP响应
            response_body = response.read()
            http_response = build_http_response(response, response_body)

            # 4. 返回响应给remote_server
            long_conn.sendall(http_response)
```

### 5. 双端口职责分离

| 端口 | 协议 | 用途 | 主要API |
|------|------|------|---------|
| 8871 | HTTP | 管理API、内网服务连接 | `/api/auth`, `/api/connect`, `/api/token/<token>` |
| 8872 | HTTP | 用户访问入口、Web界面 | `GET /`, `POST /access` |
| 50000-60000 | TCP | 代理数据转发 | Socket长连接 |

### 6. 文件服务功能

local_server提供文件列表服务：

```
用户访问流程：
1. 用户浏览器 → remote_server(8872) → Token验证
2. 验证通过后跳转到代理端口
3. 代理端口 → local_server → Flask文件服务
4. Flask返回文件列表HTML
```

文件列表API：
- `GET /api/dirs` - 获取共享目录列表
- `GET /api/list?dir=<id>` - 获取指定目录的文件列表

## 快速开始

### 环境准备

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows: venv\Scripts\activate
# Linux/macOS: source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 启动服务

```bash
# 启动远程代理服务器
python tool/start_remote.py

# 启动内网HTTP服务
python tool/start_local.py
```

### 访问服务

内网服务启动后会输出代理端口和Token，例如：
```
代理访问地址: http://<server-ip>:50000
本地访问地址: http://localhost:5000
Token: abcdef123456...
```

#### Token访问方式

1. **通过Web界面访问**：访问 `http://<remote-server>:8872`，输入Token后访问内网服务

2. **通过API验证Token**：
   ```bash
   # 查询Token信息
   curl http://<remote-server>:8871/api/token/<token>

   # 通过Token访问代理服务
   curl http://<remote-server>:8871/api/access?token=<token>
   ```

3. **Token验证响应示例**：
   ```json
   {
     "status": "success",
     "proxy_port": 50000,
     "proxy_url": "http://0.0.0.0:50000",
     "message": "Token验证成功"
   }
   ```

## 配置管理

```bash
# 显示配置
python tool/config_tool.py show

# 修改远程服务器密码
python tool/config_tool.py remote --username admin --password newpassword

# 修改内网服务密码（交互式）
python tool/config_tool.py local --interactive
```

## 部署注意

1. **端口开放**：远程服务器需开放8871、8872端口和50000-60000端口范围
2. **公网IP**：内网服务配置中需设置远程服务器的公网IP
3. **安全建议**：生产环境使用强密码，定期更换
4. **防火墙**：配置防火墙允许端口访问
