#!/usr/bin/env python3
"""
内网HTTP服务 - 提供文件访问和代理连接功能

功能特性：
- 文件列表查看和文件下载
- 自动连接远程代理服务器
- 支持用户名密码认证
- 端口转发功能
- 自动重连机制
"""

import os
import json
import socket
import threading
import time
import requests
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

# 配置加载
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

def load_config():
    """加载配置文件"""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"[内网服务] 配置文件解析错误: {e}")
            return {}
    return {}

# 全局配置
config = load_config()
LOCAL_HOST = config.get('host', '127.0.0.1')
LOCAL_PORT = config.get('port', 5000)
REMOTE_HOST = config.get('remote_server', {}).get('host', '127.0.0.1')
REMOTE_PORT = config.get('remote_server', {}).get('port', 8871)
USERNAME = config.get('remote_server', {}).get('auth', {}).get('username', '')
PASSWORD = config.get('remote_server', {}).get('auth', {}).get('password', '')

# Flask应用
app = Flask(__name__)

class LocalServer:
    """内网服务核心类"""
    
    def __init__(self):
        self.proxy_port = None
        self.running = True
        self.connected = False
        
    def _log(self, message):
        """日志记录"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [内网服务] {message}")
    
    def _authenticate_with_remote(self):
        """向远程服务器认证并获取代理端口"""
        auth_url = f"http://{REMOTE_HOST}:{REMOTE_PORT}/api/auth"
        payload = {'username': USERNAME, 'password': PASSWORD}
        
        try:
            self._log(f"正在连接远程服务器: {REMOTE_HOST}:{REMOTE_PORT}")
            response = requests.post(auth_url, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                self.proxy_port = data.get('port')
                self.connected = True
                
                self._log(f"认证成功，代理端口: {self.proxy_port}")
                self._log(f"代理访问地址: http://{REMOTE_HOST}:{self.proxy_port}")
                self._log(f"本地访问地址: http://{LOCAL_HOST}:{LOCAL_PORT}")
                return True
            else:
                error_msg = response.json().get('message', '未知错误')
                self._log(f"认证失败: {error_msg}")
                return False
                
        except requests.exceptions.ConnectionError:
            self._log("无法连接远程服务器")
            return False
        except requests.exceptions.Timeout:
            self._log("连接远程服务器超时")
            return False
        except Exception as e:
            self._log(f"认证异常: {e}")
            return False
    
    def _forward_data(self, source, destination):
        """双向数据转发"""
        try:
            while self.running:
                data = source.recv(4096)
                if not data:
                    break
                destination.sendall(data)
        except Exception as e:
            self._log(f"数据转发异常: {e}")
        finally:
            try:
                source.close()
            except:
                pass
            try:
                destination.close()
            except:
                pass
    
    def _connect_to_remote(self):
        """连接到远程代理服务器"""
        while self.running:
            # 先认证
            if not self._authenticate_with_remote():
                self._log("等待5秒后重新连接...")
                time.sleep(5)
                continue
            
            try:
                # 连接到远程代理端口
                proxy_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                proxy_socket.connect((REMOTE_HOST, self.proxy_port))
                self._log(f"已连接到远程代理端口: {self.proxy_port}")
                
                # 连接到本地服务
                local_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                local_socket.connect((LOCAL_HOST, LOCAL_PORT))
                
                # 启动双向转发
                forward_thread = threading.Thread(
                    target=self._forward_data,
                    args=(proxy_socket, local_socket)
                )
                forward_thread.daemon = True
                forward_thread.start()
                
                self._forward_data(local_socket, proxy_socket)
                
            except ConnectionRefusedError:
                self._log("无法连接远程代理端口")
            except Exception as e:
                self._log(f"代理连接异常: {e}")
            finally:
                self.proxy_port = None
                self.connected = False
            
            if self.running:
                self._log("等待5秒后重新连接...")
                time.sleep(5)
    
    def start_proxy_thread(self):
        """启动代理连接线程"""
        proxy_thread = threading.Thread(target=self._connect_to_remote)
        proxy_thread.daemon = True
        proxy_thread.start()
    
    def stop(self):
        """停止服务"""
        self.running = False
        self._log("服务已停止")

# 创建内网服务实例
local_server = LocalServer()

# Flask路由
@app.route('/')
def index():
    """首页"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>内网文件服务</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            h1 { color: #333; }
            ul { list-style-type: none; padding: 0; }
            li { margin: 10px 0; }
            a { color: #0066cc; text-decoration: none; }
            a:hover { text-decoration: underline; }
        </style>
    </head>
    <body>
        <h1>内网HTTP服务 - 文件访问</h1>
        <p>当前目录: /</p>
        <ul>
            <li><a href="/list">列出文件</a></li>
            <li><a href="/test.txt">查看测试文件</a></li>
            <li><a href="/api/status">查看服务状态</a></li>
        </ul>
    </body>
    </html>
    """

@app.route('/list')
def list_files():
    """列出当前目录文件"""
    try:
        files = []
        for item in os.listdir('.'):
            if os.path.isfile(item):
                files.append({
                    'name': item,
                    'size': os.path.getsize(item),
                    'type': 'file'
                })
            elif os.path.isdir(item):
                files.append({
                    'name': item,
                    'type': 'directory'
                })
        return jsonify(files)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/<path:filename>')
def get_file(filename):
    """获取文件内容"""
    if os.path.exists(filename) and os.path.isfile(filename):
        return send_from_directory('.', filename)
    return jsonify({'error': '文件不存在'}), 404

@app.route('/api/status')
def status():
    """获取服务状态"""
    return jsonify({
        'proxy_port': local_server.proxy_port,
        'connected': local_server.connected,
        'remote_server': f"{REMOTE_HOST}:{REMOTE_PORT}",
        'local_server': f"{LOCAL_HOST}:{LOCAL_PORT}"
    })

if __name__ == '__main__':
    local_server._log(f"配置信息 - 远程服务器: {REMOTE_HOST}:{REMOTE_PORT}, 用户名: {USERNAME}")
    
    # 启动代理连接线程
    local_server.start_proxy_thread()
    
    local_server._log(f"HTTP服务启动，端口 {LOCAL_PORT}")
    
    try:
        app.run(host=LOCAL_HOST, port=LOCAL_PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        local_server.stop()