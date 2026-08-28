nextflow.enable.dsl=2

// Synthetic template: Basic Illumina variant calling pipeline
include { step_qc__fastqc } from '../steps/step_qc__fastqc'
include { step_trimming__trimmomatic } from '../steps/step_trimming__trimmomatic'
include { step_mapping__bwa_mem2 } from '../steps/step_mapping__bwa_mem2'
include { step_variant__bcftools } from '../steps/step_variant__bcftools'
include { extractKey } from '../functions/common.nf'
include { getSingleInput; getReference } from '../functions/parameters.nf'

workflow module_illumina_variant_calling {
    take:
        reads
        reference

    main:
        step_qc__fastqc(reads)

        trimmed = step_trimming__trimmomatic(reads).trimmed

        trimmed.cross(reference) { extractKey(it) }
            .multiMap {
                reads: it[0]
                refs:  it[1][1..3]
            }
            .set { readsAndReferences }

        mapped = step_mapping__bwa_mem2(readsAndReferences.reads, readsAndReferences.refs)

        step_variant__bcftools(mapped.aligned_bam, readsAndReferences.refs)

    emit:
        variants = step_variant__bcftools.out.variants
}

workflow {
    module_illumina_variant_calling(getSingleInput(), getReference('fa'))
}
