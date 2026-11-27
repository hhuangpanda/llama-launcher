"""llama-server flags the launcher sets or reads itself, by their canonical long spelling."""

HOST = "--host"
PORT = "--port"
API_KEY = "--api-key"
API_KEY_FILE = "--api-key-file"
MODEL = "--model"
ALIAS = "--alias"
MMPROJ = "--mmproj"
DRAFT_MODEL = "--spec-draft-model"
LORA = "--lora"
CTX_SIZE = "--ctx-size"
CACHE_TYPE_K = "--cache-type-k"
CACHE_TYPE_V = "--cache-type-v"
SLEEP_IDLE_SECONDS = "--sleep-idle-seconds"
METRICS = "--metrics"
METRICS_MIN_BUILD = 10519  # from here /metrics neither wakes a sleeping server nor resets its idle timer
SPEC_TYPE = "--spec-type"
# SPEC_TYPE values. Without the flag (or with "none"), llama-server picks the type from the draft's header (b10413+).
DRAFT_MTP = "draft-mtp"
DRAFT_DFLASH = "draft-dflash"  # runs every DFlash-family draft (dspark, dflash2, ...) from b12377
DRAFT_SIMPLE = "draft-simple"
DFLASH_FAMILY = (DRAFT_DFLASH, "draft-dspark", "draft-dflash2", "draft-lilicorr")  # at most one per run
NGRAM_TYPES = ("ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod", "ngram-cache")
SPEC_AUTO = "auto"  # Profile.spec_type: pass no SPEC_TYPE at all, dropping an inherited one
AUTO_SPEC_MIN_BUILD = 10413
VERSION = "--version"
HELP = "--help"

# Dataclass field -> flag, for fields that become a single flag when set.
SIDECAR_FLAGS = {"mmproj": MMPROJ}  # ModelEntry
CACHE_FLAGS = {"cache_type_k": CACHE_TYPE_K, "cache_type_v": CACHE_TYPE_V}  # GlobalOptions and Profile
# Profile. A draft (speed, VRAM) and a LoRA (output) are choices one profile makes, unlike the mmproj.
PROFILE_FLAGS = {"ctx_size": CTX_SIZE, **CACHE_FLAGS, "draft": DRAFT_MODEL, "lora": LORA}

CONNECTION = (HOST, PORT, API_KEY, API_KEY_FILE)  # from settings; the API key goes in the environment
RESERVED = (*CONNECTION, MODEL)  # never allowed in args
FIELDS = (MODEL, ALIAS, *SIDECAR_FLAGS.values(), *PROFILE_FLAGS.values())  # stored as fields, not args
MANAGED = (*FIELDS, *CONNECTION, SLEEP_IDLE_SECONDS)  # everything the launcher sets itself
