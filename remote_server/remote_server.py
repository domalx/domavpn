#!/usr/bin/env python3
"""
远程代理服务器 - 提供端口转发和认证服务

功能特性：
- HTTP API接口用于认证和管理
- SHA256密码加密存储
- IP黑名单机制（连续3次认证失败）
- 自动端口分配和清理
- 支持多客户端并发连接
"""

import socket
import threading
import os
import json
import hashlib
import time
from datetime import datetime
from flask import Flask, request, jsonify
from werkzeug.serving import WSGIServer

# 配置加载
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

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

# 全局配置
config = load_config()
LISTEN_HOST = config.get('listen_host', '0.0.0.0')
LISTEN_PORT = config.get('listen_port', 8871)
AUTH_ENABLED = config.get('auth', {}).get('enabled', False)
USERNAME = config.get('auth', {}).get('username', '')
PASSWORD_HASH = hashlib.sha256(config.get('auth', {}).get('password', '').encode()).hexdigest()

# 安全配置
MAX_FAILED_ATTEMPTS = 3
BLACKLIST_DURATION = 300  # 5分钟
PORT_POOL_START = 50000
PORT_POOL_END = 60000

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
        
        # 启动端口清理线程
        cleanup_thread = threading.Thread(target=self._cleanup_expired_ports)
        cleanup_thread.daemon = True
        cleanup_thread.start()
    
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
        
        if ip in self.failed_attempts:
            attempt = self.failed_attempts[ip]
            if attempt['count'] >= MAX_FAILED_ATTEMPTS:
                if time.time() - attempt['last_attempt'] < BLACKLIST_DURATION:
                    self.blacklist.add(ip)
                    self._log(f"IP {ip} 已被加入黑名单")
                    return True
                else:
                    self.failed_attempts[ip] = {'count': 0, 'last_attempt': 0}
        return False
    
    def _record_failed_attempt(self, ip):
        """记录认证失败尝试"""
        if ip not in self.failed_attempts:
            self.failed_attempts[ip] = {'count': 0, 'last_attempt': 0}
        
        self.failed_attempts[ip]['count'] += 1
        self.failed_attempts[ip]['last_attempt'] = time.time()
        
        if self.failed_attempts[ip]['count'] >= MAX_FAILED_ATTEMPTS:
            self.blacklist.add(ip)
            self._log(f"IP {ip} 连续{MAX_FAILED_ATTEMPTS}次认证失败，已拉黑")
    
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
    
    def _handle_proxy_client(self, proxy_port):
        """处理代理客户端连接"""
        proxy_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        proxy_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            proxy_socket.bind((LISTEN_HOST, proxy_port))
            proxy_socket.listen(5)
            proxy_socket.settimeout(300)
            
            self.proxy_clients[proxy_port] = {
                'socket': proxy_socket, 
                'created_at': time.time(),
                'client_count': 0
            }
            self._log(f"代理端口 {proxy_port} 已准备就绪")
            
            while self.running:
                try:
                    client_conn, client_addr = proxy_socket.accept()
                    self.proxy_clients[proxy_port]['client_count'] += 1
                    self._log(f"用户连接到端口 {proxy_port}: {client_addr}")
                    
                    # 启动新线程处理客户端
                    client_thread = threading.Thread(
                        target=self._handle_single_client,
                        args=(client_conn, proxy_port, client_addr)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                    
                except socket.timeout:
                    if time.time() - self.proxy_clients[proxy_port]['created_at'] > 300:
                        self._log(f"代理端口 {proxy_port} 超时关闭")
                        break
                except Exception as e:
                    self._log(f"端口 {proxy_port} 接受连接异常: {e}")
                    break
        
        except Exception as e:
            self._log(f"创建代理端口 {proxy_port} 失败: {e}")
        finally:
            if proxy_port in self.proxy_clients:
                try:
                    self.proxy_clients[proxy_port]['socket'].close()
                except:
                    pass
                del self.proxy_clients[proxy_port]
                self._log(f"代理端口 {proxy_port} 已释放")
    
    def _handle_single_client(self, client_conn, proxy_port, client_addr):
        """处理单个客户端连接"""
        try:
            # 连接内网服务
            local_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            local_socket.connect(('127.0.0.1', 5000))
            
            # 启动双向转发
            forward_thread = threading.Thread(
                target=self._forward_data,
                args=(client_conn, local_socket)
            )
            forward_thread.daemon = True
            forward_thread.start()
            
            self._forward_data(local_socket, client_conn)
            
        except ConnectionRefusedError:
            self._log(f"无法连接内网服务 127.0.0.1:5000")
            client_conn.close()
        except Exception as e:
            self._log(f"处理客户端 {client_addr} 异常: {e}")
        finally:
            if proxy_port in self.proxy_clients:
                self.proxy_clients[proxy_port]['client_count'] -= 1
    
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
                    self.proxy_clients[port]['socket'].close()
                except:
                    pass
                del self.proxy_clients[port]
                self._log(f"端口 {port} 已过期并释放")
    
    def _log(self, message):
        """日志记录"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [远程服务器] {message}")
    
    def auth_handler(self):
        """认证接口处理"""
        client_ip = self._get_client_ip()
        
        # 检查黑名单
        if self._is_blacklisted(client_ip):
            return jsonify({
                'status': 'error', 
                'message': 'IP已被拉黑，请5分钟后重试'
            }), 403
        
        # 解析请求数据
        try:
            data = request.get_json()
        except Exception as e:
            self._record_failed_attempt(client_ip)
            return jsonify({'status': 'error', 'message': '请求格式错误'}), 400
        
        if not data or 'username' not in data or 'password' not in data:
            self._record_failed_attempt(client_ip)
            return jsonify({'status': 'error', 'message': '缺少认证信息'}), 400
        
        # 验证凭证
        if self._verify_credentials(data['username'], data['password']):
            # 重置失败计数
            if client_ip in self.failed_attempts:
                self.failed_attempts[client_ip] = {'count': 0, 'last_attempt': 0}
            
            # 分配端口并启动代理
            proxy_port = self._get_random_port()
            proxy_thread = threading.Thread(
                target=self._handle_proxy_client,
                args=(proxy_port,)
            )
            proxy_thread.daemon = True
            proxy_thread.start()
            
            # 等待端口绑定完成
            time.sleep(0.1)
            
            self._log(f"认证成功 - IP: {client_ip}, 端口: {proxy_port}")
            
            return jsonify({
                'status': 'success',
                'port': proxy_port,
                'message': f'代理端口已分配: {proxy_port}',
                'expire_in': 300
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
        return jsonify({
            'status': 'running',
            'auth_enabled': AUTH_ENABLED,
            'active_proxies': len(self.proxy_clients),
            'blacklist_count': len(self.blacklist)
        })
    
    def blacklist_handler(self):
        """获取黑名单信息"""
        return jsonify({
            'blacklist': list(self.blacklist),
            'failed_attempts': dict(self.failed_attempts)
        })
    
    def remove_blacklist_handler(self, ip):
        """从黑名单移除IP"""
        if ip in self.blacklist:
            self.blacklist.remove(ip)
            if ip in self.failed_attempts:
                self.failed_attempts[ip] = {'count': 0, 'last_attempt': 0}
            self._log(f"IP {ip} 已从黑名单移除")
            return jsonify({'status': 'success', 'message': f'IP {ip} 已移除'}), 200
        return jsonify({'status': 'error', 'message': 'IP不在黑名单中'}), 404

# 创建代理服务器实例
proxy_server = ProxyServer()

# API路由
@app.route('/api/auth', methods=['POST'])
def auth():
    return proxy_server.auth_handler()

@app.route('/api/health', methods=['GET'])
def health():
    return proxy_server.health_handler()

@app.route('/api/blacklist', methods=['GET'])
def blacklist():
    return proxy_server.blacklist_handler()

@app.route('/api/blacklist/<ip>', methods=['DELETE'])
def remove_blacklist(ip):
    return proxy_server.remove_blacklist_handler(ip)

if __name__ == '__main__':
    proxy_server._log(f"启动成功，监听端口: {LISTEN_PORT}")
    proxy_server._log(f"认证功能: {'已启用' if AUTH_ENABLED else '已禁用'}")
    proxy_server._log(f"公网访问地址: http://<server-ip>:{LISTEN_PORT}")
    proxy_server._log("按 Ctrl+C 停止服务")
    
    try:
        # 使用Werkzeug的WSGIServer
        server = WSGIServer((LISTEN_HOST, LISTEN_PORT), app)
        server.serve_forever()
    except KeyboardInterrupt:
        proxy_server.running = False
        proxy_server._log("收到停止信号，正在关闭...")