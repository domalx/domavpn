#!/usr/bin/env python3
"""
远程代理服务器 - 提供端口转发和认证服务

功能特性：
- HTTP API接口用于认证和管理
- SHA256密码加密存储
- IP黑名单机制（连续3次认证失败）
- 自动端口分配和清理
- 支持多客户端并发连接
- 日志文件记录
- 连接统计功能
- 配置热更新支持
- 长连接支持
- 双端口架构：8871用于内网服务连接，8872用于用户访问
- Token访问机制：内网服务连接时分配Token，用户通过Token访问对应内网服务
"""

import socket
import threading
import os
import json
import hashlib
import time
import select
import secrets
import string
from datetime import datetime
from flask import Flask, request, jsonify

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
LOG_PATH = os.path.join(os.path.dirname(__file__), 'server.log')

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"[远程服务器] 配置文件解析错误: {e}")
            return {}
    return {}

def log_to_file(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_line = f"[{timestamp}] {message}\n"
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(log_line)
    except Exception as e:
        print(f"日志写入失败: {e}")

config = load_config()
LISTEN_HOST = config.get('listen_host', '0.0.0.0')
LISTEN_PORT = config.get('listen_port', 8871)
LISTEN_PORT_USER = config.get('listen_port_user', 8872)
AUTH_ENABLED = config.get('auth', {}).get('enabled', False)
USERNAME = config.get('auth', {}).get('username', '')
PASSWORD_HASH = hashlib.sha256(config.get('auth', {}).get('password', '').encode()).hexdigest()
MAX_FAILED_ATTEMPTS = config.get('security', {}).get('max_failed_attempts', 3)
BLACKLIST_DURATION = config.get('security', {}).get('blacklist_duration', 300)
PORT_POOL_START = config.get('port_pool', {}).get('start', 50000)
PORT_POOL_END = config.get('port_pool', {}).get('end', 60000)

app = Flask(__name__)
user_app = Flask(__name__)

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), 'templates')

@user_app.route('/')
def index():
    try:
        with open(os.path.join(TEMPLATE_DIR, 'index.html'), 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        proxy_server._log(f"读取模板失败: {e}")
        return '<h1>Error loading template</h1>', 500

@user_app.route('/access', methods=['POST'])
def access():
    token = request.form.get('token', '').strip()
    
    if not token:
        status = 'error'
        message = 'Token不能为空'
        proxy_port = ''
    else:
        proxy_port = proxy_server._get_port_by_token(token)
        if not proxy_port:
            status = 'error'
            message = '无效的Token或Token已过期'
            proxy_port = ''
        elif proxy_port not in proxy_server.proxy_clients or not proxy_server.proxy_clients[proxy_port].get('local_conn'):
            status = 'error'
            message = '内网服务未连接'
            proxy_port = str(proxy_port)
        else:
            status = 'success'
            message = '验证成功'
            proxy_port = str(proxy_port)
    
    try:
        with open(os.path.join(TEMPLATE_DIR, 'access.html'), 'r', encoding='utf-8') as f:
            content = f.read()
            content = content.replace('{{PROXY_PORT}}', proxy_port)
            content = content.replace('{{STATUS}}', status)
            content = content.replace('{{MESSAGE}}', message)
            return content
    except Exception as e:
        proxy_server._log(f"读取模板失败: {e}")
        return '<p>Error loading template</p>', 500

class ProxyServer:
    def __init__(self):
        self.proxy_clients = {}
        self.failed_attempts = {}
        self.blacklist = set()
        self.next_port = PORT_POOL_START
        self.port_lock = threading.Lock()
        self.running = True
        self.stats = {
            'total_connections': 0,
            'successful_auth': 0,
            'failed_auth': 0,
            'current_clients': 0,
            'start_time': time.time()
        }
        self.token_map = {}
        self.token_lock = threading.Lock()

        cleanup_thread = threading.Thread(target=self._cleanup_expired_ports)
        cleanup_thread.daemon = True
        cleanup_thread.start()

        blacklist_thread = threading.Thread(target=self._cleanup_blacklist)
        blacklist_thread.daemon = True
        blacklist_thread.start()

    def _get_client_ip(self):
        if request.headers.get('X-Forwarded-For'):
            return request.headers.get('X-Forwarded-For').split(',')[0].strip()
        elif request.headers.get('X-Real-IP'):
            return request.headers.get('X-Real-IP')
        return request.remote_addr

    def _is_blacklisted(self, ip):
        return ip in self.blacklist

    def _record_failed_attempt(self, ip):
        if ip not in self.failed_attempts:
            self.failed_attempts[ip] = {'count': 0, 'last_attempt': 0}
        self.failed_attempts[ip]['count'] += 1
        self.failed_attempts[ip]['last_attempt'] = time.time()
        self.stats['failed_auth'] += 1
        if self.failed_attempts[ip]['count'] >= MAX_FAILED_ATTEMPTS:
            self.blacklist.add(ip)
            log_message = f"IP {ip} 连续{MAX_FAILED_ATTEMPTS}次认证失败，已拉黑"
            self._log(log_message)
            log_to_file(log_message)

    def _cleanup_blacklist(self):
        while self.running:
            time.sleep(60)
            now = time.time()
            expired_ips = []
            for ip in list(self.blacklist):
                if ip in self.failed_attempts:
                    if now - self.failed_attempts[ip]['last_attempt'] >= BLACKLIST_DURATION:
                        expired_ips.append(ip)
            for ip in expired_ips:
                self.blacklist.remove(ip)
                if ip in self.failed_attempts:
                    self.failed_attempts[ip] = {'count': 0, 'last_attempt': 0}
                log_message = f"IP {ip} 已从黑名单移除"
                self._log(log_message)
                log_to_file(log_message)

    def _verify_credentials(self, username_input, password_input):
        if not AUTH_ENABLED:
            return True
        if username_input == USERNAME:
            input_hash = hashlib.sha256(password_input.encode()).hexdigest()
            if input_hash == PASSWORD_HASH:
                return True
        return False

    def _get_random_port(self):
        with self.port_lock:
            port = self.next_port
            self.next_port += 1
            if self.next_port > PORT_POOL_END:
                self.next_port = PORT_POOL_START
            return port

    def _generate_token(self):
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(32))

    def _get_port_by_token(self, token):
        with self.token_lock:
            return self.token_map.get(token)

    def _register_token(self, token, port):
        with self.token_lock:
            self.token_map[token] = port

    def _unregister_token(self, token):
        with self.token_lock:
            if token in self.token_map:
                del self.token_map[token]

    def _forward_with_select(self, client_conn, local_conn, client_addr, header_data=None):
        self._log(f"[转发] 开始处理客户端请求: {client_addr}")
        if header_data:
            try:
                local_conn.sendall(header_data)
                self._log(f"[转发] 发送初始数据 {len(header_data)} bytes")
            except Exception as e:
                self._log(f"[转发] 发送初始数据失败: {e}")
                return
        try:
            conn_dict = {client_conn: local_conn, local_conn: client_conn}
            while self.running:
                try:
                    if client_conn.fileno() == -1 or local_conn.fileno() == -1:
                        self._log("[转发] 套接字已关闭")
                        break
                    readable, _, exceptional = select.select(
                        [client_conn, local_conn], [], [client_conn, local_conn], 1
                    )
                    if exceptional:
                        self._log(f"[转发] 异常: {exceptional}")
                        break
                    for src in readable:
                        dst = conn_dict[src]
                        try:
                            if dst.fileno() == -1:
                                self._log("[转发] 目标套接字已关闭")
                                return
                            data = src.recv(4096)
                            if not data:
                                self._log(f"[转发] 对方关闭连接")
                                return
                            if b'HEARTBEAT' in data:
                                non_heartbeat_data = data.replace(b'HEARTBEAT', b'')
                                if non_heartbeat_data:
                                    self._log(f"[转发] {src.getpeername()} -> {dst.getpeername()}, {len(non_heartbeat_data)} bytes")
                                    dst.sendall(non_heartbeat_data)
                                continue
                            self._log(f"[转发] {src.getpeername()} -> {dst.getpeername()}, {len(data)} bytes")
                            dst.sendall(data)
                        except (OSError, socket.error) as e:
                            if e.winerror == 10038:
                                self._log(f"[转发] 套接字已无效，连接可能已关闭")
                            else:
                                self._log(f"[转发] 读写异常: {e}")
                            return
                except (select.error, OSError, socket.error) as e:
                    if getattr(e, 'winerror', None) == 10038:
                        self._log(f"[转发] select套接字已无效")
                    else:
                        self._log(f"[转发] select错误: {e}")
                    break
                except Exception as e:
                    self._log(f"[转发] 未知异常: {e}")
                    break
        except Exception as e:
            self._log(f"[转发] 异常: {e}")
        finally:
            try:
                if client_conn.fileno() != -1:
                    client_conn.close()
            except (OSError, socket.error):
                pass
            self._log(f"[转发] 连接已关闭")

    def _handle_user_request(self, client_conn, proxy_port, client_addr, header_data=None):
        try:
            if proxy_port not in self.proxy_clients or not self.proxy_clients[proxy_port].get('local_conn'):
                self._log(f"代理端口 {proxy_port} 无内网服务连接")
                error_msg = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 20\r\n\r\nNo Local Service"
                client_conn.sendall(error_msg)
                return
            local_conn = self.proxy_clients[proxy_port]['local_conn']
            self._log(f"用户请求代理端口: {proxy_port}，来源: {client_addr}")
            self._forward_with_select(client_conn, local_conn, client_addr, header_data)
        except Exception as e:
            self._log(f"处理客户端 {client_addr} 请求异常: {e}")
        finally:
            if proxy_port in self.proxy_clients:
                self.proxy_clients[proxy_port]['client_count'] -= 1
            self.stats['current_clients'] -= 1
            try:
                client_conn.close()
            except (OSError, socket.error):
                pass

    def _handle_proxy_client(self, proxy_port, token):
        proxy_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        proxy_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            proxy_socket.bind((LISTEN_HOST, proxy_port))
            proxy_socket.listen(5)
            proxy_socket.settimeout(None)
            if proxy_port not in self.proxy_clients:
                self.proxy_clients[proxy_port] = {
                    'created_at': time.time(),
                    'client_count': 0,
                    'total_clients': 0,
                    'local_conn': None,
                    'send_lock': threading.Lock(),
                    'token': token
                }
            log_message = f"代理端口 {proxy_port} 已准备就绪，Token: {token[:8]}...，等待内网服务连接..."
            self._log(log_message)
            log_to_file(log_message)
            listener_thread = threading.Thread(target=self._accept_local_connection, args=(proxy_port, proxy_socket))
            listener_thread.daemon = True
            listener_thread.start()
            listener_thread.join()
        except Exception as e:
            log_message = f"代理端口 {proxy_port} 异常: {e}"
            self._log(log_message)
            log_to_file(log_message)
        finally:
            proxy_socket.close()
            if proxy_port in self.proxy_clients:
                if self.proxy_clients[proxy_port].get('token'):
                    self._unregister_token(self.proxy_clients[proxy_port]['token'])
                if self.proxy_clients[proxy_port].get('local_conn'):
                    try:
                        self.proxy_clients[proxy_port]['local_conn'].close()
                    except (OSError, socket.error):
                        pass
                del self.proxy_clients[proxy_port]
                log_message = f"代理端口 {proxy_port} 已释放"
                self._log(log_message)
                log_to_file(log_message)

    def _accept_local_connection(self, proxy_port, proxy_socket):
        proxy_socket.settimeout(300)
        try:
            local_conn, local_addr = proxy_socket.accept()
            local_conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            local_conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.proxy_clients[proxy_port]['local_conn'] = local_conn
            self.proxy_clients[proxy_port]['last_heartbeat'] = time.time()
            self.proxy_clients[proxy_port]['local_addr'] = local_addr
            log_message = f"内网服务已连接，代理端口: {proxy_port}，来源: {local_addr}"
            self._log(log_message)
            log_to_file(log_message)
            heartbeat_thread = threading.Thread(target=self._heartbeat_check, args=(proxy_port, local_conn))
            heartbeat_thread.daemon = True
            heartbeat_thread.start()
            user_accept_thread = threading.Thread(target=self._accept_user_connections, args=(proxy_port, proxy_socket))
            user_accept_thread.daemon = True
            user_accept_thread.start()
            heartbeat_thread.join()
            user_accept_thread.join()
        except socket.timeout:
            log_message = f"代理端口 {proxy_port} 等待内网服务连接超时"
            self._log(log_message)
            log_to_file(log_message)
        except Exception as e:
            log_message = f"代理端口 {proxy_port} 接受内网连接异常: {e}"
            self._log(log_message)
            log_to_file(log_message)

    def _heartbeat_check(self, proxy_port, local_conn):
        while self.running:
            time.sleep(30)
            if proxy_port not in self.proxy_clients:
                break
            if self.proxy_clients[proxy_port].get('local_conn') != local_conn:
                break
            if proxy_port in self.proxy_clients:
                self.proxy_clients[proxy_port]['last_heartbeat'] = time.time()

    def _accept_user_connections(self, proxy_port, proxy_socket):
        proxy_socket.settimeout(None)
        while self.running and self.proxy_clients.get(proxy_port, {}).get('local_conn'):
            try:
                client_conn, client_addr = proxy_socket.accept()
                self.stats['current_clients'] += 1
                self.proxy_clients[proxy_port]['client_count'] += 1
                self.proxy_clients[proxy_port]['total_clients'] += 1
                log_message = f"用户连接代理端口: {proxy_port}，来源: {client_addr}"
                self._log(log_message)
                client_thread = threading.Thread(target=self._handle_user_request, args=(client_conn, proxy_port, client_addr))
                client_thread.daemon = True
                client_thread.start()
            except Exception as e:
                if self.running:
                    self._log(f"代理端口 {proxy_port} 接受用户连接异常: {e}")
                break

    def _cleanup_expired_ports(self):
        while self.running:
            time.sleep(60)
            now = time.time()
            expired_ports = []
            for port, info in self.proxy_clients.items():
                if now - info['created_at'] > 300:
                    expired_ports.append(port)
            for port in expired_ports:
                try:
                    if self.proxy_clients[port].get('local_conn'):
                        self.proxy_clients[port]['local_conn'].close()
                    if self.proxy_clients[port].get('token'):
                        self._unregister_token(self.proxy_clients[port]['token'])
                except:
                    pass
                if port in self.proxy_clients:
                    del self.proxy_clients[port]
                log_message = f"端口 {port} 已过期并释放"
                self._log(log_message)
                log_to_file(log_message)

    def _log(self, message):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [远程服务器] {message}")

    def connect_handler(self):
        client_ip = self._get_client_ip()
        if self._is_blacklisted(client_ip):
            return jsonify({'status': 'error', 'message': 'IP已被拉黑'}), 403
        proxy_port = self._get_random_port()
        token = self._generate_token()
        self._register_token(token, proxy_port)
        log_message = f"内网服务长连接请求，分配代理端口: {proxy_port}, Token: {token[:8]}..."
        self._log(log_message)
        log_to_file(log_message)
        def start_listener():
            self._handle_proxy_client(proxy_port, token)
        listener_thread = threading.Thread(target=start_listener)
        listener_thread.daemon = True
        listener_thread.start()
        return jsonify({
            'status': 'success',
            'port': proxy_port,
            'token': token,
            'message': f'长连接已建立，代理端口: {proxy_port}'
        }), 200

    def auth_handler(self):
        client_ip = self._get_client_ip()
        if self._is_blacklisted(client_ip):
            return jsonify({'status': 'error', 'message': 'IP已被拉黑，请5分钟后重试'}), 403
        try:
            data = request.get_json()
        except Exception as e:
            self._record_failed_attempt(client_ip)
            return jsonify({'status': 'error', 'message': '请求格式错误'}), 400
        if not data or 'username' not in data or 'password' not in data:
            self._record_failed_attempt(client_ip)
            return jsonify({'status': 'error', 'message': '缺少认证信息'}), 400
        if self._verify_credentials(data['username'], data['password']):
            if client_ip in self.failed_attempts:
                self.failed_attempts[client_ip] = {'count': 0, 'last_attempt': 0}
            self.stats['successful_auth'] += 1
            log_message = f"认证成功 - IP: {client_ip}"
            self._log(log_message)
            log_to_file(log_message)
            return jsonify({'status': 'success', 'message': '认证成功，请建立长连接'}), 200
        else:
            self._record_failed_attempt(client_ip)
            attempts_left = MAX_FAILED_ATTEMPTS - self.failed_attempts[client_ip]['count']
            return jsonify({'status': 'error', 'message': f'认证失败，剩余尝试次数: {attempts_left}', 'attempts_left': attempts_left}), 401

    def health_handler(self):
        uptime = int(time.time() - self.stats['start_time'])
        return jsonify({
            'status': 'running',
            'auth_enabled': AUTH_ENABLED,
            'active_proxies': len(self.proxy_clients),
            'blacklist_count': len(self.blacklist),
            'stats': self.stats,
            'uptime_seconds': uptime
        })

    def blacklist_handler(self):
        return jsonify({'blacklist': list(self.blacklist), 'failed_attempts': dict(self.failed_attempts)})

    def remove_blacklist_handler(self, ip):
        if ip in self.blacklist:
            self.blacklist.remove(ip)
            if ip in self.failed_attempts:
                self.failed_attempts[ip] = {'count': 0, 'last_attempt': 0}
            log_message = f"IP {ip} 已从黑名单移除"
            self._log(log_message)
            log_to_file(log_message)
            return jsonify({'status': 'success', 'message': f'IP {ip} 已移除'}), 200
        return jsonify({'status': 'error', 'message': 'IP不在黑名单中'}), 404

    def proxies_handler(self):
        proxies_info = []
        for port, info in self.proxy_clients.items():
            proxies_info.append({
                'port': port,
                'created_at': datetime.fromtimestamp(info['created_at']).strftime('%Y-%m-%d %H:%M:%S'),
                'client_count': info['client_count'],
                'total_clients': info['total_clients'],
                'has_local_conn': info.get('local_conn') is not None,
                'token_prefix': info.get('token', '')[:8] + '...' if info.get('token') else None
            })
        return jsonify({'proxies': proxies_info, 'total': len(proxies_info)})

    def access_by_token_handler(self):
        token = request.args.get('token') or request.headers.get('X-Proxy-Token')
        if not token:
            return jsonify({'status': 'error', 'message': '缺少Token参数'}), 400
        proxy_port = self._get_port_by_token(token)
        if not proxy_port:
            return jsonify({'status': 'error', 'message': '无效的Token或Token已过期'}), 404
        if proxy_port not in self.proxy_clients:
            return jsonify({'status': 'error', 'message': '代理服务不存在'}), 404
        client_info = self.proxy_clients[proxy_port]
        if not client_info.get('local_conn'):
            return jsonify({'status': 'error', 'message': '内网服务未连接'}), 503
        return jsonify({
            'status': 'success',
            'proxy_port': proxy_port,
            'proxy_url': f'http://{LISTEN_HOST}:{proxy_port}',
            'message': 'Token验证成功'
        }), 200

    def get_token_info_handler(self, token):
        proxy_port = self._get_port_by_token(token)
        if not proxy_port:
            return jsonify({'status': 'error', 'message': 'Token不存在'}), 404
        if proxy_port in self.proxy_clients:
            info = self.proxy_clients[proxy_port]
            return jsonify({
                'status': 'success',
                'token_prefix': token[:8] + '...',
                'proxy_port': proxy_port,
                'created_at': datetime.fromtimestamp(info['created_at']).strftime('%Y-%m-%d %H:%M:%S'),
                'has_local_conn': info.get('local_conn') is not None,
                'client_count': info['client_count']
            }), 200
        else:
            return jsonify({'status': 'error', 'message': '代理端口已释放'}), 404

    def reload_config_handler(self):
        global config, LISTEN_HOST, AUTH_ENABLED, USERNAME, PASSWORD_HASH
        global MAX_FAILED_ATTEMPTS, BLACKLIST_DURATION, PORT_POOL_START, PORT_POOL_END
        try:
            config = load_config()
            LISTEN_HOST = config.get('listen_host', '0.0.0.0')
            AUTH_ENABLED = config.get('auth', {}).get('enabled', False)
            USERNAME = config.get('auth', {}).get('username', '')
            PASSWORD_HASH = hashlib.sha256(config.get('auth', {}).get('password', '').encode()).hexdigest()
            MAX_FAILED_ATTEMPTS = config.get('security', {}).get('max_failed_attempts', 3)
            BLACKLIST_DURATION = config.get('security', {}).get('blacklist_duration', 300)
            PORT_POOL_START = config.get('port_pool', {}).get('start', 50000)
            PORT_POOL_END = config.get('port_pool', {}).get('end', 60000)
            log_message = "配置已热更新"
            self._log(log_message)
            log_to_file(log_message)
            return jsonify({'status': 'success', 'message': '配置已更新'}), 200
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'配置更新失败: {e}'}), 500

proxy_server = ProxyServer()

@app.route('/api/auth', methods=['POST'])
def auth():
    return proxy_server.auth_handler()

@app.route('/api/connect', methods=['POST'])
def connect():
    return proxy_server.connect_handler()

@app.route('/api/health', methods=['GET'])
def health():
    return proxy_server.health_handler()

@app.route('/api/blacklist', methods=['GET'])
def blacklist():
    return proxy_server.blacklist_handler()

@app.route('/api/blacklist/<ip>', methods=['DELETE'])
def remove_blacklist(ip):
    return proxy_server.remove_blacklist_handler(ip)

@app.route('/api/proxies', methods=['GET'])
def proxies():
    return proxy_server.proxies_handler()

@app.route('/api/token/<token>', methods=['GET'])
def get_token_info(token):
    return proxy_server.get_token_info_handler(token)

@app.route('/api/reload', methods=['POST'])
def reload_config():
    return proxy_server.reload_config_handler()

@user_app.route('/api/access', methods=['GET'])
def user_access():
    return proxy_server.access_by_token_handler()

def run_user_proxy_server():
    import re
    user_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    user_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        user_socket.bind((LISTEN_HOST, LISTEN_PORT_USER))
        user_socket.listen(100)
        log_message = f"用户访问服务已启动，监听端口: {LISTEN_PORT_USER}"
        proxy_server._log(log_message)
        log_to_file(log_message)
        while proxy_server.running:
            try:
                user_socket.settimeout(1)
                client_conn, client_addr = user_socket.accept()
                proxy_server._log(f"[8872] 收到连接: {client_addr}")
                client_ip = client_addr[0]
                if proxy_server._is_blacklisted(client_ip):
                    proxy_server._log(f"用户访问被拒绝，IP在黑名单: {client_addr}")
                    error_msg = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 20\r\n\r\nIP Blocked"
                    try:
                        client_conn.sendall(error_msg)
                    except:
                        pass
                    client_conn.close()
                    continue
                token = None
                header_data = b''
                proxy_server._log(f"[8872] 开始读取Header，来源: {client_addr}")
                client_conn.settimeout(5)
                try:
                    while b'\r\n\r\n' not in header_data:
                        chunk = client_conn.recv(256)
                        if not chunk:
                            proxy_server._log(f"[8872] 客户端断开，来源: {client_addr}")
                            break
                        header_data += chunk
                        if len(header_data) > 4096:
                            proxy_server._log(f"[8872] Header过长，来源: {client_addr}")
                            break
                    proxy_server._log(f"[8872] 读取Header完成，长度: {len(header_data)}，来源: {client_addr}")
                    if header_data:
                        header_str = header_data.decode('utf-8', errors='ignore')
                        for line in header_str.split('\r\n'):
                            if line.lower().startswith('token:'):
                                token = line.split(':', 1)[1].strip()
                            elif 'token=' in line.lower():
                                match = re.search(r'token=([^&\s]+)', line.lower())
                                if match:
                                    token = match.group(1)
                except socket.timeout:
                    proxy_server._log(f"[8872] 读取Header超时，来源: {client_addr}")
                except Exception as e:
                    proxy_server._log(f"[8872] 读取Header异常: {e}")
                if not token:
                    proxy_server._log(f"[8872] Token缺失，来源: {client_addr}")
                    error_msg = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 30\r\n\r\nMissing Token"
                    try:
                        client_conn.sendall(error_msg)
                    except:
                        pass
                    client_conn.close()
                    continue
                proxy_port = proxy_server._get_port_by_token(token)
                if not proxy_port:
                    proxy_server._log(f"[8872] 无效Token: {token[:8]}..., 来源: {client_addr}")
                    error_msg = b"HTTP/1.1 404 Not Found\r\nContent-Length: 30\r\n\r\nInvalid Token"
                    try:
                        client_conn.sendall(error_msg)
                    except:
                        pass
                    client_conn.close()
                    continue
                if proxy_port not in proxy_server.proxy_clients or not proxy_server.proxy_clients[proxy_port].get('local_conn'):
                    proxy_server._log(f"[8872] 代理端口{proxy_port}服务不可用，来源: {client_addr}")
                    error_msg = b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 30\r\n\r\nService Not Available"
                    try:
                        client_conn.sendall(error_msg)
                    except:
                        pass
                    client_conn.close()
                    continue
                proxy_server._log(f"[8872] Token验证通过，代理端口: {proxy_port}，来源: {client_addr}")
                forward_thread = threading.Thread(target=proxy_server._handle_user_request, args=(client_conn, proxy_port, client_addr, header_data))
                forward_thread.daemon = True
                forward_thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                proxy_server._log(f"[8872] 用户访问服务异常: {e}")
    except Exception as e:
        proxy_server._log(f"用户访问服务启动失败: {e}")
    finally:
        user_socket.close()

if __name__ == '__main__':
    log_message = f"启动成功，管理端口: {LISTEN_PORT}，用户访问端口: {LISTEN_PORT_USER}"
    proxy_server._log(log_message)
    log_to_file(log_message)
    proxy_server._log(f"认证功能: {'已启用' if AUTH_ENABLED else '已禁用'}")
    proxy_server._log("按 Ctrl+C 停止服务")
    
    def run_flask_servers():
        import werkzeug.serving
        werkzeug.serving.run_simple(LISTEN_HOST, LISTEN_PORT_USER, user_app, threaded=True)
    
    user_web_thread = threading.Thread(target=run_flask_servers)
    user_web_thread.daemon = True
    user_web_thread.start()
    
    try:
        app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        proxy_server.running = False
        log_message = "收到停止信号，正在关闭..."
        proxy_server._log(log_message)
        log_to_file(log_message)