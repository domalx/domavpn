# domavpn - 内网穿透代理程序

基于 Python Flask 框架实现的轻量级内网穿透代理程序。

## 功能特点

- **Flask框架**：稳定可靠的HTTP服务
- **自动连接**：内网服务自动连接远程服务器
- **随机端口分配**：动态分配代理端口（50000-60000）
- **安全认证**：用户名密码认证（SHA256加密）
- **IP黑名单**：连续N次认证失败自动拉黑（默认3次，5分钟）
- **跨平台**：支持Windows/Linux/macOS
- **日志记录**：日志输出到控制台和文件
- **连接统计**：实时统计连接数、认证次数等
- **配置热更新**：无需重启即可更新配置
- **文件上传**：支持通过Web界面上传文件
- **自动重连**：指数退避策略自动重新连接

## 项目结构

```
domavpn/
├── README.md
├── requirements.txt
├── .gitignore
├── local_server/
│   ├── local_server.py    # 内网HTTP服务
│   ├── config.json        # 内网服务配置
│   └── server.log         # 日志文件（自动生成）
├── remote_server/
│   ├── remote_server.py   # 远程代理服务器
│   ├── config.json        # 远程服务器配置
│   └── server.log         # 日志文件（自动生成）
└── tool/
    ├── config_tool.py     # 配置管理工具
    ├── start_local.py     # 内网服务启动脚本
    └── start_remote.py    # 远程服务器启动脚本
```

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

内网服务启动后会输出代理端口，例如：
```
代理访问地址: http://<server-ip>:50000
本地访问地址: http://localhost:5000
```

### 配置管理

```bash
# 显示配置
python tool/config_tool.py show

# 修改远程服务器密码
python tool/config_tool.py remote --username admin --password newpassword

# 修改内网服务密码（交互式）
python tool/config_tool.py local --interactive
```

## 配置说明

### 远程服务器配置 (remote_server/config.json)

```json
{
    "listen_host": "0.0.0.0",
    "listen_port": 8871,
    "auth": {
        "enabled": true,
        "username": "admin",
        "password": "password123"
    },
    "security": {
        "max_failed_attempts": 3,
        "blacklist_duration": 300
    },
    "port_pool": {
        "start": 50000,
        "end": 60000
    }
}
```

### 内网服务配置 (local_server/config.json)

```json
{
    "host": "127.0.0.1",
    "port": 5000,
    "remote_server": {
        "host": "127.0.0.1",
        "port": 8871,
        "auth": {
            "username": "admin",
            "password": "password123"
        }
    },
    "retry": {
        "min_delay": 5,
        "max_delay": 60
    }
}
```

## API接口

### 远程服务器API

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/auth | POST | 认证并获取代理端口 |
| /api/health | GET | 健康检查（含统计信息） |
| /api/blacklist | GET | 获取黑名单列表 |
| /api/blacklist/<ip> | DELETE | 移除黑名单IP |
| /api/proxies | GET | 获取活跃代理端口列表 |
| /api/reload | POST | 热更新配置 |

### 内网服务API

| 接口 | 方法 | 说明 |
|------|------|------|
| / | GET | 首页（含状态显示） |
| /list | GET | 列出文件（支持path参数） |
| /upload | GET/POST | 文件上传 |
| /<filename> | GET | 获取文件内容 |
| /api/status | GET | 获取服务状态和统计 |
| /api/reload | GET | 热更新配置 |

## 配置参数说明

### 远程服务器

| 参数 | 说明 | 默认值 |
|------|------|--------|
| listen_host | 监听地址 | 0.0.0.0 |
| listen_port | 监听端口 | 8871 |
| auth.enabled | 是否启用认证 | true |
| auth.username | 用户名 | admin |
| auth.password | 密码 | password123 |
| security.max_failed_attempts | 最大失败次数 | 3 |
| security.blacklist_duration | 拉黑时长(秒) | 300 |
| port_pool.start | 端口池起始 | 50000 |
| port_pool.end | 端口池结束 | 60000 |

### 内网服务

| 参数 | 说明 | 默认值 |
|------|------|--------|
| host | 监听地址 | 127.0.0.1 |
| port | 监听端口 | 5000 |
| remote_server.host | 远程服务器地址 | 127.0.0.1 |
| remote_server.port | 远程服务器端口 | 8871 |
| retry.min_delay | 最小重试延迟(秒) | 5 |
| retry.max_delay | 最大重试延迟(秒) | 60 |

## 部署注意

1. **端口开放**：远程服务器需开放8871端口和50000-60000端口范围
2. **公网IP**：内网服务配置中需设置远程服务器的公网IP
3. **安全建议**：生产环境使用强密码，定期更换
4. **防火墙**：配置防火墙允许端口访问

## 使用示例

### 1. 列出文件

```bash
curl http://localhost:50000/list
```

响应示例：
```json
{
    "path": "",
    "files": [
        {"name": "test.txt", "size": 123, "type": "file", "mtime": "2024-01-01 12:00:00"}
    ],
    "total": 1
}
```

### 2. 上传文件

```bash
curl -X POST -F "file=@example.txt" http://localhost:50000/upload
```

### 3. 查看服务状态

```bash
curl http://localhost:8871/api/health
```

响应示例：
```json
{
    "status": "running",
    "auth_enabled": true,
    "active_proxies": 1,
    "blacklist_count": 0,
    "stats": {
        "total_connections": 10,
        "successful_auth": 5,
        "failed_auth": 0,
        "current_clients": 2,
        "uptime_seconds": 3600,
        "uptime_human": "1h 0m 0s"
    }
}
```

## 许可证

MIT License