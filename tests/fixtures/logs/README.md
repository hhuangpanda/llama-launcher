Captured 2026-11-20 from llama-server b11860 on an RTX 5080 Laptop GPU,
except `synthetic-oom.log`.

| file | exit | what |
|---|---|---|
| unknown-argument | 1 | `--bogus-flag` |
| file-not-found | 1 | `-m` points at a missing file |
| mmproj-mismatch | 1 | Gemma model + Qwen3.6 mmproj |
| draft-mismatch | 0xC0000005 | Qwen3.8 GSQ + Gemma MTP draft: crash |
| mtp-without-head | 1 | `--spec-type draft-mtp` on a model with no MTP head and no draft |
| oversized-ctx-loaded | ready | 262144 ctx f16 KV with `--fit off`: loads anyway (WDDM spills to shared memory) |
| port-shared-loaded | ready | `--port 9931` while another server listens there: binds anyway (address reuse) |
| sleep-wake-run | ready | `--sleep-idle-seconds 15`: sleep, then wake on a chat request |
| synthetic-oom | - | hand-written from llama.cpp's CUDA allocation failure messages |
