#!/usr/bin/env python3
"""
内网HTTP服务 - 提供文件访问和代理连接功能

功能特性：
- 文件列表查看和文件下载
- 自动连接远程代理服务器（长连接）
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
from flask import Flask, request, jsonify, send_from_directory, render_template

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
app = Flask(__name__,
            template_folder='../frontend/templates',
            static_folder='../frontend/static')

class LocalServer:
    """内网服务核心类"""

    def __init__(self):
        self.running = True
        self.connected = False
        self.retry_delay = MIN_RETRY_DELAY
        self.proxy_port = None
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

    def _log(self, message):
        """日志记录"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [内网服务] {message}")

    def _authenticate(self):
        """向远程服务器认证"""
        auth_url = f"http://{REMOTE_HOST}:{REMOTE_PORT}/api/auth"
        payload = {'username': USERNAME, 'password': PASSWORD}

        try:
            self._log(f"正在连接远程服务器认证: {REMOTE_HOST}:{REMOTE_PORT}")
            response = requests.post(auth_url, json=payload, timeout=10)

            if response.status_code == 200:
                self.stats['successful_connections'] += 1
                log_message = "认证成功"
                self._log(log_message)
                log_to_file(log_message)
                return True
            else:
                error_msg = response.json().get('message', '未知错误')
                log_message = f"认证失败: {error_msg}"
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

    def _establish_long_connection(self):
        """建立与远程服务器的TCP长连接"""
        while self.running:
            if not self.connected:
                if not self._authenticate():
                    self._log(f"等待{self.retry_delay}秒后重新连接...")
                    time.sleep(self.retry_delay)
                    self.retry_delay = min(self.retry_delay * 2, MAX_RETRY_DELAY)
                    continue

                try:
                    # 请求连接并获取代理端口
                    connect_url = f"http://{REMOTE_HOST}:{REMOTE_PORT}/api/connect"
                    response = requests.post(connect_url, json={}, timeout=15)

                    if response.status_code == 200:
                        data = response.json()
                        self.proxy_port = data.get('port')

                        self._log(f"收到代理端口: {self.proxy_port}，正在连接...")

                        # 立即连接到分配的代理端口
                        self.long_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        self.long_conn.settimeout(10)
                        self.long_conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        self.long_conn.connect((REMOTE_HOST, self.proxy_port))

                        self.connected = True
                        self.retry_delay = MIN_RETRY_DELAY
                        self.stats['total_reconnections'] += 1

                        log_message = f"TCP长连接已建立，代理端口: {self.proxy_port}"
                        self._log(log_message)
                        log_to_file(log_message)

                        self._log(f"代理访问地址: http://{REMOTE_HOST}:{self.proxy_port}")
                        self._log(f"本地访问地址: http://{LOCAL_HOST}:{LOCAL_PORT}")

                        # 启动本地代理和心跳线程
                        self._start_local_proxy()
                        self._start_heartbeat()

                    elif response.status_code == 408:
                        log_message = "建立长连接超时，等待重试"
                        self._log(log_message)
                        time.sleep(self.retry_delay)
                        continue
                    else:
                        log_message = f"建立长连接失败: {response.json().get('message', '未知错误')}"
                        self._log(log_message)
                        time.sleep(self.retry_delay)
                        continue

                except Exception as e:
                    log_message = f"建立长连接异常: {e}"
                    self._log(log_message)
                    log_to_file(log_message)
                    if self.long_conn:
                        try:
                            self.long_conn.close()
                        except:
                            pass
                        self.long_conn = None
                    time.sleep(self.retry_delay)
                    continue

            # 检查连接状态
            time.sleep(5)

            if self.connected and self.long_conn:
                try:
                    # 发送心跳信号
                    self.long_conn.sendall(b'HEARTBEAT')
                except:
                    log_message = "长连接已断开，准备重连"
                    self._log(log_message)
                    self.connected = False
                    if self.long_conn:
                        try:
                            self.long_conn.close()
                        except:
                            pass
                        self.long_conn = None

    def _start_local_proxy(self):
        """启动本地代理，将远程请求转发到本地Flask服务"""
        def local_proxy_loop():
            while self.running and self.connected:
                local_socket = None
                try:
                    local_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    local_socket.settimeout(5)
                    local_socket.connect(('127.0.0.1', LOCAL_PORT))
                    local_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                    self._log("本地代理已连接到Flask服务")

                    while self.running and self.connected:
                        try:
                            with self.conn_lock:
                                if not self.long_conn:
                                    break
                                data = self.long_conn.recv(8192)
                            if not data:
                                self._log("远程连接已关闭")
                                break
                            if data == b'HEARTBEAT':
                                continue
                            local_socket.sendall(data)

                            local_socket.settimeout(5)
                            try:
                                response = local_socket.recv(8192)
                                if response:
                                    with self.conn_lock:
                                        if self.long_conn:
                                            self.long_conn.sendall(response)
                            except socket.timeout:
                                pass
                        except Exception as e:
                            self._log(f"本地代理转发异常: {e}")
                            break
                except Exception as e:
                    if self.running and self.connected:
                        self._log(f"本地代理连接Flask失败: {e}")
                        time.sleep(1)
                finally:
                    if local_socket:
                        try:
                            local_socket.close()
                        except:
                            pass

        self.proxy_thread = threading.Thread(target=local_proxy_loop, daemon=True)
        self.proxy_thread.start()

    def _start_heartbeat(self):
        """启动心跳保活机制"""
        def heartbeat_loop():
            while self.running and self.connected:
                time.sleep(30)
                if self.connected:
                    try:
                        with self.conn_lock:
                            if self.long_conn:
                                self.long_conn.sendall(b'HEARTBEAT')
                        self._log("心跳发送成功")
                    except Exception as e:
                        self._log(f"心跳发送失败: {e}")
                        with self.conn_lock:
                            self.connected = False
                            if self.long_conn:
                                try:
                                    self.long_conn.close()
                                except:
                                    pass
                                self.long_conn = None
                        break

        self.heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()

    def start_proxy_thread(self):
        """启动代理连接线程"""
        proxy_thread = threading.Thread(target=self._establish_long_connection)
        proxy_thread.daemon = True
        proxy_thread.start()

    def stop(self):
        """停止服务"""
        self.running = False
        if self.long_conn:
            try:
                self.long_conn.close()
            except:
                pass
        log_message = "服务已停止"
        self._log(log_message)
        log_to_file(log_message)

local_server = LocalServer()

@app.route('/')
def index():
    """首页"""
    return render_template('local_index.html',
                          connected=local_server.connected,
                          proxy_port=local_server.proxy_port,
                          remote_host=REMOTE_HOST)

@app.route('/files')
def list_files_page():
    """文件管理页面"""
    return render_template('local_files.html')

@app.route('/upload-page')
def upload_page():
    """文件上传页面"""
    return render_template('local_upload.html')

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

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """文件上传"""
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

    return jsonify({'error': '上传失败'}), 500

@app.route('/api/delete', methods=['POST'])
def delete_file():
    """删除文件"""
    try:
        data = request.get_json()
        if not data or 'filename' not in data:
            return jsonify({'error': '缺少参数'}), 400

        filename = data['filename']
        if not os.path.exists(filename):
            return jsonify({'error': '文件不存在'}), 404

        if os.path.isfile(filename):
            os.remove(filename)
            log_message = f"文件已删除: {filename}"
            local_server._log(log_message)
            log_to_file(log_message)
            return jsonify({'status': 'success', 'message': f'文件 {filename} 已删除'}), 200
        elif os.path.isdir(filename):
            os.rmdir(filename)
            log_message = f"目录已删除: {filename}"
            local_server._log(log_message)
            log_to_file(log_message)
            return jsonify({'status': 'success', 'message': f'目录 {filename} 已删除'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/rename', methods=['POST'])
def rename_file():
    """重命名文件"""
    try:
        data = request.get_json()
        if not data or 'oldname' not in data or 'newname' not in data:
            return jsonify({'error': '缺少参数'}), 400

        oldname = data['oldname']
        newname = data['newname']

        if not os.path.exists(oldname):
            return jsonify({'error': '原文件不存在'}), 404

        if os.path.exists(newname):
            return jsonify({'error': '新文件名已存在'}), 400

        os.rename(oldname, newname)
        log_message = f"文件已重命名: {oldname} -> {newname}"
        local_server._log(log_message)
        log_to_file(log_message)

        return jsonify({'status': 'success', 'message': f'文件已重命名为 {newname}'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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