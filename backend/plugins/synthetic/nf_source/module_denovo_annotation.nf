nextflow.enable.dsl=2

// Synthetic template: De novo assembly with annotation
include { step_qc__fastqc } from '../steps/step_qc__fastqc'
include { step_trimming__trimmomatic } from '../steps/step_trimming__trimmomatic'
include { step_assembly__spades } from '../steps/step_assembly__spades'
include { step_annotation__prokka } from '../steps/step_annotation__prokka'
include { getSingleInput } from '../functions/parameters.nf'

workflow module_denovo_annotation {
    take:
        reads

    main:
        step_qc__fastqc(reads)

        trimmed = step_trimming__trimmomatic(reads).trimmed

        assembled = step_assembly__spades(trimmed).assembly

        // Prokka is a void tool — no emit channels
        step_annotation__prokka(assembled)

    emit:
        assembly = assembled
}

workflow {
    module_denovo_annotation(getSingleInput())
}
