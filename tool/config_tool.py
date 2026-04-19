#!/usr/bin/env python3
"""
配置管理工具 - 用于修改远程服务器和内网服务的用户名密码

使用方法:
    python tool/config_tool.py --help
"""

import os
import json
import argparse
import getpass

def load_config(filepath):
    """加载配置文件"""
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_config(filepath, config):
    """保存配置文件"""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def set_remote_auth(username=None, password=None, enabled=None):
    """设置远程服务器认证信息"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'remote_server', 'config.json')
    config = load_config(config_path)
    
    if username is not None:
        if 'auth' not in config:
            config['auth'] = {}
        config['auth']['username'] = username
    
    if password is not None:
        if 'auth' not in config:
            config['auth'] = {}
        config['auth']['password'] = password
    
    if enabled is not None:
        if 'auth' not in config:
            config['auth'] = {}
        config['auth']['enabled'] = enabled
    
    save_config(config_path, config)
    print(f"远程服务器配置已更新: {config_path}")
    print(json.dumps(config, indent=2, ensure_ascii=False))

def set_local_auth(username=None, password=None):
    """设置内网服务认证信息"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'local_server', 'config.json')
    config = load_config(config_path)
    
    if 'remote_server' not in config:
        config['remote_server'] = {}
    if 'auth' not in config['remote_server']:
        config['remote_server']['auth'] = {}
    
    if username is not None:
        config['remote_server']['auth']['username'] = username
    
    if password is not None:
        config['remote_server']['auth']['password'] = password
    
    save_config(config_path, config)
    print(f"内网服务配置已更新: {config_path}")
    print(json.dumps(config, indent=2, ensure_ascii=False))

def show_config():
    """显示当前配置"""
    print("=== 远程服务器配置 ===")
    remote_config = load_config(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'remote_server', 'config.json'))
    print(json.dumps(remote_config, indent=2, ensure_ascii=False))
    
    print("\n=== 内网服务配置 ===")
    local_config = load_config(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'local_server', 'config.json'))
    print(json.dumps(local_config, indent=2, ensure_ascii=False))

def main():
    parser = argparse.ArgumentParser(description='配置管理工具 - 修改用户名密码')
    
    subparsers = parser.add_subparsers(dest='command', help='可用命令')
    
    # remote 命令
    remote_parser = subparsers.add_parser('remote', help='设置远程服务器配置')
    remote_parser.add_argument('--username', help='用户名')
    remote_parser.add_argument('--password', help='密码')
    remote_parser.add_argument('--enabled', type=bool, help='是否启用认证')
    remote_parser.add_argument('--interactive', '-i', action='store_true', help='交互式输入密码')
    
    # local 命令
    local_parser = subparsers.add_parser('local', help='设置内网服务配置')
    local_parser.add_argument('--username', help='用户名')
    local_parser.add_argument('--password', help='密码')
    local_parser.add_argument('--interactive', '-i', action='store_true', help='交互式输入密码')
    
    # show 命令
    subparsers.add_parser('show', help='显示当前配置')
    
    args = parser.parse_args()
    
    if args.command == 'remote':
        username = args.username
        password = args.password
        
        if args.interactive:
            if not username:
                username = input("请输入用户名: ")
            if not password:
                password = getpass.getpass("请输入密码: ")
        
        set_remote_auth(username=username, password=password, enabled=args.enabled)
    
    elif args.command == 'local':
        username = args.username
        password = args.password
        
        if args.interactive:
            if not username:
                username = input("请输入用户名: ")
            if not password:
                password = getpass.getpass("请输入密码: ")
        
        set_local_auth(username=username, password=password)
    
    elif args.command == 'show':
        show_config()
    
    else:
        parser.print_help()

if __name__ == '__main__':
    main()