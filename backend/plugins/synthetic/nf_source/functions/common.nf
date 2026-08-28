// Synthetic helper functions — mirrors cohesive-ngsmanager's functions/common.nf
// These stubs exist so that `include` statements in nf_source/ resolve properly.

def extractKey(item) {
    // Extract the keying element (riscd) from a tuple for .cross() operations
    return item[0]
}

def extractDsRef(item) {
    // Extract dataset-reference compound key
    return item[0]
}

def getEmpty() {
    return file('NO_FILE')
}

def parseMetadataFromFileName(filename) {
    return [:]
}

def executionMetadata() {
    return [
        ds: params.containsKey('ds') ? params.ds : 'default',
        stageDir: params.containsKey('stageDir') ? params.stageDir : 'results'
    ]
}

def taskMemory(task, fallback) {
    return task.memory ?: fallback
}
