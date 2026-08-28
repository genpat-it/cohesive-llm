nextflow.enable.dsl=2

// Synthetic step: Genome annotation with Prokka (void tool — no emit channels)
include { parseMetadataFromFileName; executionMetadata } from '../functions/common.nf'

def ex = executionMetadata()

process PROKKA {
    container 'biocontainers/prokka:1.14.6'
    publishDir "${params.outdir}/${ex.ds}/${ex.stageDir}/step_annotation__prokka/${sample_id}", mode: 'copy'

    input:
    tuple val(sample_id), path(assembly)

    output:
    path("${sample_id}_prokka/*")

    script:
    """
    prokka --outdir ${sample_id}_prokka --prefix ${sample_id} \\
      --cpus ${task.cpus} $assembly
    """
}

workflow step_annotation__prokka {
    take:
    assembly

    main:
    PROKKA(assembly)
}
