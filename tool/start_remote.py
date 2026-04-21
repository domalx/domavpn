#!/usr/bin/env python3
"""
远程代理服务器启动脚本
"""

import subprocess
import sys
import os

def main():
    # 跨平台兼容的虚拟环境Python路径
    if os.name == 'nt':  # Windows
        venv_python = os.path.join(os.path.dirname(__file__), '..', 'venv', 'Scripts', 'python.exe')
    else:  # Linux/macOS
        venv_python = os.path.join(os.path.dirname(__file__), '..', 'venv', 'bin', 'python3')

    if not os.path.exists(venv_python):
        print("错误: 虚拟环境不存在，请先运行: python -m venv venv")
        sys.exit(1)

    print("正在启动远程代理服务器...")
    subprocess.run([venv_python, os.path.join(os.path.dirname(__file__), '..', 'remote_server', 'remote_server.py')])

if __name__ == '__main__':
    main()