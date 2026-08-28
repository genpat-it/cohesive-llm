nextflow.enable.dsl=2

// Synthetic step: De novo assembly with SPAdes
include { parseMetadataFromFileName; executionMetadata } from '../functions/common.nf'

def ex = executionMetadata()

process SPADES {
    container 'biocontainers/spades:3.15.5'
    publishDir "${params.outdir}/${ex.ds}/${ex.stageDir}/step_assembly__spades/${sample_id}", mode: 'copy'

    input:
    tuple val(sample_id), path(reads)

    output:
    tuple val(sample_id), path("${sample_id}_assembly/scaffolds.fasta"), emit: assembly
    path("${sample_id}_assembly/spades.log"), emit: log

    script:
    """
    spades.py --isolate -1 ${reads[0]} -2 ${reads[1]} \\
      -o ${sample_id}_assembly -t ${task.cpus}
    """
}

workflow step_assembly__spades {
    take:
    reads

    main:
    SPADES(reads)

    emit:
    assembly = SPADES.out.assembly
}
