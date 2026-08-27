nextflow.enable.dsl=2

process GREET {
    tag "$name"
    cpus 1
    memory '256 MB'

    input:
    val name

    output:
    path "${name}.txt"

    script:
    """
    echo "hello $name" > ${name}.txt
    sleep 1
    """
}

process COMBINE {
    cpus 1

    input:
    path files

    output:
    path "all.txt"

    script:
    """
    cat ${files} > all.txt
    """
}

workflow {
    names = Channel.of('alpha', 'beta', 'gamma')
    GREET(names)
    COMBINE(GREET.out.collect())
}
