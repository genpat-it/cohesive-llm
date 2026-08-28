// Synthetic parameter functions — mirrors cohesive-ngsmanager's functions/parameters.nf
// These stubs exist so that `include` statements in nf_source/ resolve properly.

def getSingleInput() {
    if (!params.containsKey('input')) {
        exit 2, "missing required param: input"
    }
    return Channel.fromFilePairs(params.input)
}

def getInput() {
    return getSingleInput()
}

def getReference(format) {
    if (!params.containsKey('reference')) {
        exit 2, "missing required param: reference"
    }
    def ref = file(params.reference)
    return Channel.of([ params.containsKey('ds') ? params.ds : 'default', 'ref', ref ])
}

def getHost() {
    if (!params.containsKey('host')) {
        exit 2, "missing required param: host"
    }
    return Channel.of([ params.ds ?: 'default', file(params.host) ])
}

def hasFastqData(reads) {
    return reads != null && reads.size() > 0
}

def hasEnoughFastqData(reads) {
    return hasFastqData(reads)
}

def isIlluminaPaired(reads) {
    return reads instanceof List && reads.size() == 2
}

def isIonTorrent(reads) {
    return false
}

def isNanopore(reads) {
    return false
}

def param(key) {
    return params.containsKey(key) ? params[key] : null
}
