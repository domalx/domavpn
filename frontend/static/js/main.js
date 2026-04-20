// 主JavaScript文件

// 删除文件提示
function deleteFilePrompt() {
    const filename = prompt('请输入要删除的文件名:');
    if (filename) {
        fetch('/api/delete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({filename: filename})
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                showAlert('success', data.message);
            } else {
                showAlert('error', data.error || '删除失败');
            }
        })
        .catch(error => {
            showAlert('error', '操作失败: ' + error);
        });
    }
}

// 重命名文件提示
function renameFilePrompt() {
    const oldname = prompt('请输入原文件名:');
    if (oldname) {
        const newname = prompt('请输入新文件名:');
        if (newname) {
            fetch('/api/rename', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({oldname: oldname, newname: newname})
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    showAlert('success', data.message);
                } else {
                    showAlert('error', data.error || '重命名失败');
                }
            })
            .catch(error => {
                showAlert('error', '操作失败: ' + error);
            });
        }
    }
}

// 显示文件列表
function loadFileList() {
    fetch('/list')
    .then(response => response.json())
    .then(data => {
        if (data.files) {
            const fileList = document.getElementById('file-list');
            if (fileList) {
                let html = '<table><thead><tr><th>名称</th><th>大小</th><th>类型</th><th>修改时间</th></tr></thead><tbody>';
                data.files.forEach(file => {
                    if (file.type === 'file') {
                        html += `<tr>
                            <td><a href="/${file.name}" class="file-name">${file.name}</a></td>
                            <td class="file-size">${formatFileSize(file.size)}</td>
                            <td>文件</td>
                            <td class="file-time">${file.mtime}</td>
                        </tr>`;
                    } else {
                        html += `<tr>
                            <td><strong>${file.name}/</strong></td>
                            <td>-</td>
                            <td>目录</td>
                            <td class="file-time">${file.mtime}</td>
                        </tr>`;
                    }
                });
                html += '</tbody></table>';
                fileList.innerHTML = html;
            }
        }
    })
    .catch(error => {
        console.error('加载文件列表失败:', error);
    });
}

// 加载服务状态
function loadStatus() {
    fetch('/api/status')
    .then(response => response.json())
    .then(data => {
        const statusElement = document.getElementById('status');
        if (statusElement) {
            const statusClass = data.connected ? 'online' : 'offline';
            const statusText = data.connected ? '已连接' : '未连接';
            statusElement.className = `status ${statusClass}`;
            statusElement.innerHTML = `代理状态: ${statusText} | 端口: ${data.proxy_port || 'N/A'}`;
        }
    })
    .catch(error => {
        console.error('加载状态失败:', error);
    });
}

// 格式化文件大小
function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// 显示提示信息
function showAlert(type, message) {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.textContent = message;
    document.body.appendChild(alertDiv);
    
    // 3秒后自动移除
    setTimeout(() => {
        alertDiv.remove();
    }, 3000);
}

// 页面加载完成后执行
document.addEventListener('DOMContentLoaded', function() {
    // 加载状态
    loadStatus();
    
    // 每10秒刷新一次状态
    setInterval(loadStatus, 10000);
    
    // 如果是文件列表页面，加载文件列表
    if (window.location.pathname === '/list') {
        loadFileList();
    }
    
    // 上传表单提交处理
    const uploadForm = document.getElementById('upload-form');
    if (uploadForm) {
        uploadForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const formData = new FormData(this);
            
            fetch('/upload', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    showAlert('success', data.message);
                    this.reset();
                } else {
                    showAlert('error', data.error || '上传失败');
                }
            })
            .catch(error => {
                showAlert('error', '上传失败: ' + error);
            });
        });
    }
});