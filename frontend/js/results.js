import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10.6.1/dist/mermaid.esm.min.mjs';
import { validatePipeline, publishPipeline } from './api.js?v=29';
import { confirmDialog, promptDialog } from './modal.js?v=29';

// Initialize Mermaid with updated configuration
mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    flowchart: {
        useMaxWidth: false,
        htmlLabels: true,
        curve: 'basis'
    },
    securityLevel: 'loose'
});

let isMermaidCodeView = false;
let rawNextflowData = "";
let rawMermaidData = "";

// Zoom & Pan state
let currentScale = 1;
let translateX = 0;
let translateY = 0;
let isDragging = false;
let startX = 0;
let startY = 0;

export function initResultsUi() {
    const nextflowCodeContainer = document.getElementById('nextflowCodeContainer');
    const nextflowCodeBlock = document.getElementById('nextflowCodeBlock');
    const nextflowEmpty = document.getElementById('nextflowEmpty');
    const copyNextflowBtn = document.getElementById('copyNextflowBtn');
    
    const mermaidContainer = document.getElementById('mermaidContainer');
    const mermaidDiagram = document.getElementById('mermaidDiagram');
    const mermaidCodeContainer = document.getElementById('mermaidCodeContainer');
    const mermaidCodeBlock = document.getElementById('mermaidCodeBlock');
    const mermaidEmpty = document.getElementById('mermaidEmpty');
    const toggleMermaidBtn = document.getElementById('toggleMermaidBtn');
    const copyMermaidBtn = document.getElementById('copyMermaidBtn');

    const validateBtn = document.getElementById('validateBtn');
    const publishBtn = document.getElementById('publishBtn');

    if (publishBtn) {
        publishBtn.addEventListener('click', async () => {
            if (!rawNextflowData) return;

            // The reviewer needs to know whether it validates: run it first.
            publishBtn.disabled = true;
            publishBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Validating...';
            const validation = await validatePipeline(rawNextflowData);

            const suggested = (window.__lastUserQuery || 'pipeline')
                .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 40);
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
                publishBtn.disabled = false;
                publishBtn.innerHTML = '<i class="fas fa-code-branch"></i> Propose';
                return;
            }

            publishBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Opening PR...';
            const result = await publishPipeline({
                nextflow_code: rawNextflowData,
                name: name,
                description: window.__lastUserQuery || name,
                user_query: window.__lastUserQuery || '',
                components: window.__lastComponents || [],
                validation: validation,
                model: window.__llmModel || '',
                plugin: window.__activePlugin || '',
            });

            publishBtn.disabled = false;
            if (result.error) {
                publishBtn.innerHTML = '<i class="fas fa-times-circle"></i> Failed';
                publishBtn.classList.add('validate-fail');
                confirmDialog({
                    title: 'Could not open the pull request',
                    message: `<code style="display:block;padding:6px 10px;background:#fef2f2;border-radius:6px;font-size:12px;color:#991b1b;word-break:break-word;">${String(result.error).replace(/</g, '&lt;')}</code>`,
                    confirmText: 'OK', cancelText: '', danger: true, icon: 'fa-times-circle',
                });
            } else {
                publishBtn.innerHTML = '<i class="fas fa-check-circle"></i> Proposed';
                publishBtn.classList.add('validate-pass');
                confirmDialog({
                    title: 'Pipeline proposed',
                    message: `A draft pull request was opened on the framework. It has not been merged and nothing runs until a reviewer approves it.<br><br>
                        <a href="${result.pull_request_url}" target="_blank" rel="noopener" style="display:block;padding:8px 12px;background:#f0fdf4;border-radius:6px;color:#166534;word-break:break-all;">${result.pull_request_url}</a>
                        <br><code style="font-size:12px;color:#475569;">branch ${result.branch}<br>commit ${result.commit_sha.slice(0, 10)}</code>`,
                    confirmText: 'OK', cancelText: '', icon: 'fa-code-branch',
                });
            }
            setTimeout(() => {
                publishBtn.innerHTML = '<i class="fas fa-code-branch"></i> Propose';
                publishBtn.classList.remove('validate-pass', 'validate-fail');
            }, 8000);
        });
    }


    const openMermaidLiveBtn = document.getElementById('openMermaidLiveBtn');

    copyNextflowBtn.addEventListener('click', () => copyToClipboard(rawNextflowData, copyNextflowBtn));

    if (validateBtn) {
        validateBtn.addEventListener('click', async () => {
            if (!rawNextflowData) return;
            validateBtn.disabled = true;
            validateBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Validating...';

            const result = await validatePipeline(rawNextflowData);

            if (result.success && (!result.warnings || result.warnings.length === 0)) {
                validateBtn.innerHTML = '<i class="fas fa-check-circle"></i> Valid';
                validateBtn.classList.add('validate-pass');
                validateBtn.classList.remove('validate-fail');
            } else if (result.success && result.warnings && result.warnings.length > 0) {
                validateBtn.innerHTML = '<i class="fas fa-check-circle"></i> Valid';
                validateBtn.classList.add('validate-pass');
                validateBtn.classList.remove('validate-fail');
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
                validateBtn.innerHTML = '<i class="fas fa-times-circle"></i> Invalid';
                validateBtn.classList.add('validate-fail');
                validateBtn.classList.remove('validate-pass');
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

            validateBtn.disabled = false;
            setTimeout(() => {
                validateBtn.innerHTML = '<i class="fas fa-play-circle"></i> Validate';
                validateBtn.classList.remove('validate-pass', 'validate-fail');
            }, 5000);
        });
    }
    copyMermaidBtn.addEventListener('click', () => copyToClipboard(rawMermaidData, copyMermaidBtn));

    if (openMermaidLiveBtn) {
        openMermaidLiveBtn.addEventListener('click', () => {
            if (!rawMermaidData) return;
            const state = {
                code: rawMermaidData,
                mermaid: { theme: 'default' },
                autoSync: true,
                updateDiagram: true,
            };
            const json = JSON.stringify(state);
            // pako-less approach: use base64 encoding for mermaid.live
            const encoded = btoa(unescape(encodeURIComponent(json)));
            window.open(`https://mermaid.live/edit#base64:${encoded}`, '_blank');
        });
    }
    
    toggleMermaidBtn.addEventListener('click', () => {
        if (!rawMermaidData) return;
        isMermaidCodeView = !isMermaidCodeView;
        if (isMermaidCodeView) {
            mermaidContainer.style.display = 'none';
            mermaidCodeContainer.style.display = 'block';
            toggleMermaidBtn.classList.add('active');
        } else {
            mermaidContainer.style.display = 'flex';
            mermaidCodeContainer.style.display = 'none';
            toggleMermaidBtn.classList.remove('active');
        }
    });

    initPanZoom(mermaidContainer, mermaidDiagram);

    return { renderNextflow, renderMermaid, clearResults };

    function clearResults() {
        nextflowCodeBlock.textContent = '';
        nextflowCodeContainer.style.display = 'none';
        nextflowEmpty.style.display = 'flex';
        
        mermaidDiagram.innerHTML = '';
        mermaidContainer.style.display = 'none';
        mermaidCodeContainer.style.display = 'none';
        mermaidCodeBlock.textContent = '';
        mermaidEmpty.style.display = 'flex';
    }

    function renderNextflow(code) {
        if (!code) return;
        nextflowEmpty.style.display = 'none';
        
        const beautified = customFormat(code, true); // true = skip smart indent for nextflow
        rawNextflowData = beautified;
        nextflowCodeBlock.textContent = beautified;
        nextflowCodeContainer.style.display = 'block';
        Prism.highlightElement(nextflowCodeBlock);
        nextflowCodeContainer.scrollTop = 0;
    }

    function renderMermaid(code) {
        if (!code) return;
        mermaidEmpty.style.display = 'none';

        const cleaned = cleanMermaidCode(code);
        const beautified = customFormat(cleaned);
        rawMermaidData = beautified;
        mermaidCodeBlock.textContent = beautified;
        Prism.highlightElement(mermaidCodeBlock);

        if (isMermaidCodeView) {
            mermaidCodeContainer.style.display = 'block';
        } else {
            mermaidContainer.style.display = 'flex';
        }

        function tryRender(diagramCode) {
            mermaidDiagram.innerHTML = `<div class="mermaid">${diagramCode}</div>`;
            mermaid.init(undefined, mermaidDiagram.querySelector('.mermaid'));
            setTimeout(() => {
                const svg = mermaidDiagram.querySelector('svg');
                if (svg) {
                    resetZoom();
                    addZoomControls(mermaidContainer);
                }
            }, 100);
        }

        try {
            tryRender(cleaned);
        } catch (err) {
            console.warn('Initial Mermaid parse failed, attempting auto-repair...', err);
            try {
                const repaired = autoRepairMermaid(cleaned);
                tryRender(repaired);
                // Subtle badge indicating diagram was auto-recovered
                const note = document.createElement('div');
                note.style.cssText = 'position:absolute; bottom:12px; left:12px; font-size:11px; color:var(--text-muted); background:rgba(0,0,0,0.06); padding:3px 8px; border-radius:4px; pointer-events:none; z-index:5;';
                note.innerHTML = '<i class="fas fa-magic" style="margin-right:4px;"></i>Diagram Auto-Recovered';
                mermaidContainer.appendChild(note);
            } catch (err2) {
                console.warn('Auto-repair failed, building topological fallback...', err2);
                try {
                    const fallback = buildTopologicalFallback(cleaned);
                    tryRender(fallback);
                } catch (err3) {
                    console.error('All Mermaid render attempts failed:', err3);
                    mermaidDiagram.innerHTML = `
                        <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100%; color:var(--text-muted); text-align:center; padding:24px;">
                            <i class="fas fa-project-diagram" style="font-size:32px; margin-bottom:12px; opacity:0.4;"></i>
                            <div style="font-size:13px; font-weight:500; margin-bottom:4px;">Diagram Render Notice</div>
                            <div style="font-size:12px; opacity:0.7; max-width:320px; margin-bottom:12px;">The diagram structure could not be rendered graphically. You can inspect or copy the Mermaid syntax in Code View.</div>
                            <button class="control-btn active" style="font-size:12px; padding:6px 12px;" onclick="document.getElementById('toggleMermaidBtn').click();">
                                <i class="fas fa-code" style="margin-right:6px;"></i>Switch to Code View
                            </button>
                        </div>
                    `;
                }
            }
        }
    }
}

function initPanZoom(container, diagram) {
    container.addEventListener('wheel', (e) => {
        e.preventDefault();
        const delta = Math.sign(e.deltaY) * -0.1;
        zoomChart(diagram, delta);
    });

    container.addEventListener('mousedown', (e) => {
        isDragging = true;
        startX = e.clientX - translateX;
        startY = e.clientY - translateY;
    });

    window.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        translateX = e.clientX - startX;
        translateY = e.clientY - startY;
        applyTransform(diagram);
    });

    window.addEventListener('mouseup', () => {
        isDragging = false;
    });
}

function zoomChart(diagram, delta) {
    currentScale += delta;
    if (currentScale < 0.2) currentScale = 0.2;
    if (currentScale > 4) currentScale = 4;
    applyTransform(diagram);
}

function applyTransform(diagram) {
    const svg = diagram.querySelector('svg');
    if (svg) {
        svg.style.transform = `translate(${translateX}px, ${translateY}px) scale(${currentScale})`;
    }
}

function resetZoom() {
    currentScale = 1;
    translateX = 0;
    translateY = 0;
    const diagram = document.getElementById('mermaidDiagram');
    if(diagram) applyTransform(diagram);
}

function addZoomControls(container) {
    // Remove old controls if any
    const old = container.querySelector('.zoom-controls');
    if (old) old.remove();

    const controls = document.createElement('div');
    controls.className = 'zoom-controls';
    controls.innerHTML = `
        <button id="zoomIn" title="Zoom In"><i class="fas fa-plus"></i></button>
        <button id="zoomOut" title="Zoom Out"><i class="fas fa-minus"></i></button>
        <button id="zoomReset" title="Reset Zoom"><i class="fas fa-expand"></i></button>
    `;

    container.appendChild(controls);

    const diagram = document.getElementById('mermaidDiagram');
    
    controls.querySelector('#zoomIn').addEventListener('click', (e) => {
        e.stopPropagation();
        zoomChart(diagram, 0.2);
    });
    
    controls.querySelector('#zoomOut').addEventListener('click', (e) => {
        e.stopPropagation();
        zoomChart(diagram, -0.2);
    });
    
    controls.querySelector('#zoomReset').addEventListener('click', (e) => {
        e.stopPropagation();
        resetZoom();
    });
}

async function copyToClipboard(text, btnElement) {
    if (!text) return;
    try {
        await navigator.clipboard.writeText(text);
        const originalIcon = btnElement.innerHTML;
        btnElement.innerHTML = '<i class="fas fa-check"></i>';
        btnElement.style.color = 'var(--success)';
        btnElement.style.borderColor = 'var(--success)';
        
        setTimeout(() => {
            btnElement.innerHTML = originalIcon;
            btnElement.style.color = '';
            btnElement.style.borderColor = '';
        }, 1500);
    } catch(err) { console.error(err); }
}

function customFormat(code, skipIndent = false) {
    if (!code) return '';
    // Handle literal \\n that might come from stringified JSON
    let rawCode = code.replace(/\\n/g, '\n');
    
    if (skipIndent) {
        return rawCode;
    }
    
    const lines = rawCode.trim().split('\n');
    let indentLevel = 0;
    const formatted = [];
    for (let line of lines) {
        line = line.trim();
        if(!line) continue;
        if (line.match(/^[}\])]/) || line.startsWith('end')) indentLevel = Math.max(0, indentLevel - 1);
        formatted.push('    '.repeat(indentLevel) + line);
        if (line.endsWith('{') || line.endsWith('[') || line.endsWith('(') || (line.includes('subgraph') && !line.includes('end'))) {
            indentLevel++;
        }
    }
    return formatted.join('\n');
}

function cleanMermaidCode(raw) {
    if (!raw) return '';
    let code = raw.replace(/\\n/g, '\n').trim();
    // Strip markdown code fences if wrapped
    if (code.startsWith('```')) {
        code = code.replace(/^```[a-z]*\n?/i, '').replace(/```\s*$/, '').trim();
    }
    return code;
}

function autoRepairMermaid(raw) {
    let lines = cleanMermaidCode(raw).split('\n');
    let out = [];
    let inHeader = false;

    for (let line of lines) {
        let trimmed = line.trim();
        if (!trimmed) continue;

        // Ensure header exists
        if (trimmed.startsWith('flowchart') || trimmed.startsWith('graph')) {
            inHeader = true;
            out.push(trimmed);
            continue;
        }

        // Clean unquoted edge labels: -->|some text| => -->|"some text"|
        trimmed = trimmed.replace(/-->\|([^"|]+)\|/g, (match, label) => {
            const safe = label.trim().replace(/"/g, "'");
            return `-->|"${safe}"|`;
        });

        // Strip double arrows / dangling arrows
        if (trimmed.endsWith('-->')) {
            continue;
        }

        out.push(trimmed);
    }

    if (!out.some(l => l.startsWith('flowchart') || l.startsWith('graph'))) {
        out.unshift('flowchart TD');
    }

    return out.join('\n');
}

function buildTopologicalFallback(raw) {
    const lines = cleanMermaidCode(raw).split('\n');
    const nodes = new Set();
    const edges = [];

    for (let line of lines) {
        const arrowMatch = line.match(/([a-zA-Z0-9_]+)\s*-->.*?([a-zA-Z0-9_]+)/);
        if (arrowMatch) {
            const src = arrowMatch[1];
            const tgt = arrowMatch[2];
            if (src !== tgt) {
                nodes.add(src);
                nodes.add(tgt);
                edges.push(`${src} --> ${tgt}`);
            }
        }
    }

    const fallbackLines = [
        'flowchart TD',
        '    classDef process fill:#4A90E2,stroke:#357ABD,stroke-width:2px,color:#fff,rx:5px,ry:5px;'
    ];

    for (const n of nodes) {
        fallbackLines.push(`    ${n}["${n}"]:::process`);
    }
    for (const e of edges) {
        fallbackLines.push(`    ${e}`);
    }

    if (edges.length === 0) {
        fallbackLines.push('    start([Start]) --> finish([Finish])');
    }

    return fallbackLines.join('\n');
}
