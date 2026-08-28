nextflow.enable.dsl=2

// Synthetic step: Read alignment with BWA-MEM2
include { parseMetadataFromFileName; executionMetadata; extractKey; extractDsRef } from '../functions/common.nf'

def ex = executionMetadata()

process BWA_MEM2 {
    container 'biocontainers/bwa-mem2:2.2.1'
    publishDir "${params.outdir}/${ex.ds}/${ex.stageDir}/step_mapping__bwa_mem2/${sample_id}", mode: 'copy'

    input:
    tuple val(sample_id), path(reads)
    tuple val(ref_riscd), val(ref_code), path(reference)

    output:
    tuple val(sample_id), path("*.sorted.bam"), emit: aligned_bam
    tuple val(sample_id), path("*.bai"), emit: bam_index

    script:
    """
    bwa-mem2 mem -t ${task.cpus} $reference $reads | \\
      samtools sort -@ ${task.cpus} -o ${sample_id}.sorted.bam
    samtools index ${sample_id}.sorted.bam
    """
}

// Coverage utilities
process SAMTOOLS_DEPTH {
    container 'biocontainers/samtools:1.17'

    input:
    tuple val(sample_id), path(bam)

    output:
    tuple val(sample_id), path("*.depth.txt"), emit: coverage

    script:
    """
    samtools depth -a $bam > ${sample_id}.depth.txt
    """
}

workflow step_mapping__bwa_mem2 {
    take:
    reads
    reference

    main:
    BWA_MEM2(reads, reference)
    SAMTOOLS_DEPTH(BWA_MEM2.out.aligned_bam)

    emit:
    aligned_bam = BWA_MEM2.out.aligned_bam
    bam_index = BWA_MEM2.out.bam_index
    coverage = SAMTOOLS_DEPTH.out.coverage
}
