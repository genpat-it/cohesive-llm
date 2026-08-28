nextflow.enable.dsl=2

// Synthetic step: Quality control with FastQC (void tool — publishDir only)
include { parseMetadataFromFileName; executionMetadata } from '../functions/common.nf'

def ex = executionMetadata()

process FASTQC {
    container 'biocontainers/fastqc:0.12.1'
    publishDir "${params.outdir}/${ex.ds}/${ex.stageDir}/step_qc__fastqc/${sample_id}", mode: 'copy'

    input:
    tuple val(sample_id), path(reads)

    output:
    path("*_fastqc.{html,zip}")

    script:
    """
    fastqc $reads --threads ${task.cpus}
    """
}

workflow step_qc__fastqc {
    take:
    reads

    main:
    FASTQC(reads)
}
