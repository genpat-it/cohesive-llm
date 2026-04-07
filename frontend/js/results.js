import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10.6.1/dist/mermaid.esm.min.mjs';

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

    copyNextflowBtn.addEventListener('click', () => copyToClipboard(rawNextflowData, copyNextflowBtn));
    copyMermaidBtn.addEventListener('click', () => copyToClipboard(rawMermaidData, copyMermaidBtn));
    
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
        
        const beautified = customFormat(code);
        rawMermaidData = beautified;
        mermaidCodeBlock.textContent = beautified;
        Prism.highlightElement(mermaidCodeBlock);
        
        if (isMermaidCodeView) {
            mermaidCodeContainer.style.display = 'block';
        } else {
            mermaidContainer.style.display = 'flex';
        }
        
        mermaidDiagram.innerHTML = `<div class="mermaid">${code}</div>`;
        
        try {
            mermaid.init(undefined, mermaidDiagram.querySelector('.mermaid'));
            setTimeout(() => {
                const svg = mermaidDiagram.querySelector('svg');
                if (svg) {
                    resetZoom();
                    addZoomControls(mermaidContainer);
                }
            }, 100);
        } catch (err) {
            mermaidDiagram.innerHTML = `<div style="color:red; padding:20px;">Render failed: ${err.message}</div>`;
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
