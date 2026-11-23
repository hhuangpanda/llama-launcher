"""llama-server flags the launcher sets or reads itself, by their canonical long spelling."""

HOST = "--host"
PORT = "--port"
API_KEY = "--api-key"
API_KEY_FILE = "--api-key-file"
MODEL = "--model"
ALIAS = "--alias"
MMPROJ = "--mmproj"
DRAFT_MODEL = "--spec-draft-model"
CTX_SIZE = "--ctx-size"
CACHE_TYPE_K = "--cache-type-k"
CACHE_TYPE_V = "--cache-type-v"
SLEEP_IDLE_SECONDS = "--sleep-idle-seconds"
METRICS = "--metrics"
METRICS_MIN_BUILD = 10519  # from here /metrics neither wakes a sleeping server nor resets its idle timer
SPEC_TYPE = "--spec-type"
DRAFT_MTP = "draft-mtp"  # a SPEC_TYPE value
VERSION = "--version"
HELP = "--help"

# Dataclass field -> flag, for fields that become a single flag when set.
SIDECAR_FLAGS = {"mmproj": MMPROJ, "draft": DRAFT_MODEL}  # ModelEntry
CACHE_FLAGS = {"cache_type_k": CACHE_TYPE_K, "cache_type_v": CACHE_TYPE_V}  # GlobalOptions and Profile
PROFILE_FLAGS = {"ctx_size": CTX_SIZE, **CACHE_FLAGS}  # Profile

CONNECTION = (HOST, PORT, API_KEY, API_KEY_FILE)  # from settings; the API key goes in the environment
RESERVED = (*CONNECTION, MODEL)  # never allowed in args
FIELDS = (MODEL, ALIAS, *SIDECAR_FLAGS.values(), *PROFILE_FLAGS.values())  # stored as fields, not args
MANAGED = (*FIELDS, *CONNECTION, SLEEP_IDLE_SECONDS)  # everything the launcher sets itself
