PARSER_VERSION = "v1"
# v2: 2 KB chunks; container types (class/struct/...) keep only their header chunk;
# whitespace-only gaps are not chunks.
CHUNKING_VERSION = "v2"
LEXICAL_INDEX_VERSION = "v1"
# v3: size cap, binary sniffing, vendored/generated exclusions, docs and config text files.
INCLUSION_RULES_VERSION = "v3"
# v2: the embedding input starts with the file path ("path :: Name(signature)").
EMBEDDING_INPUT_VERSION = "v2"
# Small enough that a whole chunk fits the embedding model's input window (~512 tokens of code).
MAX_CHUNK_BYTES = 2048
