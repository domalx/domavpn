#!/usr/bin/env python3
"""
内网HTTP服务启动脚本
"""

import subprocess
import sys
import os

def main():
    venv_python = os.path.join(os.path.dirname(__file__), '..', 'venv', 'Scripts', 'python.exe')

    if not os.path.exists(venv_python):
        print("错误: 虚拟环境不存在，请先运行: python -m venv venv")
        sys.exit(1)

    print("正在启动内网HTTP服务...")
    subprocess.run([venv_python, os.path.join(os.path.dirname(__file__), '..', 'local_server', 'local_server.py')])

if __name__ == '__main__':
    main()