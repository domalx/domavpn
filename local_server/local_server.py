#!/usr/bin/env python3
"""
内网HTTP服务 - 提供文件访问和代理连接功能

功能特性：
- 文件列表查看和文件下载
- 自动连接远程代理服务器
- 支持用户名密码认证
- 端口转发功能
- 自动重连机制
- 日志文件记录
- 连接统计功能
- 文件上传功能
"""

import os
import json
import socket
import threading
import time
import requests
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, redirect, url_for

# 配置加载
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
LOG_PATH = os.path.join(os.path.dirname(__file__), 'server.log')

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

def log_to_file(message):
    """写入日志文件"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_line = f"[{timestamp}] {message}\n"
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(log_line)
    except Exception as e:
        print(f"日志写入失败: {e}")

# 全局配置
config = load_config()
LOCAL_HOST = config.get('host', '127.0.0.1')
LOCAL_PORT = config.get('port', 5000)
REMOTE_HOST = config.get('remote_server', {}).get('host', '127.0.0.1')
REMOTE_PORT = config.get('remote_server', {}).get('port', 8871)
USERNAME = config.get('remote_server', {}).get('auth', {}).get('username', '')
PASSWORD = config.get('remote_server', {}).get('auth', {}).get('password', '')
MAX_RETRY_DELAY = config.get('retry', {}).get('max_delay', 60)
MIN_RETRY_DELAY = config.get('retry', {}).get('min_delay', 5)

# Flask应用
app = Flask(__name__)

class LocalServer:
    """内网服务核心类"""
    
    def __init__(self):
        self.proxy_port = None
        self.running = True
        self.connected = False
        self.retry_delay = MIN_RETRY_DELAY
        self.stats = {
            'total_reconnections': 0,
            'successful_connections': 0,
            'failed_connections': 0,
            'start_time': time.time()
        }
    
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
                self.retry_delay = MIN_RETRY_DELAY
                self.stats['successful_connections'] += 1
                
                log_message = f"认证成功，代理端口: {self.proxy_port}"
                self._log(log_message)
                log_to_file(log_message)
                
                self._log(f"代理访问地址: http://{REMOTE_HOST}:{self.proxy_port}")
                self._log(f"本地访问地址: http://{LOCAL_HOST}:{LOCAL_PORT}")
                return True
            else:
                error_msg = response.json().get('message', '未知错误')
                log_message = f"认证失败: {error_msg}"
                self._log(log_message)
                log_to_file(log_message)
                self.stats['failed_connections'] += 1
                return False
                
        except requests.exceptions.ConnectionError:
            log_message = "无法连接远程服务器"
            self._log(log_message)
            log_to_file(log_message)
            self.stats['failed_connections'] += 1
            return False
        except requests.exceptions.Timeout:
            log_message = "连接远程服务器超时"
            self._log(log_message)
            log_to_file(log_message)
            self.stats['failed_connections'] += 1
            return False
        except Exception as e:
            log_message = f"认证异常: {e}"
            self._log(log_message)
            log_to_file(log_message)
            self.stats['failed_connections'] += 1
            return False
    
    def _forward_data(self, source, destination):
        """双向数据转发"""
        try:
            while self.running:
                source.settimeout(30)
                data = source.recv(4096)
                if not data:
                    break
                destination.sendall(data)
        except socket.timeout:
            self._log("连接超时")
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
            if not self._authenticate_with_remote():
                self._log(f"等待{self.retry_delay}秒后重新连接...")
                time.sleep(self.retry_delay)
                self.retry_delay = min(self.retry_delay * 2, MAX_RETRY_DELAY)
                continue
            
            try:
                proxy_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                proxy_socket.settimeout(10)
                proxy_socket.connect((REMOTE_HOST, self.proxy_port))
                
                log_message = f"已连接到远程代理端口: {self.proxy_port}"
                self._log(log_message)
                log_to_file(log_message)
                
                local_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                local_socket.settimeout(5)
                local_socket.connect((LOCAL_HOST, LOCAL_PORT))
                
                forward_thread = threading.Thread(
                    target=self._forward_data,
                    args=(proxy_socket, local_socket)
                )
                forward_thread.daemon = True
                forward_thread.start()
                
                self._forward_data(local_socket, proxy_socket)
                
            except ConnectionRefusedError:
                log_message = "无法连接远程代理端口"
                self._log(log_message)
                log_to_file(log_message)
            except Exception as e:
                log_message = f"代理连接异常: {e}"
                self._log(log_message)
                log_to_file(log_message)
            finally:
                self.proxy_port = None
                self.connected = False
                self.stats['total_reconnections'] += 1
                self.retry_delay = min(self.retry_delay * 2, MAX_RETRY_DELAY)
            
            if self.running:
                self._log(f"等待{self.retry_delay}秒后重新连接...")
                time.sleep(self.retry_delay)
    
    def start_proxy_thread(self):
        """启动代理连接线程"""
        proxy_thread = threading.Thread(target=self._connect_to_remote)
        proxy_thread.daemon = True
        proxy_thread.start()
    
    def stop(self):
        """停止服务"""
        self.running = False
        log_message = "服务已停止"
        self._log(log_message)
        log_to_file(log_message)

local_server = LocalServer()

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
            .status { padding: 10px; margin: 20px 0; border-radius: 5px; }
            .status.online { background-color: #e8f5e9; color: #2e7d32; }
            .status.offline { background-color: #ffebee; color: #c62828; }
        </style>
    </head>
    <body>
        <h1>内网HTTP服务 - 文件访问</h1>
        <div class="status {}">
            代理状态: {} | 端口: {}
        </div>
        <p>当前目录: /</p>
        <ul>
            <li><a href="/list">列出文件</a></li>
            <li><a href="/upload">上传文件</a></li>
            <li><a href="/api/status">查看服务状态</a></li>
        </ul>
    </body>
    </html>
    """.format(
        'online' if local_server.connected else 'offline',
        '已连接' if local_server.connected else '未连接',
        local_server.proxy_port or 'N/A'
    )

@app.route('/list')
def list_files():
    """列出当前目录文件"""
    try:
        files = []
        base_path = '.'
        path = request.args.get('path', '')
        full_path = os.path.join(base_path, path)
        
        if not os.path.exists(full_path) or not os.path.isdir(full_path):
            return jsonify({'error': '路径不存在'}), 404
        
        for item in os.listdir(full_path):
            item_path = os.path.join(full_path, item)
            if os.path.isfile(item_path):
                files.append({
                    'name': item,
                    'size': os.path.getsize(item_path),
                    'type': 'file',
                    'mtime': datetime.fromtimestamp(os.path.getmtime(item_path)).strftime('%Y-%m-%d %H:%M:%S')
                })
            elif os.path.isdir(item_path):
                files.append({
                    'name': item,
                    'type': 'directory',
                    'mtime': datetime.fromtimestamp(os.path.getmtime(item_path)).strftime('%Y-%m-%d %H:%M:%S')
                })
        
        files.sort(key=lambda x: (x['type'], x['name']))
        return jsonify({
            'path': path,
            'files': files,
            'total': len(files)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/<path:filename>')
def get_file(filename):
    """获取文件内容"""
    if os.path.exists(filename) and os.path.isfile(filename):
        return send_from_directory('.', filename)
    return jsonify({'error': '文件不存在'}), 404

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    """文件上传"""
    if request.method == 'POST':
        if 'file' not in request.files:
            return jsonify({'error': '未选择文件'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': '未选择文件'}), 400
        
        if file:
            file.save(os.path.join('.', file.filename))
            log_message = f"文件上传成功: {file.filename}"
            local_server._log(log_message)
            log_to_file(log_message)
            return jsonify({
                'status': 'success',
                'message': f'文件 {file.filename} 上传成功',
                'filename': file.filename
            }), 200
    
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>文件上传</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            h1 { color: #333; }
            form { margin: 20px 0; }
            input[type="file"] { margin: 10px 0; }
            button { padding: 10px 20px; background-color: #0066cc; color: white; border: none; border-radius: 5px; }
        </style>
    </head>
    <body>
        <h1>文件上传</h1>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file">
            <br>
            <button type="submit">上传</button>
        </form>
        <p><a href="/">返回首页</a></p>
    </body>
    </html>
    """

@app.route('/api/status')
def status():
    """获取服务状态"""
    uptime = int(time.time() - local_server.stats['start_time'])
    return jsonify({
        'proxy_port': local_server.proxy_port,
        'connected': local_server.connected,
        'remote_server': f"{REMOTE_HOST}:{REMOTE_PORT}",
        'local_server': f"{LOCAL_HOST}:{LOCAL_PORT}",
        'stats': {
            'total_reconnections': local_server.stats['total_reconnections'],
            'successful_connections': local_server.stats['successful_connections'],
            'failed_connections': local_server.stats['failed_connections'],
            'uptime_seconds': uptime,
            'uptime_human': f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s"
        }
    })

@app.route('/api/reload')
def reload_config():
    """热更新配置"""
    global config, LOCAL_HOST, LOCAL_PORT, REMOTE_HOST, REMOTE_PORT
    global USERNAME, PASSWORD, MAX_RETRY_DELAY, MIN_RETRY_DELAY
    
    try:
        config = load_config()
        LOCAL_HOST = config.get('host', '127.0.0.1')
        LOCAL_PORT = config.get('port', 5000)
        REMOTE_HOST = config.get('remote_server', {}).get('host', '127.0.0.1')
        REMOTE_PORT = config.get('remote_server', {}).get('port', 8871)
        USERNAME = config.get('remote_server', {}).get('auth', {}).get('username', '')
        PASSWORD = config.get('remote_server', {}).get('auth', {}).get('password', '')
        MAX_RETRY_DELAY = config.get('retry', {}).get('max_delay', 60)
        MIN_RETRY_DELAY = config.get('retry', {}).get('min_delay', 5)
        
        log_message = "配置已热更新"
        local_server._log(log_message)
        log_to_file(log_message)
        
        return jsonify({'status': 'success', 'message': '配置已更新'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'配置更新失败: {e}'}), 500

if __name__ == '__main__':
    log_message = f"配置信息 - 远程服务器: {REMOTE_HOST}:{REMOTE_PORT}, 用户名: {USERNAME}"
    local_server._log(log_message)
    log_to_file(log_message)
    
    local_server.start_proxy_thread()
    
    log_message = f"HTTP服务启动，端口 {LOCAL_PORT}"
    local_server._log(log_message)
    log_to_file(log_message)
    
    try:
        app.run(host=LOCAL_HOST, port=LOCAL_PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        local_server.stop()