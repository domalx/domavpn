# domavpn - 内网穿透代理程序

基于 Python Flask 框架实现的轻量级内网穿透代理程序。

## 功能特点

- **Flask框架**：稳定可靠的HTTP服务
- **自动连接**：内网服务自动连接远程服务器
- **随机端口分配**：动态分配代理端口
- **安全认证**：用户名密码认证（SHA256加密）
- **IP黑名单**：连续3次认证失败拉黑5分钟
- **跨平台**：支持Windows/Linux/macOS

## 项目结构

```
domavpn/
├── README.md
├── requirements.txt
├── .gitignore
├── local_server/
│   ├── local_server.py    # 内网HTTP服务
│   └── config.json        # 内网服务配置
├── remote_server/
│   ├── remote_server.py   # 远程代理服务器
│   └── config.json        # 远程服务器配置
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
    }
}
```

## API接口

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/auth | POST | 认证并获取代理端口 |
| /api/health | GET | 健康检查 |
| /api/blacklist | GET | 获取黑名单 |
| /api/blacklist/<ip> | DELETE | 移除黑名单IP |
| /list | GET | 列出文件（内网服务） |

## 部署注意

1. 远程服务器需开放端口：8871（主端口）和50000-60000（代理端口）
2. 内网服务配置中需设置远程服务器的公网IP
3. 生产环境建议使用HTTPS和强密码

## 许可证

MIT License