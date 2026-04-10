import { checkSession } from './api.js?v=11';

const BASE_PATH = (typeof window !== 'undefined' && window.IZS_BASE_PATH) || '';
const API_BASE = BASE_PATH + '/api';

// Auth guard
const currentUser = await checkSession();
if (!currentUser) throw new Error('Not authenticated');
document.documentElement.classList.remove('auth-pending');

// --- Init Drawflow ---
const drawflowEl = document.getElementById('drawflow');
const editor = new Drawflow(drawflowEl);
editor.reroute = true;
editor.start();

// Track node data
const nodeDataMap = {};  // drawflow node id → component data
let nodeIdCounter = 0;

// --- Load catalog ---
async function loadCatalog() {
    const res = await fetch(`${API_BASE}/catalog/components`, { credentials: 'same-origin' });
    if (!res.ok) return {};
    return await res.json();
}

function renderPalette(catalog) {
    const list = document.getElementById('paletteList');
    list.innerHTML = '';

    for (const [domain, components] of Object.entries(catalog)) {
        const domainEl = document.createElement('div');
        domainEl.className = 'palette-domain';
        domainEl.dataset.domain = domain;

        const label = document.createElement('div');
        label.className = 'palette-domain-label';
        label.textContent = domain || 'Other';
        domainEl.appendChild(label);

        for (const comp of components) {
            const item = document.createElement('div');
            item.className = 'palette-item';
            item.draggable = true;
            item.dataset.componentId = comp.id;
            item.dataset.tool = comp.tool || '';
            item.dataset.description = comp.description || '';
            item.dataset.inputs = JSON.stringify(comp.inputs || []);
            item.dataset.outputs = JSON.stringify(comp.outputs || []);

            item.innerHTML = `
                <i class="fas fa-cube"></i>
                <div>
                    <span class="tool-name">${comp.tool || comp.id.split('__').pop()}</span>
                    <div style="font-size:10px; color:var(--text-muted); margin-top:1px; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${comp.id}">${comp.id}</div>
                </div>
            `;

            item.addEventListener('dragstart', (e) => {
                e.dataTransfer.setData('application/json', JSON.stringify(comp));
            });

            domainEl.appendChild(item);
        }

        list.appendChild(domainEl);
    }
}

// --- Search filter ---
document.getElementById('paletteSearch').addEventListener('input', (e) => {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll('.palette-item').forEach(item => {
        const text = (item.dataset.componentId + ' ' + item.dataset.tool + ' ' + item.dataset.description).toLowerCase();
        item.style.display = text.includes(q) ? '' : 'none';
    });
    document.querySelectorAll('.palette-domain').forEach(dom => {
        const visible = dom.querySelectorAll('.palette-item[style=""], .palette-item:not([style])');
        dom.style.display = visible.length > 0 ? '' : 'none';
    });
});

// --- Drop on canvas ---
drawflowEl.addEventListener('dragover', (e) => e.preventDefault());

drawflowEl.addEventListener('drop', (e) => {
    e.preventDefault();
    const data = JSON.parse(e.dataTransfer.getData('application/json'));
    const inputs = (data.inputs || []).length || 1;
    const outputs = (data.outputs || []).length || 1;

    const toolName = data.tool || data.id.split('__').pop();
    const domainShort = (data.domain || '').split(' ')[0] || 'Step';

    const html = `
        <div class="node-content">
            <div class="node-header">${domainShort}</div>
            <div class="node-title">${toolName}</div>
            <div class="node-tool">${data.id}</div>
        </div>
    `;

    // Calculate position relative to canvas
    const rect = drawflowEl.getBoundingClientRect();
    const x = (e.clientX - rect.left - editor.precanvas.getBoundingClientRect().left + drawflowEl.scrollLeft) / editor.zoom;
    const y = (e.clientY - rect.top - editor.precanvas.getBoundingClientRect().top + drawflowEl.scrollTop) / editor.zoom;

    const nodeId = editor.addNode(
        data.id,        // name
        inputs,         // inputs
        outputs,        // outputs
        x, y,           // position
        data.id,        // class
        {},             // data
        html            // html
    );

    nodeDataMap[nodeId] = {
        node_id: nodeId,
        component_id: data.id,
        tool: data.tool,
    };

    updateNodeCount();
});

// --- Node count ---
function updateNodeCount() {
    const count = Object.keys(editor.export().drawflow.Home.data).length;
    document.getElementById('nodeCount').textContent = count;
}

editor.on('nodeRemoved', () => updateNodeCount());

// --- Clear ---
document.getElementById('clearBtn').addEventListener('click', () => {
    editor.clear();
    Object.keys(nodeDataMap).forEach(k => delete nodeDataMap[k]);
    updateNodeCount();
});

// --- Generate ---
document.getElementById('generateBtn').addEventListener('click', async () => {
    const exported = editor.export().drawflow.Home.data;
    const nodeIds = Object.keys(exported);

    if (nodeIds.length === 0) return;

    const nodes = [];
    const edges = [];

    for (const nid of nodeIds) {
        const node = exported[nid];
        const data = nodeDataMap[nid];
        if (data) {
            nodes.push(data);
        }
        // Extract connections
        for (const [outputKey, conns] of Object.entries(node.outputs || {})) {
            for (const conn of conns.connections || []) {
                edges.push({
                    source: parseInt(nid),
                    target: parseInt(conn.node),
                });
            }
        }
    }

    const btn = document.getElementById('generateBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating...';

    try {
        const res = await fetch(`${API_BASE}/generate-from-graph`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nodes, edges }),
        });

        const result = await res.json();

        if (result.nextflow_code) {
            document.getElementById('resultCode').textContent = result.nextflow_code;
            document.getElementById('resultPanel').classList.add('open');
        } else {
            alert(result.error || 'Generation failed');
        }
    } catch (err) {
        alert('Error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-bolt"></i> Generate Pipeline';
    }
});

// --- Result panel ---
document.getElementById('closeResultBtn').addEventListener('click', () => {
    document.getElementById('resultPanel').classList.remove('open');
});

document.getElementById('copyResultBtn').addEventListener('click', async () => {
    const code = document.getElementById('resultCode').textContent;
    if (code) {
        await navigator.clipboard.writeText(code);
        const btn = document.getElementById('copyResultBtn');
        btn.innerHTML = '<i class="fas fa-check"></i>';
        setTimeout(() => { btn.innerHTML = '<i class="fas fa-copy"></i>'; }, 1500);
    }
});

// --- Init ---
const catalog = await loadCatalog();
renderPalette(catalog);
