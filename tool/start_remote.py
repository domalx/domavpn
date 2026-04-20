#!/usr/bin/env python3
"""
远程代理服务器启动脚本
"""

import subprocess
import sys
import os

def main():
    # 检查虚拟环境
    if sys.platform == 'win32':
        venv_python = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'venv', 'Scripts', 'python.exe')
    else:
        venv_python = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'venv', 'bin', 'python')
    
    if not os.path.exists(venv_python):
        print("虚拟环境不存在，正在创建...")
        subprocess.run([sys.executable, '-m', 'venv', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'venv')], check=True)
        
    # 安装依赖
    print("检查依赖...")
    subprocess.run([venv_python, '-m', 'pip', 'install', '-r', 'requirements.txt'], check=True)
    
    # 启动服务
    print("启动远程代理服务器...")
    subprocess.run([venv_python, 'remote_server/remote_server.py'], check=True)

if __name__ == '__main__':
    main()