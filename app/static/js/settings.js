const CONFIG_GROUPS = {
    smb: ['smb_server', 'smb_share', 'smb_username', 'smb_password', 'smb_domain', 'smb_mount_path'],
    ocr: ['paddleocr_base_url', 'paddleocr_api_key'],
    ai: ['qwen_base_url', 'qwen_api_key', 'qwen_model', 'ai_enabled'],
    scan: ['scan_concurrency', 'gbt_standard'],
    auth: ['yz_login_url'],
    db: ['db_host', 'db_port', 'db_name', 'db_user', 'db_password'],
};

let allConfigs = {};

function loadConfigs() {
    fetch('/api/config')
        .then(r => r.json())
        .then(data => {
            allConfigs = {};
            data.configs.forEach(c => {
                allConfigs[c.key] = c;
            });

            Object.keys(CONFIG_GROUPS).forEach(g => {
                document.getElementById(g + '-configs').innerHTML = '';
            });

            Object.keys(CONFIG_GROUPS).forEach(group => {
                const container = document.getElementById(group + '-configs');
                CONFIG_GROUPS[group].forEach(key => {
                    const c = allConfigs[key];
                    if (!c) return;
                    const el = createConfigElement(c);
                    container.appendChild(el);
                });
            });
        });
}

function createConfigElement(c) {
    const isSecret = c.key.includes('password') || c.key.includes('api_key');
    const isBool = c.value === 'true' || c.value === 'false' || c.key === 'ai_enabled';
    const div = document.createElement('div');
    div.className = 'mb-3';

    if (isBool) {
        const switchDiv = document.createElement('div');
        switchDiv.className = 'form-check form-switch';
        const checkbox = document.createElement('input');
        checkbox.className = 'form-check-input';
        checkbox.type = 'checkbox';
        checkbox.id = 'cfg-' + c.key;
        checkbox.checked = c.value === 'true';
        const label = document.createElement('label');
        label.className = 'form-check-label';
        label.htmlFor = 'cfg-' + c.key;
        label.textContent = c.description || c.key;
        switchDiv.appendChild(checkbox);
        switchDiv.appendChild(label);
        div.appendChild(switchDiv);
    } else {
        const label = document.createElement('label');
        label.className = 'form-label small fw-semibold';
        label.textContent = c.description || c.key;
        const input = document.createElement('input');
        input.type = isSecret ? 'password' : 'text';
        input.className = 'form-control form-control-sm';
        input.id = 'cfg-' + c.key;
        input.value = c.value || '';
        input.placeholder = c.description || '';
        div.appendChild(label);
        div.appendChild(input);
    }
    return div;
}

function saveAll() {
    const promises = Object.keys(allConfigs).map(key => {
        const el = document.getElementById('cfg-' + key);
        if (!el) return Promise.resolve();
        const value = el.type === 'checkbox' ? String(el.checked) : el.value;
        return fetch('/api/config/' + key, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({value}),
        });
    });

    Promise.all(promises).then(() => {
        const toast = new bootstrap.Toast(document.getElementById('saveToast'));
        toast.show();
    });
}

function testSMB() {
    testConnection('smb', 'SMB', '/api/config/test-smb');
}

function testOCR() {
    testConnection('ocr', 'OCR', '/api/config/test-ocr');
}

function testAI() {
    testConnection('ai', 'AI', '/api/config/test-ai');
}

function testConnection(group, name, url) {
    const resultDiv = document.getElementById(group + '-result');
    resultDiv.classList.remove('d-none');
    resultDiv.innerHTML = '';
    const infoDiv = document.createElement('div');
    infoDiv.className = 'alert alert-info py-2 mb-0';
    infoDiv.innerHTML = '<i class="bi bi-hourglass-split me-2"></i>正在测试...';
    resultDiv.appendChild(infoDiv);

    fetch(url, {method: 'POST'})
        .then(r => r.json())
        .then(data => {
            const alertClass = data.success ? 'alert-success' : 'alert-danger';
            const icon = data.success ? 'bi-check-circle' : 'bi-x-circle';
            const msg = data.message || JSON.stringify(data);
            resultDiv.innerHTML = '';
            const alertDiv = document.createElement('div');
            alertDiv.className = 'alert ' + alertClass + ' py-2 mb-0';
            alertDiv.innerHTML = '<i class="bi ' + icon + ' me-2"></i>' + msg;
            resultDiv.appendChild(alertDiv);
        })
        .catch(e => {
            resultDiv.innerHTML = '';
            const errDiv = document.createElement('div');
            errDiv.className = 'alert alert-danger py-2 mb-0';
            errDiv.innerHTML = '<i class="bi bi-x-circle me-2"></i>测试失败: ' + e.message;
            resultDiv.appendChild(errDiv);
        });
}

document.addEventListener('DOMContentLoaded', loadConfigs);
