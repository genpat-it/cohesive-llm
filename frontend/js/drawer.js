import { checkSession, showToast } from './api.js?v=25';
import { confirmDialog, promptDialog } from './modal.js?v=25';

const BASE_PATH = (typeof window !== 'undefined' && window.IZS_BASE_PATH && !window.IZS_BASE_PATH.includes('{{')) ? window.IZS_BASE_PATH : '';
const API_BASE = (typeof window !== 'undefined' && window.IZS_API_BASE) || 
    (typeof window !== 'undefined' && (window.location.port === '9000' || window.location.port === '3000' || window.location.port === '5500') ? 'http://localhost:8080' : (BASE_PATH || ''));

// Auth guard
const currentUser = await checkSession();
if (!currentUser) throw new Error('Not authenticated');
// same header as the chat: who is signed in is visible here too
const userLabelEl = document.getElementById('userLabel');
if (userLabelEl) userLabelEl.textContent = currentUser.username || '—';
document.documentElement.classList.remove('auth-pending');

// --- Init Drawflow ---
const drawflowEl = document.getElementById('drawflow');
const editor = new Drawflow(drawflowEl);
editor.reroute = true;
editor.start();

// Track node data
const nodeDataMap = {};  // drawflow node id → component data
let currentDrawingId = null;

// --- API helpers ---
async function apiFetch(path, opts = {}) {
    const res = await fetch(`${API_BASE}${path}`, { credentials: 'same-origin', ...opts });
    if (res.status === 401) { window.location.href = BASE_PATH + '/login.html'; throw new Error('Unauthorized'); }
    return res;
}

// --- Load catalog ---
async function loadCatalog() {
    const res = await apiFetch('/catalog/components');
    if (!res.ok) return {};
    return await res.json();
}

// The catalogue carries no domain (`domain` is null for every component), but
// the framework's taxonomy is already written into the ids: step_1PP_,
// step_4TY_, multi_... We derive it from there rather than leaving fifty
// components in one flat list.
const GROUPS = [
    ['0SQ', 'Sequences'],
    ['1PP', 'Pre-processing'],
    ['2AS', 'Assembly'],
    ['2MG', 'Metagenomics'],
    ['3TX', 'Taxonomy'],
    ['4AN', 'Annotation & AMR'],
    ['4TY', 'Typing'],
];

function groupOf(comp) {
    const id = comp.id || '';
    for (const [code, label] of GROUPS) {
        if (id.startsWith('step_' + code)) return `${code} · ${label}`;
    }
    if (id.startsWith('multi_')) return 'Multi-sample';
    return 'Other';
}

function paletteItem(comp) {
    const item = document.createElement('div');
    item.className = 'palette-item';
    item.draggable = true;
    item.dataset.componentId = comp.id;
    item.dataset.tool = comp.tool || '';
    item.dataset.description = comp.description || '';
    item.dataset.inputs = JSON.stringify(comp.inputs || []);
    item.dataset.outputs = JSON.stringify(comp.outputs || []);

    const inputs = comp.inputs || [];
    const outputs = comp.outputs || [];
    const inputHtml = inputs.length > 0
        ? inputs.slice(0, 3).map(n =>
            DATA_INPUTS.has(n)
                ? `<span style="color:#2563eb;">${n}</span>`
                : `<span style="color:#c2410c;" title="runtime parameter">${n}</span>`
          ).join(', ')
        : '<span style="color:#2563eb;">data</span>';
    const outputNames = outputs.slice(0, 3).map(n => n.includes('.') ? n.split('.').pop() : n).join(', ') || 'out';

    const ghUrl = comp.file_path ? `${FRAMEWORK_REPO}/blob/main/${comp.file_path}` : '';
    const ghLink = ghUrl
        ? `<a href="${ghUrl}" target="_blank" title="View source" onclick="event.stopPropagation();" style="color:var(--text-muted); font-size:11px; flex-shrink:0;"><i class="fab fa-github"></i></a>`
        : '';

    // il nome del tool basta a riconoscerlo: l'id completo sta nel tooltip
    item.innerHTML = `
        <i class="fas fa-cube"></i>
        <div style="min-width:0; flex:1;">
            <span class="tool-name">${comp.tool || comp.id.split('__').pop()}</span>
            <div class="chan">${inputHtml} &rarr; <span style="color:#059669;">${outputNames}</span></div>
        </div>
        ${ghLink}
    `;
    item.title = comp.id + (comp.description ? '\n\n' + comp.description : '');

    item.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('application/json', JSON.stringify(comp));
    });
    return item;
}

function renderPalette(catalog) {
    const list = document.getElementById('paletteList');
    list.innerHTML = '';

    const all = Object.values(catalog).flat();
    const buckets = new Map();
    for (const comp of all) {
        const g = groupOf(comp);
        if (!buckets.has(g)) buckets.set(g, []);
        buckets.get(g).push(comp);
    }

    const order = GROUPS.map(([c, l]) => `${c} · ${l}`).concat(['Multi-sample', 'Other']);
    const names = [...buckets.keys()].sort((a, b) => {
        const ia = order.indexOf(a), ib = order.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });

    for (const name of names) {
        const comps = buckets.get(name).sort((a, b) => (a.id || '').localeCompare(b.id || ''));

        const group = document.createElement('details');
        group.className = 'palette-group palette-domain';
        group.dataset.domain = name;

        const head = document.createElement('summary');
        head.className = 'palette-group-head';
        head.innerHTML = `<i class="fas fa-chevron-right chev"></i>
                          <span class="name">${name}</span>
                          <span class="count">${comps.length}</span>`;
        group.appendChild(head);

        const body = document.createElement('div');
        body.className = 'palette-group-body';
        for (const comp of comps) body.appendChild(paletteItem(comp));
        group.appendChild(body);

        list.appendChild(group);
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
        const visible = [...dom.querySelectorAll('.palette-item')]
            .filter(it => it.style.display !== 'none').length;
        dom.style.display = visible > 0 ? '' : 'none';
        // cercando si vuole vedere subito il risultato, non doverlo aprire
        if (q) dom.open = visible > 0;
        else dom.open = false;
        const c = dom.querySelector('.count');
        if (c) c.textContent = visible;
    });
});

const FRAMEWORK_REPO = 'https://github.com/genpat-it/cohesive-ngsmanager';

// --- Build node HTML ---
function buildNodeHtml(comp) {
    const toolName = comp.tool || comp.id.split('__').pop();
    const domainShort = (comp.domain || '').split(' ')[0] || 'Step';
    const filePath = comp.file_path || '';
    const ghLink = filePath ? `${FRAMEWORK_REPO}/blob/main/${filePath}` : '';
    const ghIcon = ghLink
        ? `<a href="${ghLink}" target="_blank" title="View source on GitHub" style="color:var(--text-muted); font-size:12px; position:absolute; top:8px; right:8px;" onclick="event.stopPropagation();"><i class="fab fa-github"></i></a>`
        : '';

    return `
        <div class="node-content" style="position:relative;">
            ${ghIcon}
            <div class="node-header">${domainShort}</div>
            <div class="node-title">${toolName}</div>
            <div class="node-tool">${comp.id}</div>
        </div>
    `;
}

// Inputs that carry sequencing data (from other steps)
const DATA_INPUTS = new Set([
    'reads', 'rawreads', 'trimmed', 'assembly', 'assembled', 'contigs',
    'kraken', 'depleted', 'consensus', 'fastq', 'fasta', 'vcf',
    'trimmedAndHost', 'short_reads', 'long_reads',
]);

// --- Label Drawflow input/output dots after node is added ---
function labelNodePorts(nodeId, comp) {
    const nodeEl = drawflowEl.querySelector(`#node-${nodeId}`);
    if (!nodeEl) return;

    const inputs = comp.inputs || [];
    const outputs = comp.outputs || [];

    nodeEl.querySelectorAll('.input').forEach((el, i) => {
        const name = inputs[i] || 'in';
        el.setAttribute('data-label', name);
        // Runtime parameters get orange styling
        if (name !== 'in' && !DATA_INPUTS.has(name)) {
            el.classList.add('port-param');
        }
    });
    nodeEl.querySelectorAll('.output').forEach((el, i) => {
        const name = outputs[i] || 'out';
        const short = name.includes('.') ? name.split('.').pop() : name;
        el.setAttribute('data-label', short);
    });
}

// --- Drop on canvas ---
drawflowEl.addEventListener('dragover', (e) => e.preventDefault());

drawflowEl.addEventListener('drop', (e) => {
    e.preventDefault();
    const data = JSON.parse(e.dataTransfer.getData('application/json'));
    addNodeToCanvas(data, e.clientX, e.clientY);
});

function addNodeToCanvas(comp, clientX, clientY) {
    const numInputs = Math.max((comp.inputs || []).length, 1);
    const numOutputs = Math.max((comp.outputs || []).length, 1);

    const html = buildNodeHtml(comp);

    const rect = drawflowEl.getBoundingClientRect();
    const preRect = editor.precanvas.getBoundingClientRect();
    const x = (clientX - preRect.left) / editor.zoom;
    const y = (clientY - preRect.top) / editor.zoom;

    const nodeId = editor.addNode(
        comp.id,
        numInputs,
        numOutputs,
        x, y,
        comp.id,
        { component_id: comp.id, tool: comp.tool, file_path: comp.file_path || '' },
        html
    );

    nodeDataMap[nodeId] = {
        node_id: nodeId,
        component_id: comp.id,
        tool: comp.tool,
        inputs: comp.inputs || [],
        outputs: comp.outputs || [],
    };

    // Label the Drawflow dots
    setTimeout(() => labelNodePorts(nodeId, comp), 0);

    updateNodeCount();
    return nodeId;
}

// --- Node count ---
function updateNodeCount() {
    const count = Object.keys(editor.export().drawflow.Home.data).length;
    document.getElementById('nodeCount').textContent = count;
}

// Double-click on connection to remove it
drawflowEl.addEventListener('dblclick', (e) => {
    const path = e.target.closest('.main-path');
    if (path) {
        const connEl = path.closest('.connection');
        if (connEl) {
            const classes = connEl.classList;
            // Extract node_in/out and input/output from class names
            let nodeOut, nodeIn, outputClass, inputClass;
            classes.forEach(c => {
                if (c.startsWith('node_in_node-')) nodeIn = c.replace('node_in_node-', '');
                if (c.startsWith('node_out_node-')) nodeOut = c.replace('node_out_node-', '');
                if (c.startsWith('output_')) outputClass = c;
                if (c.startsWith('input_')) inputClass = c;
            });
            if (nodeOut && nodeIn && outputClass && inputClass) {
                editor.removeSingleConnection(nodeOut, nodeIn, outputClass, inputClass);
            }
        }
    }
});

editor.on('nodeRemoved', (id) => {
    delete nodeDataMap[id];
    updateNodeCount();
});

// --- Clear ---
document.getElementById('clearBtn').addEventListener('click', () => {
    editor.clear();
    Object.keys(nodeDataMap).forEach(k => delete nodeDataMap[k]);
    currentDrawingId = null;
    updateTitle('Untitled');
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
        for (const [outputKey, conns] of Object.entries(node.outputs || {})) {
            for (const conn of conns.connections || []) {
                edges.push({
                    source: parseInt(nid),
                    target: parseInt(conn.node),
                    source_port: outputKey,
                    target_port: conn.input,
                });
            }
        }
    }

    // the reviewer needs the provenance: which components, wired which way
    window.__graphComponents = nodes.map(n => n.component_id);
    window.__graphTopology = edges
        .map(e => `${nodes.find(n => n.node_id === e.source)?.component_id} -> ${nodes.find(n => n.node_id === e.target)?.component_id}`)
        .join('; ');

    const btn = document.getElementById('generateBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating...';

    try {
        // Include full graph state for auto-save
        const fullExport = editor.export();
        const res = await apiFetch('/generate-from-graph', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                nodes,
                edges,
                drawing_id: currentDrawingId,
                graph_json: { drawflow: fullExport, nodeDataMap: { ...nodeDataMap } },
            }),
        });

        const result = await res.json();

        if (result.nextflow_code) {
            document.getElementById('resultCode').textContent = result.nextflow_code;
            document.getElementById('resultPanel').classList.add('open');
        } else {
            confirmDialog({
                title: 'Generation failed',
                message: result.error || 'Unknown error',
                confirmText: 'OK',
                danger: true,
                icon: 'fa-times-circle',
            });
        }
    } catch (err) {
        confirmDialog({
            title: 'Generation error',
            message: err.message,
            confirmText: 'OK',
            danger: true,
            icon: 'fa-exclamation-triangle',
        });
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

// --- Validate from drawer ---
document.getElementById('validateDrawerBtn').addEventListener('click', async () => {
    const code = document.getElementById('resultCode').textContent;
    if (!code) return;

    const btn = document.getElementById('validateDrawerBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Validating...';

    try {
        const res = await apiFetch('/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nextflow_code: code }),
        });
        const result = await res.json();

        if (result.success && (!result.warnings || result.warnings.length === 0)) {
            btn.innerHTML = '<i class="fas fa-check-circle"></i> Valid';
            btn.classList.add('validate-pass');
            btn.classList.remove('validate-fail');
        } else if (result.success) {
            btn.innerHTML = '<i class="fas fa-check-circle"></i> Valid';
            btn.classList.add('validate-pass');
            btn.classList.remove('validate-fail');
            const warningHtml = result.warnings
                .map(w => `<code style="display:block;margin:4px 0;padding:6px 10px;background:#fffbeb;border-radius:6px;font-size:12px;color:#92400e;word-break:break-word;">${w.replace(/</g,'&lt;')}</code>`)
                .join('');
            confirmDialog({
                title: 'Syntax valid',
                message: `The pipeline code is syntactically correct, but Nextflow reports missing runtime parameters. This is expected for framework pipelines — parameters are provided at execution time.<br><br>${warningHtml}`,
                confirmText: 'OK',
                icon: 'fa-check-circle',
            });
        } else {
            btn.innerHTML = '<i class="fas fa-times-circle"></i> Invalid';
            btn.classList.add('validate-fail');
            btn.classList.remove('validate-pass');
            const errorHtml = result.errors
                .map(e => `<code style="display:block;margin:4px 0;padding:6px 10px;background:#fef2f2;border-radius:6px;font-size:12px;color:#991b1b;word-break:break-word;">${e.replace(/</g,'&lt;')}</code>`)
                .join('');
            confirmDialog({
                title: 'Validation failed',
                message: `The generated pipeline has syntax or structural errors:<br><br>${errorHtml}`,
                confirmText: 'OK',
                cancelText: '',
                danger: true,
                icon: 'fa-times-circle',
            });
        }
    } catch (err) {
        confirmDialog({
            title: 'Validation error',
            message: err.message,
            confirmText: 'OK',
            danger: true,
            icon: 'fa-exclamation-triangle',
        });
    } finally {
        btn.disabled = false;
        setTimeout(() => {
            btn.innerHTML = '<i class="fas fa-play-circle"></i> Validate';
            btn.classList.remove('validate-pass', 'validate-fail');
        }, 5000);
    }
});

// ==========================================================================
// SAVE / LOAD DRAWINGS
// ==========================================================================

function updateTitle(title) {
    const el = document.getElementById('drawingTitle');
    if (el) el.textContent = title;
}

// Save
document.getElementById('saveBtn').addEventListener('click', async () => {
    const exported = editor.export();
    const title = prompt('Drawing name:', currentDrawingId ? (document.getElementById('drawingTitle')?.textContent || 'Untitled') : 'Untitled');
    if (!title) return;

    const payload = {
        title,
        graph_json: {
            drawflow: exported,
            nodeDataMap: { ...nodeDataMap },
        },
    };

    try {
        let res;
        if (currentDrawingId) {
            res = await apiFetch(`/drawings/${currentDrawingId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        } else {
            res = await apiFetch('/drawings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        }
        const data = await res.json();
        currentDrawingId = data.id;
        updateTitle(data.title);
        refreshDrawingsList();
    } catch (err) {
        confirmDialog({ title: 'Save failed', message: err.message, confirmText: 'OK', danger: true, icon: 'fa-exclamation-triangle' });
    }
});

// Load drawing
async function loadDrawing(id) {
    try {
        const res = await apiFetch(`/drawings/${id}`);
        const data = await res.json();

        editor.clear();
        Object.keys(nodeDataMap).forEach(k => delete nodeDataMap[k]);

        editor.import(data.graph_json.drawflow);

        // Restore nodeDataMap
        const saved = data.graph_json.nodeDataMap || {};
        for (const [k, v] of Object.entries(saved)) {
            nodeDataMap[k] = v;
        }

        // Re-label ports after import
        for (const [nid, comp] of Object.entries(nodeDataMap)) {
            setTimeout(() => labelNodePorts(nid, comp), 0);
        }

        currentDrawingId = data.id;
        updateTitle(data.title);
        updateNodeCount();

        // Update URL
        window.history.replaceState(null, '', `${BASE_PATH}/drawer?drawing=${data.id}`);

        // Check version mismatch
        const savedVersion = data.graph_json._version;
        if (savedVersion) {
            const sysRes = await apiFetch('/system-info');
            const sysInfo = await sysRes.json();
            const currentCommit = sysInfo.framework?.commit;
            if (currentCommit && savedVersion.framework_commit && savedVersion.framework_commit !== currentCommit) {
                confirmDialog({
                    title: 'Framework updated',
                    message: `This drawing was created with framework <strong>${savedVersion.framework_commit}</strong>, but the current version is <strong>${currentCommit}</strong>. Some components may have changed. Please verify the pipeline before generating.`,
                    confirmText: 'OK',
                    icon: 'fa-exclamation-triangle',
                });
            }
        }
    } catch (err) {
        confirmDialog({ title: 'Load failed', message: err.message, confirmText: 'OK', danger: true, icon: 'fa-exclamation-triangle' });
    }
}

// Delete drawing
async function deleteDrawing(id) {
    const confirmed = await confirmDialog({
        title: 'Delete drawing?',
        message: 'This drawing will be permanently removed.',
        confirmText: 'Delete',
        cancelText: 'Cancel',
        danger: true,
        icon: 'fa-trash',
    });
    if (!confirmed) return;
    await apiFetch(`/drawings/${id}`, { method: 'DELETE' });
    if (currentDrawingId === id) {
        currentDrawingId = null;
        editor.clear();
        Object.keys(nodeDataMap).forEach(k => delete nodeDataMap[k]);
        updateTitle('Untitled');
        updateNodeCount();
    }
    refreshDrawingsList();
}

// Refresh saved drawings list
async function refreshDrawingsList() {
    const list = document.getElementById('drawingsList');
    if (!list) return;

    const res = await apiFetch('/drawings');
    const drawings = await res.json();

    list.innerHTML = '';
    if (drawings.length === 0) {
        list.innerHTML = '<div style="padding:12px; color:var(--text-muted); font-size:12px; text-align:center;">No saved drawings</div>';
        return;
    }

    for (const d of drawings) {
        const item = document.createElement('div');
        item.className = 'drawing-item';
        if (d.id === currentDrawingId) item.classList.add('active');

        const date = new Date(d.updated_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });

        item.innerHTML = `
            <div class="drawing-info" style="flex:1; min-width:0; cursor:pointer;">
                <div style="font-size:13px; font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${d.title}</div>
                <div style="font-size:10px; color:var(--text-muted);">${date}</div>
            </div>
            <button class="drawing-action drawing-link" title="Copy link"><i class="fas fa-link"></i></button>
            <button class="drawing-action drawing-delete" title="Delete"><i class="fas fa-trash"></i></button>
        `;

        item.querySelector('.drawing-info').addEventListener('click', () => loadDrawing(d.id));
        item.querySelector('.drawing-link').addEventListener('click', async (e) => {
            e.stopPropagation();
            const url = `${window.location.origin}${BASE_PATH}/drawer?drawing=${d.id}`;
            await navigator.clipboard.writeText(url);
            showToast('Link copied to clipboard', 'fa-link');
        });
        item.querySelector('.drawing-delete').addEventListener('click', (e) => {
            e.stopPropagation();
            deleteDrawing(d.id);
        });

        list.appendChild(item);
    }
}

// ==========================================================================
// EXPORT (SVG / PNG)
// ==========================================================================
document.getElementById('exportBtn').addEventListener('click', async () => {
    const choice = await new Promise((resolve) => {
        // Simple export menu using confirmDialog-style approach
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay visible';
        overlay.innerHTML = `
            <div class="modal-card">
                <div class="modal-icon"><i class="fas fa-download"></i></div>
                <div class="modal-title">Export Drawing</div>
                <div class="modal-message">Choose export format:</div>
                <div class="modal-actions" style="flex-wrap:wrap; gap:8px;">
                    <button class="modal-btn modal-btn-primary" data-fmt="svg"><i class="fas fa-file-code"></i> SVG</button>
                    <button class="modal-btn modal-btn-primary" data-fmt="png"><i class="fas fa-file-image"></i> PNG</button>
                    <button class="modal-btn" data-fmt="cancel">Cancel</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        overlay.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-fmt]');
            if (btn) {
                overlay.remove();
                resolve(btn.dataset.fmt);
            }
        });
    });

    if (choice === 'cancel') return;

    const canvas = editor.precanvas;
    if (!canvas) return;

    if (choice === 'svg') {
        exportAsSvg(canvas);
    } else if (choice === 'png') {
        exportAsPng(canvas);
    }
});

function exportAsSvg(sourceEl) {
    // Clone the visible area and serialize as SVG foreignObject
    const rect = sourceEl.getBoundingClientRect();
    const clone = sourceEl.cloneNode(true);

    const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${rect.width}" height="${rect.height}">
  <foreignObject width="100%" height="100%">
    <div xmlns="http://www.w3.org/1999/xhtml" style="font-family:Inter,sans-serif;">
      ${clone.outerHTML}
    </div>
  </foreignObject>
</svg>`;

    downloadFile(svg, 'pipeline-drawing.svg', 'image/svg+xml');
}

function exportAsPng(sourceEl) {
    // Use html2canvas-like approach via canvas
    const svgData = `<svg xmlns="http://www.w3.org/2000/svg" width="${sourceEl.scrollWidth}" height="${sourceEl.scrollHeight}">
      <foreignObject width="100%" height="100%">
        <div xmlns="http://www.w3.org/1999/xhtml" style="font-family:Inter,sans-serif;">
          ${sourceEl.cloneNode(true).outerHTML}
        </div>
      </foreignObject>
    </svg>`;

    const img = new Image();
    const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);

    img.onload = () => {
        const canvas = document.createElement('canvas');
        canvas.width = sourceEl.scrollWidth * 2;
        canvas.height = sourceEl.scrollHeight * 2;
        const ctx = canvas.getContext('2d');
        ctx.scale(2, 2);
        ctx.fillStyle = '#fafbfc';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0);
        URL.revokeObjectURL(url);

        canvas.toBlob((pngBlob) => {
            const a = document.createElement('a');
            a.href = URL.createObjectURL(pngBlob);
            a.download = 'pipeline-drawing.png';
            a.click();
            URL.revokeObjectURL(a.href);
        });
    };
    img.src = url;
}

function downloadFile(content, filename, mime) {
    const blob = new Blob([content], { type: mime });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
}

// --- Init ---
const catalog = await loadCatalog();
renderPalette(catalog);
refreshDrawingsList();

// Auto-load drawing from URL param ?drawing=ID
const urlParams = new URLSearchParams(window.location.search);
const loadId = urlParams.get('drawing');
if (loadId) {
    loadDrawing(parseInt(loadId));
}


// --- Propose: opens a pull request on the framework, as the chat does ---
document.getElementById('proposeDrawerBtn').addEventListener('click', async () => {
    const code = document.getElementById('resultCode').textContent;
    if (!code) return;

    const btn = document.getElementById('proposeDrawerBtn');
    btn.disabled = true;

    // the reviewer needs to know whether it validates: run it first
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Validating...';
    let validation = null;
    try {
        const vr = await apiFetch('/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nextflow_code: code }),
        });
        validation = await vr.json();
    } catch (e) {
        validation = null;
    }

    const title = (document.getElementById('drawingTitle')?.textContent || 'pipeline').trim();
    const suggested = title.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 40);
    const name = await promptDialog({
        title: 'Name the module',
        message: 'The pipeline will be committed as <code>modules/module_&lt;name&gt;.nf</code> on a new branch.',
        placeholder: 'e.g. listeria_typing',
        initialValue: suggested || 'pipeline',
        confirmText: 'Open pull request',
        icon: 'fa-code-branch',
        maxLength: 40,
    });
    if (!name) {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-code-branch"></i> Propose';
        return;
    }

    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Opening PR...';
    let result;
    try {
        const res = await apiFetch('/publish', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                nextflow_code: code,
                name: name,
                description: title,
                // from the canvas the topology was decided by the operator, not
                // the model: that is the "request" a reviewer needs to read
                user_query: 'Drawn on the canvas: ' + (window.__graphTopology || ''),
                components: window.__graphComponents || [],
                validation: validation,
                model: window.__llmModel || '',
                plugin: window.__activePlugin || '',
            }),
        });
        result = await res.json();
        if (!res.ok) result = { error: result.detail || 'request failed' };
    } catch (e) {
        result = { error: String(e) };
    }

    btn.disabled = false;
    if (result.error) {
        btn.innerHTML = '<i class="fas fa-times-circle"></i> Failed';
        btn.classList.add('validate-fail');
        confirmDialog({
            title: 'Could not open the pull request',
            message: `<code style="display:block;padding:6px 10px;background:#fef2f2;border-radius:6px;font-size:12px;color:#991b1b;word-break:break-word;">${String(result.error).replace(/</g, '&lt;')}</code>`,
            confirmText: 'OK', cancelText: '', danger: true, icon: 'fa-times-circle',
        });
    } else {
        btn.innerHTML = '<i class="fas fa-check-circle"></i> Proposed';
        btn.classList.add('validate-pass');
        confirmDialog({
            title: 'Pipeline proposed',
            message: `A draft pull request was opened on the framework. It has not been merged and nothing runs until a reviewer approves it.<br><br>
                <a href="${result.pull_request_url}" target="_blank" rel="noopener" style="display:block;padding:8px 12px;background:#f0fdf4;border-radius:6px;color:#166534;word-break:break-all;">${result.pull_request_url}</a>
                <br><code style="font-size:12px;color:#475569;">branch ${result.branch}<br>commit ${String(result.commit_sha).slice(0, 10)}</code>`,
            confirmText: 'OK', cancelText: '', icon: 'fa-code-branch',
        });
    }
    setTimeout(() => {
        btn.innerHTML = '<i class="fas fa-code-branch"></i> Propose';
        btn.classList.remove('validate-pass', 'validate-fail');
    }, 8000);
});


// --- Drawings column: collapses to give the canvas the full width, and the
//     choice is remembered between sessions. ---
const drawingsPanel   = document.getElementById('drawingsPanel');
const drawingsReopen  = document.getElementById('drawingsReopen');
const drawingsCollapse = document.getElementById('drawingsCollapse');

function setDrawingsVisible(visible) {
    drawingsPanel.classList.toggle('collapsed', !visible);
    drawingsReopen.classList.toggle('visible', !visible);
    try { localStorage.setItem('izs_drawings_panel', visible ? 'open' : 'closed'); } catch (e) {}
}

drawingsCollapse.addEventListener('click', () => setDrawingsVisible(false));
drawingsReopen.addEventListener('click', () => setDrawingsVisible(true));

let savedState = 'open';
try { savedState = localStorage.getItem('izs_drawings_panel') || 'open'; } catch (e) {}
setDrawingsVisible(savedState !== 'closed');


// "New drawing" clears the canvas the way "New chat" clears the conversation
document.getElementById('newDrawingBtn').addEventListener('click', () => {
    document.getElementById('clearBtn').click();
});


// log out, as in the chat
const logoutEl = document.getElementById('logoutBtn');
if (logoutEl) logoutEl.addEventListener('click', async () => {
    const { logout } = await import('./api.js?v=25');
    logout();
});
