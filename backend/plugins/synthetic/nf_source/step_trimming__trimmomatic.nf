nextflow.enable.dsl=2

// Synthetic step: Read trimming with Trimmomatic
include { parseMetadataFromFileName; executionMetadata } from '../functions/common.nf'

def ex = executionMetadata()

process TRIMMOMATIC {
    container 'biocontainers/trimmomatic:0.39'
    publishDir "${params.outdir}/${ex.ds}/${ex.stageDir}/step_trimming__trimmomatic/${sample_id}", mode: 'copy'

    input:
    tuple val(sample_id), path(reads)

    output:
    tuple val(sample_id), path("*_trimmed.fastq.gz"), emit: trimmed_reads
    path("*_unpaired.fastq.gz"), emit: unpaired

    script:
    """
    trimmomatic PE $reads \\
      ${sample_id}_R1_trimmed.fastq.gz ${sample_id}_R1_unpaired.fastq.gz \\
      ${sample_id}_R2_trimmed.fastq.gz ${sample_id}_R2_unpaired.fastq.gz \\
      ILLUMINACLIP:adapters.fa:2:30:10 \\
      LEADING:3 TRAILING:3 SLIDINGWINDOW:4:15 MINLEN:36
    """
}

workflow step_trimming__trimmomatic {
    take:
    reads

    main:
    TRIMMOMATIC(reads)

    emit:
    trimmed = TRIMMOMATIC.out.trimmed_reads
}
