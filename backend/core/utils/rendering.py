NF_TEMPLATE_AST = """nextflow.enable.dsl=2

// --- IMPORTS ---
{% for imp in imports -%}
{%- set funcs = imp.functions | join('; ') -%}
include { {{ funcs }} } from '{{ imp.module_path }}'
{% endfor %}
// --- GLOBALS ---
{% for g in globals -%}
{{ g.type }} {{ g.name }} = {{ g.value }}
{% endfor %}
// --- INLINE PROCESSES ---
{% for p in inline_processes -%}
process {{ p.name }} {
    {% if p.container %}container '{{ p.container }}'{% endif %}
    {%- if p.input_declarations %}
    input:
    {% for ind in p.input_declarations %}
    {{ ind }}
    {% endfor %}
    {%- endif %}
    {%- if p.output_declarations %}
    output:
    {% for outd in p.output_declarations %}
    {{ outd }}
    {% endfor %}
    {%- endif %}
    script:
    \"\"\"
{{ p.script_block }}
    \"\"\"
}
{% endfor %}
// --- SUB WORKFLOWS ---
{% for sw in sub_workflows -%}
workflow {{ sw.name }} {
{%- if sw.take_channels %}
    take:
        {% for ch in sw.take_channels %}{{ ch }}{% if not loop.last %}\n        {% endif %}{% endfor %}
{% endif %}
    main:
{{ sw.body_code | indent(8, true) }}
{%- if sw.emit_channels %}
    emit:
        {% for em in sw.emit_channels %}{{ em }}{% if not loop.last %}\n        {% endif %}{% endfor %}
{% endif %}
}
{% endfor %}
// --- ENTRYPOINT ---
workflow {
{{ entrypoint.body_code | indent(4, true) }}
}
"""
