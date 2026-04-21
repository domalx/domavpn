# domavpn - 内网穿透代理程序

基于 Python Flask 框架实现的轻量级内网穿透代理程序。

## 功能特点

- **Flask框架**：稳定可靠的HTTP服务
- **自动连接**：内网服务自动连接远程服务器
- **随机端口分配**：动态分配代理端口（50000-60000）
- **安全认证**：用户名密码认证（SHA256加密）
- **IP黑名单**：连续N次认证失败自动拉黑（默认3次，5分钟）
- **跨平台**：支持Windows/Linux/macOS
- **连接统计**：实时统计连接数、认证次数等
- **配置热更新**：无需重启即可更新配置
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

## 部署注意

1. **端口开放**：远程服务器需开放8871端口和50000-60000端口范围
2. **公网IP**：内网服务配置中需设置远程服务器的公网IP
3. **安全建议**：生产环境使用强密码，定期更换
4. **防火墙**：配置防火墙允许端口访问