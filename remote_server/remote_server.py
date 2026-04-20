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
"""

import socket
import threading
import os
import json
import hashlib
import time
import select
from datetime import datetime
from flask import Flask, request, jsonify

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
            print(f"[远程服务器] 配置文件解析错误: {e}")
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
LISTEN_HOST = config.get('listen_host', '0.0.0.0')
LISTEN_PORT = config.get('listen_port', 8871)
AUTH_ENABLED = config.get('auth', {}).get('enabled', False)
USERNAME = config.get('auth', {}).get('username', '')
PASSWORD_HASH = hashlib.sha256(config.get('auth', {}).get('password', '').encode()).hexdigest()

# 安全配置
MAX_FAILED_ATTEMPTS = config.get('security', {}).get('max_failed_attempts', 3)
BLACKLIST_DURATION = config.get('security', {}).get('blacklist_duration', 300)
PORT_POOL_START = config.get('port_pool', {}).get('start', 50000)
PORT_POOL_END = config.get('port_pool', {}).get('end', 60000)

# Flask应用
app = Flask(__name__)

class ProxyServer:
    """远程代理服务器核心类"""

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

        # 启动清理线程
        cleanup_thread = threading.Thread(target=self._cleanup_expired_ports)
        cleanup_thread.daemon = True
        cleanup_thread.start()

        # 启动黑名单清理线程
        blacklist_thread = threading.Thread(target=self._cleanup_blacklist)
        blacklist_thread.daemon = True
        blacklist_thread.start()

    def _get_client_ip(self):
        """获取客户端真实IP"""
        if request.headers.get('X-Forwarded-For'):
            return request.headers.get('X-Forwarded-For').split(',')[0].strip()
        elif request.headers.get('X-Real-IP'):
            return request.headers.get('X-Real-IP')
        return request.remote_addr

    def _is_blacklisted(self, ip):
        """检查IP是否在黑名单中"""
        if ip in self.blacklist:
            return True
        return False

    def _record_failed_attempt(self, ip):
        """记录认证失败尝试"""
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
        """定期清理过期的黑名单"""
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
        """验证用户名密码"""
        if not AUTH_ENABLED:
            return True

        if username_input == USERNAME:
            input_hash = hashlib.sha256(password_input.encode()).hexdigest()
            if input_hash == PASSWORD_HASH:
                return True
        return False

    def _get_random_port(self):
        """获取一个可用的随机端口"""
        with self.port_lock:
            port = self.next_port
            self.next_port += 1
            if self.next_port > PORT_POOL_END:
                self.next_port = PORT_POOL_START
            return port

    def _forward_with_select(self, client_conn, local_conn, client_addr):
        """使用select进行双向数据转发"""
        self._log(f"[转发] 开始处理客户端请求: {client_addr}")
        try:
            conn_dict = {client_conn: local_conn, local_conn: client_conn}
            while self.running:
                try:
                    readable, _, exceptional = select.select(
                        [client_conn, local_conn], [], [client_conn, local_conn], 1
                    )
                    if exceptional:
                        self._log(f"[转发] 异常: {exceptional}")
                        break
                    for src in readable:
                        dst = conn_dict[src]
                        try:
                            data = src.recv(4096)
                            if not data:
                                self._log(f"[转发] 对方关闭连接")
                                return
                            if data == b'HEARTBEAT':
                                self._log(f"[转发] 忽略心跳信号")
                                continue
                            self._log(f"[转发] {src.getpeername()} -> {dst.getpeername()}, {len(data)} bytes: {data[:50]}")
                            dst.sendall(data)
                        except Exception as e:
                            self._log(f"[转发] 读写异常: {e}")
                            return
                except select.error as e:
                    self._log(f"[转发] select错误: {e}")
                    break
        except Exception as e:
            self._log(f"[转发] 异常: {e}")
        finally:
            try:
                client_conn.close()
            except:
                pass
            self._log(f"[转发] 连接已关闭")

    def _handle_client_request(self, client_conn, proxy_port, client_addr):
        """处理用户客户端的请求"""
        try:
            if proxy_port not in self.proxy_clients or not self.proxy_clients[proxy_port].get('local_conn'):
                self._log(f"代理端口 {proxy_port} 无内网服务连接")
                error_msg = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 20\r\n\r\nNo Local Service"
                client_conn.sendall(error_msg)
                return

            local_conn = self.proxy_clients[proxy_port]['local_conn']
            self._log(f"用户请求代理端口: {proxy_port}，来源: {client_addr}")

            self._forward_with_select(client_conn, local_conn, client_addr)

        except Exception as e:
            self._log(f"处理客户端 {client_addr} 请求异常: {e}")
        finally:
            if proxy_port in self.proxy_clients:
                self.proxy_clients[proxy_port]['client_count'] -= 1
            self.stats['current_clients'] -= 1
            try:
                client_conn.close()
            except:
                pass

    def _handle_proxy_client(self, proxy_port):
        """处理代理端口（用户访问用）"""
        proxy_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        proxy_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            proxy_socket.bind((LISTEN_HOST, proxy_port))
            proxy_socket.listen(5)
            proxy_socket.settimeout(None)  # 阻塞模式

            if proxy_port not in self.proxy_clients:
                self.proxy_clients[proxy_port] = {
                    'created_at': time.time(),
                    'client_count': 0,
                    'total_clients': 0,
                    'local_conn': None,
                    'send_lock': threading.Lock()
                }

            log_message = f"代理端口 {proxy_port} 已准备就绪，等待内网服务连接..."
            self._log(log_message)
            log_to_file(log_message)

            # 等待内网服务连接（带超时）
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
                if self.proxy_clients[proxy_port].get('local_conn'):
                    try:
                        self.proxy_clients[proxy_port]['local_conn'].close()
                    except:
                        pass
                del self.proxy_clients[proxy_port]
                log_message = f"代理端口 {proxy_port} 已释放"
                self._log(log_message)
                log_to_file(log_message)

    def _accept_local_connection(self, proxy_port, proxy_socket):
        """接受内网服务的连接"""
        proxy_socket.settimeout(60)
        try:
            local_conn, local_addr = proxy_socket.accept()

            # 启用TCP Keepalive，让操作系统处理心跳检测
            local_conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            local_conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            # 内网服务已连接，注册长连接
            self.proxy_clients[proxy_port]['local_conn'] = local_conn
            self.proxy_clients[proxy_port]['last_heartbeat'] = time.time()
            self.proxy_clients[proxy_port]['local_addr'] = local_addr

            log_message = f"内网服务已连接，代理端口: {proxy_port}，来源: {local_addr}"
            self._log(log_message)
            log_to_file(log_message)

            # 启动心跳检测线程（不读取数据，只检查连接状态）
            heartbeat_thread = threading.Thread(
                target=self._heartbeat_check,
                args=(proxy_port, local_conn)
            )
            heartbeat_thread.daemon = True
            heartbeat_thread.start()

            # 启动用户请求接受线程
            user_accept_thread = threading.Thread(
                target=self._accept_user_connections,
                args=(proxy_port, proxy_socket)
            )
            user_accept_thread.daemon = True
            user_accept_thread.start()

            # 等待任一线程结束
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
        """检测内网服务连接的心跳（不读取数据）"""
        while self.running:
            time.sleep(30)
            if proxy_port not in self.proxy_clients:
                break

            if self.proxy_clients[proxy_port].get('local_conn') != local_conn:
                break

            if proxy_port in self.proxy_clients:
                self.proxy_clients[proxy_port]['last_heartbeat'] = time.time()

    def _accept_user_connections(self, proxy_port, proxy_socket):
        """接受用户连接并转发数据"""
        proxy_socket.settimeout(None)  # 恢复阻塞模式
        while self.running and self.proxy_clients.get(proxy_port, {}).get('local_conn'):
            try:
                client_conn, client_addr = proxy_socket.accept()
                self.stats['current_clients'] += 1
                self.proxy_clients[proxy_port]['client_count'] += 1
                self.proxy_clients[proxy_port]['total_clients'] += 1

                log_message = f"用户连接代理端口: {proxy_port}，来源: {client_addr}"
                self._log(log_message)

                # 处理用户请求
                client_thread = threading.Thread(
                    target=self._handle_client_request,
                    args=(client_conn, proxy_port, client_addr)
                )
                client_thread.daemon = True
                client_thread.start()

            except Exception as e:
                if self.running:
                    self._log(f"代理端口 {proxy_port} 接受用户连接异常: {e}")
                break

    def _heartbeat_receiver(self, proxy_port, local_conn):
        """接收心跳信号并保持长连接活跃"""
        try:
            local_conn.settimeout(30)
            while self.running and self.proxy_clients.get(proxy_port, {}).get('local_conn') == local_conn:
                try:
                    data = local_conn.recv(1024)
                    if not data:
                        self._log(f"代理端口 {proxy_port} 内网服务连接已关闭")
                        break

                    if proxy_port in self.proxy_clients:
                        self.proxy_clients[proxy_port]['last_heartbeat'] = time.time()

                    if data == b'HEARTBEAT':
                        continue

                except socket.timeout:
                    if proxy_port in self.proxy_clients:
                        last_hb = self.proxy_clients[proxy_port].get('last_heartbeat', 0)
                        if time.time() - last_hb > 60:
                            self._log(f"代理端口 {proxy_port} 心跳超时")
                            break
                    continue
                except Exception as e:
                    self._log(f"代理端口 {proxy_port} 接收异常: {e}")
                    break
        except Exception as e:
            self._log(f"代理端口 {proxy_port} 心跳线程异常: {e}")
        finally:
            if proxy_port in self.proxy_clients:
                self.proxy_clients[proxy_port]['local_conn'] = None

    def _cleanup_expired_ports(self):
        """定期清理过期的代理端口"""
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
                except:
                    pass
                if port in self.proxy_clients:
                    del self.proxy_clients[port]
                log_message = f"端口 {port} 已过期并释放"
                self._log(log_message)
                log_to_file(log_message)

    def _log(self, message):
        """日志记录"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [远程服务器] {message}")

    def connect_handler(self):
        """长连接建立接口"""
        client_ip = self._get_client_ip()

        if self._is_blacklisted(client_ip):
            return jsonify({'status': 'error', 'message': 'IP已被拉黑'}), 403

        # 自动分配代理端口
        proxy_port = self._get_random_port()

        log_message = f"内网服务长连接请求，分配代理端口: {proxy_port}"
        self._log(log_message)
        log_to_file(log_message)

        # 立即返回端口号给内网服务，同时在后台启动监听线程
        def start_listener():
            self._handle_proxy_client(proxy_port)

        listener_thread = threading.Thread(target=start_listener)
        listener_thread.daemon = True
        listener_thread.start()

        return jsonify({
            'status': 'success',
            'port': proxy_port,
            'message': f'长连接已建立，代理端口: {proxy_port}'
        }), 200

    def auth_handler(self):
        """认证接口处理"""
        client_ip = self._get_client_ip()

        if self._is_blacklisted(client_ip):
            return jsonify({
                'status': 'error',
                'message': 'IP已被拉黑，请5分钟后重试'
            }), 403

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

            return jsonify({
                'status': 'success',
                'message': '认证成功，请建立长连接'
            }), 200
        else:
            self._record_failed_attempt(client_ip)
            attempts_left = MAX_FAILED_ATTEMPTS - self.failed_attempts[client_ip]['count']
            return jsonify({
                'status': 'error',
                'message': f'认证失败，剩余尝试次数: {attempts_left}',
                'attempts_left': attempts_left
            }), 401

    def health_handler(self):
        """健康检查接口"""
        uptime = int(time.time() - self.stats['start_time'])
        return jsonify({
            'status': 'running',
            'auth_enabled': AUTH_ENABLED,
            'active_proxies': len(self.proxy_clients),
            'blacklist_count': len(self.blacklist),
            'stats': {
                'total_connections': self.stats['total_connections'],
                'successful_auth': self.stats['successful_auth'],
                'failed_auth': self.stats['failed_auth'],
                'current_clients': self.stats['current_clients'],
                'uptime_seconds': uptime,
                'uptime_human': f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s"
            }
        })

    def blacklist_handler(self):
        """获取黑名单信息"""
        return jsonify({
            'blacklist': list(self.blacklist),
            'failed_attempts': dict(self.failed_attempts),
            'max_failed_attempts': MAX_FAILED_ATTEMPTS,
            'blacklist_duration': BLACKLIST_DURATION
        })

    def remove_blacklist_handler(self, ip):
        """从黑名单移除IP"""
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
        """获取代理端口列表"""
        proxies_info = []
        for port, info in self.proxy_clients.items():
            proxies_info.append({
                'port': port,
                'created_at': datetime.fromtimestamp(info['created_at']).strftime('%Y-%m-%d %H:%M:%S'),
                'client_count': info['client_count'],
                'total_clients': info['total_clients'],
                'has_local_conn': info.get('local_conn') is not None
            })
        return jsonify({
            'proxies': proxies_info,
            'total': len(proxies_info)
        })

    def reload_config_handler(self):
        """热更新配置"""
        global config, LISTEN_HOST, LISTEN_PORT, AUTH_ENABLED, USERNAME, PASSWORD_HASH
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

@app.route('/api/reload', methods=['POST'])
def reload_config():
    return proxy_server.reload_config_handler()

if __name__ == '__main__':
    log_message = f"启动成功，监听端口: {LISTEN_PORT}"
    proxy_server._log(log_message)
    log_to_file(log_message)

    proxy_server._log(f"认证功能: {'已启用' if AUTH_ENABLED else '已禁用'}")
    proxy_server._log(f"公网访问地址: http://<server-ip>:{LISTEN_PORT}")
    proxy_server._log("按 Ctrl+C 停止服务")

    try:
        app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        proxy_server.running = False
        log_message = "收到停止信号，正在关闭..."
        proxy_server._log(log_message)
        log_to_file(log_message)