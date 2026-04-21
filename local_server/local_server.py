#!/usr/bin/env python3
"""
内网HTTP服务 - 提供文件列表展示和代理连接功能

功能特性：
- 配置指定目录的文件列表查看（支持多个目录）
- 自动连接远程代理服务器（长连接）
- 支持用户名密码认证
- 从远程代理服务器获得token并存储在本地配置文件中
- 用户访问代理时可以通过token进行访问内网http服务
"""

import os
import json
import socket
import threading
import time
import requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
LOG_PATH = os.path.join(os.path.dirname(__file__), 'server.log')
SHARED_DIRS_PATH = os.path.join(os.path.dirname(__file__), 'shared_dirs.json')

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"[内网服务] 配置文件解析错误: {e}")
            return {}
    return {}

def load_shared_dirs():
    if os.path.exists(SHARED_DIRS_PATH):
        try:
            with open(SHARED_DIRS_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"[内网服务] 共享目录配置解析错误: {e}")
            return []
    return []

def save_shared_dirs(dirs):
    try:
        with open(SHARED_DIRS_PATH, 'w', encoding='utf-8') as f:
            json.dump(dirs, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[内网服务] 保存共享目录配置失败: {e}")

def log_to_file(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_line = f"[{timestamp}] {message}\n"
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(log_line)
    except Exception as e:
        print(f"日志写入失败: {e}")

config = load_config()
LOCAL_HOST = config.get('host', '127.0.0.1')
LOCAL_PORT = config.get('port', 5000)
REMOTE_HOST = config.get('remote_server', {}).get('host', '127.0.0.1')
REMOTE_PORT = config.get('remote_server', {}).get('port', 8871)
USERNAME = config.get('remote_server', {}).get('auth', {}).get('username', '')
PASSWORD = config.get('remote_server', {}).get('auth', {}).get('password', '')
MAX_RETRY_DELAY = config.get('retry', {}).get('max_delay', 60)
MIN_RETRY_DELAY = config.get('retry', {}).get('min_delay', 5)

app = Flask(__name__,
            template_folder='templates')

class LocalServer:
    def __init__(self):
        self.running = True
        self.connected = False
        self.retry_delay = MIN_RETRY_DELAY
        self.proxy_port = None
        self.token = None
        self.stats = {
            'total_reconnections': 0,
            'successful_connections': 0,
            'failed_connections': 0,
            'start_time': time.time()
        }
        self.long_conn = None
        self.heartbeat_thread = None
        self.proxy_thread = None
        self.conn_lock = threading.Lock()
        self._load_token_from_config()

    def _load_token_from_config(self):
        try:
            config_data = load_config()
            if 'token' in config_data:
                self.token = config_data['token']
                self.proxy_port = config_data.get('proxy_port')
                token_updated = config_data.get('token_updated_at', '未知')
                self._log(f"从配置文件加载Token: {self.token[:8]}...")
                self._log(f"代理端口: {self.proxy_port}, 更新时间: {token_updated}")
        except Exception as e:
            self._log(f"加载Token失败: {e}")

    def _log(self, message):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [内网服务] {message}")

    def _save_token_to_config(self):
        try:
            if self.token:
                config_data = load_config()
                config_data['token'] = self.token
                config_data['proxy_port'] = self.proxy_port
                config_data['token_updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                    json.dump(config_data, f, indent=4, ensure_ascii=False)
                self._log(f"Token已保存到配置文件")
        except Exception as e:
            self._log(f"保存Token失败: {e}")

    def _establish_long_connection(self):
        while self.running:
            try:
                self._log(f"正在连接远程服务器认证: {REMOTE_HOST}:{REMOTE_PORT}")
                auth_response = requests.post(
                    f"http://{REMOTE_HOST}:{REMOTE_PORT}/api/auth",
                    json={'username': USERNAME, 'password': PASSWORD},
                    timeout=10
                )

                if auth_response.status_code == 200:
                    self._log("认证成功")
                else:
                    self._log(f"认证失败: {auth_response.status_code}")
                    time.sleep(self.retry_delay)
                    continue

                connect_response = requests.post(
                    f"http://{REMOTE_HOST}:{REMOTE_PORT}/api/connect",
                    timeout=10
                )

                if connect_response.status_code == 200:
                    data = connect_response.json()
                    self.proxy_port = data.get('port')
                    self.token = data.get('token')
                    self._log(f"收到代理端口: {self.proxy_port}，Token: {self.token[:8]}...")
                    self._save_token_to_config()
                else:
                    self._log(f"连接失败: {connect_response.status_code}")
                    time.sleep(self.retry_delay)
                    continue

                self._log(f"TCP长连接已建立，代理端口: {self.proxy_port}")
                self._log(f"代理访问地址: http://{REMOTE_HOST}:{self.proxy_port}")
                self._log(f"本地访问地址: http://{LOCAL_HOST}:{LOCAL_PORT}")

                self.long_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.long_conn.connect((REMOTE_HOST, self.proxy_port))
                self._log(f"已连接到代理端口: {self.proxy_port}")

                with self.conn_lock:
                    self.connected = True
                    self.stats['successful_connections'] += 1
                    self.retry_delay = MIN_RETRY_DELAY

                self._start_proxy_forwarding()
                self._start_heartbeat()

                while self.running and self.connected:
                    time.sleep(1)

            except requests.exceptions.ConnectionError:
                self._log("连接远程服务器失败")
                with self.conn_lock:
                    self.connected = False
                    self.stats['failed_connections'] += 1
                time.sleep(self.retry_delay)
                self.retry_delay = min(self.retry_delay * 2, MAX_RETRY_DELAY)
            except Exception as e:
                self._log(f"连接异常: {e}")
                with self.conn_lock:
                    self.connected = False
                time.sleep(self.retry_delay)

    def _start_proxy_forwarding(self):
        def forward_loop():
            self._log("开始代理数据转发")
            import select
            while self.running and self.connected:
                try:
                    with self.conn_lock:
                        if not self.connected or not self.long_conn:
                            break
                        long_conn = self.long_conn

                    readable, _, exceptional = select.select([long_conn], [], [long_conn], 1)
                    if exceptional:
                        self._log("代理连接异常")
                        break
                    for src in readable:
                        try:
                            data = src.recv(8192)
                            if not data:
                                self._log("代理连接已关闭")
                                with self.conn_lock:
                                    self.connected = False
                                break
                            if b'HEARTBEAT' in data:
                                continue

                            header_end = data.find(b'\r\n\r\n')
                            if header_end != -1:
                                headers = data[:header_end].decode('utf-8', errors='ignore')
                                body_start = header_end + 4
                                body = data[body_start:] if body_start < len(data) else b''

                                lines = headers.split('\r\n')
                                request_line = lines[0] if lines else ''
                                path = '/'
                                for line in lines:
                                    if line.startswith('GET') or line.startswith('POST') or line.startswith('PUT') or line.startswith('DELETE'):
                                        parts = line.split(' ')
                                        if len(parts) >= 2:
                                            path = parts[1]
                                        break

                                self._log(f"收到HTTP请求: {request_line}, Path: {path}")

                                try:
                                    import http.client
                                    conn = http.client.HTTPConnection('127.0.0.1', LOCAL_PORT, timeout=30)
                                    conn.request(method='GET', url=path, body=body if body else None)
                                    response = conn.getresponse()

                                    status = response.status
                                    reason = response.reason
                                    response_headers = dict(response.getheaders())
                                    response_body = response.read()
                                    conn.close()

                                    response_line = f"HTTP/1.1 {status} {reason}\r\n"
                                    response_headers_str = ''.join([f"{k}: {v}\r\n" for k, v in response_headers.items()])
                                    response_headers_str += "Content-Length: {}\r\n".format(len(response_body))

                                    http_response = (response_line + response_headers_str + "\r\n").encode() + response_body

                                    with self.conn_lock:
                                        if self.long_conn:
                                            self.long_conn.sendall(http_response)
                                            self._log(f"发送响应: {status} {reason}, {len(response_body)} bytes")
                                except Exception as e:
                                    self._log(f"转发到本地服务异常: {e}")
                                    error_response = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"
                                    with self.conn_lock:
                                        if self.long_conn:
                                            self.long_conn.sendall(error_response)
                            else:
                                self._log(f"收到非HTTP数据: {len(data)} bytes")
                                with self.conn_lock:
                                    if self.long_conn:
                                        self.long_conn.sendall(data)
                        except Exception as e:
                            self._log(f"读取代理数据异常: {e}")
                            with self.conn_lock:
                                self.connected = False
                            break
                except Exception as e:
                    self._log(f"代理转发异常: {e}")
                    with self.conn_lock:
                        self.connected = False
                    break

        forward_thread = threading.Thread(target=forward_loop, daemon=True)
        forward_thread.start()

    def _start_heartbeat(self):
        def heartbeat_loop():
            while self.running and self.connected:
                time.sleep(30)
                if self.connected:
                    try:
                        with self.conn_lock:
                            if self.long_conn:
                                self.long_conn.sendall(b'HEARTBEAT')
                        self._log("心跳发送成功")
                    except (OSError, socket.error):
                        self._log("长连接已断开，准备重连")
                        with self.conn_lock:
                            self.connected = False
                            if self.long_conn:
                                try:
                                    self.long_conn.close()
                                except (OSError, socket.error):
                                    pass
                                self.long_conn = None
                        break

        self.heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()

    def start_proxy_thread(self):
        proxy_thread = threading.Thread(target=self._establish_long_connection)
        proxy_thread.daemon = True
        proxy_thread.start()

    def stop(self):
        self.running = False
        if self.long_conn:
            try:
                self.long_conn.close()
            except:
                pass
        self._log("服务已停止")
        log_to_file("服务已停止")

local_server = LocalServer()

@app.route('/')
def index():
    shared_dirs = load_shared_dirs()
    return render_template('index.html',
                          shared_dirs=shared_dirs,
                          connected=local_server.connected,
                          proxy_port=local_server.proxy_port,
                          token=local_server.token,
                          remote_host=f"{REMOTE_HOST}:8872")

@app.route('/api/dirs')
def get_dirs():
    shared_dirs = load_shared_dirs()
    return jsonify({'dirs': shared_dirs})

@app.route('/api/list')
def list_files():
    dir_id = request.args.get('dir')
    if not dir_id:
        return jsonify({'error': '缺少目录参数'}), 400

    shared_dirs = load_shared_dirs()
    dir_info = next((d for d in shared_dirs if d.get('id') == dir_id), None)

    if not dir_info:
        return jsonify({'error': '目录不存在'}), 404

    dir_path = dir_info.get('path')
    if not dir_path or not os.path.exists(dir_path) or not os.path.isdir(dir_path):
        return jsonify({'error': '目录路径无效'}), 404

    try:
        files = []
        for item in os.listdir(dir_path):
            item_path = os.path.join(dir_path, item)
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

        files.sort(key=lambda x: (x['type'] != 'directory', x['name'].lower()))
        return jsonify({
            'dir_id': dir_id,
            'dir_name': dir_info.get('name', dir_id),
            'dir_path': dir_path,
            'files': files,
            'total': len(files)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/api/status')
def status():
    uptime = int(time.time() - local_server.stats['start_time'])
    shared_dirs = load_shared_dirs()
    return jsonify({
        'proxy_port': local_server.proxy_port,
        'token': local_server.token,
        'token_prefix': local_server.token[:8] + '...' if local_server.token else None,
        'connected': local_server.connected,
        'remote_server': f"{REMOTE_HOST}:{REMOTE_PORT}",
        'local_server': f"{LOCAL_HOST}:{LOCAL_PORT}",
        'proxy_url': f"http://{REMOTE_HOST}:{local_server.proxy_port}" if local_server.proxy_port else None,
        'access_url_with_token': f"http://{REMOTE_HOST}:{REMOTE_PORT}/api/access?token={local_server.token}" if local_server.token else None,
        'shared_dirs': shared_dirs,
        'stats': {
            'total_reconnections': local_server.stats['total_reconnections'],
            'successful_connections': local_server.stats['successful_connections'],
            'failed_connections': local_server.stats['failed_connections'],
            'uptime_seconds': uptime,
            'uptime_human': f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s"
        }
    })

if __name__ == '__main__':
    print(f"内网服务启动中...")
    print(f"本地访问地址: http://{LOCAL_HOST}:{LOCAL_PORT}")
    print(f"远程代理服务器: http://{REMOTE_HOST}:{REMOTE_PORT}")

    local_server.start_proxy_thread()

    try:
        app.run(host=LOCAL_HOST, port=LOCAL_PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        local_server.stop()