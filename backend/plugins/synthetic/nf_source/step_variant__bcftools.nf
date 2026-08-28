nextflow.enable.dsl=2

// Synthetic step: Variant calling with BCFtools
include { parseMetadataFromFileName; executionMetadata } from '../functions/common.nf'

def ex = executionMetadata()

process BCFTOOLS_CALL {
    container 'biocontainers/bcftools:1.19'
    publishDir "${params.outdir}/${ex.ds}/${ex.stageDir}/step_variant__bcftools/${sample_id}", mode: 'copy'

    input:
    tuple val(sample_id), path(bam)
    tuple val(ref_riscd), val(ref_code), path(reference)

    output:
    tuple val(sample_id), path("*.vcf.gz"), emit: variants

    script:
    """
    bcftools mpileup -f $reference $bam | \\
      bcftools call -mv -Oz -o ${sample_id}.vcf.gz
    """
}

workflow step_variant__bcftools {
    take:
    aligned_bam
    reference

    main:
    BCFTOOLS_CALL(aligned_bam, reference)

    emit:
    variants = BCFTOOLS_CALL.out.variants
}
